"""Run from repository root: python -m uvicorn shipos.api.server:app."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from shipos.api import adapter
from shipos.contracts import Decision, GuestManifest, ResourceClass, WorldSnapshot
from shipos.data import load_envelope, load_guests, load_priorities, load_ship
from shipos.engine import admission, arbitration, shedding
from shipos.physics import budget

STATIC = Path(__file__).parent / "static"
PRIORITIES = ("survey_primary", "sar_primary")


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal[
        "admit", "priority", "crew", "scenario", "mode", "reset", "event", "arbitration", "shedding"
    ]
    card: str | None = None
    value: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    priority: Literal["survey_primary", "sar_primary"] | None = None
    crew: Literal[0, 6, 14] | None = None
    scenario: Literal["A", "B"] | None = None
    mode: Literal["LIVE", "SAMPLE"] | None = None
    event: Literal["genset_trip", "battery_report"] | None = None


@dataclass
class Session:
    id: str
    guests: dict[str, GuestManifest] = field(default_factory=load_guests)
    snapshot: WorldSnapshot = field(default_factory=lambda: adapter.snapshot("A", "LIVE"))
    mode: str = "LIVE"
    scenario: str = "A"
    priority: str = "survey_primary"
    decision: Decision | None = None
    history: list[dict] = field(default_factory=list)
    card: str | None = None
    notice: str = "Baseline Scenario A snapshot; LIVE calculations, no simulation clock."


def decide(state: Session):
    start = perf_counter()
    state.decision = admission.decide(
        load_ship(),
        state.guests,
        state.snapshot,
        state.guests[state.card],
        load_priorities(state.priority),
        load_envelope(),
    )
    state.decision = state.decision.model_copy(
        update={"latency_ms": (perf_counter() - start) * 1000}
    )


def apply(state: Session, cmd: Command) -> Session:
    # Commit only after the entire command and response succeed.
    state = deepcopy(state)
    if cmd.op == "reset":
        return Session(state.id)
    required = {
        "admit": "card",
        "crew": "crew",
        "priority": "priority",
        "scenario": "scenario",
        "mode": "mode",
        "event": "event",
    }
    if cmd.op in required and getattr(cmd, required[cmd.op]) is None:
        raise ValueError(f"{required[cmd.op]} is required")
    state.decision = None
    state.notice = "LIVE engine calculation" if state.mode == "LIVE" else "SAMPLE mode"
    if cmd.op == "admit":
        if cmd.card not in state.guests or cmd.card in state.snapshot.guests:
            raise ValueError("Select a proposed card that is not already aboard")
        if cmd.value is None:
            raise ValueError("A finite, nonnegative mass_t value is required")
        raw = state.guests[cmd.card].model_dump()
        raw["mass_t"] = cmd.value
        state.guests[cmd.card] = GuestManifest.model_validate(raw)
        state.card = cmd.card
        decide(state)
    elif cmd.op in ("crew", "priority"):
        if cmd.op == "crew":
            state.snapshot = state.snapshot.model_copy(update={"crew": cmd.crew})
        else:
            state.priority = cmd.priority
        if state.card:
            decide(state)
    elif cmd.op in ("mode", "scenario"):
        state.mode = cmd.mode if cmd.op == "mode" else state.mode
        state.scenario = cmd.scenario if cmd.op == "scenario" else "A"
        state.snapshot = adapter.snapshot(state.scenario, state.mode)
        state.card = None
        state.history = []
        state.notice = (
            "SAMPLE fixture snapshot; admission still calls the real engine"
            if state.mode == "SAMPLE"
            else "Baseline A snapshot; LIVE calculations, no clock"
        )
    elif cmd.op == "event":
        state.snapshot = adapter.apply_event(cmd.event)
    elif state.mode == "SAMPLE":
        state.decision = adapter.preview(cmd.op)
        state.notice = "SAMPLE fixture preview; edits and priority do not affect this preview"
    elif cmd.op == "arbitration":
        state.decision = arbitration.decide(
            load_ship(),
            state.guests,
            state.snapshot,
            load_priorities(state.priority),
            "G4",
            "recovery",
        )
    else:
        state.decision = shedding.plan(
            load_ship(), state.guests, state.snapshot, load_priorities(state.priority)
        )
    return state


def view(state: Session) -> dict:
    ship = load_ship()
    try:
        values = {r.cls: r for r in budget.gauges(ship, state.guests, state.snapshot)}
        error = None
    except NotImplementedError as exc:
        values, error = {}, str(exc)
    gauges = []
    for cls in ResourceClass:
        result = values.get(cls)
        # The public gauge's default zero latency is not a measured response time.
        available = result is not None and cls != ResourceClass.TIME
        gauges.append(
            {
                **(
                    result.model_dump(mode="json")
                    if available
                    else {"cls": cls.value, "unit": result.unit if result else None}
                ),
                "status": "available" if available else "unavailable",
                "source": "physics.budget.gauges / current snapshot"
                if available
                else error or "No response-time measurement for this snapshot",
            }
        )
    return {
        "id": state.id,
        "mode": state.mode,
        "scenario": state.scenario,
        "notice": state.notice,
        "ship": ship.model_dump(mode="json"),
        "guests": {k: v.model_dump(mode="json") for k, v in state.guests.items()},
        "snapshot": state.snapshot.model_dump(mode="json"),
        "priority": load_priorities(state.priority).model_dump(mode="json"),
        "priority_name": state.priority,
        "priorities": PRIORITIES,
        "envelope": load_envelope().model_dump(mode="json"),
        "gauges": gauges,
        "decision": state.decision.model_dump(mode="json") if state.decision else None,
        "history": state.history,
    }


def create_app() -> FastAPI:
    app = FastAPI(title="ShipOS offline governor")
    sessions: dict[str, Session] = {}
    locks: dict[str, asyncio.Lock] = {}
    peers: dict[str, set[WebSocket]] = {}

    def get(sid):
        if sid not in sessions:
            raise HTTPException(404, "Session not found; create a new session")
        return sessions[sid]

    async def execute(sid, cmd):
        get(sid)
        async with locks[sid]:
            try:
                updated = await run_in_threadpool(apply, sessions[sid], cmd)
            except NotImplementedError as exc:
                updated = deepcopy(sessions[sid])
                updated.decision = None
                updated.notice = f"{updated.mode} unavailable: {exc}"
            except (ValueError, KeyError) as exc:
                raise HTTPException(422, str(exc)) from exc
            if updated.decision:
                updated.history = (
                    updated.history
                    + [{"mode": updated.mode, "decision": updated.decision.model_dump(mode="json")}]
                )[-30:]
            response = await run_in_threadpool(view, updated)
            sessions[sid] = updated
            for ws in tuple(peers[sid]):
                try:
                    await ws.send_json(response)
                except (WebSocketDisconnect, RuntimeError, OSError):
                    peers[sid].discard(ws)
            return response

    @app.post("/api/sessions")
    def new_session():
        state = Session(uuid4().hex)
        sessions[state.id] = state
        locks[state.id], peers[state.id] = asyncio.Lock(), set()
        return view(state)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Exclude raw invalid inputs (NaN/Infinity) and exception objects from JSON.
        return JSONResponse(
            status_code=422,
            content={"detail": [{k: e[k] for k in ("loc", "msg", "type")} for e in exc.errors()]},
        )

    @app.get("/api/sessions/{sid}")
    def snapshot(sid: str):
        return view(get(sid))

    @app.post("/api/sessions/{sid}/commands")
    async def command(sid: str, cmd: Command):
        return await execute(sid, cmd)

    @app.websocket("/ws/{sid}")
    async def updates(ws: WebSocket, sid: str):
        if sid not in sessions:
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            async with locks[sid]:
                await ws.send_json(view(get(sid)))
                peers[sid].add(ws)
            while True:
                try:
                    cmd = Command.model_validate(await ws.receive_json())
                    await execute(sid, cmd)
                except (ValidationError, HTTPException, ValueError) as exc:
                    await ws.send_json({"error": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            peers[sid].discard(ws)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
