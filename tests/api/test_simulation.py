import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from shipos.api import adapter
from shipos.api.server import create_app
from shipos.contracts import Envelope
from shipos.sim.log import chain_line


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


def new(client):
    return client.post("/api/sessions").json()


def cmd(client, sid, **body):
    result = client.post(f"/api/sessions/{sid}/commands", json=body)
    assert result.status_code == 200, result.text
    return result.json()


def evidence(client, sid):
    response = client.get(f"/api/sessions/{sid}/evidence")
    assert response.status_code == 200
    return response.json()


def test_interactive_replay_inputs_isolation_and_reset(client):
    first, other = new(client), new(client)
    sid = first["id"]
    baseline = evidence(client, sid)
    original = first["guests"]["G4"]["mass_t"]
    edited = cmd(client, sid, op="admit", card="G4", value=20)
    assert edited["decision"]["verdict"] == "no"
    assert edited["proposed_cards"]["G4"]["mass_t"] == 20
    assert edited["manifests"]["G4"]["mass_t"] == original
    assert "G4" not in edited["snapshot"]["guests"]
    cmd(client, sid, op="priority", priority="sar_primary")
    cmd(client, sid, op="crew", crew=6)
    final = cmd(client, sid, op="step", ticks=5)
    assert final["snapshot"]["t_s"] == 7
    saved = evidence(client, sid)
    records = [json.loads(line)["record"] for line in saved["jsonl"].splitlines()]
    assert [r["events"][0]["kind"] for r in records[1:4]] == [
        "card_presented",
        "priority_swap",
        "crew_change",
    ]
    assert all(e["t_s"] == r["snapshot"]["t_s"] for r in records[1:] for e in r["events"])
    assert all("latency_ms" not in d for r in records[1:] for d in r["decisions"])
    assert sid not in saved["jsonl"]
    replay = cmd(client, sid, op="replay")["replay"]
    assert replay["byte_identical"] and replay["final_hash"] == saved["final_hash"]
    assert replay["snapshot"] == final["snapshot"]
    assert evidence(client, sid) == saved
    assert evidence(client, other["id"]) == baseline
    reset = cmd(client, sid, op="reset")
    assert reset["history"] == [] and reset["proposed_cards"] == {} and reset["replay"] is None
    assert evidence(client, sid) == baseline
    cmd(client, sid, op="admit", card="G4", value=20)
    cmd(client, sid, op="priority", priority="sar_primary")
    cmd(client, sid, op="crew", crew=6)
    cmd(client, sid, op="step", ticks=5)
    assert evidence(client, sid) == saved


def test_injection_websocket_http_agreement_and_invalid_atomicity(client):
    state = new(client)
    sid = state["id"]
    state = cmd(client, sid, op="scenario", scenario="B")
    baseline = evidence(client, sid)
    assert json.loads(baseline["jsonl"])["record"]["seed"] == 6
    with client.websocket_connect(f"/ws/{sid}") as ws:
        assert ws.receive_json()["snapshot"]["guests"]["G4"]["battery_pct"] == 30
        ws.send_json({"op": "event", "event": "battery_report", "battery_pct": 17})
        update = ws.receive_json()
        assert update["snapshot"]["guests"]["G4"]["battery_pct"] == 17
        assert update["snapshot"]["guests"]["G4"]["mode"] == "flying"
        assert update["decision"] is None and "unavailable" in update["notice"]
        assert client.get(f"/api/sessions/{sid}").json() == update
        response = cmd(client, sid, op="event", event="genset_trip")
        assert ws.receive_json() == response
        assert response["snapshot"]["platform"]["gensets_online"] == 1
        assert response["snapshot"]["t_s"] == 1
    before = evidence(client, sid)
    for body in [
        {"op": "event", "event": "genset_trip"},
        {"op": "event", "event": "battery_report", "battery_pct": 101},
        {"op": "step", "ticks": 0},
        {"op": "step", "ticks": 121},
        {"op": "step", "ticks": 1.5},
        {"op": "admit", "card": "G4", "value": 3},
    ]:
        assert client.post(f"/api/sessions/{sid}/commands", json=body).status_code == 422
        assert evidence(client, sid) == before
        assert client.get(f"/api/sessions/{sid}").json() == response
    assert cmd(client, sid, op="replay")["replay"]["byte_identical"]
    raw = client.get(f"/api/sessions/{sid}/replay.jsonl")
    assert raw.content == before["jsonl"].encode()
    assert raw.headers["x-shipos-final-hash"] == before["final_hash"]
    cmd(client, sid, op="reset")
    cmd(client, sid, op="scenario", scenario="B")
    assert evidence(client, sid) == baseline


def test_baseline_b_timeline_horizon_and_true_nominal_load(client):
    sid = new(client)["id"]
    cmd(client, sid, op="scenario", scenario="B")
    for _ in range(6):
        state = cmd(client, sid, op="step", ticks=120)
    state = cmd(client, sid, op="step")
    assert state["snapshot"]["t_s"] == 720
    assert state["snapshot"]["platform"]["gensets_online"] == 1
    assert state["snapshot"]["guests"]["G4"]["mode"] == "flying"
    assert state["snapshot"]["guests"]["G3"]["mode"] == "surveying"
    nominal = sum(
        next(m["power_kw"] for m in state["manifests"][gid]["modes"] if m["name"] == rt["mode"])
        for gid, rt in state["snapshot"]["guests"].items()
    )
    assert nominal == 49.5
    assert state["unavailable"][-1]["t_s"] == 720
    before = evidence(client, sid)
    assert client.post(f"/api/sessions/{sid}/commands", json={"op": "step"}).status_code == 422
    assert evidence(client, sid) == before
    assert adapter.replay_evidence(before)["byte_identical"]


def test_export_captures_envelope_and_execution_replay_detects_changes(client, monkeypatch):
    sid = new(client)["id"]
    cmd(client, sid, op="admit", card="G4", value=0.9)
    saved = evidence(client, sid)

    def no_yaml(*args, **kwargs):
        raise AssertionError("Replay must use exported inputs")

    monkeypatch.setattr(adapter, "load_envelope", no_yaml)
    assert adapter.replay_evidence(saved)["byte_identical"]
    assert Envelope.model_validate(saved["envelope"])
    missing = deepcopy(saved)
    del missing["envelope"]
    with pytest.raises(KeyError):
        adapter.replay_evidence(missing)
    # Recompute a perfectly valid chain containing a false engine verdict.
    changed, previous = [], "0" * 64
    for line in saved["jsonl"].splitlines():
        record = json.loads(line)["record"]
        if record["type"] == "tick":
            record["decisions"][0]["verdict"] = "no"
        encoded, previous = chain_line(record, len(changed), previous)
        changed.append(encoded)
    forged = {**saved, "jsonl": b"".join(changed).decode(), "final_hash": previous}
    with pytest.raises(ValueError, match="replay differs"):
        adapter.replay_evidence(forged)


def test_fast_forward_rolls_back_whole_command_on_later_invalid_timeline_event(client):
    sid = new(client)["id"]
    cmd(client, sid, op="scenario", scenario="B")
    cmd(client, sid, op="event", event="genset_trip")
    for _ in range(5):
        state = cmd(client, sid, op="step", ticks=120)
    assert state["snapshot"]["t_s"] == 600
    saved = evidence(client, sid)
    response = client.post(f"/api/sessions/{sid}/commands", json={"op": "step", "ticks": 120})
    assert response.status_code == 422 and "reduce" in response.json()["detail"]
    assert evidence(client, sid) == saved
    assert client.get(f"/api/sessions/{sid}").json() == state
