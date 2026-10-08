from __future__ import annotations

from collections import deque
from dataclasses import dataclass

IDLE_S = 3.0


@dataclass(frozen=True)
class Rev:
    t: float
    rev: int
    rpm_inst: float | None
    rpm_avg: float | None


class CadenceTracker:
    """Turns pulse timestamps into revolutions and RPM (rolling mean of N intervals)."""

    def __init__(self, pulses_per_rev: int = 1, window: int = 4, idle_s: float = IDLE_S):
        self.ppr = pulses_per_rev
        self.idle_s = idle_s
        self.pulses = 0
        self.last_t: float | None = None
        self.intervals: deque[float] = deque(maxlen=window)
        self._sum = 0.0

    @property
    def revs(self) -> float:
        return self.pulses / self.ppr

    def _rpm(self, interval: float) -> float:
        return 60.0 / (interval * self.ppr)

    def add_pulse(self, t: float) -> Rev:
        if self.last_t is not None:
            if len(self.intervals) == self.intervals.maxlen:
                self._sum -= self.intervals[0]
            iv = t - self.last_t
            self.intervals.append(iv)
            self._sum += iv
        self.last_t = t
        self.pulses += 1
        if not self.intervals:
            return Rev(t, self.pulses // self.ppr, None, None)
        return Rev(t, self.pulses // self.ppr, self._rpm(self.intervals[-1]),
                   self._rpm(self._sum / len(self.intervals)))

    def rpm(self, now: float) -> float:
        """Current RPM. Decays once pedalling slows down and drops to 0 when idle."""
        if not self.intervals or self.last_t is None:
            return 0.0
        since = now - self.last_t
        if since >= self.idle_s:
            return 0.0
        avg = self._rpm(self._sum / len(self.intervals))
        # no pulse for longer than the average interval means we are slower than avg
        return min(avg, self._rpm(since)) if since > 0 else avg
