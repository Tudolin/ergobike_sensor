import pytest

from ergobike.cadence import CadenceTracker


def test_rpm_and_idle():
    tr = CadenceTracker()
    for i in range(6):
        rev = tr.add_pulse(i * 0.75)
    assert rev.rev == 6 and rev.rpm_inst == pytest.approx(80)
    assert tr.rpm(3.80) == pytest.approx(80)
    assert tr.rpm(3.75 + 1.5) == pytest.approx(40)    # slowing down: capped by 60/since
    assert tr.rpm(3.75 + 3.0) == 0.0


def test_rolling_window_drops_old_intervals():
    tr = CadenceTracker(window=4)
    t = 0.0
    for iv in [2.0] * 5 + [0.5] * 4:
        t += iv
        rev = tr.add_pulse(t)
    assert rev.rpm_avg == pytest.approx(120)


def test_pulses_per_rev():
    tr = CadenceTracker(pulses_per_rev=2)
    for i in range(5):
        tr.add_pulse(i * 0.5)
    assert tr.revs == 2.5
    assert tr.rpm(2.01) == pytest.approx(60)
