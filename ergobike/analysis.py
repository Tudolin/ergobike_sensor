"""Post-ride metrics. Everything is derived from a 1 s grid holding the cadence
of the revolution in progress at each second (0 while stopped)."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from dataclasses import dataclass
from functools import cached_property
from xml.sax.saxutils import escape

import numpy as np

from .cadence import IDLE_S

STEP_S = 1.0

ZONES = [
    ("Z1", "Leve", 0, 60),
    ("Z2", "Moderada", 60, 75),
    ("Z3", "Ritmo", 75, 90),
    ("Z4", "Forte", 90, 105),
    ("Z5", "Sprint", 105, math.inf),
]
BEST_WINDOWS = [(60, "1 min"), (300, "5 min"), (600, "10 min"), (1200, "20 min"),
                (1800, "30 min"), (3600, "60 min")]


HR_ZONES = [  # share of max heart rate
    ("Z1", "Muito leve", 0.50, 0.60),
    ("Z2", "Leve", 0.60, 0.70),
    ("Z3", "Moderada", 0.70, 0.80),
    ("Z4", "Forte", 0.80, 0.90),
    ("Z5", "Máxima", 0.90, math.inf),
]


@dataclass(frozen=True)
class Athlete:
    weight_kg: float = 75.0
    age: int = 30
    sex: str = "m"
    hr_max: int | None = None
    hr_rest: int = 60

    @property
    def max_hr(self) -> int:
        return int(self.hr_max) if self.hr_max else round(208 - 0.7 * self.age)   # Tanaka

    @classmethod
    def from_settings(cls, cfg: dict) -> "Athlete":
        return cls(float(cfg.get("weight_kg") or 75), int(cfg.get("age") or 30), cfg.get("sex") or "m",
                   cfg.get("hr_max") or None, int(cfg.get("hr_rest") or 60))

    def kcal_per_min(self, bpm: float) -> float:
        """Keytel et al. 2005: energy expenditure from heart rate during exercise."""
        if self.sex == "f":
            k = -20.4022 + 0.4472 * bpm - 0.1263 * self.weight_kg + 0.074 * self.age
        else:
            k = -55.0969 + 0.6309 * bpm + 0.1988 * self.weight_kg + 0.2017 * self.age
        return max(k, 0.0) / 4.184

    def trimp_per_min(self, bpm: float) -> float:
        """Banister TRIMP for one minute at `bpm`."""
        hrr = (bpm - self.hr_rest) / max(self.max_hr - self.hr_rest, 1)
        hrr = min(max(hrr, 0.0), 1.0)
        a, b = (0.86, 1.67) if self.sex == "f" else (0.64, 1.92)
        return hrr * a * math.exp(b * hrr)


def hr_zone_index(bpm: float, max_hr: int) -> int | None:
    pct = bpm / max_hr
    for i, (_, _, lo, hi) in enumerate(HR_ZONES):
        if lo <= pct < hi:
            return i
    return None


class HeartRateSeries:
    """Heart rate samples of a ride: (ride time in seconds, bpm)."""

    def __init__(self, samples, athlete: Athlete):
        arr = np.asarray(samples, dtype=float).reshape(-1, 2)
        self.t, self.bpm = arr[:, 0], arr[:, 1]
        self.athlete = athlete

    def __len__(self) -> int:
        return len(self.t)

    @cached_property
    def dt(self) -> np.ndarray:
        # seconds each sample stands for; gaps (watch off the wrist) count as nothing
        if not len(self.t):
            return np.zeros(0)
        d = np.diff(self.t, append=self.t[-1] + 1.0)
        return np.where(d <= 5.0, d, 0.0)

    @property
    def avg(self) -> float:
        return float(np.average(self.bpm, weights=self.dt)) if self.dt.sum() else 0.0

    @property
    def max(self) -> float:
        return float(self.bpm.max(initial=0))

    @property
    def kcal(self) -> int:
        return round(sum(self.athlete.kcal_per_min(b) * d / 60 for b, d in zip(self.bpm, self.dt)))

    @property
    def trimp(self) -> float:
        return round(sum(self.athlete.trimp_per_min(b) * d / 60 for b, d in zip(self.bpm, self.dt)), 1)

    def at(self, t):
        return np.interp(t, self.t, self.bpm) if len(self.t) else np.zeros_like(np.asarray(t, float))

    def mean_between(self, a: float, b: float) -> float | None:
        m = (self.t >= a) & (self.t < b)
        return float(self.bpm[m].mean()) if m.any() else None

    def zones(self) -> list[dict]:
        mx = self.athlete.max_hr
        secs = [0.0] * len(HR_ZONES)
        for b, d in zip(self.bpm, self.dt):
            if (i := hr_zone_index(b, mx)) is not None:
                secs[i] += d
        total = sum(secs) or 1.0
        return [{"id": zid, "name": name, "lo": round(lo * mx),
                 "hi": None if math.isinf(hi) else round(hi * mx),
                 "seconds": round(sec, 1), "pct": round(100 * sec / total, 1)}
                for (zid, name, lo, hi), sec in zip(HR_ZONES, secs)]

    def series(self, max_points: int = 900) -> dict:
        stride = max(1, math.ceil(len(self.t) / max_points))
        return {"t": np.round(self.t[::stride], 1).tolist(),
                "bpm": self.bpm[::stride].round().astype(int).tolist()}


def zone_index(rpm: float) -> int:
    for i, (_, _, lo, hi) in enumerate(ZONES):
        if lo <= rpm < hi:
            return i
    return 0


def met_for_cadence(rpm: float) -> float:
    # rough MET values for stationary cycling; without load this is only a ballpark
    if rpm < 50:
        return 3.5
    if rpm < 70:
        return 5.0
    if rpm < 90:
        return 6.8
    return 8.8


def estimate_kcal(avg_rpm: float, weight_kg: float, moving_s: float) -> int:
    return round(met_for_cadence(avg_rpm) * weight_kg * moving_s / 3600) if moving_s else 0


def rolling_mean(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(x) == 0:
        return x.copy()
    c = np.cumsum(np.insert(x, 0, 0.0))
    k = np.arange(len(x))
    lo = np.maximum(0, k - n + 1)
    return (c[k + 1] - c[lo]) / (k + 1 - lo)


def default_title(started: datetime) -> str:
    h = started.hour
    period = ("da madrugada" if h < 5 else "da manhã" if h < 12
              else "da tarde" if h < 18 else "da noite")
    return f"Pedal {period}"


class Ride:
    """A recorded ride: pulse timestamps (seconds from start) plus its settings."""

    def __init__(self, times, duration: float, pulses_per_rev: int = 1, wheel_m: float = 0.0,
                 weight_kg: float = 75.0, hr: HeartRateSeries | None = None):
        self.times = np.asarray(times, dtype=float)
        self.duration = float(duration)
        self.ppr = pulses_per_rev
        self.wheel_m = wheel_m
        self.weight_kg = weight_kg
        self.hr = hr if hr is not None and len(hr) else None

    # ---- base series
    @cached_property
    def intervals(self) -> np.ndarray:
        return np.diff(self.times)

    @cached_property
    def grid(self) -> tuple[np.ndarray, np.ndarray]:
        t = np.arange(0.0, max(self.duration, STEP_S), STEP_S)
        rpm = np.zeros_like(t)
        if len(self.times) >= 2:
            idx = np.searchsorted(self.times, t, side="right")
            ok = (idx >= 1) & (idx < len(self.times))
            iv = self.times[idx[ok]] - self.times[idx[ok] - 1]
            r = 60.0 / (iv * self.ppr)
            r[iv >= IDLE_S] = 0.0
            rpm[ok] = r
        return t, rpm

    @property
    def rpm(self) -> np.ndarray:
        return self.grid[1]

    # ---- summary
    @cached_property
    def moving_s(self) -> float:
        iv = self.intervals
        return float(iv[iv < IDLE_S].sum())

    @property
    def revs(self) -> int:
        return len(self.times) // self.ppr

    @property
    def avg_rpm(self) -> float:
        moving = int((self.intervals < IDLE_S).sum())
        return 60.0 * moving / (self.moving_s * self.ppr) if self.moving_s else 0.0

    @property
    def max_rpm(self) -> float:
        return float(rolling_mean(self.rpm, 5).max(initial=0))

    @property
    def distance_km(self) -> float:
        return self.revs * self.wheel_m / 1000

    @property
    def hr_coverage(self) -> float:
        if self.hr is None or not self.duration:
            return 0.0
        return float(self.hr.dt.sum()) / self.duration

    @property
    def kcal(self) -> int:
        # heart rate beats the cadence-based guess once it covers most of the ride
        if self.hr_coverage >= 0.5:
            return self.hr.kcal
        return estimate_kcal(self.avg_rpm, self.weight_kg, self.moving_s)

    def cardiac_drift(self) -> float | None:
        """Aerobic decoupling: drop in rpm per heartbeat from the first to the second half, in %.
        Under ~5% means the effort was sustainable. Needs 10+ minutes with heart rate."""
        if self.hr is None or self.moving_s < 600 or self.hr_coverage < 0.8:
            return None
        t, rpm = self.grid
        hr = self.hr.at(t)
        idx = np.flatnonzero((rpm > 0) & (hr > 0))
        if len(idx) < 600:
            return None
        first, second = idx[: len(idx) // 2], idx[len(idx) // 2:]
        ef1 = rpm[first].mean() / hr[first].mean()
        ef2 = rpm[second].mean() / hr[second].mean()
        return round(100 * (ef1 - ef2) / ef1, 1)

    def best_efforts(self) -> dict[str, float]:
        c = np.cumsum(np.insert(self.rpm, 0, 0.0))
        out = {}
        for seconds, label in BEST_WINDOWS:
            n = int(seconds / STEP_S)
            if len(self.rpm) >= n:
                out[label] = round(float(((c[n:] - c[:-n]) / n).max()), 1)
        return out

    def summary(self) -> dict:
        return {
            "duration_s": round(self.duration, 1),
            "moving_s": round(self.moving_s, 1),
            "revs": self.revs,
            "avg_rpm": round(self.avg_rpm, 1),
            "max_rpm": round(self.max_rpm, 1),
            "distance_km": round(self.distance_km, 3),
            "kcal": self.kcal,
            "bests": self.best_efforts(),
            "sparkline": self.sparkline(),
            "avg_hr": round(self.hr.avg) if self.hr else None,
            "max_hr": round(self.hr.max) if self.hr else None,
            "trimp": self.hr.trimp if self.hr else None,
        }

    # ---- detail
    def zones(self) -> list[dict]:
        rpm = self.rpm
        out = []
        for zid, name, lo, hi in ZONES:
            sec = float(((rpm > 0) & (rpm >= lo) & (rpm < hi)).sum() * STEP_S)
            out.append({"id": zid, "name": name, "lo": lo,
                        "hi": None if math.isinf(hi) else hi, "seconds": sec})
        total = sum(z["seconds"] for z in out) or 1.0
        for z in out:
            z["pct"] = round(100 * z["seconds"] / total, 1)
        return out

    def splits(self) -> dict:
        """Per-km splits when distance is known, 5 min blocks otherwise."""
        times, ppr = self.times, self.ppr
        unit = "km" if self.wheel_m > 0 else "5 min"
        if len(times) < 2:
            return {"unit": unit, "rows": []}
        if self.wheel_m > 0:
            per_km = 1000.0 / self.wheel_m * ppr
            edges = [0.0]
            k = 1
            while (i := math.ceil(k * per_km)) < len(times):
                edges.append(float(times[i]))
                k += 1
            if self.duration - edges[-1] > 5:
                edges.append(self.duration)
        else:
            edges = [*np.arange(0.0, self.duration, 300.0).tolist(), self.duration]

        counts = np.diff(np.searchsorted(times, edges, side="right"))
        rows = []
        for n, (a, b, pulses) in enumerate(zip(edges[:-1], edges[1:], counts), start=1):
            dur = b - a
            rows.append({
                "n": n,
                "seconds": round(dur, 1),
                "revs": int(pulses) // ppr,
                "avg_rpm": round(60.0 * pulses / (dur * ppr), 1) if dur > 0 else 0.0,
                "km": round(pulses / ppr * self.wheel_m / 1000, 3) if self.wheel_m else None,
                "partial": unit == "km" and n == len(edges) - 1 and b == self.duration,
            })
        return {"unit": unit, "rows": rows}

    def series(self, max_points: int = 900, smooth_s: int = 5) -> dict:
        t, rpm = self.grid
        smooth = rolling_mean(rpm, smooth_s)
        stride = max(1, math.ceil(len(t) / max_points))
        return {"t": np.round(t[::stride], 1).tolist(), "rpm": np.round(smooth[::stride], 1).tolist()}

    def sparkline(self, points: int = 48) -> list[float]:
        rpm = self.rpm
        if len(rpm) == 0:
            return []
        return [round(float(c.mean()), 1) for c in np.array_split(rpm, min(points, len(rpm)))]

    def step_results(self, steps: list[dict]) -> list[dict]:
        """Average cadence and share of time inside the target range, per workout step."""
        t, rpm = self.grid
        out = []
        start = 0.0
        for s in steps:
            end = start + s["seconds"]
            seg = rpm[(t >= start) & (t < end)]
            if len(seg):
                row = {**s, "start_s": start, "avg_rpm": round(float(seg.mean()), 1),
                       "in_target_pct": round(100 * float(((seg >= s["lo"]) & (seg <= s["hi"])).mean()))}
                if self.hr is not None:
                    avg = self.hr.mean_between(start, end)
                    row["avg_hr"] = round(avg) if avg else None
                    # recovery: how much heart rate falls during a rest step (first 60 s)
                    if s["kind"] == "rest" and self.hr.t[0] <= start and self.hr.t[-1] >= end:
                        row["hr_drop"] = round(float(self.hr.at(start) - self.hr.at(min(start + 60, end))))
                out.append(row)
            start = end
        return out

    def detail(self) -> dict:
        d = {"series": self.series(), "zones": self.zones(), "splits": self.splits()}
        if self.hr is not None:
            d["heart"] = {"series": self.hr.series(), "zones": self.hr.zones(),
                          "avg": round(self.hr.avg), "max": round(self.hr.max), "trimp": self.hr.trimp,
                          "drift_pct": self.cardiac_drift(), "max_hr": self.hr.athlete.max_hr,
                          "kcal_from_hr": self.hr_coverage >= 0.5}
        return d

    # ---- export
    def to_tcx(self, started_at: str, title: str, kcal: int, step: float = 5.0) -> str:
        """Indoor TCX (no GPS) with cadence and estimated distance.
        Strava, Garmin Connect and TrainingPeaks all accept it."""
        start = datetime.fromisoformat(started_at).astimezone(timezone.utc).timestamp()
        t, rpm = self.grid
        smooth = rolling_mean(rpm, int(step))

        def iso(s: float) -> str:
            return datetime.fromtimestamp(start + s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        stride = int(step / STEP_S)
        pulses_at = np.searchsorted(self.times, t[::stride], side="right")
        stamps = t[::stride]
        hr = self.hr.at(stamps) if self.hr is not None else [None] * len(stamps)
        points = "".join(
            f"<Trackpoint><Time>{iso(ts)}</Time>"
            f"<DistanceMeters>{n / self.ppr * self.wheel_m:.1f}</DistanceMeters>"
            + (f"<HeartRateBpm><Value>{int(round(h))}</Value></HeartRateBpm>" if h else "")
            + f"<Cadence>{min(254, int(round(c)))}</Cadence></Trackpoint>"
            for ts, n, c, h in zip(stamps, pulses_at, smooth[::stride], hr))
        hr_lap = ""
        if self.hr is not None:
            hr_lap = (f"<AverageHeartRateBpm><Value>{round(self.hr.avg)}</Value></AverageHeartRateBpm>"
                      f"<MaximumHeartRateBpm><Value>{round(self.hr.max)}</Value></MaximumHeartRateBpm>")
        total_m = len(self.times) / self.ppr * self.wheel_m
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">'
            f'<Activities><Activity Sport="Biking"><Id>{iso(0)}</Id>'
            f'<Lap StartTime="{iso(0)}"><TotalTimeSeconds>{self.duration:.0f}</TotalTimeSeconds>'
            f'<DistanceMeters>{total_m:.1f}</DistanceMeters><Calories>{kcal}</Calories>{hr_lap}'
            '<Intensity>Active</Intensity><TriggerMethod>Manual</TriggerMethod>'
            f'<Track>{points}</Track></Lap>'
            f'<Notes>{escape(title)}</Notes></Activity></Activities></TrainingCenterDatabase>\n'
        )
