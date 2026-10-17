"""Mass, stability and positions (PLAN §1 loadout table)."""

import pytest

from shipos.contracts import Line, Placement
from shipos.physics import mass, positions


def P(**slots):
    return Placement(slots=slots, line=Line.PORT)


@pytest.mark.parametrize(
    ("ids", "tonnes"),
    [
        (["G1", "G2", "G5"], 6.57),
        (["G2", "G3", "G4"], 10.04),
        (["G1", "G3", "G4", "G5"], 9.61),
        (["G2", "G3", "G4", "G5"], 13.065),
        (["G1", "G2", "G4", "G5"], 7.61),
    ],
)
def test_raw_mass(guests, ids, tonnes):
    assert mass.raw_t([guests[i] for i in ids]) == pytest.approx(tonnes, abs=1e-3)


def test_stability_b_loadout(ship, guests):
    placed = {
        "G1": P(body="mast3"),
        "G3": P(body="bay1"),
        "G4": P(body="hangar"),
        "G5": P(body="bay2", antenna="hangar_roof"),
    }
    assert mass.stability_t(ship, guests, placed) == pytest.approx(9.913, abs=1e-3)
    placed["G5"] = P(body="bay2", antenna="mast3")
    assert mass.stability_t(ship, guests, placed) == pytest.approx(9.958, abs=1e-3)


def test_bay_conflict_g2_g3_g5(ship, guests):
    placed = {
        "G3": P(body="bay1"),
        "G5": P(body="bay1a", antenna="hangar_roof"),
        "G2": P(body="bay2"),
    }
    results = {r.resource: r for r in positions.check(ship, guests, placed)}
    assert not results["deck.bay1"].ok
    assert results["deck.bay1"].demand == 3 and results["deck.bay1"].capacity == 2


def test_mast_rules(ship, guests):
    ok = {r.resource: r for r in positions.check(ship, guests, {"G1": P(body="mast3")})}
    assert all(r.ok for r in ok.values())
    taken = positions.check(ship, guests, {"G1": P(body="mast1")})
    assert any(not r.ok and r.resource == "mast.mast1" for r in taken)


def test_g1_loses_30_percent_next_to_radar(ship, guests):
    assert positions.capability_factor(ship, guests["G1"], P(body="mast3")) == pytest.approx(0.7)
    assert positions.capability_factor(ship, guests["G5"], P(body="bay2")) == 1.0


def test_hangar_roof_antenna_clearance(ship, guests):
    res = positions.check(ship, guests, {"G5": P(body="bay2", antenna="hangar_roof")})
    assert all(r.ok for r in res)


def test_slot_kind_must_be_allowed(ship, guests):
    res = positions.check(ship, guests, {"G3": P(body="bay2")})
    assert any(not r.ok and r.resource == "deck.allowed.G3" for r in res)


def test_unplaced_part_is_flagged(ship, guests):
    res = positions.check(ship, guests, {"G5": P(body="bay2")})
    assert any(not r.ok and r.resource == "deck.unplaced.G5.antenna" for r in res)
    # stability charges the unplaced antenna at its worst allowed slot (mast, × 3)
    assert mass.stability_t(ship, guests, {"G5": P(body="bay2")}) == pytest.approx(3.075)
