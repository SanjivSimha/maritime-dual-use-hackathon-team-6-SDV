"""D5 crew policy. Expected times come from A.6 (ship.transitions) and the G4 card, never from
the policy's own output."""

import copy
import json
from pathlib import Path

import pytest

from shipos.contracts import DeckState, GuestRuntime, WorldSnapshot
from shipos.crew import (
    BatteryForecast,
    DeckEvidence,
    HumanConfirmation,
    early_clearance,
    machinery_lock,
    recovery_gate,
)
from shipos.statemachine import plan_transition

FIXTURE = Path(__file__).parents[1] / "fixtures" / "snapshot_scenario_b_min8.json"
BASE = WorldSnapshot.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
NOW = BASE.t_s


def snap(crew: int, deck: DeckState = DeckState.OCCUPIED, **modes: str) -> WorldSnapshot:
    guests = dict(BASE.guests)
    for gid, mode in modes.items():
        template = guests.get(gid, guests["G1"])
        guests[gid] = template.model_copy(update={"id": gid, "mode": mode})
    ship = BASE.ship.model_copy(update={"deck": deck})
    return BASE.model_copy(update={"crew": crew, "ship": ship, "guests": guests})


def hi(ranges, crew):
    return ranges[crew].hi


# --------------------------------------------------------------------------- 14 crew


def test_14_occupied_without_confirmation(ship):
    gate = recovery_gate(ship, snap(14))
    T = ship.transitions
    assert not gate.permitted
    assert gate.confirm == "human" and gate.confirmation == "pending"
    assert gate.blockers == ("deck_occupied", "confirmation_pending")
    req = {r.variable: r for r in gate.requests}
    assert req["deck"].duration_s == hi(T.deck_clear_s, 14)
    assert req["confirm:deck"].start_s == hi(T.deck_clear_s, 14)
    assert req["confirm:deck"].duration_s == hi(T.confirm_s, 14)


def test_14_confirmation_does_not_clear_an_occupied_deck(ship):
    gate = recovery_gate(ship, snap(14), confirmation=HumanConfirmation(t_s=NOW))
    assert not gate.permitted and gate.confirmation == "pending"
    assert "deck_occupied" in gate.blockers


def test_14_clear_deck_still_needs_confirmation(ship):
    gate = recovery_gate(ship, snap(14, DeckState.CLEAR))
    assert not gate.permitted and gate.blockers == ("confirmation_pending",)
    assert [r.variable for r in gate.requests] == ["confirm:deck"]


def test_14_clear_and_confirmed(ship):
    gate = recovery_gate(ship, snap(14, DeckState.CLEAR), confirmation=HumanConfirmation(NOW - 5))
    assert gate.permitted and gate.confirmation == "received" and gate.confirm == "human"
    assert gate.blockers == () and gate.requests == ()


def test_confirmation_from_the_future_is_ignored(ship):
    gate = recovery_gate(ship, snap(14, DeckState.CLEAR), confirmation=HumanConfirmation(NOW + 1))
    assert not gate.permitted and gate.confirmation == "pending"


# --------------------------------------------------------------------------- 6 crew


def lead_6(ship):
    T = ship.transitions
    return hi(T.deck_clear_s, 6) + hi(T.confirm_s, 6)  # clear, then confirm (A.6)


def approach(guests):
    return guests["G4"].activity("recovery").duration_min * 60


def test_6_forecast_far_off_waits(ship, guests):
    slack = 100
    land_by = NOW + approach(guests) + lead_6(ship) + slack
    advice = early_clearance(ship, snap(6), guests["G4"], BatteryForecast(NOW, land_by))
    assert not advice.request and advice.start_by_s == pytest.approx(NOW + slack)
    assert advice.lead_s == pytest.approx(lead_6(ship))


def test_6_forecast_requests_early_clearance(ship, guests):
    land_by = NOW + approach(guests) + lead_6(ship)
    advice = early_clearance(ship, snap(6), guests["G4"], BatteryForecast(NOW - 30, land_by))
    assert advice.request and not advice.late
    req = {r.variable: r for r in advice.requests}
    assert req["deck"].duration_s == hi(ship.transitions.deck_clear_s, 6)
    assert req["confirm:deck"].duration_s == hi(ship.transitions.confirm_s, 6)
    # The request is not readiness: the deck is still occupied and unconfirmed.
    assert not recovery_gate(ship, snap(6)).permitted


def test_6_late_forecast_requests_but_flags_late(ship, guests):
    land_by = NOW + approach(guests)  # no time left for the A.6 lead
    advice = early_clearance(ship, snap(6), guests["G4"], BatteryForecast(NOW, land_by))
    assert advice.request and advice.late
    assert not recovery_gate(ship, snap(6)).permitted


@pytest.mark.parametrize("forecast", [None, BatteryForecast(issued_s=NOW + 60, land_by_s=NOW)])
def test_6_missing_forecast_requests_nothing(ship, guests, forecast):
    advice = early_clearance(ship, snap(6), guests["G4"], forecast)
    assert not advice.request and advice.blockers == ("no_forecast",)
    assert not recovery_gate(ship, snap(6)).permitted


def test_6_confirmation_still_required(ship):
    gate = recovery_gate(ship, snap(6, DeckState.CLEAR))
    assert gate.confirm == "human" and gate.blockers == ("confirmation_pending",)
    assert gate.requests[0].duration_s == hi(ship.transitions.confirm_s, 6)
    assert recovery_gate(ship, snap(6, DeckState.CLEAR), HumanConfirmation(NOW)).permitted


# --------------------------------------------------------------------------- 0 crew


def test_0_occupied_deck_cannot_be_cleared(ship, guests):
    gate = recovery_gate(ship, snap(0), evidence=DeckEvidence(NOW, deck_clear=True))
    assert not gate.permitted and gate.confirm == "forbidden"
    assert gate.blockers == ("no_crew_to_clear_deck",) and gate.confirmation == "unavailable"
    forecast = BatteryForecast(NOW, NOW)
    assert not early_clearance(ship, snap(0), guests["G4"], forecast).request


@pytest.mark.parametrize(
    ("evidence", "blocker"),
    [
        (None, "no_deck_evidence"),
        (DeckEvidence(NOW - 1, deck_clear=True), "stale_deck_evidence"),
        (DeckEvidence(NOW, deck_clear=False), "evidence_not_clear"),
    ],
)
def test_0_clear_deck_without_verification_fails_closed(ship, evidence, blocker):
    gate = recovery_gate(ship, snap(0, DeckState.CLEAR), evidence=evidence)
    assert not gate.permitted and gate.confirm == "forbidden" and gate.blockers == (blocker,)


def test_0_clear_deck_with_verification_is_automatic(ship):
    evidence = DeckEvidence(NOW, deck_clear=True)
    gate = recovery_gate(ship, snap(0, DeckState.CLEAR), evidence=evidence)
    assert gate.permitted and gate.confirm == "automatic" and gate.confirmation == "automatic"
    assert evidence.source == "simulated_camera"


def test_human_confirmation_does_not_stand_in_for_evidence_at_0(ship):
    gate = recovery_gate(ship, snap(0, DeckState.CLEAR), confirmation=HumanConfirmation(NOW))
    assert not gate.permitted and gate.blockers == ("no_deck_evidence",)


@pytest.mark.parametrize(
    ("crew", "deck", "evidence", "feasible"),
    [
        (0, DeckState.CLEAR, DeckEvidence(NOW, True), True),
        (0, DeckState.CLEAR, None, False),
        (0, DeckState.OCCUPIED, DeckEvidence(NOW, True), False),
        (14, DeckState.OCCUPIED, None, True),
    ],
)
def test_planner_accepts_the_gate_confirm_value(ship, guests, crew, deck, evidence, feasible):
    s = snap(crew, deck)
    gate = recovery_gate(ship, s, evidence=evidence)
    target = guests["G4"].mode("recovery").conditions
    plan = plan_transition(ship, s.ship, target, crew, confirm=gate.confirm)
    assert plan.feasible is feasible


# --------------------------------------------------------------------------- winch lock


def test_winch_locked_while_g3_surveys(guests):
    lock = machinery_lock(guests, snap(14, G2="profiling", G3="surveying"), "G2", "winch")
    assert lock.locked and lock.by == ("G3.surveying",)


def test_hangar_doors_locked_by_the_same_condition(guests):
    lock = machinery_lock(guests, snap(14, G3="surveying"), "G4", "recovery")
    assert lock.locked and lock.by == ("G3.surveying",)


@pytest.mark.parametrize(("g3_mode", "mode"), [("standby", "winch"), ("surveying", "profiling")])
def test_winch_allowed_without_restriction(guests, g3_mode, mode):
    lock = machinery_lock(guests, snap(14, G2="profiling", G3=g3_mode), "G2", mode)
    assert not lock.locked and lock.by == ()


# --------------------------------------------------------------------------- purity


def test_deterministic_and_does_not_mutate(ship, guests):
    s = snap(6, G2="profiling")
    before = (s.model_dump(), ship.model_dump(), copy.deepcopy(guests))
    forecast = BatteryForecast(NOW, NOW + 600)
    first = (
        recovery_gate(ship, s, HumanConfirmation(NOW)),
        early_clearance(ship, s, guests["G4"], forecast),
        machinery_lock(guests, s, "G2", "winch"),
    )
    second = (
        recovery_gate(ship, s, HumanConfirmation(NOW)),
        early_clearance(ship, s, guests["G4"], forecast),
        machinery_lock(guests, s, "G2", "winch"),
    )
    assert first == second
    assert (s.model_dump(), ship.model_dump(), guests) == before
    assert isinstance(s.guests["G2"], GuestRuntime)
