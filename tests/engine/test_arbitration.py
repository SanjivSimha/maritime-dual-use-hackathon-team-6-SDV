"""D3 right of way (Scenario B minute 8): hard constraint first, then mission-weighted cost."""

import time

from shipos.contracts import ActionKind, Verdict
from shipos.engine import arbitration
from tests.engine.scenario import scenario_b


def decide(ship, guests, prio, snap=None):
    return arbitration.decide(ship, guests, snap or scenario_b(), prio, "G4", "recovery")


def test_conflict_is_on_ship_state(ship, guests, survey):
    d = decide(ship, guests, survey)
    assert set(d.binding.facts["conflict"].split(", ")) >= {"speed_kn", "course", "noise"}


def test_survey_primary_g3_pauses_and_leg_is_rerun(ship, guests, survey):
    d = decide(ship, guests, survey)
    assert d.verdict == Verdict.DECIDED and d.winner == "G4" and d.loser == "G3"
    kinds = {a.guest: a.kind for a in d.chosen.actions}
    assert kinds["G3"] == ActionKind.PAUSE and kinds["G4"] == ActionKind.PROCEED
    g3 = next(c for c in d.costs if c.guest == "G3")
    assert g3.amount == 23  # 8 min of leg lost + 15-min pause
    assert d.binding.resource == "time.landing_deadline" and d.binding.capacity == 12
    assert d.binding.demand > 12  # waiting for the end of the leg is off the table
    assert any("wait" in a.name and "deadline" in a.rejected_because for a in d.alternatives)
    assert d.state_requests[0].variable == "hold:G3"  # vehicle holds before the link breaks


def test_sar_primary_survey_suspended_and_charge_reserved(ship, guests, sar):
    d = decide(ship, guests, sar)
    assert d.winner == "G4"
    kinds = {a.guest: a.kind for a in d.chosen.actions}
    assert kinds["G3"] == ActionKind.SUSPEND
    reserve = [a for a in d.chosen.actions if a.kind == ActionKind.RESERVE]
    assert reserve and reserve[0].guest == "G4" and reserve[0].kw_recovered == 35


def test_two_priority_files_two_outcomes(ship, guests, survey, sar):
    a, b = decide(ship, guests, survey), decide(ship, guests, sar)
    assert a.chosen.name != b.chosen.name and a.rationale != b.rationale


def test_reduced_crew_cannot_ready_the_deck_in_time(ship, guests, survey):
    d = decide(ship, guests, survey, scenario_b(crew=6))
    assert d.winner == "G3" and d.loser == "G4"
    assert d.binding.resource == "time.ready" and d.binding.demand == 18
    risk = next(c for c in d.costs if c.guest == "G4")
    assert risk.unit == "drone-risk"


def test_healthy_battery_lets_the_drone_wait_under_survey(ship, guests, survey, sar):
    snap = scenario_b(battery=70)
    assert decide(ship, guests, survey, snap).winner == "G3"
    assert decide(ship, guests, sar, snap).winner == "G4"


def test_wind_above_limit_blocks_recovery(ship, guests, sar):
    d = decide(ship, guests, sar, scenario_b(wind=27))
    assert d.winner == "G3" and "wind" in d.rationale


def test_within_two_seconds(ship, guests, survey):
    t0 = time.perf_counter()
    decide(ship, guests, survey)
    assert time.perf_counter() - t0 < ship.timing.arbitration_s
