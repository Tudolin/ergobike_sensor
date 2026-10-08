"""Reed switch pulse detection on a microphone input.

The mic input is AC-coupled, so the reed closing shows up as a step that decays
over ~0.2 s and the opening as a step in the opposite direction. Between pulses
the baseline wanders by +-0.25, which rules out a plain amplitude threshold.

The detector compares the mean of the last `m` samples with the mean of the `m`
before (m = 1 ms). A real step passes through almost unchanged, while baseline
drift, mains hum and short interference spikes average out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

POLARITIES = {"close": -1, "open": 1, "both": 0}


def box_steps(buf: np.ndarray, m: int) -> np.ndarray:
    """mean(buf[j+m : j+2m]) - mean(buf[j : j+m]) for every j."""
    c = np.cumsum(np.insert(np.asarray(buf, dtype=np.float64), 0, 0.0))
    means = (c[m:] - c[:-m]) / m
    return (means[m:] - means[:-m]).astype(np.float32)


class EdgeDetector:
    """Streaming step detector. Returns absolute sample indices.

    Timestamps should be derived from these indices (idx / rate). PortAudio's
    inputBufferAdcTime is zero or garbage on MME, so the sample count is the only
    clock worth trusting.
    """

    def __init__(self, rate: int, threshold: float = 0.1, polarity: int = -1,
                 window_s: float = 0.001, min_interval_s: float = 0.2):
        if polarity not in (-1, 0, 1):
            raise ValueError("polarity must be -1, 0 or 1")
        self.rate = rate
        self.threshold = threshold
        self.polarity = polarity
        self.m = max(1, round(rate * window_s))
        self.min_interval = max(1, round(rate * min_interval_s))
        self.n = 0
        self.last_peak = 0.0
        self._tail: np.ndarray | None = None
        self._last_edge = -(10 ** 12)

    def steps(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        keep = 2 * self.m - 1
        if self._tail is None:
            self._tail = np.full(keep, x[0] if x.size else 0.0, dtype=np.float32)
        buf = np.concatenate((self._tail, x))
        self._tail = buf[-keep:]
        return box_steps(buf, self.m)

    def process(self, x: np.ndarray) -> list[int]:
        if len(x) == 0:
            return []
        d = self.steps(x)
        self.last_peak = float(np.abs(d).max(initial=0))
        s = np.abs(d) if self.polarity == 0 else d * self.polarity
        above = s > self.threshold
        out = []
        if above.any():
            # one candidate per run of samples above the threshold
            starts = np.flatnonzero(above & ~np.concatenate(([False], above[:-1])))
            for i in starts:
                idx = self.n + int(i)
                if idx - self._last_edge >= self.min_interval:
                    out.append(idx)
                    self._last_edge = idx
        self.n += len(x)
        return out


@dataclass
class Calibration:
    noise: float
    peak_min: float
    peak_max: float
    close_count: int
    close_median: float
    open_count: int
    open_median: float
    polarity: str
    threshold: float | None

    @property
    def margin(self) -> float:
        median = self.close_median if self.polarity == "close" else self.open_median
        return median / max(self.noise, 1e-6)

    def to_dict(self) -> dict:
        return {
            "noise": self.noise, "peak_min": self.peak_min, "peak_max": self.peak_max,
            "close": {"count": self.close_count, "median": self.close_median},
            "open": {"count": self.open_count, "median": self.open_median},
            "suggest_polarity": self.polarity, "suggest_threshold": self.threshold,
        }


def calibrate(x: np.ndarray, rate: int, window_s: float = 0.001) -> Calibration:
    """Measure noise and step size on a raw recording and suggest settings."""
    x = np.asarray(x, dtype=np.float32)
    m = max(1, round(rate * window_s))
    d = box_steps(x, m) if len(x) >= 2 * m else np.zeros(0, np.float32)
    noise = float(np.percentile(np.abs(d), 99.0)) if d.size else 0.0
    floor = max(noise * 5, 0.05)
    gap = round(rate * 0.05)

    def edges(sign: int) -> tuple[int, float]:
        s = d * sign
        idx = np.flatnonzero(s > floor)
        if not idx.size:
            return 0, 0.0
        groups = np.split(idx, np.flatnonzero(np.diff(idx) > gap) + 1)
        peaks = [float(s[g].max()) for g in groups]
        return len(peaks), float(np.median(peaks))

    close_n, close_med = edges(-1)
    open_n, open_med = edges(1)
    # the closing edge is the steadier one in practice; only switch if it is much weaker
    polarity = "open" if open_med > 2 * close_med else "close"
    median = open_med if polarity == "open" else close_med
    threshold = round(math.sqrt(max(noise, 1e-3) * median), 3) if median else None
    return Calibration(noise, float(x.min(initial=0)), float(x.max(initial=0)),
                       close_n, close_med, open_n, open_med, polarity, threshold)
