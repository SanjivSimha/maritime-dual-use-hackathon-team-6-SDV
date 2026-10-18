"""Contracts + data: every input loads, every "$ref" resolves, fixtures match the contract types."""

import json
from pathlib import Path

import pytest
import yaml

from shipos.assumptions import A, load_registry
from shipos.contracts import Decision, GuestManifest, Range, SlotKind, WorldSnapshot
from shipos.data import load_envelope, load_guests, load_priorities, load_ship
from shipos.paths import DATA_DIR

FIXTURES = Path(__file__).parent / "fixtures"


def _refs(node):
    if isinstance(node, str) and node.startswith("$"):
        yield node[1:]
    elif isinstance(node, dict):
        for v in node.values():
            yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def test_every_ref_in_data_resolves():
    reg = load_registry(DATA_DIR / "assumptions")
    for path in DATA_DIR.rglob("*.yaml"):
        if "assumptions" in path.parts:
            continue
        for ref in _refs(yaml.safe_load(path.read_text())):
            assert ref in reg, f"{path.name}: unknown assumption ${ref}"


def test_every_assumption_has_a_reason():
    for a in load_registry(DATA_DIR / "assumptions").values():
        assert a.why.strip(), a.id


def test_unknown_assumption_raises():
    with pytest.raises(KeyError, match="unknown assumption"):
        A("does.not.exist")


def test_ship_loads_with_annex_values():
    ship = load_ship()
    assert ship.power.reserve_kw["cruise"] == {2: 200, 1: 100}
    assert ship.cooling.cap_lo_kw == 160 and ship.cooling.cap_hi_kw == 120
    assert [s.id for s in ship.slots if s.kind == SlotKind.MAST and s.occupied_by is None] == [
        "mast3"
    ]
    assert ship.mass.margin_t == 10 and ship.mass.stability_budget_t == 12


def test_five_guest_cards_load():
    guests = load_guests()
    assert {"G1", "G2", "G3", "G4", "G5"} <= set(guests)  # Annex B; added cards also load
    for g in guests.values():
        assert isinstance(g, GuestManifest)
        g.mode(g.initial_mode)  # initial mode exists
        for m in g.modes:
            if m.next_mode:
                g.mode(m.next_mode)
    assert guests["G2"].total_mass_t == pytest.approx(3.5)
    assert guests["G4"].total_mass_t == pytest.approx(1.04)
    assert guests["G5"].total_mass_t == pytest.approx(3.025)


def test_priority_files_give_different_weights():
    guests = load_guests()
    survey, sar = load_priorities("survey_primary"), load_priorities("sar_primary")
    assert survey.weight(guests["G3"]) > survey.weight(guests["G4"])
    assert sar.weight(guests["G4"]) > sar.weight(guests["G3"])
    assert sar.mission_critical(guests["G5"]) and not survey.mission_critical(guests["G5"])


def test_envelope_default():
    env = load_envelope()
    assert env.seawater_c_max == 30 and env.gensets_online == [2, 1]


def test_range_intersection():
    assert Range(lo=2.5, hi=3.5).intersect(Range(lo=5, hi=12)) is None
    assert Range(hi=5).intersect(Range(lo=4, hi=8)) == Range(lo=4, hi=5)


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("decision_*.json")))
def test_decision_fixtures_validate(name):
    Decision.model_validate(json.loads((FIXTURES / name).read_text()))


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("snapshot_*.json")))
def test_snapshot_fixtures_validate(name):
    WorldSnapshot.model_validate(json.loads((FIXTURES / name).read_text()))


def test_fixtures_exist():
    assert list(FIXTURES.glob("decision_*.json")) and list(FIXTURES.glob("snapshot_*.json"))
