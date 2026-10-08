import numpy as np
import pytest

from ergobike.analysis import Ride
from ergobike.workouts import BUILTIN, PlanTracker, Step, Workout, intervals


def test_builtin_durations():
    assert BUILTIN["hiit-30-30"].duration == 300 + 10 * 60 + 300
    assert BUILTIN["tabata"].duration == 300 + 8 * 30 + 300
    assert BUILTIN["pyramid"].duration == 300 + 7 * 120 + 300
    assert not BUILTIN["free"].structured


def test_intervals_without_rest_or_warmup():
    w = intervals("x", 3, 60, 90, 100, 0, 0, 0, warmup_s=0, cooldown_s=0)
    assert [s.kind for s in w.steps] == ["work"] * 3
    assert w.duration == 180


def test_roundtrip_through_dict():
    w = BUILTIN["pyramid"]
    assert Workout.from_dict(w.to_dict()).steps == w.steps


def test_plan_position_and_status():
    plan = PlanTracker(intervals("x", 2, 30, 95, 115, 30, 55, 70, warmup_s=60, cooldown_s=0))
    s = plan.state(75.0, 100.0)
    assert s["index"] == 1 and s["step"]["kind"] == "work"
    assert s["step_remaining_s"] == 15.0 and s["status"] == "in"
    assert s["next"]["kind"] == "rest"
    assert plan.state(95.0, 100.0)["status"] == "above"
    assert plan.state(95.0, 40.0)["status"] == "below"
    end = plan.state(200.0, 80.0)
    assert end["finished"] and end["progress"] == 1.0


def test_plan_compliance_counts_time_in_target():
    plan = PlanTracker(Workout("w", "w", steps=[Step("a", "steady", 60, 80, 90)]))
    t = 0.0
    for rpm in [85] * 30 + [70] * 10:
        iv = 60 / rpm
        t += iv
        plan.add_interval(t, iv, rpm)
    pct = plan.state(t, 70)["compliance"][0]
    assert 70 < pct < 85


def test_step_results_after_ride():
    w = intervals("x", 2, 60, 95, 115, 60, 55, 70, warmup_s=0, cooldown_s=0)
    times = np.concatenate([np.arange(0.5, 60, 60 / 100), np.arange(60.3, 120, 60 / 60),
                            np.arange(120.4, 180, 60 / 80), np.arange(180.5, 240, 60 / 62)])
    res = Ride(times, 240.0).step_results([s.__dict__ for s in w.steps])
    assert [r["in_target_pct"] for r in res] == pytest.approx([100, 100, 0, 100], abs=5)
    assert res[0]["avg_rpm"] == pytest.approx(100, abs=3)


def test_new_records(tmp_path):
    from ergobike.storage import Database

    db = Database(str(tmp_path / "r.db"))

    def ride(rpm: float) -> int:
        sid = db.start_session("x", 1, 0, 75)
        iv = 60 / rpm
        db.add_revs(sid, [(0.5 + i * iv, i + 1, None, None) for i in range(int(70 / iv))])
        db.finish_session(sid, 70.0)
        return sid

    assert db.new_records(ride(70)) == []           # first ride: nothing to beat
    assert db.new_records(ride(65)) == []
    rec = db.new_records(ride(80))
    assert [r["label"] for r in rec] == ["1 min"] and rec[0]["previous"] == pytest.approx(70, abs=1)
