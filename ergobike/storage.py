from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta

from .analysis import BEST_WINDOWS, Ride, default_title

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY,
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    device         TEXT,
    pulses_per_rev INTEGER NOT NULL DEFAULT 1,
    wheel_m        REAL NOT NULL DEFAULT 0,
    duration_s     REAL,
    revs           INTEGER,
    avg_rpm        REAL,
    max_rpm        REAL,
    distance_km    REAL
);
CREATE TABLE IF NOT EXISTS revs (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    t_s        REAL NOT NULL,
    rev        INTEGER NOT NULL,
    rpm_inst   REAL,
    rpm_avg    REAL
);
CREATE INDEX IF NOT EXISTS revs_session ON revs(session_id, t_s);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# columns added after the first version; older databases get them via ALTER TABLE
MIGRATIONS = {
    "title": "TEXT", "notes": "TEXT", "weight_kg": "REAL", "moving_s": "REAL",
    "kcal": "REAL", "bests": "TEXT", "sparkline": "TEXT",
}

DEFAULT_SETTINGS = {
    "name": "", "device": None, "threshold": 0.1, "edge": "close", "pulses_per_rev": 1,
    "wheel_m": 6.0, "weight_kg": 75.0, "weekly_goal_min": 150,
}

SUMMARY_COLUMNS = ("id, started_at, ended_at, title, notes, duration_s, moving_s, revs, avg_rpm,"
                   " max_rpm, distance_km, kcal, bests, sparkline, wheel_m, pulses_per_rev,"
                   " weight_kg")


class Database:
    """SQLite store shared by the API threads and the recorder thread."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(sessions)")}
        for col, typ in MIGRATIONS.items():
            if col not in have:
                self.db.execute(f"ALTER TABLE sessions ADD COLUMN {col} {typ}")
        self.db.commit()
        self._settings: dict | None = None

    def close(self) -> None:
        self.db.close()

    # ---- settings (cached: the live snapshot reads them several times a second)
    def settings(self) -> dict:
        if self._settings is None:
            with self.lock:
                rows = self.db.execute("SELECT key, value FROM settings").fetchall()
            self._settings = {**DEFAULT_SETTINGS, **{r["key"]: json.loads(r["value"]) for r in rows}}
        return dict(self._settings)

    def update_settings(self, values: dict) -> dict:
        with self.lock:
            self.db.executemany(
                "INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(k, json.dumps(v)) for k, v in values.items() if k in DEFAULT_SETTINGS])
            self.db.commit()
            self._settings = None
        return self.settings()

    # ---- recording
    def start_session(self, device: str, ppr: int, wheel_m: float, weight_kg: float) -> int:
        now = datetime.now()
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO sessions (started_at, device, pulses_per_rev, wheel_m, weight_kg, title)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (now.isoformat(timespec="seconds"), device, ppr, wheel_m, weight_kg,
                 default_title(now)))
            self.db.commit()
        return cur.lastrowid

    def add_revs(self, sid: int, rows: list[tuple]) -> None:
        if not rows:
            return
        with self.lock:
            self.db.executemany("INSERT INTO revs VALUES (?, ?, ?, ?, ?)",
                                [(sid, *r) for r in rows])
            self.db.commit()

    def pulse_times(self, sid: int) -> list[float]:
        with self.lock:
            return [r[0] for r in self.db.execute(
                "SELECT t_s FROM revs WHERE session_id = ? ORDER BY t_s", (sid,))]

    def _ride(self, sid: int, duration: float | None = None) -> Ride | None:
        with self.lock:
            s = self.db.execute("SELECT pulses_per_rev, wheel_m, weight_kg, duration_s"
                                " FROM sessions WHERE id = ?", (sid,)).fetchone()
        if s is None:
            return None
        times = self.pulse_times(sid)
        if duration is None:
            duration = s["duration_s"] or (times[-1] if times else 0.0)
        return Ride(times, duration, s["pulses_per_rev"], s["wheel_m"],
                    s["weight_kg"] or DEFAULT_SETTINGS["weight_kg"])

    def _store_summary(self, sid: int, ride: Ride, ended_at: str | None = None) -> None:
        agg = ride.summary()
        with self.lock:
            self.db.execute(
                "UPDATE sessions SET ended_at = COALESCE(?, ended_at), duration_s=?, moving_s=?,"
                " revs=?, avg_rpm=?, max_rpm=?, distance_km=?, kcal=?, bests=?, sparkline=?"
                " WHERE id=?",
                (ended_at, agg["duration_s"], agg["moving_s"], agg["revs"], agg["avg_rpm"],
                 agg["max_rpm"], agg["distance_km"], agg["kcal"], json.dumps(agg["bests"]),
                 json.dumps(agg["sparkline"]), sid))
            self.db.commit()

    def finish_session(self, sid: int, duration_s: float) -> bool:
        """Store the aggregates. A session without pedalling is deleted."""
        ride = self._ride(sid, duration_s)
        if ride is None or len(ride.times) < 2:
            self.delete_session(sid)
            return False
        self._store_summary(sid, ride, datetime.now().isoformat(timespec="seconds"))
        return True

    def recover_unfinished(self) -> None:
        """Close sessions left open when the app was killed mid-ride."""
        with self.lock:
            ids = [r["id"] for r in self.db.execute("SELECT id FROM sessions WHERE ended_at IS NULL")]
        for sid in ids:
            times = self.pulse_times(sid)
            self.finish_session(sid, times[-1] if times else 0.0)

    def _backfill(self) -> None:
        """Sessions saved by older versions have no sparkline/bests yet."""
        with self.lock:
            ids = [r["id"] for r in self.db.execute(
                "SELECT id FROM sessions WHERE ended_at IS NOT NULL AND sparkline IS NULL")]
        for sid in ids:
            self._store_summary(sid, self._ride(sid))

    # ---- reading
    @staticmethod
    def _summary(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["bests"] = json.loads(d["bests"]) if d["bests"] else {}
        d["sparkline"] = json.loads(d["sparkline"]) if d["sparkline"] else []
        if d["moving_s"] is None:
            d["moving_s"] = d["duration_s"]
        return d

    def activities(self, limit: int = 30, offset: int = 0) -> list[dict]:
        self._backfill()
        with self.lock:
            rows = self.db.execute(
                f"SELECT {SUMMARY_COLUMNS} FROM sessions WHERE ended_at IS NOT NULL"
                " ORDER BY started_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [self._summary(r) for r in rows]

    def activity(self, sid: int) -> dict | None:
        with self.lock:
            row = self.db.execute(f"SELECT {SUMMARY_COLUMNS} FROM sessions WHERE id = ?",
                                  (sid,)).fetchone()
        if row is None:
            return None
        d = self._summary(row)
        d.update(self._ride(sid).detail())
        return d

    def update_activity(self, sid: int, title: str | None = None, notes: str | None = None) -> None:
        with self.lock:
            if title is not None:
                self.db.execute("UPDATE sessions SET title=? WHERE id=?", (title.strip(), sid))
            if notes is not None:
                self.db.execute("UPDATE sessions SET notes=? WHERE id=?", (notes, sid))
            self.db.commit()

    def delete_session(self, sid: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            self.db.commit()

    def export_tcx(self, sid: int) -> str | None:
        with self.lock:
            s = self.db.execute("SELECT started_at, title, kcal FROM sessions WHERE id=?",
                                (sid,)).fetchone()
        if s is None:
            return None
        return self._ride(sid).to_tcx(s["started_at"], s["title"] or "Pedal", int(s["kcal"] or 0))

    def export_csv(self, sid: int) -> str:
        with self.lock:
            rows = self.db.execute("SELECT t_s, rev, rpm_inst, rpm_avg FROM revs"
                                   " WHERE session_id=? ORDER BY t_s", (sid,)).fetchall()
        fmt = lambda v: "" if v is None else f"{v:.1f}"
        lines = ["t_s,rev,rpm_inst,rpm_avg"]
        lines += [f"{r[0]:.3f},{r[1]},{fmt(r[2])},{fmt(r[3])}" for r in rows]
        return "\n".join(lines) + "\n"

    # ---- progress
    def stats(self, weeks: int = 12) -> dict:
        self._backfill()
        with self.lock:
            rows = [self._summary(r) for r in self.db.execute(
                f"SELECT {SUMMARY_COLUMNS} FROM sessions WHERE ended_at IS NOT NULL")]
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        week_starts = [monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
        by_week = {w: {"week": w.isoformat(), "count": 0, "moving_s": 0.0, "distance_km": 0.0,
                       "revs": 0} for w in week_starts}
        days: dict[str, float] = {}
        totals = {"count": 0, "moving_s": 0.0, "distance_km": 0.0, "revs": 0, "kcal": 0.0}
        records: dict[str, dict] = {}
        longest = farthest = None
        active_weeks = set()

        for a in rows:
            day = datetime.fromisoformat(a["started_at"]).date()
            week = day - timedelta(days=day.weekday())
            active_weeks.add(week)
            moving = a["moving_s"] or 0.0
            dist = a["distance_km"] or 0.0
            days[day.isoformat()] = days.get(day.isoformat(), 0.0) + moving
            if week in by_week:
                b = by_week[week]
                b["count"] += 1
                b["moving_s"] += moving
                b["distance_km"] += dist
                b["revs"] += a["revs"] or 0
            totals["count"] += 1
            totals["moving_s"] += moving
            totals["distance_km"] += dist
            totals["revs"] += a["revs"] or 0
            totals["kcal"] += a["kcal"] or 0
            ref = {"id": a["id"], "title": a["title"], "started_at": a["started_at"]}
            for label, v in a["bests"].items():
                if label not in records or v > records[label]["rpm"]:
                    records[label] = {**ref, "rpm": v}
            if longest is None or moving > longest["moving_s"]:
                longest = {**ref, "moving_s": moving}
            if farthest is None or dist > farthest["distance_km"]:
                farthest = {**ref, "distance_km": dist}

        # consecutive weeks with at least one ride; the current week counts once it has one
        streak = 0
        week = monday if monday in active_weeks else monday - timedelta(weeks=1)
        while week in active_weeks:
            streak += 1
            week -= timedelta(weeks=1)

        return {
            "weeks": [by_week[w] for w in week_starts],
            "days": days,
            "totals": totals,
            "streak_weeks": streak,
            "records": [{"label": label, **records[label]}
                        for _, label in BEST_WINDOWS if label in records],
            "longest": longest,
            "farthest": farthest,
        }
