import time

import pytest
from fastapi.testclient import TestClient

from conftest import DATA
from ergobike.server import create_app

REPLAY = str(DATA / "realtek_mme_77rpm.wav")


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(tmp_path / "t.db", replay=REPLAY)) as c:
        yield c


def live(client) -> dict:
    return client.get("/api/live").json()


def wait_for(cond, timeout=10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_full_ride(client):
    assert wait_for(lambda: live(client)["pedaling"])
    sid = client.post("/api/workout/start").json()["session_id"]
    assert live(client)["state"] == "running"
    assert wait_for(lambda: live(client)["revs"] >= 4, timeout=12)
    assert 65 < live(client)["avg_rpm"] < 85

    client.post("/api/workout/pause")
    paused = live(client)
    assert paused["state"] == "paused"
    time.sleep(1.5)
    assert live(client)["revs"] == paused["revs"]
    client.post("/api/workout/resume")
    time.sleep(1.5)

    r = client.post("/api/workout/finish", json={"save": True, "title": "Test", "notes": "ok"})
    assert r.json()["session_id"] == sid

    a = client.get(f"/api/activities/{sid}").json()
    assert (a["title"], a["notes"]) == ("Test", "ok")
    assert 65 < a["avg_rpm"] < 85 and a["revs"] >= 5
    assert a["series"]["t"] and len(a["zones"]) == 5
    feed = client.get("/api/activities").json()
    assert feed[0]["id"] == sid and feed[0]["sparkline"]
    stats = client.get("/api/stats").json()
    assert stats["totals"]["count"] == 1 and stats["streak_weeks"] == 1
    tcx = client.get(f"/api/activities/{sid}/export.tcx")
    assert tcx.status_code == 200 and "<Cadence>" in tcx.text
    assert client.get(f"/api/activities/{sid}/export.csv").text.startswith("t_s,rev")
    client.delete(f"/api/activities/{sid}")
    assert client.get(f"/api/activities/{sid}").status_code == 404


def test_discarded_ride_is_not_saved(client):
    client.post("/api/workout/start")
    assert client.post("/api/workout/finish", json={"save": False}).json()["session_id"] is None
    assert client.get("/api/activities").json() == []


def test_settings_and_calibration(client):
    cfg = client.put("/api/settings", json={"weight_kg": 80, "name": "Rafa", "bogus": 1}).json()
    assert cfg["weight_kg"] == 80 and cfg["name"] == "Rafa" and "bogus" not in cfg
    report = client.post("/api/calibrate?seconds=3").json()
    assert report["suggest_polarity"] == "close" and report["close"]["count"] >= 3


def test_sensor_settings_locked_during_ride(client):
    client.post("/api/workout/start")
    assert client.put("/api/settings", json={"threshold": 0.3}).status_code == 409
    assert client.get("/api/settings").json()["threshold"] == 0.1
    client.post("/api/workout/finish", json={"save": False})


def test_old_database_is_migrated_and_backfilled(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
            device TEXT, pulses_per_rev INTEGER NOT NULL DEFAULT 1, wheel_m REAL NOT NULL DEFAULT 0,
            duration_s REAL, revs INTEGER, avg_rpm REAL, max_rpm REAL, distance_km REAL);
        CREATE TABLE revs (session_id INTEGER NOT NULL, t_s REAL NOT NULL, rev INTEGER NOT NULL,
            rpm_inst REAL, rpm_avg REAL);
        INSERT INTO sessions VALUES (1, '2026-10-01T08:00:00', '2026-10-01T08:01:00', '2', 1, 0,
            60, 59, 60, 60, 0);
    """)
    old.executemany("INSERT INTO revs VALUES (1, ?, ?, NULL, NULL)", [(i + 0.5, i + 1) for i in range(59)])
    old.commit()
    old.close()
    with TestClient(create_app(path, replay=REPLAY)) as c:
        feed = c.get("/api/activities").json()
        assert len(feed) == 1 and feed[0]["sparkline"] and feed[0]["bests"]["1 min"] > 0


def test_structured_workout_ride(client):
    workouts = {w["id"]: w for w in client.get("/api/workouts").json()}
    assert {"free", "hiit-30-30", "tabata", "pyramid"} <= set(workouts)
    client.post("/api/workout/start", json={"workout_id": "hiit-30-30"})
    assert wait_for(lambda: live(client)["revs"] >= 3, timeout=12)
    snap = live(client)
    assert snap["workout"]["id"] == "hiit-30-30"
    assert snap["plan"]["index"] == 0 and snap["plan"]["step"]["kind"] == "warmup"
    assert snap["plan"]["status"] in ("below", "in", "above")
    sid = client.post("/api/workout/finish", json={"save": True}).json()["session_id"]
    a = client.get(f"/api/activities/{sid}").json()
    assert a["title"] == "HIIT 30/30" and a["workout"]["id"] == "hiit-30-30"
    assert a["step_results"][0]["kind"] == "warmup"


def test_free_ride_with_goal(client):
    client.post("/api/workout/start", json={"workout_id": "free", "goal": {"type": "time", "value": 600}})
    assert wait_for(lambda: live(client).get("goal") and live(client)["goal"]["done"] > 1)
    goal = live(client)["goal"]
    assert goal["type"] == "time" and 0 < goal["progress"] < 0.05
    assert live(client)["plan"] is None
    client.post("/api/workout/finish", json={"save": False})


def test_custom_interval_workout(client):
    body = {"name": "Meu HIIT", "rounds": 4, "work_s": 40, "work_lo": 95, "work_hi": 110,
            "rest_s": 20, "rest_lo": 55, "rest_hi": 70, "warmup_s": 120, "cooldown_s": 0}
    w = client.post("/api/workouts", json=body).json()
    assert w["duration_s"] == 120 + 4 * 60 and not w["builtin"]
    assert any(x["id"] == w["id"] for x in client.get("/api/workouts").json())
    assert client.post("/api/workouts", json={**body, "work_lo": 120}).status_code == 422
    assert client.delete("/api/workouts/hiit-30-30").status_code == 400
    client.delete(f"/api/workouts/{w['id']}")
    assert all(x["id"] != w["id"] for x in client.get("/api/workouts").json())

