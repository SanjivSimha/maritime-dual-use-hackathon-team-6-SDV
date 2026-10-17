"""Crew-evidence lifecycle: the helper, not the test, drops confirmations between episodes."""

import copy
import dataclasses
import json
import math
from pathlib import Path

import pytest

from shipos.contracts import DeckState, WorldSnapshot
from shipos.crew import (
    CrewEvidence,
    DeckEvidence,
    HumanConfirmation,
    LifecycleError,
    confirm,
    gate,
    observe,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "snapshot_scenario_b_min8.json"
BASE = WorldSnapshot.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
T0 = BASE.t_s
CLEAR, OCCUPIED = DeckState.CLEAR, DeckState.OCCUPIED


def snap(t: float, deck: DeckState, crew: int = 14) -> WorldSnapshot:
    ship = BASE.ship.model_copy(update={"deck": deck})
    return BASE.model_copy(update={"t_s": t, "ship": ship, "crew": crew})


def run(ship, steps):
    """Feed (snapshot, confirmation | None) pairs; return the state and each tick's gate."""
    state, gates = CrewEvidence(), []
    for s, confirmation in steps:
        state = observe(state, s)
        if confirmation is not None:
            state = confirm(state, confirmation)
        gates.append(gate(ship, state, s))
    return state, gates


def test_occupation_discards_confirmation_and_reopening_needs_a_new_one(ship):
    state, gates = run(
        ship,
        [
            (snap(T0, CLEAR), HumanConfirmation(T0)),
            (snap(T0 + 1, CLEAR), None),
            (snap(T0 + 2, OCCUPIED), None),
            (snap(T0 + 3, CLEAR), None),
            (snap(T0 + 4, CLEAR), HumanConfirmation(T0 + 4)),
        ],
    )
    assert [g.permitted for g in gates] == [True, True, False, False, True]
    assert gates[3].blockers == ("confirmation_pending",)
    assert gates[3].confirm == "human" and gates[3].confirmation == "pending"
    assert state.confirmation == HumanConfirmation(T0 + 4)


def test_old_confirmation_cannot_be_replayed_into_a_new_episode(ship):
    state, _ = run(
        ship,
        [(snap(T0, CLEAR), HumanConfirmation(T0)), (snap(T0 + 1, OCCUPIED), None)],
    )
    state = observe(state, snap(T0 + 2, CLEAR))
    with pytest.raises(LifecycleError, match="predates the deck becoming clear"):
        confirm(state, HumanConfirmation(T0))


def test_clear_deck_alone_never_confirms(ship):
    state, gates = run(ship, [(snap(T0 + t, CLEAR), None) for t in range(3)])
    assert state.confirmation is None
    assert all(not g.permitted and g.confirmation == "pending" for g in gates)


def test_cannot_confirm_an_occupied_deck_or_before_observing(ship):
    with pytest.raises(LifecycleError, match="observe a snapshot"):
        confirm(CrewEvidence(), HumanConfirmation(T0))
    with pytest.raises(LifecycleError, match="occupied"):
        confirm(observe(CrewEvidence(), snap(T0, OCCUPIED)), HumanConfirmation(T0))


@pytest.mark.parametrize("t", [math.nan, math.inf, -math.inf, T0 + 1])
def test_invalid_confirmation_leaves_state_untouched(ship, t):
    state = observe(CrewEvidence(), snap(T0, CLEAR))
    before = copy.deepcopy(state)
    with pytest.raises(LifecycleError):
        confirm(state, HumanConfirmation(t))
    assert state == before and state.confirmation is None


def test_crew_change_discards_confirmation(ship):
    state, _ = run(ship, [(snap(T0, CLEAR, crew=14), HumanConfirmation(T0))])
    state = observe(state, snap(T0 + 1, CLEAR, crew=6))
    assert state.confirmation is None
    assert gate(ship, state, snap(T0 + 1, CLEAR, crew=6)).blockers == ("confirmation_pending",)


def test_time_cannot_go_backwards_reset_is_a_fresh_state(ship):
    state, _ = run(ship, [(snap(T0, CLEAR), HumanConfirmation(T0))])
    with pytest.raises(LifecycleError, match="new CrewEvidence"):
        observe(state, snap(T0 - 1, CLEAR))
    fresh = observe(CrewEvidence(), snap(T0 - 1, CLEAR))  # reset run
    assert fresh.confirmation is None and fresh.evidence is None
    assert not gate(ship, fresh, snap(T0 - 1, CLEAR)).permitted


def test_sessions_are_independent(ship):
    a, _ = run(ship, [(snap(T0, CLEAR), HumanConfirmation(T0))])
    b, _ = run(ship, [(snap(T0, CLEAR), None)])
    assert gate(ship, a, snap(T0, CLEAR)).permitted
    assert not gate(ship, b, snap(T0, CLEAR)).permitted


@pytest.mark.parametrize(
    ("evidence", "blocker"),
    [
        (None, "no_deck_evidence"),
        (DeckEvidence(T0 - 1, deck_clear=True), "stale_deck_evidence"),
        (DeckEvidence(T0, deck_clear=False), "evidence_not_clear"),
    ],
)
def test_zero_crew_evidence_fails_closed(ship, evidence, blocker):
    s = snap(T0, CLEAR, crew=0)
    g = gate(ship, observe(CrewEvidence(), s, evidence), s)
    assert not g.permitted and g.confirm == "forbidden" and g.blockers == (blocker,)


def test_zero_crew_evidence_is_same_tick_only(ship):
    s0, s1 = snap(T0, CLEAR, crew=0), snap(T0 + 1, CLEAR, crew=0)
    state = observe(CrewEvidence(), s0, DeckEvidence(T0, deck_clear=True))
    assert gate(ship, state, s0).permitted and gate(ship, state, s0).confirm == "automatic"
    state = observe(state, s1)  # no new reading this tick
    assert state.evidence is None
    assert gate(ship, state, s1).blockers == ("no_deck_evidence",)


@pytest.mark.parametrize(
    "evidence",
    [
        DeckEvidence(math.nan, deck_clear=True),
        DeckEvidence(T0 + 1, deck_clear=True),
        DeckEvidence(T0, deck_clear=True, source="deck_camera"),
    ],
)
def test_invalid_evidence_is_rejected(ship, evidence):
    state = observe(CrewEvidence(), snap(T0 - 1, CLEAR, crew=0))
    before = copy.deepcopy(state)
    with pytest.raises(LifecycleError):
        observe(state, snap(T0, CLEAR, crew=0), evidence)
    assert state == before


@pytest.mark.parametrize("t", [math.nan, math.inf])
def test_nonfinite_snapshot_time_is_rejected(t):
    state = CrewEvidence()
    with pytest.raises(LifecycleError, match="finite"):
        observe(state, snap(t, CLEAR))
    assert state == CrewEvidence()


def test_gate_requires_the_observed_snapshot(ship):
    state = observe(CrewEvidence(), snap(T0, CLEAR))
    for other in (snap(T0 + 1, CLEAR), snap(T0, OCCUPIED), snap(T0, CLEAR, crew=6)):
        with pytest.raises(LifecycleError, match="observe snapshot"):
            gate(ship, state, other)


def test_deterministic_serialisable_and_copyable(ship):
    steps = [
        (snap(T0, CLEAR), HumanConfirmation(T0)),
        (snap(T0 + 1, OCCUPIED), None),
        (snap(T0 + 2, CLEAR), HumanConfirmation(T0 + 2)),
    ]
    first, second = run(ship, steps), run(ship, steps)
    assert first == second
    state = first[0]
    assert copy.deepcopy(state) == state
    as_dict = dataclasses.asdict(state)
    assert json.loads(json.dumps(as_dict)) == as_dict
    assert as_dict["confirmation"] == {"t_s": T0 + 2, "by": "deck officer"}
