"""Transition planner (A.6): timed ship-state requests, crew-scaled, planned on upper bounds."""

import pytest

from shipos.contracts import Course, DeckState, Emission, Env, Noise, ShipState
from shipos.statemachine import plan_transition

SURVEYING = ShipState(
    speed_kn=3,
    course=Course.STEADY,
    noise=Noise.QUIET,
    deck=DeckState.OCCUPIED,
    emission=Emission.NORMAL,
)
ENV = Env(seawater_c=24, sea_state=3, wind_kn=22)


def recovery(guests):
    return guests["G4"].mode("recovery").conditions


def test_recovery_full_crew(ship, guests):
    plan = plan_transition(
        ship, SURVEYING, recovery(guests), crew=14, env=ENV, hold_first=["G3"], guests=guests
    )
    assert plan.feasible
    steps = {s.variable: s for s in plan.steps}
    assert steps["hold:G3"].duration_s == 60 and steps["hold:G3"].start_s == 0
    assert steps["speed_kn"].start_s == 60  # no manoeuvre before the vehicle holds
    assert steps["speed_kn"].duration_s == 300
    assert steps["deck"].duration_s == 300 and steps["confirm:deck"].start_s == 300
    assert plan.ready_s == pytest.approx(360)


def test_recovery_reduced_crew_is_slower(ship, guests):
    plan = plan_transition(
        ship, SURVEYING, recovery(guests), crew=6, env=ENV, hold_first=["G3"], guests=guests
    )
    assert plan.ready_s == pytest.approx(720 + 180)


def test_uncrewed_cannot_clear_an_occupied_deck(ship, guests):
    plan = plan_transition(ship, SURVEYING, recovery(guests), crew=0, env=ENV, guests=guests)
    assert not plan.feasible and "deck" in plan.blocker


def test_uncrewed_with_clear_deck_and_automatic_check(ship, guests):
    clear = SURVEYING.model_copy(update={"deck": DeckState.CLEAR})
    plan = plan_transition(
        ship, clear, recovery(guests), crew=0, env=ENV, guests=guests, confirm="automatic"
    )
    assert plan.feasible and plan.ready_s == pytest.approx(300)


def test_uncrewed_human_confirmation_impossible(ship, guests):
    clear = SURVEYING.model_copy(update={"deck": DeckState.CLEAR})
    plan = plan_transition(ship, clear, recovery(guests), crew=0, env=ENV, guests=guests)
    assert not plan.feasible and "confirm" in plan.blocker


def test_wind_above_flight_deck_limit_blocks(ship, guests):
    windy = Env(seawater_c=24, sea_state=3, wind_kn=27)
    plan = plan_transition(ship, SURVEYING, recovery(guests), crew=14, env=windy, guests=guests)
    assert not plan.feasible and "wind" in plan.blocker


def test_return_to_survey_state(ship, guests):
    recovering = ShipState(
        speed_kn=5,
        course=Course.FREE,
        noise=Noise.MANOEUVRING,
        deck=DeckState.CLEAR,
        emission=Emission.NORMAL,
    )
    plan = plan_transition(
        ship, recovering, guests["G3"].mode("surveying").conditions, crew=14, env=ENV, guests=guests
    )
    assert plan.feasible
    steps = {s.variable: s.duration_s for s in plan.steps}
    assert steps == {"speed_kn": 300, "course": 180, "noise": 240}
    assert plan.ready_s == pytest.approx(300)  # A.6 variables settle concurrently


def test_leaving_silence_is_a_mission_decision(ship, guests):
    silent = SURVEYING.model_copy(update={"emission": Emission.SILENT})
    plan = plan_transition(
        ship, silent, guests["G5"].mode("on").conditions, crew=14, env=ENV, guests=guests
    )
    assert not plan.feasible and "silence" in plan.blocker


def test_no_change_needed(ship, guests):
    plan = plan_transition(
        ship, SURVEYING, guests["G3"].mode("surveying").conditions, crew=14, env=ENV, guests=guests
    )
    assert plan.feasible and plan.ready_s == 0 and plan.steps == ()
