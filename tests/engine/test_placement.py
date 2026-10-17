"""D2 placement: maximise remaining margin, keep future guests least constrained."""

import time

from shipos.contracts import Line, Placement, Verdict
from shipos.engine import placement


def run(ship, guests, prio, env, present, fixed=None, movable=frozenset()):
    return placement.decide(ship, guests, present, fixed or {}, prio, env, movable=movable)


def test_initial_loadout_survey(ship, guests, survey, envelope):
    d = run(ship, guests, survey, envelope, ["G1", "G2", "G5"])
    assert d.verdict == Verdict.YES
    p = d.placement
    assert p["G1"].slots["body"] == "mast3"
    assert p["G2"].slots["body"] == "bay2"  # survey: G2 (high) gets the preferred bay
    assert p["G5"].slots["body"] in ("bay1a", "bay1b")
    assert p["G5"].slots["antenna"] == "hangar_roof"  # mast3 is G1's
    assert d.alternatives and all(a.rejected_because for a in d.alternatives)
    tolerated = {a.guest for a in d.chosen.actions if a.facts.get("preferred") == "no"}
    assert "G5" in tolerated


def test_priority_file_changes_who_gets_bay2(ship, guests, sar, envelope):
    d = run(ship, guests, sar, envelope, ["G1", "G2", "G5"])
    assert d.placement["G5"].slots["body"] == "bay2"  # rescue: G5 (high) outranks G2 (medium)
    assert d.placement["G2"].slots["body"] in ("bay1a", "bay1b")


def test_antenna_keeps_the_mast_free_when_it_can(ship, guests, survey, envelope):
    d = run(ship, guests, survey, envelope, ["G2", "G5"])
    assert d.placement["G5"].slots["antenna"] == "hangar_roof"
    assert any("mast3" in a.name for a in d.alternatives)


def test_card_into_fixed_loadout(ship, guests, survey, envelope):
    fixed = {
        "G1": Placement(slots={"body": "mast3"}, line=Line.STBD),
        "G2": Placement(slots={"body": "bay2"}, line=Line.STBD),
        "G5": Placement(slots={"body": "bay1a", "antenna": "hangar_roof"}, line=Line.PORT),
    }
    d = run(ship, guests, survey, envelope, ["G1", "G2", "G5", "G4"], fixed)
    assert d.verdict == Verdict.YES and d.placement["G4"].slots["body"] == "hangar"
    for gid, p in fixed.items():
        assert d.placement[gid] == p


def test_g3_cannot_fit_beside_g2_and_g5(ship, guests, survey, envelope):
    fixed = {
        "G2": Placement(slots={"body": "bay2"}, line=Line.STBD),
        "G5": Placement(slots={"body": "bay1a", "antenna": "hangar_roof"}, line=Line.PORT),
    }
    d = run(ship, guests, survey, envelope, ["G2", "G5", "G3"], fixed, movable=frozenset(fixed))
    assert d.verdict == Verdict.NO
    assert d.binding.resource in ("deck.bay1", "deck.bay2")


def test_lines_balanced(ship, guests, survey, envelope):
    d = run(ship, guests, survey, envelope, ["G1", "G3", "G4", "G5"])
    lines = {p.line for p in d.placement.values()}
    assert lines == {Line.PORT, Line.STBD}


def test_deterministic_and_fast(ship, guests, survey, envelope):
    t0 = time.perf_counter()
    a = run(ship, guests, survey, envelope, ["G1", "G2", "G4", "G5"])
    b = run(ship, guests, survey, envelope, ["G1", "G2", "G4", "G5"])
    assert a.placement == b.placement and a.rationale == b.rationale
    assert time.perf_counter() - t0 < ship.timing.admission_s
    assert a.rationale.startswith("Place")
