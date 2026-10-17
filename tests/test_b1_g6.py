"""G6 (own card): validates, every number traced to data/assumptions/g6.yaml, ramp honest."""

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from shipos.api import adapter
from shipos.api.server import create_app
from shipos.assumptions import registry
from shipos.contracts import Noise, SlotKind
from shipos.data import load_guest
from shipos.engine import admission
from shipos.engine.kernel import Kernel
from shipos.paths import DATA_DIR
from shipos.validation import validate_cards

CARD = DATA_DIR / "guests" / "G6.yaml"


def numbers(node):
    if isinstance(node, bool):
        return
    if isinstance(node, int | float):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from numbers(v)
    elif isinstance(node, list):
        for v in node:
            yield from numbers(v)


def test_g6_validates():
    assert validate_cards([CARD]) == []


def test_g6_numbers_all_come_from_its_registry():
    raw = yaml.safe_load(CARD.read_text(encoding="utf-8"))
    assert set(numbers(raw)) <= {0}  # only the "off" mode's 0 kW is written literally
    g6 = {a.id: a for a in registry().values() if a.file == "g6.yaml"}
    assert g6 and all(aid.startswith("guests.g6.") for aid in g6)
    for a in g6.values():
        assert a.unit and a.range and a.why.strip() and a.source.strip(), a.id
        assert a.range[0] <= a.value <= a.range[1], a.id


def test_g6_mounts_in_a_bay_and_uses_the_ramp_only_as_a_condition():
    g6 = load_guest(CARD)
    assert set(g6.allowed) <= {SlotKind.BAY1_HALF, SlotKind.BAY2}
    assert {m.name for m in g6.modes if m.conditions and m.conditions.uses_ramp} == {
        "deploying",
        "recovering",
    }
    listening = g6.mode("listening").conditions
    assert listening.noise == Noise.QUIET and listening.deck_machinery_allowed is False
    assert [(e.kind, e.target, e.target_mode) for e in g6.excludes] == [
        ("conditional", "G2", "winch")
    ]


def test_g6_conditions_and_conditional_winch_exclusion(ship, guests):
    g6 = guests["G6"]
    assert g6.installation.where == "port_only"
    for name in ("deploying", "recovering"):
        mode = g6.mode(name)
        assert mode.deck_machinery
        assert mode.conditions.uses_ramp and not mode.conditions.uses_flight_deck
        assert mode.conditions.sea_state_max is not None
    for name in ("deploying", "listening", "recovering"):
        speed = g6.mode(name).conditions.speed_kn
        assert speed.lo == registry()["guests.g6.tow_speed_lo_kn"].value
        assert speed.hi == registry()["guests.g6.tow_speed_hi_kn"].value
    kernel = Kernel(ship, guests)
    assert not kernel.compatible(("G6", "listening"), ("G2", "winch"))
    assert not kernel.compatible(("G2", "winch"), ("G6", "listening"))
    assert kernel.compatible(("G6", "listening"), ("G2", "profiling"))
    assert kernel.compatible(("G6", "standby"), ("G2", "winch"))


@pytest.mark.parametrize("scenario,execution", [("A", False), ("A", True), ("B", True)])
def test_g6_complete_import_actual_admission_export_replay(
    scenario, execution, monkeypatch, tmp_path
):
    """Use the original resolved card and real engine; admission never installs it."""
    raw = load_guest(CARD).model_dump(mode="json")
    real_decide, seen = admission.decide, []

    def observed(ship, manifests, snapshot, card, priorities, envelope):
        decision = real_decide(ship, manifests, snapshot, card, priorities, envelope)
        seen.append((card.model_dump(mode="json"), decision.model_dump(mode="json")))
        return decision

    monkeypatch.setattr(admission, "decide", observed)
    with TestClient(create_app()) as client:
        sid = client.post("/api/sessions").json()["id"]
        endpoint = f"/api/sessions/{sid}/commands"
        if scenario != "A":
            assert client.post(endpoint, json={"op": "scenario", "scenario": scenario}).is_success
        if execution:
            assert client.post(
                endpoint, json={"op": "execution", "enabled": True, "fresh_run": True}
            ).is_success
        before = client.get(f"/api/sessions/{sid}").json()
        response = client.post(endpoint, json={"op": "import_card", "card_json": json.dumps(raw)})
        assert response.is_success, response.text
        state = response.json()
        assert seen == [(raw, state["decision"])]
        assert state["decision"]["subject"] == "G6"
        assert state["decision"]["binding"] and state["decision"]["rationale"]
        assert state["proposed_cards"]["G6"] == raw
        assert "G6" not in state["snapshot"]["guests"]
        assert set(state["snapshot"]["guests"]) == set(before["snapshot"]["guests"])
        assert state["execution"]["enabled"] == execution
        saved = client.get(f"/api/sessions/{sid}/evidence").json()
        record = json.loads(saved["jsonl"].splitlines()[-1])["record"]
        assert record["injected_events"][0] == {
            "t_s": before["next_t_s"],
            "kind": "card_presented",
            "payload": {"card": raw},
        }
        assert record["decisions"] == [
            {k: v for k, v in state["decision"].items() if k != "latency_ms"}
        ]
        exported = tmp_path / "g6-evidence.json"
        exported.write_text(json.dumps(saved, allow_nan=False), encoding="utf-8")
        assert client.get(f"/api/sessions/{sid}/replay.jsonl").content == saved["jsonl"].encode()

        read_text = Path.read_text

        def no_card_file(path, *args, **kwargs):
            if path.name == "G6.yaml":
                raise AssertionError("Replay must reconstruct G6 from evidence")
            return read_text(path, *args, **kwargs)

        def no_catalogue(*args, **kwargs):
            raise AssertionError("Replay must not reload the catalogue")

        monkeypatch.setattr(Path, "read_text", no_card_file)
        monkeypatch.setattr(adapter, "load_guests", no_catalogue)
        replay = adapter.replay_evidence(json.loads(exported.read_text(encoding="utf-8")))
        assert replay["byte_identical"] and replay["final_hash"] == saved["final_hash"]
        assert replay["snapshot"] == state["snapshot"]
        assert seen == [(raw, state["decision"])] * 2


@pytest.mark.parametrize("winch_kw", [15, 16])
def test_g6_one_number_change_reaches_actual_power_checks(winch_kw):
    """Controlled JSON edits need not change the admission verdict to change its evidence."""
    raw = load_guest(CARD).model_dump(mode="json")
    for mode in raw["modes"]:
        if mode["name"] in ("deploying", "recovering"):
            mode["power_kw"] = winch_kw
    with TestClient(create_app()) as client:
        sid = client.post("/api/sessions").json()["id"]
        response = client.post(
            f"/api/sessions/{sid}/commands",
            json={"op": "import_card", "card_json": json.dumps(raw)},
        )
        assert response.is_success, response.text
        totals = [
            c for c in response.json()["decision"]["checks"] if c["resource"] == "power.total"
        ]
        assert totals and all(c["contributors"]["G6"] == winch_kw for c in totals)
