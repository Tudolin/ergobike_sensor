"""Live workout recording.

The audio source stays open while the app runs, so the UI can show "sensor ok"
before a ride starts. Ride time is stream time minus pauses, so stored
timestamps are continuous and charts have no gaps for pauses.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from .analysis import HR_ZONES, ZONES, Athlete, estimate_kcal, hr_zone_index, zone_index
from .audio import PulseSource, ReplaySource, default_device
from .cadence import IDLE_S, CadenceTracker
from .heartrate import PULSOID_WS, HeartRateMonitor
from .pulses import POLARITIES, EdgeDetector, calibrate
from .storage import Database
from .workouts import BUILTIN, Goal, PlanTracker, Workout

FLUSH_S = 10.0
TICK_S = 0.25
LIVE_WINDOW_S = 600


class LiveStats:
    """Moving time, average cadence and time per zone, updated per pulse in O(1)."""

    def __init__(self, ppr: int):
        self.ppr = ppr
        self.moving_s = 0.0
        self.moving_intervals = 0
        self.zone_seconds = [0.0] * len(ZONES)
        self.last_t: float | None = None

    def add(self, t: float) -> None:
        if self.last_t is not None:
            iv = t - self.last_t
            if iv < IDLE_S:
                self.moving_s += iv
                self.moving_intervals += 1
                self.zone_seconds[zone_index(60.0 / (iv * self.ppr))] += iv
        self.last_t = t

    @property
    def avg_rpm(self) -> float:
        return 60.0 * self.moving_intervals / (self.moving_s * self.ppr) if self.moving_s else 0.0

    def zones(self) -> list[dict]:
        return [{"id": zid, "name": name, "lo": lo, "hi": None if hi == float("inf") else hi,
                 "seconds": round(sec, 1)}
                for (zid, name, lo, hi), sec in zip(ZONES, self.zone_seconds)]


class LiveHeart:
    """Running heart-rate stats for the ride in progress (one sample per second)."""

    def __init__(self, athlete: Athlete):
        self.athlete = athlete
        self.count = 0
        self.total = 0.0
        self.max = 0
        self.kcal = 0.0
        self.trimp = 0.0
        self.covered_s = 0.0
        self.zone_seconds = [0.0] * len(HR_ZONES)
        self.points: deque[tuple[float, int]] = deque()

    def add(self, t: float, bpm: int, dt: float) -> None:
        self.count += 1
        self.total += bpm
        self.max = max(self.max, bpm)
        self.covered_s += dt
        self.kcal += self.athlete.kcal_per_min(bpm) * dt / 60
        self.trimp += self.athlete.trimp_per_min(bpm) * dt / 60
        if (i := hr_zone_index(bpm, self.athlete.max_hr)) is not None:
            self.zone_seconds[i] += dt
        self.points.append((round(t, 1), bpm))
        while self.points[0][0] < t - LIVE_WINDOW_S:
            self.points.popleft()

    @property
    def avg(self) -> float:
        return self.total / self.count if self.count else 0.0


class Recorder:
    def __init__(self, db: Database, replay: str | None = None, rate: int = 44100,
                 hr_url: str = PULSOID_WS, hr_token: str | None = None):
        self.db = db
        self.replay = replay
        self.rate = rate
        self.hr_url = hr_url
        self.hr_token = hr_token          # demo/test override, never stored in settings
        self.heart: HeartRateMonitor | None = None
        self.live_hr: LiveHeart | None = None
        self.pending_hr: list[tuple[float, int]] = []
        self._last_hr_t: float | None = None
        self.lock = threading.RLock()
        self.source: PulseSource | None = None
        self.source_error: str | None = None
        self.state = "idle"                 # idle | running | paused
        self.session_id: int | None = None
        self.tracker: CadenceTracker | None = None
        self.stats: LiveStats | None = None
        self.workout: Workout | None = None
        self.plan: PlanTracker | None = None
        self.t_start = 0.0
        self.paused_total = 0.0
        self.paused_at: float | None = None
        self.pending: list[tuple] = []
        self.live_points: deque[tuple[float, float]] = deque()
        self.last_pulse_at: float | None = None
        self.step_level = 0.0
        self._snapshot: dict = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="recorder", daemon=True)

    # ---- lifecycle
    def start(self) -> None:
        self.db.recover_unfinished()
        self.open_source()
        self.open_heart_rate()
        self._refresh()
        self._thread.start()

    def shutdown(self) -> None:
        if self.state != "idle":
            self.finish(save=True)
        self._stop.set()
        self._thread.join(timeout=2)
        self._close_source()
        if self.heart:
            self.heart.stop()

    def _close_source(self) -> None:
        if self.source:
            try:
                self.source.stop()
            except Exception:
                pass
            self.source = None

    def open_source(self) -> None:
        """(Re)open the input with the current settings. Not allowed mid-ride."""
        with self.lock:
            if self.state != "idle":
                raise RuntimeError("finalize o treino antes de trocar o sensor")
            self._close_source()
            cfg = self.db.settings()
            if cfg["device"] is None and not self.replay:
                if (dev := default_device()) is not None:
                    cfg = self.db.update_settings({"device": dev})

            def make_detector(rate: int) -> EdgeDetector:
                return EdgeDetector(rate, threshold=float(cfg["threshold"]),
                                    polarity=POLARITIES[cfg["edge"]])

            try:
                if self.replay:
                    self.source = ReplaySource(self.replay, make_detector).start()
                else:
                    self.source = PulseSource(cfg["device"], self.rate,
                                              make_detector(self.rate)).start()
                self.source_error = None
            except Exception as e:
                self.source = None
                self.source_error = str(e)

    def open_heart_rate(self) -> None:
        """(Re)connect to Pulsoid with the token from settings. Safe to call mid-ride."""
        token = self.hr_token or (self.db.settings().get("pulsoid_token") or "").strip()
        old, self.heart = self.heart, None
        if old:
            old.stop()
        if token:
            self.heart = HeartRateMonitor(token, self.hr_url).start()

    # ---- ride control
    def _ride_time(self, stream_t: float) -> float:
        return stream_t - self.t_start - self.paused_total

    def begin(self, workout: Workout | None = None) -> int:
        with self.lock:
            if self.state != "idle":
                return self.session_id
            if not self.source:
                raise RuntimeError(self.source_error or "sensor indisponível")
            cfg = self.db.settings()
            ppr = int(cfg["pulses_per_rev"])
            self.tracker = CadenceTracker(ppr)
            self.stats = LiveStats(ppr)
            athlete = Athlete.from_settings(cfg)
            self.live_hr = LiveHeart(athlete)
            self.pending_hr = []
            self._last_hr_t = None
            self.workout = workout or BUILTIN["free"]
            self.plan = PlanTracker(self.workout) if self.workout.structured else None
            self.session_id = self.db.start_session(
                "replay" if self.replay else str(cfg["device"]), ppr,
                float(cfg["wheel_m"]), float(cfg["weight_kg"]), self.workout, athlete)
            self.t_start = self.source.stream_time
            self.paused_total, self.paused_at = 0.0, None
            self.pending = []
            self.live_points.clear()
            self.state = "running"
            self._refresh()
            return self.session_id

    def pause(self) -> None:
        with self.lock:
            if self.state == "running":
                self.paused_at = self.source.stream_time
                self.state = "paused"
                self._refresh()

    def resume(self) -> None:
        with self.lock:
            if self.state == "paused":
                self.paused_total += self.source.stream_time - self.paused_at
                self.paused_at = None
                self.state = "running"
                self._refresh()

    def finish(self, save: bool = True) -> int | None:
        with self.lock:
            if self.state == "idle":
                return None
            self.resume()
            duration = (self._ride_time(self.source.stream_time) if self.source
                        else (self.tracker.last_t or 0.0))
            sid = self.session_id
            self.db.add_revs(sid, self.pending)
            self.db.add_heart_rate(sid, self.pending_hr)
            self.pending, self.pending_hr = [], []
            self.state, self.session_id = "idle", None
            self._refresh()
            if not save:
                self.db.delete_session(sid)
                return None
            return sid if self.db.finish_session(sid, duration) else None

    def calibrate(self, seconds: float) -> dict:
        source = self.source
        if not source:
            raise RuntimeError(self.source_error or "sensor indisponível")
        return calibrate(source.capture(seconds), source.rate).to_dict()

    # ---- background loop
    def _loop(self) -> None:
        last_tick = last_flush = time.monotonic()
        while not self._stop.is_set():
            with self.lock:
                if self.source:
                    while not self.source.events.empty():
                        self._on_pulse(self.source.events.get())
                now = time.monotonic()
                if self.state == "running":
                    self._sample_heart_rate()
                if now - last_tick >= TICK_S:
                    last_tick = now
                    if self.source:
                        # hold the peak for ~1 s, the step is 0 between pulses
                        self.step_level = max(self.source.take_max_step(), self.step_level * 0.6)
                    self._refresh()
                if self.state != "idle" and now - last_flush >= FLUSH_S:
                    last_flush = now
                    self.db.add_revs(self.session_id, self.pending)
                    self.db.add_heart_rate(self.session_id, self.pending_hr)
                    self.pending, self.pending_hr = [], []
            time.sleep(0.03)

    def _sample_heart_rate(self) -> None:
        bpm = self.heart.bpm if self.heart else None
        t = self._ride_time(self.source.stream_time) if self.source else 0.0
        if bpm is None or (self._last_hr_t is not None and t - self._last_hr_t < 1.0):
            return
        dt = min(t - self._last_hr_t, 5.0) if self._last_hr_t is not None else 1.0
        self._last_hr_t = t
        self.pending_hr.append((round(t, 2), bpm))
        self.live_hr.add(t, bpm, dt)

    def _on_pulse(self, stream_t: float) -> None:
        self.last_pulse_at = stream_t
        if self.state != "running":
            return
        t = self._ride_time(stream_t)
        prev = self.tracker.last_t
        rev = self.tracker.add_pulse(t)
        self.stats.add(t)
        if self.plan and prev is not None and t - prev < IDLE_S:
            self.plan.add_interval(t, t - prev, rev.rpm_inst)
        self.pending.append((rev.t, rev.rev, rev.rpm_inst, rev.rpm_avg))
        if rev.rpm_avg is not None:
            self.live_points.append((round(t, 1), round(rev.rpm_avg, 1)))
            while self.live_points[0][0] < t - LIVE_WINDOW_S:
                self.live_points.popleft()

    # ---- state for the UI
    def _refresh(self) -> None:
        self._snapshot = self._build_snapshot()

    def snapshot(self) -> dict:
        """Last snapshot built by the loop. Cheap: many clients can poll it."""
        return self._snapshot

    def _build_snapshot(self) -> dict:
        src = self.source
        stream_t = src.stream_time if src else 0.0
        since = stream_t - self.last_pulse_at if self.last_pulse_at is not None else None
        snap = {
            "state": self.state,
            "source_ok": src is not None,
            "source_error": self.source_error,
            "simulated": bool(self.replay),
            "pedaling": since is not None and since < IDLE_S,
            "step_level": round(self.step_level, 3),
            "overflows": src.overflows if src else 0,
            "hr": self._heart_info(),
        }
        if self.state == "idle":
            return snap

        cfg = self.db.settings()
        now = self._ride_time(self.paused_at if self.state == "paused" else stream_t)
        rpm = self.tracker.rpm(now) if self.state == "running" else 0.0
        revs = self.tracker.revs
        wheel = float(cfg["wheel_m"])
        avg = self.stats.avg_rpm
        kcal = estimate_kcal(avg, float(cfg["weight_kg"]), self.stats.moving_s)
        heart = self.live_hr
        if heart and heart.count and now > 0:
            if heart.covered_s / now >= 0.5:
                kcal = round(heart.kcal)
            mx = heart.athlete.max_hr
            snap["hr"].update({
                "avg": round(heart.avg), "max": heart.max, "trimp": round(heart.trimp, 1),
                "max_hr": mx, "live": list(heart.points),
                "zones": [{"id": zid, "name": name, "lo": round(lo * mx),
                           "hi": None if hi == float("inf") else round(hi * mx), "seconds": round(sec, 1)}
                          for (zid, name, lo, hi), sec in zip(HR_ZONES, heart.zone_seconds)],
            })
        snap.update({
            "session_id": self.session_id,
            "elapsed_s": round(now, 1),
            "moving_s": round(self.stats.moving_s, 1),
            "rpm": round(rpm, 1),
            "avg_rpm": round(avg, 1),
            "revs": int(revs),
            "distance_km": round(revs * wheel / 1000, 3),
            "speed_kmh": round(rpm * wheel * 0.06, 1),
            "kcal": kcal,
            "zones": self.stats.zones(),
            "live": list(self.live_points),
            "workout": self._workout_info(),
            "plan": self.plan.state(now, rpm) if self.plan else None,
            "goal": self._goal_progress(now, revs * wheel / 1000),
        })
        return snap

    def _heart_info(self) -> dict:
        if not self.heart:
            return {"status": "off"}
        bpm = self.heart.bpm
        info = {"status": self.heart.status, "error": self.heart.error, "bpm": bpm}
        if bpm:
            athlete = self.live_hr.athlete if self.live_hr else Athlete.from_settings(self.db.settings())
            z = hr_zone_index(bpm, athlete.max_hr)
            info["zone"] = HR_ZONES[z][0] if z is not None else None
            info["pct_max"] = round(100 * bpm / athlete.max_hr)
        return info

    def _workout_info(self) -> dict:
        w = self.workout
        return {"id": w.id, "name": w.name, "duration_s": w.duration,
                "steps": [s.__dict__ for s in w.steps]} if w else {}

    def _goal_progress(self, elapsed: float, km: float) -> dict | None:
        goal: Goal | None = self.workout.goal if self.workout else None
        if goal is None:
            return None
        done = elapsed if goal.type == "time" else km
        return {"type": goal.type, "value": goal.value, "done": round(done, 3),
                "progress": min(1.0, done / goal.value) if goal.value else 1.0}
