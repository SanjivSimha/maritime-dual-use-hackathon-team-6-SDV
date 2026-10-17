"""Judge-card import (`import_card`): coverage ported from PR #29 onto PR #22's interface."""

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from shipos.api.server import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def card():
    """An unfamiliar civil card that no data file defines."""
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


def post(client, sid, body):
    return client.post(
        f"/api/sessions/{sid}/commands",
        content=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )


def evidence(client, sid):
    return client.get(f"/api/sessions/{sid}/evidence").json()


def test_non_mass_edit_reaches_the_power_checks(client, card):
    sid = client.post("/api/sessions").json()["id"]
    for power in (3, 7):
        card["modes"][0]["power_kw"] = power
        response = post(client, sid, {"op": "import_card", "card_json": json.dumps(card)})
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        assert decision["subject"] == "JUDGE7"
        totals = [r for r in decision["checks"] if r["resource"] == "power.total"]
        assert totals and all(r["contributors"]["JUDGE7"] == power for r in totals)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "optional_nonfinite",
        "next_mode",
        "duplicate_mode",
        "unknown_guest",
        "blank_id",
    ],
)
def test_invalid_card_is_rejected_transactionally(client, card, change):
    state = client.post("/api/sessions").json()
    sid = state["id"]
    saved = evidence(client, sid)
    if change == "missing":
        del card["capability"]
    elif change == "optional_nonfinite":
        card["area_m2"] = 1e999  # parses as inf
    elif change == "next_mode":
        card["modes"][0]["next_mode"] = "missing"
    elif change == "duplicate_mode":
        card["modes"].append(deepcopy(card["modes"][0]))
    elif change == "unknown_guest":
        card["requires"] = [{"target": "missing", "reason": "test"}]
    else:
        card["id"] = " "
    text = json.dumps(card).replace("Infinity", "1e999")
    response = post(client, sid, {"op": "import_card", "card_json": text})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]
    assert client.get(f"/api/sessions/{sid}").json() == state
    assert evidence(client, sid) == saved


@pytest.mark.parametrize(
    "extra",
    [{"card": "G4"}, {"value": 1}, {"card_json": {"id": "JUDGE7"}}],
)
def test_ambiguous_or_non_string_forms_roll_back(client, card, extra):
    state = client.post("/api/sessions").json()
    sid = state["id"]
    saved = evidence(client, sid)
    body = {"op": "import_card", "card_json": json.dumps(card), **extra}
    assert post(client, sid, body).status_code == 422
    assert client.get(f"/api/sessions/{sid}").json() == state
    assert evidence(client, sid) == saved


def test_websocket_import_then_quick_mass_edit(client, card):
    sid = client.post("/api/sessions").json()["id"]
    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"op": "import_card", "card_json": json.dumps(card)})
        assert ws.receive_json()["decision"]["subject"] == "JUDGE7"
        response = post(client, sid, {"op": "admit", "card": "JUDGE7", "value": 0.3})
        assert response.status_code == 200, response.text
        update = ws.receive_json()
        assert update == response.json()
        assert update["proposed_cards"]["JUDGE7"]["mass_t"] == 0.3
        assert update["proposed_cards"]["JUDGE7"]["modes"][0]["power_kw"] == 3
