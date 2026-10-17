"""Caller-side crew-evidence lifecycle for `recovery_gate`. See docs/CREW_LIFECYCLE.md.

`recovery_gate` is stateless and sees one snapshot, so it cannot know that the deck was occupied
since a confirmation was given. `CrewEvidence` is the small immutable state an application keeps
per session: it observes every snapshot in order, discards the confirmation whenever the deck is
occupied, and holds only explicitly supplied operator confirmations and simulated deck evidence.
Every function returns a new state or raises `LifecycleError`, leaving the prior state as it was.
No clock, no globals, no actuation: it never clears a deck or changes a guest mode.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from shipos.contracts import DeckState, ShipSpec, WorldSnapshot
from shipos.crew.policy import DeckEvidence, HumanConfirmation, RecoveryGate, recovery_gate

SIMULATED = "simulated"  # DeckEvidence.source must say so: no real camera is integrated


class LifecycleError(ValueError):
    """Rejected input; the caller's previous state is still valid."""


@dataclass(frozen=True)
class CrewEvidence:
    """One session's crew authorisation state. `CrewEvidence()` is a fresh session or reset."""

    last_t_s: float | None = None  # last observed snapshot time; observations never go back
    crew: int | None = None  # crew aboard at that snapshot
    clear_since_s: float | None = None  # start of the current clear-deck episode, None = occupied
    confirmation: HumanConfirmation | None = None  # valid for the current episode only
    evidence: DeckEvidence | None = None  # supplied with the last observation; never carried on


def _finite(t: float, what: str) -> None:
    if not math.isfinite(t):
        raise LifecycleError(f"{what} {t!r} is not a finite simulation time")


def observe(
    state: CrewEvidence, snapshot: WorldSnapshot, evidence: DeckEvidence | None = None
) -> CrewEvidence:
    """Advance to `snapshot`. Call for every snapshot, in order, including fast-forwarded ticks.

    Occupied deck or crew change → the confirmation is discarded. `evidence` replaces any previous
    reading (none if omitted); it must be labelled simulated and not be dated after the snapshot.
    An older reading is kept as given, and `recovery_gate` reports it as stale.
    """
    t = snapshot.t_s
    _finite(t, "snapshot time")
    if state.last_t_s is not None and t < state.last_t_s:
        raise LifecycleError(
            f"snapshot time {t:g} s is before the last observed {state.last_t_s:g} s; "
            "start a new CrewEvidence() for a reset or replayed run"
        )
    if evidence is not None:
        _finite(evidence.t_s, "deck evidence time")
        if evidence.t_s > t:
            raise LifecycleError(f"deck evidence at {evidence.t_s:g} s is after snapshot {t:g} s")
        if SIMULATED not in evidence.source:
            raise LifecycleError(
                f"deck evidence source {evidence.source!r} is not labelled simulated"
            )

    occupied = snapshot.ship.deck != DeckState.CLEAR
    crew_changed = state.crew is not None and state.crew != snapshot.crew
    if occupied:
        clear_since = None
    else:
        clear_since = state.clear_since_s if state.clear_since_s is not None else t
    keep = not occupied and not crew_changed
    return CrewEvidence(
        last_t_s=t,
        crew=snapshot.crew,
        clear_since_s=clear_since,
        confirmation=state.confirmation if keep else None,
        evidence=evidence,
    )


def confirm(state: CrewEvidence, confirmation: HumanConfirmation) -> CrewEvidence:
    """Record an explicit operator confirmation for the current clear-deck episode.

    Rejected unless a snapshot has been observed, the deck was clear at it, and the confirmation
    time is finite, not after that snapshot and not before the deck became clear.
    """
    _finite(confirmation.t_s, "confirmation time")
    if state.last_t_s is None:
        raise LifecycleError("observe a snapshot before recording a confirmation")
    if state.clear_since_s is None:
        raise LifecycleError("the deck was occupied at the last observed snapshot")
    if confirmation.t_s > state.last_t_s:
        raise LifecycleError(
            f"confirmation at {confirmation.t_s:g} s is after the last observed "
            f"snapshot {state.last_t_s:g} s"
        )
    if confirmation.t_s < state.clear_since_s:
        raise LifecycleError(
            f"confirmation at {confirmation.t_s:g} s predates the deck becoming clear "
            f"at {state.clear_since_s:g} s"
        )
    return replace(state, confirmation=confirmation)


def gate(ship: ShipSpec, state: CrewEvidence, snapshot: WorldSnapshot) -> RecoveryGate:
    """`recovery_gate` with this session's evidence. `snapshot` must be the last one observed."""
    observed_clear = state.clear_since_s is not None
    if (
        state.last_t_s != snapshot.t_s
        or state.crew != snapshot.crew
        or observed_clear != (snapshot.ship.deck == DeckState.CLEAR)
    ):
        raise LifecycleError(
            f"observe snapshot {snapshot.t_s:g} s before gating (last observed: {state.last_t_s})"
        )
    return recovery_gate(ship, snapshot, state.confirmation, state.evidence)
