"""Structured workouts: a list of steps, each with a duration and a target cadence range."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from itertools import accumulate

KINDS = ("warmup", "work", "rest", "steady", "cooldown")


@dataclass(frozen=True)
class Step:
    name: str
    kind: str
    seconds: int
    lo: int
    hi: int

    def status(self, rpm: float) -> str:
        if rpm < self.lo:
            return "below"
        return "above" if rpm > self.hi else "in"


@dataclass(frozen=True)
class Goal:
    type: str           # "time" (seconds) or "distance" (km)
    value: float


@dataclass
class Workout:
    id: str
    name: str
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    goal: Goal | None = None
    builtin: bool = True

    @property
    def duration(self) -> int:
        return sum(s.seconds for s in self.steps)

    @property
    def structured(self) -> bool:
        return bool(self.steps)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_s"] = self.duration
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Workout:
        goal = d.get("goal")
        return cls(id=str(d.get("id", "custom")), name=d["name"], description=d.get("description", ""),
                   steps=[Step(**{k: s[k] for k in ("name", "kind", "seconds", "lo", "hi")})
                          for s in d.get("steps", [])],
                   goal=Goal(goal["type"], float(goal["value"])) if goal else None,
                   builtin=bool(d.get("builtin", False)))


def intervals(name: str, rounds: int, work_s: int, work_lo: int, work_hi: int,
              rest_s: int, rest_lo: int, rest_hi: int, warmup_s: int = 300,
              cooldown_s: int = 300, work_name: str = "Esforço", rest_name: str = "Recupera",
              id: str = "custom", description: str = "", builtin: bool = False) -> Workout:
    steps = []
    if warmup_s:
        steps.append(Step("Aquecimento", "warmup", warmup_s, 60, 75))
    for _ in range(rounds):
        steps.append(Step(work_name, "work", work_s, work_lo, work_hi))
        if rest_s:
            steps.append(Step(rest_name, "rest", rest_s, rest_lo, rest_hi))
    if cooldown_s:
        steps.append(Step("Desaquecimento", "cooldown", cooldown_s, 50, 65))
    return Workout(id, name, description, steps, builtin=builtin)


def _builtin() -> list[Workout]:
    warm = Step("Aquecimento", "warmup", 300, 60, 75)
    cool = Step("Desaquecimento", "cooldown", 300, 50, 65)
    pyramid = [Step(f"Degrau {rpm} rpm", "work" if rpm >= 90 else "steady", 120, rpm - 5, rpm + 5)
               for rpm in (70, 80, 90, 100, 90, 80, 70)]
    return [
        Workout("free", "Pedal livre", "Sem roteiro. Opcionalmente com meta de tempo ou distância."),
        intervals("HIIT 30/30", 10, 30, 95, 115, 30, 55, 70, id="hiit-30-30", builtin=True,
                  work_name="Sprint", description="10 tiros de 30 s forte com 30 s de recuperação."),
        intervals("Tabata", 8, 20, 100, 125, 10, 50, 65, id="tabata", builtin=True,
                  work_name="Tiro", description="8 x 20 s no máximo com 10 s de descanso. Curto e brutal."),
        intervals("Sprints 1 min", 6, 60, 100, 120, 120, 60, 70, id="sprints-1min", builtin=True,
                  work_name="Sprint", description="6 sprints de 1 min com 2 min leves entre eles."),
        Workout("pyramid", "Pirâmide", "Sobe de 70 a 100 rpm e desce, 2 min por degrau.",
                [warm, *pyramid, cool]),
        Workout("steady-30", "Cadência constante", "20 min firmes entre 80 e 90 rpm.",
                [warm, Step("Ritmo", "steady", 1200, 80, 90), cool]),
        Workout("endurance-45", "Endurance 45 min", "Volume em ritmo confortável, 75 a 85 rpm.",
                [warm, Step("Base", "steady", 2100, 75, 85), cool]),
    ]


BUILTIN = {w.id: w for w in _builtin()}


class PlanTracker:
    """Where we are in a structured workout, plus time spent inside each step's target."""

    def __init__(self, workout: Workout):
        self.workout = workout
        self.steps = workout.steps
        self.starts = [0, *accumulate(s.seconds for s in self.steps)]
        self.in_target = [0.0] * len(self.steps)
        self.step_time = [0.0] * len(self.steps)

    def index_at(self, t: float) -> int | None:
        if not self.steps or t >= self.starts[-1]:
            return None
        return bisect_right(self.starts, t) - 1

    def add_interval(self, t_end: float, seconds: float, rpm: float) -> None:
        """Credit one pedal revolution to the step it ended in."""
        i = self.index_at(t_end)
        if i is None:
            return
        self.step_time[i] += seconds
        if self.steps[i].status(rpm) == "in":
            self.in_target[i] += seconds

    def state(self, t: float, rpm: float) -> dict:
        i = self.index_at(t)
        total = self.starts[-1]
        done_s = min(t, total)
        state = {"index": i, "finished": i is None, "progress": done_s / total if total else 1.0,
                 "remaining_s": round(total - done_s, 1)}
        if i is None:
            return state
        step = self.steps[i]
        nxt = self.steps[i + 1] if i + 1 < len(self.steps) else None
        state.update({
            "step": asdict(step),
            "step_elapsed_s": round(t - self.starts[i], 1),
            "step_remaining_s": round(self.starts[i + 1] - t, 1),
            "status": step.status(rpm) if rpm > 0 else "below",
            "next": asdict(nxt) if nxt else None,
            "compliance": [round(100 * a / b) if b else None
                           for a, b in zip(self.in_target, self.step_time)],
        })
        return state
