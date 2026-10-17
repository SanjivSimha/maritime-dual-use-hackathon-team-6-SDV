import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from shipos.api import adapter
from shipos.api.server import create_app
from shipos.contracts import Envelope, GuestManifest
from shipos.engine import admission
from shipos.sim.runner import replay_jsonl


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def card():
    return {
        "id": "JUDGE7",
        "name": "Civil air-quality sampler",
        "mass_t": 0.2,
        "cooling": "water",
        "preferred": ["hangar"],
        "allowed": ["hangar"],
        "modes": [{"name": "sampling", "power_kw": 3}],
        "initial_mode": "sampling",
        "capability": {"name": "Air-quality sampling", "value_by_mission": {"survey": "medium"}},
        "installation": {"where": "port_only", "people": 2, "hours": 1},
    }


def evidence(client, sid):
    return client.get(f"/api/sessions/{sid}/evidence").json()


def test_unfamiliar_card_non_mass_edit_real_admission_and_replay(client, card, monkeypatch):
    initial = client.post("/api/sessions").json()
    sid = initial["id"]
    baseline = evidence(client, sid)
    real_decide, seen = admission.decide, []

    def observed(ship, manifests, snapshot, proposed, priorities, envelope):
        seen.append(proposed.model_dump(mode="json"))
        return real_decide(ship, manifests, snapshot, proposed, priorities, envelope)

    monkeypatch.setattr(admission, "decide", observed)
    expected_cards = []
    for power in [3, 7]:
        card["modes"][0]["power_kw"] = power
        card["compute"] = {"cores": 1, "mem_gb": 2}
        expected = GuestManifest.model_validate(card).model_dump(mode="json")
        response = client.post(
            f"/api/sessions/{sid}/commands", json={"op": "admit", "manifest": card}
        )
        assert response.status_code == 200, response.text
        state = response.json()
        expected_cards.append(expected)
        assert seen[-1] == expected
        assert state["decision"]["subject"] == "JUDGE7"
        assert state["proposed_cards"]["JUDGE7"] == expected
        assert state["manifests"] == initial["manifests"]
        assert "JUDGE7" not in state["snapshot"]["guests"]
        power_checks = [r for r in state["decision"]["checks"] if r["resource"] == "power.total"]
        assert power_checks and all(r["contributors"]["JUDGE7"] == power for r in power_checks)
    assert seen == expected_cards
    saved = evidence(client, sid)
    records = [json.loads(line)["record"] for line in saved["jsonl"].splitlines()[1:]]
    assert [r["injected_events"][0]["payload"]["card"] for r in records] == expected_cards
    raw = client.get(f"/api/sessions/{sid}/replay.jsonl")
    assert raw.content == saved["jsonl"].encode("utf-8")

    def no_card_files(*args, **kwargs):
        raise AssertionError("Execution replay must use embedded cards, not a card file")

    monkeypatch.setattr(adapter, "load_guests", no_card_files)
    replayed = replay_jsonl(
        raw.content, decide=adapter.callback(Envelope.model_validate(saved["envelope"]))
    )
    assert replayed.jsonl == raw.content
    assert adapter.replay_evidence(saved)["byte_identical"]
    assert client.post(f"/api/sessions/{sid}/commands", json={"op": "replay"}).json()["replay"][
        "byte_identical"
    ]
    assert evidence(client, sid) == saved
    monkeypatch.undo()
    reset = client.post(f"/api/sessions/{sid}/commands", json={"op": "reset"}).json()
    assert reset["proposed_cards"] == {}
    assert "JUDGE7" not in reset["guests"]
    assert evidence(client, sid) == baseline


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "structure",
        "nonfinite",
        "numeric_string",
        "optional_nonfinite",
        "reference",
        "initial_mode",
        "next_mode",
        "duplicate_mode",
        "unknown_guest",
        "installed",
        "empty_id",
    ],
)
def test_invalid_complete_card_preserves_state_and_evidence(client, card, change):
    state = client.post("/api/sessions").json()
    sid = state["id"]
    saved = evidence(client, sid)
    if change == "missing":
        del card["capability"]
    elif change == "structure":
        card["modes"] = "sampling"
    elif change == "nonfinite":
        card["modes"][0]["power_kw"] = float("nan")
    elif change == "numeric_string":
        card["modes"][0]["power_kw"] = "Infinity"
    elif change == "optional_nonfinite":
        card["area_m2"] = float("inf")
    elif change == "reference":
        card["modes"][0]["power_kw"] = "$power.unresolved"
    elif change == "initial_mode":
        card["initial_mode"] = "missing"
    elif change == "next_mode":
        card["modes"][0]["next_mode"] = "missing"
    elif change == "duplicate_mode":
        card["modes"].append(deepcopy(card["modes"][0]))
    elif change == "unknown_guest":
        card["requires"] = [{"target": "missing", "reason": "test"}]
    elif change == "installed":
        card["id"] = "G1"
    else:
        card["id"] = " "
    response = client.post(
        f"/api/sessions/{sid}/commands",
        content=json.dumps({"op": "admit", "manifest": card}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]
    assert client.get(f"/api/sessions/{sid}").json() == state
    assert evidence(client, sid) == saved


def test_malformed_json_and_ambiguous_forms_roll_back(client, card):
    state = client.post("/api/sessions").json()
    sid = state["id"]
    saved = evidence(client, sid)
    for body in [
        '{"op":"admit","manifest":',
        json.dumps({"op": "admit", "manifest": [card]}),
        json.dumps({"op": "admit", "manifest": card, "card": "G4", "value": 1}),
    ]:
        response = client.post(
            f"/api/sessions/{sid}/commands",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert client.get(f"/api/sessions/{sid}").json() == state
        assert evidence(client, sid) == saved


def test_unfamiliar_card_websocket_and_quick_mass_edit(client, card):
    initial = client.post("/api/sessions").json()
    sid = initial["id"]
    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"op": "admit", "manifest": card})
        assert ws.receive_json()["decision"]["subject"] == "JUDGE7"
        response = client.post(
            f"/api/sessions/{sid}/commands", json={"op": "admit", "card": "JUDGE7", "value": 0.3}
        )
        assert response.status_code == 200
        update = ws.receive_json()
        assert update == response.json()
        assert update["proposed_cards"]["JUDGE7"]["mass_t"] == 0.3
        assert update["proposed_cards"]["JUDGE7"]["modes"][0]["power_kw"] == 3
