"""D1 admission against the mission envelope (Scenario A and A′), PLAN §1 expectations."""

import time

import pytest

from shipos.contracts import ActionKind, Situation, Verdict
from shipos.data import load_envelope
from shipos.engine import admission
from tests.engine.scenario import scenario_a


def admit(ship, guests, prio, card, snap=None, envelope=None):
    return admission.decide(
        ship, guests, snap or scenario_a(), guests[card], prio, envelope or load_envelope()
    )


def test_g4_yes_if_interlock_with_one_genset_down(ship, guests, survey):
    d = admit(ship, guests, survey, "G4")
    assert d.verdict == Verdict.YES_IF
    assert d.binding.resource == "power.total" and d.binding.demand == pytest.approx(101.5)
    assert d.binding.capacity == 100 and "one genset down" in d.binding.corner
    (fix,) = d.chosen.actions
    assert fix.kind == ActionKind.INTERLOCK and fix.guest == "G2" and fix.other == "G4.charging"
    assert fix.when == "degraded"
    assert d.holds_one_genset_down is False
    assert d.placement["G4"].slots["body"] == "hangar"
    assert "101.5 kW" in d.rationale and "100 kW" in d.rationale and "G2" in d.rationale


def test_g4_ranked_displacement_list(ship, guests, survey):
    d = admit(ship, guests, survey, "G4")
    ranked = [d.chosen] + d.alternatives
    costs = [o.cost + o.cost_contingent for o in ranked if o.feasible]
    assert costs == sorted(costs) and len(costs) >= 2
    kinds = {a.kind for o in d.alternatives for a in o.actions}
    assert ActionKind.THROTTLE in kinds or ActionKind.SHED in kinds


def test_g4_contingency_degraded_loop(ship, guests, survey):
    d = admit(ship, guests, survey, "G4")
    assert d.contingency, "loop degraded at 30 °C must come with a pre-computed shed list"
    assert all(a.when == "degraded" for a in d.contingency)


def test_g3_yes_if_displace_g5_under_survey(ship, guests, survey):
    d = admit(ship, guests, survey, "G3")
    assert d.verdict == Verdict.YES_IF
    assert d.binding.resource == "deck.bay1"
    removed = [a.guest for a in d.chosen.actions if a.kind == ActionKind.REMOVE]
    assert removed == ["G5"]
    assert d.placement["G3"].slots["body"] == "bay1"
    assert "G5" not in d.placement
    alt_removed = [
        [a.guest for a in o.actions if a.kind == ActionKind.REMOVE] for o in d.alternatives
    ]
    assert ["G2"] in alt_removed  # ranked list includes the costlier displacement


def test_g3_refused_under_sar_because_fix_costs_more_than_it_returns(ship, guests, sar):
    d = admit(ship, guests, sar, "G3")
    assert d.verdict == Verdict.NO
    assert d.chosen is not None and d.chosen.cost >= sar.weight(guests["G3"])
    assert "more than" in d.rationale


def test_jury_changes_one_number(ship, guests, survey):
    # G3 lighter and G5 displaced still binding on Bay 1, not on mass
    light = guests["G3"].model_copy(update={"mass_t": 3.0})
    d = admission.decide(
        ship, {**guests, "G3": light}, scenario_a(), light, survey, load_envelope()
    )
    assert d.verdict == Verdict.YES_IF and d.binding.resource == "deck.bay1"
    # G4 charging at 30 kW: everything fits even with one genset down
    g4 = guests["G4"]
    modes = [m.model_copy(update={"power_kw": 30}) if m.name == "charging" else m for m in g4.modes]
    cheap = g4.model_copy(update={"modes": modes})
    d = admission.decide(
        ship, {**guests, "G4": cheap}, scenario_a(), cheap, survey, load_envelope()
    )
    assert d.verdict == Verdict.YES and d.holds_one_genset_down is True


def test_silence_expected_refuses_g5(ship, guests, survey):
    env = load_envelope().model_copy(update={"silence_expected": True})
    snap = scenario_a(installed={})
    d = admission.decide(ship, guests, snap, guests["G5"], survey, env)
    assert d.verdict == Verdict.NO and d.binding.resource == "relation.excludes.G5"


def test_a_prime_port_only_card_at_sea(ship, guests, survey):
    snap = scenario_a(situation=Situation.AT_SEA, speed=6)
    d = admit(ship, guests, survey, "G4", snap)
    assert d.verdict == Verdict.NOT_NOW
    assert d.binding.resource == "install.port_only"
    assert "14" in d.next_window and "port" in d.next_window
    assert d.rationale.startswith("Not now:")


def test_a_prime_crew_zero_proposes_logical_reconfiguration(ship, guests, survey):
    snap = scenario_a(situation=Situation.AT_SEA, crew=0, speed=6)
    d = admit(ship, guests, survey, "G4", snap)
    assert d.verdict == Verdict.NOT_NOW
    assert d.chosen and any(a.kind == ActionKind.RETASK for a in d.chosen.actions)


def test_admission_within_30_s_and_deterministic(ship, guests, survey):
    t0 = time.perf_counter()
    a = admit(ship, guests, survey, "G3")
    b = admit(ship, guests, survey, "G3")
    assert time.perf_counter() - t0 < ship.timing.admission_s
    assert a == b
