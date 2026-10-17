"""Rationale is generated from the binding constraint: numbers in, numbers out, no canned text."""

import pytest

from shipos.contracts import (
    Action,
    ActionKind,
    ConstraintResult,
    Cost,
    Decision,
    DecisionKind,
    Option,
    ResourceClass,
    Verdict,
)
from shipos.engine.rationale import fmt, render


def power(cap=100, demand=101.5, corner="slow, 30 °C, one genset down"):
    return ConstraintResult(
        cls=ResourceClass.POWER,
        resource="power.total",
        capacity=cap,
        demand=demand,
        unit="kW",
        corner=corner,
        contributors={"G4": 35, "G5": 45, "G2": 20, "G1": 1.5},
    )


def heat(cap=72, demand=86):
    return ConstraintResult(
        cls=ResourceClass.HEAT,
        resource="heat.loop",
        capacity=cap,
        demand=demand,
        unit="kW",
        corner="cruise, 30 °C, loop degraded",
        facts={"seawater_c": 30, "loop_degraded": "True"},
    )


def admission(verdict, binding, actions=(), gain=None, cost=0.0, **kw):
    chosen = Option(name="x", actions=list(actions), feasible=True, cost=cost) if actions else None
    costs = [Cost(guest="card", amount=gain, unit="capability", what="returned")] if gain else []
    return Decision(
        kind=DecisionKind.ADMISSION,
        subject="G4",
        verdict=verdict,
        binding=binding,
        chosen=chosen,
        costs=costs,
        **kw,
    )


def test_fmt():
    assert fmt(101.5) == "101.5" and fmt(100.0) == "100" and fmt(133.3333) == "133.3"
    assert fmt(0.0499) == "0.05"


def test_yes_if_names_binding_numbers_and_fix():
    d = admission(
        Verdict.YES_IF,
        power(),
        actions=[
            Action(
                guest="G2",
                kind=ActionKind.INTERLOCK,
                to_mode="winch",
                other="G4.charging",
                when="degraded",
                capability_lost=0.05,
            )
        ],
        holds_one_genset_down=False,
    )
    s = render(d)
    assert "101.5 kW" in s and "100 kW" in s and "1.5 kW short" in s
    assert "G2" in s and "winch" in s and "G4 charging" in s and "one genset down" in s


def test_refusal_on_cooling_reads_like_a_captain_sentence():
    s = render(admission(Verdict.NO, heat()))
    assert s.startswith("Refuse G4:")
    assert "30 °C" in s and "72 kW" in s and "86 kW" in s and "degraded" in s


def test_numbers_change_the_sentence():
    a = render(admission(Verdict.NO, heat(72, 86)))
    b = render(admission(Verdict.NO, heat(72, 90)))
    assert a != b and "90 kW" in b


def test_yes_reports_tightest_margin():
    s = render(admission(Verdict.YES, power(200, 126.5, "cruise, 30 °C, both gensets")))
    assert s.startswith("Admit G4:") and "73.5 kW" in s and "200 kW" in s


def test_mass_and_deck_bindings():
    m = ConstraintResult(
        cls=ResourceClass.MASS, resource="mass.margin", capacity=10, demand=12.07, unit="t"
    )
    assert "12.07 t" in render(admission(Verdict.NO, m)) and "10 t" in render(
        admission(Verdict.NO, m)
    )
    d = ConstraintResult(
        cls=ResourceClass.DECK,
        resource="deck.bay1",
        capacity=2,
        demand=3,
        unit="units",
        contributors={"G3": 2, "G5": 1},
    )
    s = render(admission(Verdict.NO, d))
    assert "bay1 holds 2 units but G3 and G5 need 3" in s


def test_arbitration_states_deadline_and_cost_to_loser():
    b = ConstraintResult(
        cls=ResourceClass.TIME,
        resource="time.landing_deadline",
        capacity=12,
        demand=32,
        unit="min",
        facts={"guest": "G4", "waiting_for": "end of G3 survey_leg"},
    )
    d = Decision(
        kind=DecisionKind.ARBITRATION,
        subject="G4 vs G3",
        verdict=Verdict.DECIDED,
        winner="G4",
        loser="G3",
        binding=b,
        chosen=Option(
            name="pause",
            feasible=True,
            actions=[
                Action(
                    guest="G3", kind=ActionKind.PAUSE, to_mode="holding", facts={"window_min": 15}
                ),
                Action(guest="G4", kind=ActionKind.PROCEED, to_mode="recovery"),
            ],
        ),
        costs=[
            Cost(guest="G3", amount=23, unit="survey-min", what="leg re-run after a 15-min pause")
        ],
    )
    s = render(d)
    assert "G4" in s and "12 min" in s and "32 min" in s and "23 survey-min" in s and "G3" in s


def test_shedding_lists_steps_in_order():
    d = Decision(
        kind=DecisionKind.SHEDDING,
        subject="genset_trip",
        verdict=Verdict.SHED,
        binding=power(100, 106.5, "one genset down"),
        chosen=Option(
            name="s",
            feasible=True,
            actions=[
                Action(
                    guest="G5",
                    kind=ActionKind.THROTTLE,
                    to_mode="low_power",
                    kw_recovered=37,
                    t_offset_s=1,
                ),
                Action(guest="G1", kind=ActionKind.SHED, kw_recovered=1.5, t_offset_s=3),
            ],
        ),
        costs=[Cost(guest="G5", amount=70, unit="% capability", what="connectivity")],
    )
    s = render(d)
    assert s.index("G5") < s.index("G1") and "106.5 kW" in s and "37 kW" in s


def test_no_action_shedding_still_explains():
    d = Decision(
        kind=DecisionKind.SHEDDING,
        subject="genset_trip",
        verdict=Verdict.NO_ACTION,
        binding=power(100, 82.5, "one genset down"),
    )
    s = render(d)
    assert "82.5 kW" in s and "17.5 kW" in s


@pytest.mark.parametrize("verdict", [Verdict.YES, Verdict.NO, Verdict.YES_IF])
def test_every_admission_sentence_cites_its_binding(verdict):
    b = power(100, 101.5)
    s = render(admission(verdict, b))
    assert fmt(b.capacity) in s and fmt(b.demand) in s
