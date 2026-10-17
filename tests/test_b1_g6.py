"""G6 (own card): validates, every number traced to data/assumptions/g6.yaml, ramp honest."""

import yaml

from shipos.assumptions import registry
from shipos.contracts import Noise, SlotKind
from shipos.data import load_guest
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
