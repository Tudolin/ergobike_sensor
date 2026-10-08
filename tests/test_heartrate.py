import json
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import DATA
from ergobike.analysis import Athlete, HeartRateSeries, Ride
from ergobike.demo_hr import FakePulsoid
from ergobike.heartrate import HeartRateMonitor, validate_token
from ergobike.server import create_app


def wait_for(cond, timeout=8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def pulsoid():
    fake = FakePulsoid(bpm=lambda t: 120 + int(t), interval=0.2).start()
    yield fake
    fake.stop()


# ---- client
def test_monitor_receives_heart_rate(pulsoid):
    mon = HeartRateMonitor("demo", pulsoid.url).start()
    try:
        assert wait_for(lambda: mon.bpm is not None)
        assert mon.status == "connected" and 120 <= mon.bpm < 130
    finally:
        mon.stop()


def test_monitor_reports_rejected_token(pulsoid):
    mon = HeartRateMonitor("wrong", pulsoid.url).start()
    try:
        assert wait_for(lambda: mon.status == "unauthorized")
        assert mon.bpm is None
    finally:
        mon.stop()


def test_monitor_accepts_plain_text_messages():
    mon = HeartRateMonitor("x", "ws://unused")
    mon._handle("131")
    assert mon.bpm == 131
    mon._handle('{"measured_at": 1, "data": {"heart_rate": 300}}')    # implausible, ignored
    assert mon.bpm == 131


class _Validate(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("Authorization") == "Bearer good"
        partial = self.headers.get("Authorization") == "Bearer partial"
        if not (ok or partial):
            self.send_response(401)
            self.end_headers()
            return
        scopes = ["data:heart_rate:read"] if ok else ["profile:read"]
        body = json.dumps({"token": "x", "client_id": "c", "expires_in": 100, "scopes": scopes}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_validate_token():
    srv = HTTPServer(("127.0.0.1", 0), _Validate)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/v1/token/validate"
    try:
        assert validate_token("good", url)["ok"]
        assert not validate_token("bad", url)["ok"]
        r = validate_token("partial", url)
        assert not r["ok"] and "data:heart_rate:read" in r["message"]
    finally:
        srv.shutdown()


# ---- analysis
def test_athlete_formulas():
    a = Athlete(weight_kg=75, age=35)
    assert a.max_hr == 184
    assert Athlete(hr_max=190).max_hr == 190
    assert 12 < a.kcal_per_min(140) < 15
    assert a.trimp_per_min(a.hr_rest) == 0
    assert a.trimp_per_min(170) > a.trimp_per_min(130)


def test_heart_rate_series_stats_and_gaps():
    a = Athlete(age=30)                               # max 187
    samples = [(t, 120) for t in range(0, 60)] + [(t, 160) for t in range(120, 180)]
    hr = HeartRateSeries(samples, a)
    assert hr.avg == pytest.approx(140, abs=1)        # the 60 s gap is not counted
    zones = {z["id"]: z["seconds"] for z in hr.zones()}
    assert zones["Z2"] == pytest.approx(59, abs=2)    # 120 bpm = 64%
    assert zones["Z4"] == pytest.approx(60, abs=2)    # 160 bpm = 86%


def test_ride_uses_heart_rate_for_calories_and_tcx():
    times = np.arange(0.5, 900, 60 / 85)
    athlete = Athlete(weight_kg=80, age=40)
    hr = HeartRateSeries([(t, 130 + t / 60) for t in range(0, 900)], athlete)
    with_hr = Ride(times, 900.0, wheel_m=6, weight_kg=80, hr=hr)
    without = Ride(times, 900.0, wheel_m=6, weight_kg=80)
    assert with_hr.kcal == hr.kcal != without.kcal
    s = with_hr.summary()
    assert s["avg_hr"] == pytest.approx(137, abs=1) and s["trimp"] > 0
    assert with_hr.cardiac_drift() > 0                # same cadence, rising heart rate
    root = ET.fromstring(with_hr.to_tcx("2026-10-08T10:00:00", "x", s["kcal"]))
    ns = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
    assert root.find(".//t:Lap/t:AverageHeartRateBpm/t:Value", ns).text == str(s["avg_hr"])
    assert root.find(".//t:Trackpoint/t:HeartRateBpm/t:Value", ns) is not None


def test_recovery_during_rest_step():
    steps = [{"name": "Sprint", "kind": "work", "seconds": 60, "lo": 95, "hi": 115},
             {"name": "Recupera", "kind": "rest", "seconds": 60, "lo": 55, "hi": 70}]
    hr = HeartRateSeries([(t, 170 if t < 60 else 170 - (t - 60) * 0.5) for t in range(0, 121)], Athlete())
    res = Ride(np.arange(0.5, 120, 0.6), 120.0, hr=hr).step_results(steps)
    assert res[0]["avg_hr"] == 170 and "hr_drop" not in res[0]
    assert res[1]["hr_drop"] == pytest.approx(30, abs=1)


# ---- end to end
def test_ride_with_heart_rate(tmp_path, pulsoid):
    app = create_app(tmp_path / "hr.db", replay=str(DATA / "realtek_mme_77rpm.wav"),
                     hr_url=pulsoid.url, hr_token="demo")
    with TestClient(app) as c:
        assert wait_for(lambda: c.get("/api/live").json()["hr"].get("bpm"))
        c.put("/api/settings", json={"age": 40, "weight_kg": 80})
        c.post("/api/workout/start")
        assert wait_for(lambda: len(c.get("/api/live").json()["hr"].get("live", [])) >= 3)
        live = c.get("/api/live").json()["hr"]
        assert live["avg"] >= 120 and live["max_hr"] == 180
        sid = c.post("/api/workout/finish", json={"save": True}).json()["session_id"]
        a = c.get(f"/api/activities/{sid}").json()
        assert a["avg_hr"] >= 120 and a["heart"]["series"]["bpm"]
        assert c.post("/api/heart-rate/test", json={}).json()["ok"]
        assert c.get("/api/settings").json()["pulsoid_token"] == ""    # demo token never stored
