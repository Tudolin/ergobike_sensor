"""Live workout recording.

The audio source stays open while the app runs, so the UI can show "sensor ok"
before a ride starts. Ride time is stream time minus pauses, so stored
timestamps are continuous and charts have no gaps for pauses.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from .analysis import ZONES, estimate_kcal, zone_index
from .audio import PulseSource, ReplaySource, default_device
from .cadence import IDLE_S, CadenceTracker
from .pulses import POLARITIES, EdgeDetector, calibrate
from .storage import Database

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


class Recorder:
    def __init__(self, db: Database, replay: str | None = None, rate: int = 44100):
        self.db = db
        self.replay = replay
        self.rate = rate
        self.lock = threading.RLock()
        self.source: PulseSource | None = None
        self.source_error: str | None = None
        self.state = "idle"                 # idle | running | paused
        self.session_id: int | None = None
        self.tracker: CadenceTracker | None = None
        self.stats: LiveStats | None = None
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
        self._refresh()
        self._thread.start()

    def shutdown(self) -> None:
        if self.state != "idle":
            self.finish(save=True)
        self._stop.set()
        self._thread.join(timeout=2)
        self._close_source()

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

    # ---- ride control
    def _ride_time(self, stream_t: float) -> float:
        return stream_t - self.t_start - self.paused_total

    def begin(self) -> int:
        with self.lock:
            if self.state != "idle":
                return self.session_id
            if not self.source:
                raise RuntimeError(self.source_error or "sensor indisponível")
            cfg = self.db.settings()
            ppr = int(cfg["pulses_per_rev"])
            self.tracker = CadenceTracker(ppr)
            self.stats = LiveStats(ppr)
            self.session_id = self.db.start_session(
                "replay" if self.replay else str(cfg["device"]), ppr,
                float(cfg["wheel_m"]), float(cfg["weight_kg"]))
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
            self.pending = []
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
                if now - last_tick >= TICK_S:
                    last_tick = now
                    if self.source:
                        # hold the peak for ~1 s, the step is 0 between pulses
                        self.step_level = max(self.source.take_max_step(), self.step_level * 0.6)
                    self._refresh()
                if self.state != "idle" and now - last_flush >= FLUSH_S:
                    last_flush = now
                    self.db.add_revs(self.session_id, self.pending)
                    self.pending = []
            time.sleep(0.03)

    def _on_pulse(self, stream_t: float) -> None:
        self.last_pulse_at = stream_t
        if self.state != "running":
            return
        t = self._ride_time(stream_t)
        rev = self.tracker.add_pulse(t)
        self.stats.add(t)
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
        }
        if self.state == "idle":
            return snap

        cfg = self.db.settings()
        now = self._ride_time(self.paused_at if self.state == "paused" else stream_t)
        rpm = self.tracker.rpm(now) if self.state == "running" else 0.0
        revs = self.tracker.revs
        wheel = float(cfg["wheel_m"])
        avg = self.stats.avg_rpm
        snap.update({
            "session_id": self.session_id,
            "elapsed_s": round(now, 1),
            "moving_s": round(self.stats.moving_s, 1),
            "rpm": round(rpm, 1),
            "avg_rpm": round(avg, 1),
            "revs": int(revs),
            "distance_km": round(revs * wheel / 1000, 3),
            "speed_kmh": round(rpm * wheel * 0.06, 1),
            "kcal": estimate_kcal(avg, float(cfg["weight_kg"]), self.stats.moving_s),
            "zones": self.stats.zones(),
            "live": list(self.live_points),
        })
        return snap
