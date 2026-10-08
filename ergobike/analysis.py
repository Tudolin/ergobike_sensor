"""Post-ride metrics. Everything is derived from a 1 s grid holding the cadence
of the revolution in progress at each second (0 while stopped)."""

from __future__ import annotations

import math
from datetime import datetime, timezone
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
                 weight_kg: float = 75.0):
        self.times = np.asarray(times, dtype=float)
        self.duration = float(duration)
        self.ppr = pulses_per_rev
        self.wheel_m = wheel_m
        self.weight_kg = weight_kg

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
    def kcal(self) -> int:
        return estimate_kcal(self.avg_rpm, self.weight_kg, self.moving_s)

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

    def detail(self) -> dict:
        return {"series": self.series(), "zones": self.zones(), "splits": self.splits()}

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
        points = "".join(
            f"<Trackpoint><Time>{iso(ts)}</Time>"
            f"<DistanceMeters>{n / self.ppr * self.wheel_m:.1f}</DistanceMeters>"
            f"<Cadence>{min(254, int(round(c)))}</Cadence></Trackpoint>"
            for ts, n, c in zip(t[::stride], pulses_at, smooth[::stride]))
        total_m = len(self.times) / self.ppr * self.wheel_m
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">'
            f'<Activities><Activity Sport="Biking"><Id>{iso(0)}</Id>'
            f'<Lap StartTime="{iso(0)}"><TotalTimeSeconds>{self.duration:.0f}</TotalTimeSeconds>'
            f'<DistanceMeters>{total_m:.1f}</DistanceMeters><Calories>{kcal}</Calories>'
            '<Intensity>Active</Intensity><TriggerMethod>Manual</TriggerMethod>'
            f'<Track>{points}</Track></Lap>'
            f'<Notes>{escape(title)}</Notes></Activity></Activities></TrainingCenterDatabase>\n'
        )
