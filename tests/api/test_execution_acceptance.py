"""Execution acceptance through the real API (no substituted responses).

Scenario B with execution on: the baseline battery report at t=480 anchors a landing deadline
at t=1200; real D3 and the simulator's execution controller then run. Everything below is the
simulated ship's state; no real equipment is commanded.
"""

import json

import pytest
from fastapi.testclient import TestClient

from shipos.api import adapter
from shipos.api.server import create_app
from shipos.sim.runner import Runner

# Needs school's executor (PR #36). Without it the API refuses execution, so skip, not fail.
pytestmark = pytest.mark.skipif(
    not hasattr(Runner, "execution_status"), reason="simulator execution (PR #36) not present"
)

CACHE = {
    "source": "strait_of_sicily",
    "scenario_start": "2026-10-17T08:00:00+02:00",
    "layered_water": False,
    "timezone_offset_min": 120,
    "allow_stale": False,
}


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


def cmd(client, sid, **body):
    response = client.post(f"/api/sessions/{sid}/commands", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def rejected(client, sid, **body):
    before = client.get(f"/api/sessions/{sid}").json()
    evidence = client.get(f"/api/sessions/{sid}/evidence").json()
    response = client.post(f"/api/sessions/{sid}/commands", json=body)
    assert response.status_code == 422, response.text
    assert client.get(f"/api/sessions/{sid}").json() == before
    assert client.get(f"/api/sessions/{sid}/evidence").json() == evidence
    return response.json()["detail"]


def step_to(client, sid, t):
    """Advance until the next tick to run is t, within the per-command tick bound."""
    state = client.get(f"/api/sessions/{sid}").json()
    while state["next_t_s"] < t:
        state = cmd(client, sid, op="step", ticks=min(adapter.MAX_TICKS, t - state["next_t_s"]))
    return state


def execution_run(client, priority="survey_primary", crew=14, environment=None):
    sid = client.post("/api/sessions").json()["id"]
    cmd(client, sid, op="scenario", scenario="B")
    cmd(client, sid, op="priority", priority=priority)
    cmd(client, sid, op="crew", crew=crew)
    if environment:
        cmd(client, sid, op="environment", fresh_run=True, environment=environment)
    state = cmd(client, sid, op="execution", enabled=True, fresh_run=True)
    assert state["execution"]["enabled"] and state["priority_name"] == priority
    assert state["snapshot"]["crew"] == crew and state["scenario"] == "B"
    return sid, state


def last_arbitration(state):
    return next(
        h["decision"] for h in reversed(state["history"]) if h["decision"]["kind"] == "arbitration"
    )


def test_observation_horizon_and_tick_bound_are_unchanged(client):
    sid = client.post("/api/sessions").json()["id"]
    state = cmd(client, sid, op="scenario", scenario="B")
    assert state["horizon_s"] == adapter.HORIZON_S == 720
    assert rejected(client, sid, op="step", ticks=adapter.MAX_TICKS + 1)  # pydantic bound
    step_to(client, sid, adapter.HORIZON_S + 1)
    detail = rejected(client, sid, op="step")
    assert detail == "The observation run ends at 720 s; start a fresh run to continue"
    assert detail.isascii()


@pytest.mark.parametrize(
    ("priority", "g3_action"), [("survey_primary", "pause"), ("sar_primary", "suspend")]
)
def test_recovery_executes_after_preparation_and_operator_confirmation(client, priority, g3_action):
    sid, state = execution_run(client, priority)
    assert state["horizon_s"] == adapter.EXECUTION_HORIZON_S
    state = step_to(client, sid, 482)
    d3 = last_arbitration(state)
    assert d3["winner"] == "G4" and (
        d3["chosen"]["actions"][0]["guest"],
        d3["chosen"]["actions"][0]["kind"],
    ) == ("G3", g3_action)
    # Before the deck is clear, a confirmation is refused and changes nothing.
    assert "occupied" in rejected(client, sid, op="confirm_deck", by="deck officer")
    # Preparation completes; recovery still waits for an explicit operator input.
    state = step_to(client, sid, 841)
    assert state["snapshot"]["ship"]["deck"] == "clear"
    assert state["snapshot"]["guests"]["G4"]["mode"] == "flying"
    assert (
        state["crew_policy"]["confirmation"] == "pending" and not state["crew_policy"]["permitted"]
    )
    state = cmd(client, sid, op="confirm_deck", by="deck officer")
    assert state["snapshot"]["t_s"] == 841
    assert state["snapshot"]["guests"]["G4"]["mode"] == "recovery"
    assert state["snapshot"]["guests"]["G3"]["mode"] == "holding"
    applied = state["execution"]["status"]["applied"]
    assert any(a["kind"] == "action" and a["t_s"] == 841.0 for a in applied)
    assert state["execution"]["status"]["deadlines"]["G4"]["land_by_s"] == 1200.0
    state = step_to(client, sid, 841 + 181)
    assert state["snapshot"]["guests"]["G4"]["mode"] == "launching"  # declared follow-up
    # The exported evidence re-executes byte for byte, including the operator input.
    evidence = client.get(f"/api/sessions/{sid}/evidence").json()
    assert json.loads(evidence["jsonl"].splitlines()[0])["record"]["version"] == 3
    assert "operator_confirmation" in evidence["jsonl"]
    assert adapter.replay_evidence(evidence)["byte_identical"]
    assert cmd(client, sid, op="replay")["replay"]["byte_identical"]
    # The execution horizon is finite and enforced without changing state.
    step_to(client, sid, adapter.EXECUTION_HORIZON_S + 1)
    assert "1800 s" in rejected(client, sid, op="step")


@pytest.mark.parametrize(
    ("crew", "gate_blocker"), [(6, "deck_occupied"), (0, "no_crew_to_clear_deck")]
)
def test_reduced_crew_gets_the_engine_denial_not_a_landing(client, crew, gate_blocker):
    sid, _ = execution_run(client, crew=crew)
    state = step_to(client, sid, 482)
    d3 = last_arbitration(state)
    assert d3["winner"] == "G3" and any(a["kind"] == "deny" for a in d3["chosen"]["actions"])
    blocked = state["execution"]["status"]["blocked"]
    assert any(b.get("code") == "denied_by_engine" and b.get("guest") == "G4" for b in blocked)
    assert state["execution"]["status"]["pending"] == []
    state = step_to(client, sid, 841)
    assert gate_blocker in state["crew_policy"]["blockers"]
    assert state["snapshot"]["guests"]["G4"]["mode"] == "flying"  # no fabricated landing
    rejected(client, sid, op="confirm_deck", by="deck officer")
    assert adapter.replay_evidence(client.get(f"/api/sessions/{sid}/evidence").json())[
        "byte_identical"
    ]


def test_captured_weather_execution_replays_and_reset_archives(client):
    sid, state = execution_run(client, environment=CACHE)
    assert state["environment"]["configuration"]["source"] == "strait_of_sicily"
    assert state["environment"]["eligibility_only"] is True
    state = step_to(client, sid, 482)
    evidence = client.get(f"/api/sessions/{sid}/evidence").json()
    header = json.loads(evidence["jsonl"].splitlines()[0])["record"]
    assert header["version"] == 3 and header["environment"]["cache"]
    assert adapter.replay_evidence(evidence)["byte_identical"]
    runs = len(state["previous_runs"])
    reset = cmd(client, sid, op="reset")
    assert len(reset["previous_runs"]) == runs + 1
    assert reset["previous_runs"][-1]["final_hash"] == evidence["final_hash"]
    archived = client.get(f"/api/sessions/{sid}/evidence", params={"run": runs}).json()
    assert archived == evidence
    assert reset["execution"]["enabled"] is False and reset["horizon_s"] == adapter.HORIZON_S
    assert reset["environment"]["configuration"]["source"] == "scenario"
