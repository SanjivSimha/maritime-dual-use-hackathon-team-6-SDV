"""1 Hz gauges: all eight classes present with capacity, committed and degraded capacity."""

from pathlib import Path

from shipos.contracts import ResourceClass, WorldSnapshot
from shipos.physics import budget

FIX = Path(__file__).parents[1] / "fixtures"


def test_eight_gauges_for_scenario_b(ship, guests):
    snap = WorldSnapshot.model_validate_json((FIX / "snapshot_scenario_b_min8.json").read_text())
    gauges = budget.gauges(ship, guests, snap)
    assert {g.cls for g in gauges} == set(ResourceClass)
    power = next(g for g in gauges if g.resource == "power.total")
    assert power.capacity == 200 and power.demand == 1.5 + 1 + 2 + 45
    assert power.facts["degraded_capacity"] == 100
    heat = next(g for g in gauges if g.resource == "heat.loop")
    assert round(heat.capacity, 1) == 133.3 and heat.facts["degraded_capacity"] == 80
