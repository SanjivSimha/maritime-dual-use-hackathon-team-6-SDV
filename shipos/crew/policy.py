"""D5 crew policy: "flight deck clear and confirmed before drone recovery", plus the winch lock.

Pure functions over contract objects; nothing here mutates its inputs or reads a clock. Ship
timings come from A.6 (`ShipSpec.transitions`) through the Track A planner, called read-only.
Feasibility of the landing itself (battery deadline vs. transition time) stays with Track A.

Evidence the contracts do not carry is passed in explicitly:
  * `HumanConfirmation` — a crew member confirmed the deck clear (14 / 6 aboard).
  * `DeckEvidence`      — a SIMULATED deck camera / sensor reading (0 aboard). There is no real
                          camera integration in this prototype.
  * `BatteryForecast`   — when the drone must be down, from the simulator / Track A battery logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shipos.contracts import (
    Conditions,
    DeckState,
    GuestManifest,
    ShipSpec,
    StateRequest,
    WorldSnapshot,
)
from shipos.statemachine import conflicts, crew_key, plan_transition

Confirm = Literal["human", "automatic", "forbidden"]  # plan_transition's `confirm` values
Confirmation = Literal["received", "pending", "automatic", "unavailable"]

SECONDS_PER_MIN = 60

# Blocker codes. Machine-readable on purpose: explanation text is rendered elsewhere.
DECK_OCCUPIED = "deck_occupied"
CONFIRMATION_PENDING = "confirmation_pending"
NO_CREW_TO_CLEAR_DECK = "no_crew_to_clear_deck"
NO_DECK_EVIDENCE = "no_deck_evidence"
STALE_DECK_EVIDENCE = "stale_deck_evidence"
EVIDENCE_NOT_CLEAR = "evidence_not_clear"
NO_FORECAST = "no_forecast"


@dataclass(frozen=True)
class HumanConfirmation:
    """A crew member confirmed the flight deck clear at `t_s`.

    Valid for one clear-deck episode: the caller drops it as soon as the deck is occupied again.
    """

    t_s: float
    by: str = "deck officer"


@dataclass(frozen=True)
class DeckEvidence:
    """A deck camera / sensor reading. SIMULATED in this prototype; no real camera is wired up."""

    t_s: float
    deck_clear: bool
    source: str = "simulated_camera"


@dataclass(frozen=True)
class BatteryForecast:
    """Issued at `issued_s`: the drone must be on deck by `land_by_s` (sim seconds)."""

    issued_s: float
    land_by_s: float


@dataclass(frozen=True)
class RecoveryGate:
    """May the drone start recovery now, as far as the deck and its confirmation go?"""

    permitted: bool
    confirm: Confirm  # pass to plan_transition(confirm=...)
    confirmation: Confirmation  # what has actually happened, as opposed to what is required
    blockers: tuple[str, ...]
    requests: tuple[StateRequest, ...]  # start_s relative to snapshot.t_s


@dataclass(frozen=True)
class ClearanceAdvice:
    """Should deck clearance start now, ahead of a forecast recovery?"""

    request: bool
    start_by_s: float | None  # latest start (sim s) that covers the A.6 lead and the approach
    lead_s: float | None  # planner ready time for the recovery state, A.6 upper bounds
    approach_s: float | None
    late: bool  # the forecast arrived after start_by_s
    blockers: tuple[str, ...]
    requests: tuple[StateRequest, ...]


@dataclass(frozen=True)
class MachineryLock:
    locked: bool
    by: tuple[str, ...]  # "G3.surveying": active modes whose conditions forbid deck machinery


def _humans_can_confirm(ship: ShipSpec, crew: int) -> bool:
    confirm_s = ship.transitions.confirm_s
    return confirm_s[crew_key(confirm_s, crew)] is not None


def _requests(steps) -> tuple[StateRequest, ...]:
    return tuple(
        StateRequest(variable=s.variable, to=s.to, start_s=s.start_s, duration_s=s.duration_s)
        for s in steps
    )


def recovery_gate(
    ship: ShipSpec,
    snapshot: WorldSnapshot,
    confirmation: HumanConfirmation | None = None,
    evidence: DeckEvidence | None = None,
) -> RecoveryGate:
    """Deck rule for drone recovery at the snapshot's crew level. Fails closed."""
    deck_clear = snapshot.ship.deck == DeckState.CLEAR
    now = snapshot.t_s
    blockers: list[str] = []

    if _humans_can_confirm(ship, snapshot.crew):
        confirm: Confirm = "human"
        received = deck_clear and confirmation is not None and confirmation.t_s <= now
        status: Confirmation = "received" if received else "pending"
        if not deck_clear:
            blockers.append(DECK_OCCUPIED)
        if not received:
            blockers.append(CONFIRMATION_PENDING)
    else:
        if not deck_clear:
            blockers.append(NO_CREW_TO_CLEAR_DECK)
        elif evidence is None:
            blockers.append(NO_DECK_EVIDENCE)
        elif evidence.t_s != now:
            blockers.append(STALE_DECK_EVIDENCE)
        elif not evidence.deck_clear:
            blockers.append(EVIDENCE_NOT_CLEAR)
        confirm = "forbidden" if blockers else "automatic"
        status = "unavailable" if blockers else "automatic"

    requests: tuple[StateRequest, ...] = ()
    if confirm == "human" and status == "pending":
        plan = plan_transition(
            ship, snapshot.ship, Conditions(deck=DeckState.CLEAR), snapshot.crew, confirm="human"
        )
        requests = _requests(plan.steps)
    return RecoveryGate(
        permitted=not blockers,
        confirm=confirm,
        confirmation=status,
        blockers=tuple(blockers),
        requests=requests,
    )


def early_clearance(
    ship: ShipSpec,
    snapshot: WorldSnapshot,
    drone: GuestManifest,
    forecast: BatteryForecast | None,
    activity: str = "recovery",
) -> ClearanceAdvice:
    """Start clearing the deck once the forecast landing time is within the A.6 lead.

    lead_s is the planner's ready time for the drone's recovery conditions (deck clear, then
    human confirmation, concurrently with speed / course), on upper bounds. approach_s is the
    activity's duration from the card. A missing or future-dated forecast requests nothing; a
    late one requests clearance at once but never makes the deck ready.
    """
    act = drone.activity(activity)
    mode = drone.mode(act.mode)
    approach_s = (act.duration_min or 0) * SECONDS_PER_MIN
    occupied = snapshot.ship.deck != DeckState.CLEAR
    if not _humans_can_confirm(ship, snapshot.crew):
        blockers = (NO_CREW_TO_CLEAR_DECK,) if occupied else ()
        return ClearanceAdvice(False, None, None, approach_s, False, blockers, ())
    if forecast is None or forecast.issued_s > snapshot.t_s:
        return ClearanceAdvice(False, None, None, approach_s, False, (NO_FORECAST,), ())

    plan = plan_transition(
        ship,
        snapshot.ship,
        mode.conditions,
        snapshot.crew,
        confirm="human",
        deck_machinery=mode.deck_machinery,
    )
    start_by_s = forecast.land_by_s - approach_s - plan.ready_s
    request = occupied and snapshot.t_s >= start_by_s
    deck_steps = [s for s in plan.steps if s.variable in ("deck", "confirm:deck")]
    return ClearanceAdvice(
        request=request,
        start_by_s=start_by_s,
        lead_s=plan.ready_s,
        approach_s=approach_s,
        late=forecast.issued_s > start_by_s,
        blockers=(DECK_OCCUPIED,) if occupied else (),
        requests=_requests(deck_steps) if request else (),
    )


def machinery_lock(
    guests: dict[str, GuestManifest], snapshot: WorldSnapshot, guest: str, mode: str
) -> MachineryLock:
    """Lock `guest` entering `mode` if it runs deck machinery while another guest's active mode
    forbids it (`deck_machinery_allowed: false`). Derived from conditions, never from guest ids."""
    wanted = guests[guest].mode(mode)
    if not wanted.deck_machinery:
        return MachineryLock(False, ())
    by = []
    for gid, runtime in sorted(snapshot.guests.items()):
        if gid == guest:
            continue
        active = guests[gid].mode(runtime.mode)
        if "deck_machinery" in conflicts(
            active.conditions, wanted.conditions, b_deck_machinery=True
        ):
            by.append(f"{gid}.{runtime.mode}")
    return MachineryLock(bool(by), tuple(by))
