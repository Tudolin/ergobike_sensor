from __future__ import annotations

import asyncio
import dataclasses
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audio import input_devices
from .recorder import Recorder
from .storage import Database
from .workouts import BUILTIN, Goal, intervals

WEB_DIR = Path(__file__).parent / "web"
AUDIO_SETTINGS = ("device", "threshold", "edge")


class ActivityPatch(BaseModel):
    title: str | None = None
    notes: str | None = None


class GoalRequest(BaseModel):
    type: str = Field(pattern="^(time|distance)$")
    value: float = Field(gt=0)


class StartRequest(BaseModel):
    workout_id: str = "free"
    goal: GoalRequest | None = None


class IntervalWorkoutRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    rounds: int = Field(ge=1, le=50)
    work_s: int = Field(ge=5, le=3600)
    work_lo: int = Field(ge=20, le=200)
    work_hi: int = Field(ge=20, le=220)
    rest_s: int = Field(ge=0, le=3600)
    rest_lo: int = Field(ge=0, le=200)
    rest_hi: int = Field(ge=0, le=220)
    warmup_s: int = Field(default=300, ge=0, le=3600)
    cooldown_s: int = Field(default=300, ge=0, le=3600)


class FinishRequest(BaseModel):
    save: bool = True
    title: str | None = None
    notes: str | None = None


def create_app(db_path: Path, replay: str | None = None) -> FastAPI:
    db = Database(str(db_path))
    recorder = Recorder(db, replay=replay)

    @asynccontextmanager
    async def lifespan(_app):
        recorder.start()
        yield
        recorder.shutdown()
        db.close()

    app = FastAPI(title="ErgoBike", lifespan=lifespan)
    app.state.recorder = recorder
    app.state.db = db

    def conflict(e: Exception) -> HTTPException:
        return HTTPException(409, str(e))

    # ---- live
    @app.get("/api/live")
    def live():
        return recorder.snapshot()

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        try:
            while True:
                await sock.send_json(recorder.snapshot())
                await asyncio.sleep(0.25)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.post("/api/workout/start")
    def workout_start(body: StartRequest | None = None):
        body = body or StartRequest()
        workout = db.workout(body.workout_id)
        if workout is None:
            raise HTTPException(404, "treino não encontrado")
        if body.goal:
            workout = dataclasses.replace(workout, goal=Goal(body.goal.type, body.goal.value))
        try:
            return {"session_id": recorder.begin(workout)}
        except RuntimeError as e:
            raise conflict(e) from e

    @app.post("/api/workout/pause")
    def workout_pause():
        recorder.pause()
        return recorder.snapshot()

    @app.post("/api/workout/resume")
    def workout_resume():
        recorder.resume()
        return recorder.snapshot()

    @app.post("/api/workout/finish")
    def workout_finish(body: FinishRequest):
        sid = recorder.finish(save=body.save)
        if sid is None:
            return {"session_id": None, "records": []}
        if body.title or body.notes:
            db.update_activity(sid, body.title or None, body.notes)
        return {"session_id": sid, "records": db.new_records(sid)}

    # ---- workouts
    @app.get("/api/workouts")
    def workouts():
        return [w.to_dict() for w in db.workouts()]

    @app.post("/api/workouts")
    def create_workout(body: IntervalWorkoutRequest):
        if body.work_lo >= body.work_hi or (body.rest_s and body.rest_lo >= body.rest_hi):
            raise HTTPException(422, "faixa de rpm inválida")
        desc = (f"{body.rounds} x {body.work_s} s a {body.work_lo}-{body.work_hi} rpm"
                + (f", {body.rest_s} s leve entre eles" if body.rest_s else ""))
        w = intervals(body.name.strip(), body.rounds, body.work_s, body.work_lo, body.work_hi,
                      body.rest_s, body.rest_lo, body.rest_hi, body.warmup_s, body.cooldown_s,
                      id=f"custom-{uuid.uuid4().hex[:8]}", description=desc)
        return db.save_workout(w).to_dict()

    @app.delete("/api/workouts/{wid}")
    def delete_workout(wid: str):
        if wid in BUILTIN:
            raise HTTPException(400, "treinos padrão não podem ser apagados")
        db.delete_workout(wid)
        return {"ok": True}

    # ---- settings and sensor
    @app.get("/api/devices")
    def devices():
        if replay:
            return [{"index": None, "name": f"Replay ({Path(replay).name})"}]
        return [{"index": i, "name": n} for i, n in input_devices()]

    @app.get("/api/settings")
    def get_settings():
        return db.settings()

    @app.put("/api/settings")
    def put_settings(values: dict):
        before = db.settings()
        after = db.update_settings(values)
        if any(before[k] != after[k] for k in AUDIO_SETTINGS):
            try:
                recorder.open_source()
            except RuntimeError as e:
                db.update_settings({k: before[k] for k in AUDIO_SETTINGS})
                raise conflict(e) from e
        return after

    @app.post("/api/calibrate")
    def calibrate(seconds: float = 10.0):
        try:
            return recorder.calibrate(min(max(seconds, 3.0), 30.0))
        except RuntimeError as e:
            raise conflict(e) from e

    # ---- activities
    @app.get("/api/activities")
    def activities(limit: int = 30, offset: int = 0):
        return db.activities(limit, offset)

    @app.get("/api/activities/{sid}")
    def activity(sid: int):
        if (a := db.activity(sid)) is None:
            raise HTTPException(404, "atividade não encontrada")
        return a

    @app.patch("/api/activities/{sid}")
    def patch_activity(sid: int, body: ActivityPatch):
        db.update_activity(sid, body.title, body.notes)
        return db.activity(sid)

    @app.delete("/api/activities/{sid}")
    def delete_activity(sid: int):
        db.delete_session(sid)
        return {"ok": True}

    @app.get("/api/activities/{sid}/export.tcx")
    def export_tcx(sid: int):
        if (tcx := db.export_tcx(sid)) is None:
            raise HTTPException(404, "atividade não encontrada")
        return Response(tcx, media_type="application/vnd.garmin.tcx+xml", headers={
            "Content-Disposition": f'attachment; filename="ride-{sid}.tcx"'})

    @app.get("/api/activities/{sid}/export.csv")
    def export_csv(sid: int):
        return PlainTextResponse(db.export_csv(sid), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="ride-{sid}.csv"'})

    @app.get("/api/stats")
    def stats(weeks: int = 12):
        return db.stats(weeks)

    # ---- front-end
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    return app
