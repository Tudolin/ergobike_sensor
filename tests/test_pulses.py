import numpy as np
import pytest

from conftest import detect
from ergobike.pulses import calibrate


def synth(rate=44100, rpm=60.0, seconds=10.0, closed_s=0.07, tau=0.2, noise=0.005, seed=0):
    """Mic input model: -1 step on close, +1 on open, through a first-order high-pass."""
    n = int(rate * seconds)
    dc = np.zeros(n)
    t = 0.5
    while t + closed_s < seconds:
        dc[int(t * rate):int((t + closed_s) * rate)] = -1.0
        t += 60.0 / rpm
    a = np.exp(-1.0 / (tau * rate))
    y = np.zeros(n)
    for i in range(1, n):
        y[i] = a * (y[i - 1] + dc[i] - dc[i - 1])
    y += np.random.default_rng(seed).normal(0, noise, n)
    return np.clip(y, -1, 1).astype(np.float32)


def test_one_pulse_per_rev_at_77rpm(recording):
    x, rate = recording("77rpm")
    t = detect(x, rate)
    assert len(t) == 7
    rpm = 60 / np.diff(t)
    assert np.all((rpm > 74) & (rpm < 81)), rpm


def test_close_edge_is_steadier_than_open(recording):
    x, rate = recording("77rpm")
    close = np.diff(detect(x, rate, polarity=-1))
    open_ = np.diff(detect(x, rate, polarity=1))
    assert close.std() < open_.std()


@pytest.mark.parametrize("block", [64, 1024, 4096])
def test_block_size_does_not_matter(recording, block):
    x, rate = recording("77rpm")
    assert np.array_equal(detect(x, rate, block=block), detect(x, rate))


def test_idle_has_no_pulses(recording):
    x, rate = recording("idle")
    assert len(detect(x, rate, polarity=0)) == 0


def test_interference_burst_is_ignored(recording):
    # 60 Hz hum plus ~1 ms spikes from moving the cable. A plain sample-to-sample
    # difference counted ~10 false pulses at 300 rpm on this clip.
    x, rate = recording("interferencia")
    assert len(detect(x, rate, threshold=0.05, polarity=0)) == 0


def test_slow_pedalling_counts_every_rev(recording):
    x, rate = recording("lento")
    t = detect(x, rate)
    assert len(t) == 15
    rpm = 60 / np.diff(t)
    assert np.all((rpm > 45) & (rpm < 72)), rpm
    assert len(detect(x, rate, polarity=1)) == 15


@pytest.mark.parametrize("rpm", [20, 60, 120, 180])
def test_synthetic_cadences(rpm):
    x = synth(rpm=rpm, closed_s=min(0.3, 4.2 / rpm), seconds=12)
    assert np.allclose(60 / np.diff(detect(x, 44100)), rpm, rtol=0.01)


def test_calibration_on_real_signal(recording):
    x, rate = recording("77rpm")
    c = calibrate(x, rate)
    assert c.polarity == "close"
    assert c.close_count == 7
    assert 0.05 < c.threshold < 0.3
    assert c.margin > 20


def test_calibration_without_pulses():
    x = np.random.default_rng(1).normal(0, 0.003, 44100).astype(np.float32)
    assert calibrate(x, 44100).threshold is None
