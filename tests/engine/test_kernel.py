"""Feasibility kernel: worst coincident demand, all classes, every envelope corner (PLAN §2)."""

import pytest

from shipos.contracts import Corner, Line, Placement, SpeedBand
from shipos.engine.kernel import Config, GuestConfig, Kernel, binding, corners


def full(guests, ids, placements=None):
    placements = placements or {}
    return Config(
        guests=tuple(
            GuestConfig(
                id=i, modes=tuple(m.name for m in guests[i].modes), placement=placements.get(i)
            )
            for i in ids
        )
    )


def P(line=Line.PORT, **slots):
    return Placement(slots=slots, line=line)


A_PLACED = {
    "G1": P(Line.STBD, body="mast3"),
    "G2": P(Line.STBD, body="bay2"),
    "G5": P(Line.PORT, body="bay1a", antenna="hangar_roof"),
    "G4": P(Line.PORT, body="hangar"),
}
DEGRADED = Corner(speed_band=SpeedBand.SLOW, seawater_c=30, gensets_online=1)
NOMINAL = Corner(speed_band=SpeedBand.CRUISE, seawater_c=30, gensets_online=2)


def test_default_envelope_has_four_corners(ship, envelope):
    cs = corners(ship, envelope)
    assert len(cs) == 4 and {c.seawater_c for c in cs} == {30}
    assert {(c.speed_band, c.gensets_online) for c in cs} == {
        (SpeedBand.SLOW, 2),
        (SpeedBand.SLOW, 1),
        (SpeedBand.CRUISE, 2),
        (SpeedBand.CRUISE, 1),
    }


def test_worst_coincident_all_five(ship, guests):
    k = Kernel(ship, guests)
    kw, combo = k.worst(full(guests, ["G1", "G2", "G3", "G4", "G5"]), "power")
    assert kw == pytest.approx(126.5)
    assert combo == {"G1": "on", "G2": "winch", "G3": "charging", "G4": "charging", "G5": "on"}


def test_interlock_lowers_worst_case(ship, guests):
    k = Kernel(ship, guests)
    cfg = full(guests, ["G1", "G2", "G3", "G4", "G5"])
    cfg = Config(guests=cfg.guests, interlocks=frozenset({("G2.winch", "G4.charging")}))
    kw, combo = k.worst(cfg, "power")
    assert kw == pytest.approx(112.5) and combo["G2"] == "profiling"


def test_conditions_prevent_coincidence(ship, guests):
    k = Kernel(ship, guests)
    assert not k.compatible(("G3", "surveying"), ("G4", "recovery"))
    assert not k.compatible(("G2", "winch"), ("G3", "surveying"))  # exclusion + deck machinery
    assert k.compatible(("G3", "charging"), ("G4", "charging"))


def test_scenario_a_g4_degraded_power_binds(ship, guests):
    k = Kernel(ship, guests)
    res = k.evaluate(full(guests, ["G1", "G2", "G5", "G4"], A_PLACED), DEGRADED)
    b = binding(res)
    assert b.resource == "power.total" and b.capacity == 100 and b.demand == pytest.approx(101.5)
    assert b.contributors == {"G1": 1.5, "G2": 20, "G5": 45, "G4": 35}
    heat = next(r for r in res if r.resource == "heat.loop")
    assert heat.ok and heat.demand == pytest.approx(100) and heat.capacity == pytest.approx(120)


def test_scenario_a_g4_degraded_loop_heat_binds(ship, guests):
    k = Kernel(ship, guests)
    corner = Corner(
        speed_band=SpeedBand.CRUISE, seawater_c=30, gensets_online=2, cooling_degraded=True
    )
    b = binding(k.evaluate(full(guests, ["G1", "G2", "G5", "G4"], A_PLACED), corner))
    assert b.resource == "heat.loop" and b.capacity == pytest.approx(72)


def test_nominal_corner_passes_and_reports_tightest(ship, guests):
    k = Kernel(ship, guests)
    res = k.evaluate(full(guests, ["G1", "G2", "G5", "G4"], A_PLACED), NOMINAL)
    assert all(r.ok for r in res if r.hard)
    assert binding(res).ok  # tightest passing constraint


def test_scenario_b_loadout_feasible(ship, guests):
    placed = {
        "G1": P(Line.STBD, body="mast3"),
        "G3": P(Line.PORT, body="bay1"),
        "G4": P(Line.PORT, body="hangar"),
        "G5": P(Line.STBD, body="bay2", antenna="hangar_roof"),
    }
    res = Kernel(ship, guests).evaluate(full(guests, list(placed), placed), NOMINAL)
    assert all(r.ok for r in res if r.hard)
    assert next(r for r in res if r.resource == "mass.margin").demand == pytest.approx(9.61)


def test_degraded_lines_merge_onto_surviving_line(ship, guests):
    res = Kernel(ship, guests).evaluate(full(guests, ["G1", "G2", "G5", "G4"], A_PLACED), DEGRADED)
    lines = {r.resource: r for r in res if r.resource.startswith("power.line")}
    assert set(lines) == {"power.line.port"}
    assert lines["power.line.port"].demand == pytest.approx(101.5)


def test_absolute_exclusion_in_silence(ship, guests):
    res = Kernel(ship, guests).evaluate(full(guests, ["G5"]), NOMINAL, silence_expected=True)
    assert any(r.resource == "relation.excludes.G5" and not r.ok for r in res)


def test_unplaced_config_checks_loads_only(ship, guests):
    res = Kernel(ship, guests).evaluate(full(guests, ["G2", "G3", "G4", "G5"]), NOMINAL)
    assert not any(r.resource.startswith("deck.") for r in res)
    assert next(r for r in res if r.resource == "mass.margin").demand == pytest.approx(13.065)
