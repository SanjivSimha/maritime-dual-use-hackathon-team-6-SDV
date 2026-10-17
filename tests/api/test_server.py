import pytest
from fastapi.testclient import TestClient

from shipos.api.server import create_app
from shipos.data import load_guests, load_priorities, load_ship


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


def session(client):
    response = client.post("/api/sessions")
    assert response.status_code == 200
    return response.json()


def command(client, state, **command):
    return client.post(f"/api/sessions/{state['id']}/commands", json=command)


def test_real_card_boundary_and_transactional_validation(client):
    state = session(client)
    first = command(client, state, op="admit", card="G4", value=load_guests()["G4"].mass_t)
    assert first.status_code == 200
    assert first.json()["decision"]["verdict"] == "yes_if"
    mass = load_ship().mass.margin_t + 1
    changed = command(client, state, op="admit", card="G4", value=mass).json()
    assert changed["decision"]["verdict"] == "no"
    assert changed["decision"]["binding"]["resource"] == "deck.hangar.mass"
    assert changed["decision"]["binding"]["demand"] >= mass
    assert changed["decision"]["rationale"] != first.json()["decision"]["rationale"]
    for value in [-1, "broken", None, "NaN", "Infinity"]:
        bad = command(client, state, op="admit", card="G4", value=value)
        assert bad.status_code == 422
        assert bad.json()["detail"]
    assert client.get(f"/api/sessions/{state['id']}").json() == changed
    invalid_float = client.post(
        f"/api/sessions/{state['id']}/commands",
        content='{"op":"admit","card":"G4","value":NaN}',
        headers={"Content-Type": "application/json"},
    )
    assert invalid_float.status_code == 422 and invalid_float.json()["detail"]
    restored = command(client, state, op="reset").json()
    assert restored["guests"]["G4"]["mass_t"] == load_guests()["G4"].mass_t
    assert restored["decision"] is None and restored["history"] == []
    again = command(client, state, op="admit", card="G4", value=load_guests()["G4"].mass_t).json()
    assert again["decision"]["binding"] == first.json()["decision"]["binding"]
    assert session(client)["decision"] is None


def test_controls_rerun_actual_admission(client, monkeypatch):
    from shipos.engine import admission

    real_decide, calls = admission.decide, []

    def observed(ship, guests, snapshot, card, priorities, envelope):
        calls.append((card.mass_t, snapshot.crew, priorities.id))
        return real_decide(ship, guests, snapshot, card, priorities, envelope)

    monkeypatch.setattr(admission, "decide", observed)
    state = session(client)
    command(client, state, op="admit", card="G4", value=0.9)
    command(client, state, op="priority", priority="sar_primary")
    changed = command(client, state, op="crew", crew=0).json()
    assert calls == [
        (0.9, 14, load_priorities("survey_primary").id),
        (0.9, 14, load_priorities("sar_primary").id),
        (0.9, 0, load_priorities("sar_primary").id),
    ]
    assert changed["snapshot"]["crew"] == 0 and changed["decision"] is not None


def test_websocket_real_command_and_http_broadcast(client):
    state = session(client)
    with client.websocket_connect(f"/ws/{state['id']}") as ws:
        assert ws.receive_json()["snapshot"]["crew"] == 14
        ws.send_json({"op": "admit", "card": "G4", "value": 20})
        update = ws.receive_json()
        assert update["decision"]["verdict"] == "no"
        ws.send_json({"op": "admit", "card": "G4", "value": -1})
        assert ws.receive_json()["error"]
        response = command(client, state, op="crew", crew=6)
        assert response.status_code == 200
        assert ws.receive_json()["snapshot"]["crew"] == 6
        ws.send_json({"op": "reset"})
        assert ws.receive_json()["guests"]["G4"]["mass_t"] == load_guests()["G4"].mass_t


def test_modes_unavailability_and_reset(client):
    state = session(client)
    for engine in ["arbitration", "shedding"]:
        result = command(client, state, op=engine).json()
        assert result["decision"] is None
        assert "unavailable" in result["notice"].lower()
        assert result["mode"] == "LIVE"
    before = client.get(f"/api/sessions/{state['id']}").json()
    assert command(client, state, op="scenario", scenario="B").status_code == 422
    after = client.get(f"/api/sessions/{state['id']}").json()
    assert before == after
    sample = command(client, state, op="mode", mode="SAMPLE").json()
    sample = command(client, sample, op="scenario", scenario="B").json()
    for engine in ["arbitration", "shedding"]:
        preview = command(client, state, op=engine).json()
        assert preview["mode"] == "SAMPLE"
        assert "SAMPLE" in preview["notice"]
        assert preview["decision"]["rationale"].startswith("SAMPLE")
    event = command(client, state, op="event", event="genset_trip").json()
    assert "unavailable" in event["notice"].lower()
    reset = command(client, state, op="reset").json()
    assert reset["mode"] == "LIVE" and reset["scenario"] == "A"
    assert reset["priority"]["id"] == load_priorities("survey_primary").id
    assert reset["snapshot"]["crew"] == 14


def test_gauges_layout_offline_and_bad_commands(client):
    state = session(client)
    assert len(state["gauges"]) == 8
    assert {g["cls"] for g in state["gauges"]} == {
        "power",
        "heat",
        "deck",
        "mast",
        "mass",
        "data",
        "compute",
        "time",
    }
    assert all(g["status"] in ["available", "unavailable"] for g in state["gauges"])
    assert next(g for g in state["gauges"] if g["cls"] == "time")["status"] == "unavailable"
    assert state["ship"]["slots"] and state["snapshot"]["guests"]["G2"]["placement"]
    for path in ["/", "/static/app.js", "/static/style.css"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "https://" not in response.text and "http://" not in response.text
    script = client.get("/static/app.js").text
    assert "innerHTML" not in script
    for cmd in [
        dict(op="crew", crew=7),
        dict(op="admit", card="missing", value=1),
        dict(op="priority", priority="../default"),
        dict(op="unknown"),
    ]:
        assert command(client, state, **cmd).status_code == 422
    assert client.get("/api/sessions/missing").status_code == 404
