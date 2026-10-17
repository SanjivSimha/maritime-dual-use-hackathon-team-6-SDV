"""Runtime contract regressions; observation compatibility and real public library paths."""

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from shipos.api import adapter
from shipos.api.server import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


def new(client):
    return client.post("/api/sessions").json()["id"]


def command(client, sid, **body):
    return client.post(f"/api/sessions/{sid}/commands", json=body)


def state(client, sid):
    return client.get(f"/api/sessions/{sid}").json()


def evidence(client, sid, run=None):
    return client.get(
        f"/api/sessions/{sid}/evidence", params={} if run is None else {"run": run}
    ).json()


def environment():
    return {
        "source": "strait_of_sicily",
        "scenario_start": "2026-10-17T08:00:00+02:00",
        "layered_water": False,
        "timezone_offset_min": 120,
        "allow_stale": False,
    }


def test_environment_public_construction_archives_and_replays(client, monkeypatch):
    sid, independent = new(client), new(client)
    command(client, sid, op="scenario", scenario="B")
    command(client, sid, op="priority", priority="sar_primary")
    command(client, sid, op="crew", crew=6)
    before = evidence(client, sid)
    response = command(client, sid, op="environment", fresh_run=True, environment=environment())
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["scenario"] == "B" and current["snapshot"]["crew"] == 6
    assert current["priority_name"] == "sar_primary"
    archive = current["previous_runs"][-1]["run"]
    assert evidence(client, sid, archive) == before
    assert current["environment"]["eligibility_only"]
    assert current["environment"]["provenance"]["kind"] == "open_meteo_forecast"
    assert current["execution"]["enabled"] is False
    assert state(client, independent)["environment"]["configuration"]["source"] == "scenario"
    step = command(client, sid, op="step").json()
    assert step["snapshot"]["env"]["forecast"]
    saved = evidence(client, sid)
    assert json.loads(saved["jsonl"].splitlines()[0])["record"]["environment"]

    def unavailable(*args, **kwargs):
        raise AssertionError("replay must not read a cache file")

    monkeypatch.setattr(adapter.EnvironmentInput, "from_cache", unavailable)
    assert adapter.replay_evidence(saved)["byte_identical"]


def test_invalid_runtime_commands_preserve_state_and_evidence(client):
    sid = new(client)
    before, saved = state(client, sid), evidence(client, sid)
    invalid = [
        dict(op="execution", enabled=True),
        dict(op="execution", enabled="yes", fresh_run=True),
        dict(op="environment", environment=environment()),
        dict(op="environment", fresh_run=True, environment={"source": "../../cache"}),
        dict(op="environment", fresh_run=True, environment={"source": "strait_of_sicily"}),
    ]
    bad_time = deepcopy(environment())
    bad_time["scenario_start"] = "2026-10-17T08:00:00"
    invalid.append(dict(op="environment", fresh_run=True, environment=bad_time))
    for body in invalid:
        response = command(client, sid, **body)
        assert response.status_code == 422, response.text
        assert state(client, sid) == before and evidence(client, sid) == saved


@pytest.mark.parametrize("crew", [14, 6, 0])
def test_observation_exposes_crew_requirement_separately_from_receipt(client, crew):
    sid = new(client)
    current = command(client, sid, op="crew", crew=crew).json()
    assert current["execution"]["enabled"] is False
    gate = current["crew_policy"]
    assert not gate["permitted"] and gate["blockers"]
    assert gate["confirmation"] != "received"
    assert gate["confirm"] == ("human" if crew else "forbidden")
