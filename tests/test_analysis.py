import xml.etree.ElementTree as ET

import numpy as np
import pytest

from ergobike.analysis import Ride
from ergobike.recorder import LiveStats


def steady(rpm=80.0, seconds=600.0, start=0.5):
    return np.arange(start, seconds, 60.0 / rpm)


def test_steady_ride_summary():
    t = steady(80, 600)
    s = Ride(t, 600.0, wheel_m=6.0, weight_kg=75.0).summary()
    assert s["avg_rpm"] == pytest.approx(80, abs=0.1)
    assert s["max_rpm"] == pytest.approx(80, abs=0.5)
    assert s["moving_s"] == pytest.approx(599, abs=1)
    assert s["distance_km"] == pytest.approx(len(t) * 6 / 1000)
    assert s["kcal"] == round(6.8 * 75 * s["moving_s"] / 3600)
    assert set(s["bests"]) == {"1 min", "5 min", "10 min"}
    assert len(s["sparkline"]) == 48


def test_stop_is_not_moving_time():
    t = np.concatenate([steady(90, 120), steady(90, 240, start=180)])
    ride = Ride(t, 240.0)
    assert ride.moving_s == pytest.approx(180, abs=2)
    assert ride.avg_rpm == pytest.approx(90, abs=0.2)
    assert ride.distance_km == 0


def test_zones_and_splits():
    t = np.concatenate([steady(70, 300), steady(100, 600, start=300.6)])
    zones = {z["id"]: z["seconds"] for z in Ride(t, 600.0).zones()}
    assert zones["Z2"] == pytest.approx(300, abs=3) and zones["Z4"] == pytest.approx(300, abs=3)
    blocks = Ride(t, 600.0).splits()
    assert blocks["unit"] == "5 min"
    assert [r["avg_rpm"] for r in blocks["rows"]] == pytest.approx([70, 100], abs=1)
    km = Ride(t, 600.0, wheel_m=6.0).splits()
    assert km["unit"] == "km" and km["rows"][0]["revs"] == pytest.approx(167, abs=1)


def test_live_stats_match_post_ride_analysis():
    t = np.concatenate([steady(70, 300), steady(100, 600, start=300.6)])
    live = LiveStats(1)
    for x in t:
        live.add(x)
    ride = Ride(t, 600.0)
    assert live.avg_rpm == pytest.approx(ride.avg_rpm, rel=1e-6)
    assert live.moving_s == pytest.approx(ride.moving_s)
    for a, b in zip(live.zones(), ride.zones()):
        assert a["seconds"] == pytest.approx(b["seconds"], abs=3)


def test_tcx_is_valid_xml_with_cadence():
    xml = Ride(steady(80, 120), 120.0, wheel_m=6.0).to_tcx("2026-10-08T10:00:00", "Pedal <test>", 30)
    root = ET.fromstring(xml)
    ns = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
    points = root.findall(".//t:Trackpoint", ns)
    assert len(points) == 24
    assert int(points[10].find("t:Cadence", ns).text) == 80
    assert root.find(".//t:Notes", ns).text == "Pedal <test>"
