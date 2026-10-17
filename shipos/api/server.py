"""Run from repository root: python -m uvicorn shipos.api.server:app."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from shipos.api import adapter
from shipos.contracts import Decision, GuestManifest, PriorityFile, ResourceClass
from shipos.data import load_priorities
from shipos.engine import arbitration, shedding
from shipos.paths import DATA_DIR
from shipos.physics import budget

STATIC = Path(__file__).parent / "static"


def priority_catalog() -> dict[str, PriorityFile]:
    directory = DATA_DIR / "priorities"
    return {
        path.stem: load_priorities(path.stem, directory=directory)
        for path in sorted(directory.glob("*.yaml"))
    }


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal[
        "admit",
        "import_card",
        "priority",
        "crew",
        "scenario",
        "mode",
        "reset",
        "event",
        "arbitration",
        "shedding",
        "step",
        "replay",
    ]
    card: str | None = None
    card_json: str | None = None
    value: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    priority: str | None = None
    crew: Literal[0, 6, 14] | None = None
    scenario: Literal["A", "B"] | None = None
    mode: Literal["LIVE", "SAMPLE"] | None = None
    event: Literal["genset_trip", "battery_report"] | None = None
    battery_pct: float = Field(default=18, ge=0, le=100, allow_inf_nan=False)
    ticks: int = Field(default=1, ge=1, le=adapter.MAX_TICKS, strict=True)


@dataclass
class Session:
    id: str
    simulation: adapter.Simulation = field(default_factory=adapter.Simulation)
    priorities: dict[str, PriorityFile] = field(default_factory=priority_catalog)
    mode: str = "LIVE"
    scenario: str = "A"
    priority: str = "survey_primary"
    decision: Decision | None = None
    history: list[dict] = field(default_factory=list)
    unavailable: list[dict] = field(default_factory=list)
    replay: dict | None = None
    notice: str = "LIVE Runner ready. First step emits t=0; no decision actions are applied."

    @property
    def snapshot(self):
        return self.simulation.runner.snapshot


def apply(state: Session, cmd: Command) -> Session:
    # Commit only after the entire command and response succeed.
    state = deepcopy(state)
    if cmd.op == "reset":
        return Session(state.id)
    required = {
        "admit": "card",
        "import_card": "card_json",
        "crew": "crew",
        "priority": "priority",
        "scenario": "scenario",
        "mode": "mode",
        "event": "event",
    }
    if cmd.op in required and getattr(cmd, required[cmd.op]) is None:
        raise ValueError(f"{required[cmd.op]} is required")
    if cmd.op == "replay":
        state.replay = adapter.replay_evidence(state.simulation.evidence())
        state.notice = (
            "Execution replay byte-identical (same callback, engine and runtime required)."
        )
        return state
    state.decision = None
    state.replay = None
    state.notice = "LIVE simulator progression; decisions are recorded, actions are not applied."
    context = state.simulation.runner.context
    catalogue = {**context.manifests, **context.cards}
    inputs = []
    if cmd.op == "import_card":
        card = adapter.parse_card(cmd.card_json, catalogue)
        if card.id in state.snapshot.guests:
            raise ValueError("Cannot replace an installed guest; use a proposed guest ID")
        inputs = [("card_presented", {"card": card.model_dump(mode="json")})]
    elif cmd.op == "admit":
        if cmd.card not in catalogue or cmd.card in state.snapshot.guests:
            raise ValueError("Select a proposed card that is not already aboard")
        if cmd.value is None:
            raise ValueError("A finite, nonnegative mass_t value is required")
        raw = catalogue[cmd.card].model_dump()
        raw["mass_t"] = cmd.value
        card = GuestManifest.model_validate(raw)
        inputs = [("card_presented", {"card": card.model_dump(mode="json")})]
    elif cmd.op in ("crew", "priority"):
        if cmd.op == "crew":
            inputs = [("crew_change", {"crew": cmd.crew})]
        else:
            if cmd.priority not in state.priorities:
                raise ValueError("Unknown priority; select an existing catalog key")
            state.priority = cmd.priority
            inputs = [
                (
                    "priority_swap",
                    {"priorities": state.priorities[cmd.priority].model_dump(mode="json")},
                )
            ]
    elif cmd.op in ("mode", "scenario"):
        state.mode = cmd.mode if cmd.op == "mode" else state.mode
        state.scenario = cmd.scenario if cmd.op == "scenario" else state.scenario
        # Selecting an input scenario/mode starts a fresh replay, never relabels old evidence.
        return Session(
            state.id,
            simulation=adapter.Simulation(state.scenario),
            mode=state.mode,
            scenario=state.scenario,
            notice="Runner reset. SAMPLE applies only to explicit D3/D4 previews."
            if state.mode == "SAMPLE"
            else "LIVE Runner baseline restored.",
        )
    elif cmd.op == "event":
        payload = (
            {"gensets_online": 1}
            if cmd.event == "genset_trip"
            else {"guest_id": "G4", "battery_pct": cmd.battery_pct}
        )
        inputs = [(cmd.event, payload)]
    elif cmd.op == "step":
        pass
    elif state.mode == "SAMPLE":
        state.decision = adapter.preview(cmd.op)
        state.notice = "SAMPLE fixture preview; edits and priority do not affect this preview"
    elif cmd.op == "arbitration":
        state.decision = arbitration.decide(
            context.ship,
            context.manifests,
            state.snapshot,
            context.priorities,
            "G4",
            "recovery",
        )
    else:
        state.decision = shedding.plan(
            context.ship,
            context.manifests,
            state.snapshot,
            context.priorities,
        )
    if inputs or cmd.op == "step":
        records = state.simulation.advance(cmd.ticks if cmd.op == "step" else 1, inputs)
        for record in records:
            for decision in record["decisions"]:
                state.history.append({"mode": "LIVE", "decision": decision})
            if record["decision_status"] == "engine_not_implemented":
                state.unavailable.append(
                    {
                        "t_s": record["snapshot"]["t_s"],
                        "events": [e["kind"] for e in record["events"]],
                        "status": "unavailable: engine callback batch not implemented",
                    }
                )
                state.notice = (
                    "LIVE progression; D3/D4 engine batch unavailable. No SAMPLE substitution."
                )
        decisions = records[-1]["decisions"]
        state.decision = Decision.model_validate(decisions[-1]) if decisions else None
    elif state.decision:
        # Explicit read-only engine queries/previews are outside the simulation replay.
        state.history.append(
            {"mode": state.mode, "decision": state.decision.model_dump(mode="json")}
        )
    state.history = state.history[-30:]
    return state


def view(state: Session) -> dict:
    context = state.simulation.runner.context
    ship = context.ship
    try:
        values = {r.cls: r for r in budget.gauges(ship, context.manifests, state.snapshot)}
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
        "guests": {
            k: v.model_dump(mode="json") for k, v in {**context.manifests, **context.cards}.items()
        },
        "manifests": {k: v.model_dump(mode="json") for k, v in context.manifests.items()},
        "proposed_cards": {k: v.model_dump(mode="json") for k, v in context.cards.items()},
        "snapshot": state.snapshot.model_dump(mode="json"),
        "priority": context.priorities.model_dump(mode="json"),
        "priority_name": state.priority,
        "priorities": list(state.priorities),
        "envelope": state.simulation.envelope.model_dump(mode="json"),
        "gauges": gauges,
        "decision": state.decision.model_dump(mode="json") if state.decision else None,
        "history": state.history,
        "unavailable": state.unavailable,
        "replay": state.replay,
        "next_t_s": state.simulation.runner.next_t_s,
        "final_hash": state.simulation.runner.final_hash,
        "horizon_s": adapter.HORIZON_S,
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

    @app.get("/api/sessions/{sid}/evidence")
    async def evidence(sid: str):
        get(sid)
        async with locks[sid]:
            return JSONResponse(
                get(sid).simulation.evidence(),
                headers={"Content-Disposition": 'attachment; filename="shipos-evidence.json"'},
            )

    @app.get("/api/sessions/{sid}/replay.jsonl")
    async def replay_log(sid: str):
        get(sid)
        async with locks[sid]:
            runner = get(sid).simulation.runner
            return Response(
                runner.jsonl,
                media_type="application/x-ndjson",
                headers={
                    "X-ShipOS-Final-Hash": runner.final_hash,
                    "Content-Disposition": 'attachment; filename="shipos-replay.jsonl"',
                },
            )

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
