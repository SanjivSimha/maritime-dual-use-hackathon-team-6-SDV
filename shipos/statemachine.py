"""Ship state machine (A.6): which guest conditions can hold at the same time.

A conflict is an incompatibility between two guests' conditions on the ship's state — never a
hard-coded pair of guest ids.
"""

from dataclasses import dataclass
from typing import Literal

from shipos.contracts import (
    Conditions,
    Course,
    DeckState,
    Emission,
    Env,
    GuestManifest,
    Noise,
    ShipSpec,
    ShipState,
    SpeedBand,
)


def _demands_course(c: Conditions) -> str | None:
    if c.needs_manoeuvre_freedom:
        return Course.FREE.value
    return c.course.value if c.course else None


def _demands_noise(c: Conditions) -> str | None:
    if c.needs_manoeuvre_freedom:
        return Noise.MANOEUVRING.value  # turning into wind is manoeuvring
    return c.noise.value if c.noise else None


def conflicts(
    a: Conditions | None,
    b: Conditions | None,
    a_deck_machinery: bool = False,
    b_deck_machinery: bool = False,
) -> list[str]:
    """Ship-state variables on which `a` and `b` cannot both be satisfied."""
    a = a or Conditions()
    b = b or Conditions()
    found: list[str] = []
    if a.speed_kn and b.speed_kn and a.speed_kn.intersect(b.speed_kn) is None:
        found.append("speed_kn")
    ca, cb = _demands_course(a), _demands_course(b)
    if ca and cb and ca != cb:
        found.append("course")
    na, nb = _demands_noise(a), _demands_noise(b)
    if na and nb and na != nb:
        found.append("noise")
    if a.deck and b.deck and a.deck != b.deck:
        found.append("deck")
    if a.emission and b.emission and a.emission != b.emission:
        found.append("emission")
    if (a.deck_machinery_allowed is False and b_deck_machinery) or (
        b.deck_machinery_allowed is False and a_deck_machinery
    ):
        found.append("deck_machinery")
    if (a.uses_flight_deck and b.uses_ramp) or (a.uses_ramp and b.uses_flight_deck):
        found.append("flight_deck_vs_ramp")
    if a.uses_flight_deck and b.uses_flight_deck:
        found.append("flight_deck")
    if a.uses_ramp and b.uses_ramp:
        found.append("ramp")
    return found


# --------------------------------------------------------------------------- transition planner


@dataclass(frozen=True)
class Step:
    variable: str  # "speed_kn", "course", "noise", "deck", "confirm:deck", "hold:G3", ...
    to: str
    start_s: float
    duration_s: float

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


@dataclass(frozen=True)
class TransitionPlan:
    steps: tuple[Step, ...]
    ready_s: float
    feasible: bool
    blocker: str | None = None


def crew_key(levels: dict[int, object], crew: int) -> int:
    """A.6 gives times at 14 / 6 / 0 aboard; any other crew uses the next level down."""
    return max((lvl for lvl in levels if lvl <= crew), default=min(levels))


def _band_index(ship: ShipSpec, kn: float) -> int:
    from shipos.physics.power import speed_band

    return list(SpeedBand).index(speed_band(ship, kn))


def plan_transition(
    ship: ShipSpec,
    state: ShipState,
    target: Conditions | None,
    crew: int,
    env: Env | None = None,
    hold_first: list[str] | tuple[str, ...] = (),
    guests: dict[str, GuestManifest] | None = None,
    confirm: Literal["human", "automatic", "forbidden"] = "human",
    deck_machinery: bool = False,
) -> TransitionPlan:
    """Timed requests that bring the ship from `state` to `target`, on A.6 upper bounds.

    Variables settle concurrently, except that an acoustic hold command is sent (and has
    arrived) before any manoeuvre, and a human confirms the deck only after it is clear.
    """
    target = target or Conditions()
    T = ship.transitions
    steps: list[Step] = []
    blockers: list[str] = []

    if env is not None and target.wind_max_kn is not None and env.wind_kn > target.wind_max_kn:
        blockers.append(f"wind {env.wind_kn:g} kn above the {target.wind_max_kn:g} kn limit")
    if (
        env is not None
        and target.sea_state_max is not None
        and env.sea_state > target.sea_state_max
    ):
        blockers.append(f"sea state {env.sea_state:g} above {target.sea_state_max:g}")
    if target.emission is not None and state.emission != target.emission:
        if state.emission == Emission.SILENT:
            blockers.append("leaving silence is a mission decision")
        else:
            steps.append(Step("emission", target.emission.value, 0, 0))
    if target.uses_flight_deck and state.ramp_in_use:
        blockers.append("the stern ramp is in use")
    if target.uses_ramp and state.flight_deck_in_use:
        blockers.append("the flight deck is in use")

    speed_change = target.speed_kn is not None and not target.speed_kn.contains(state.speed_kn)
    breaks_link = target.needs_manoeuvre_freedom or deck_machinery or speed_change
    t0 = 0.0
    if breaks_link and guests is not None:
        from shipos.physics.timing import link_latency_s

        for gid in hold_first:
            sc = guests[gid].safety_commands
            latency = link_latency_s(ship, sc.link) if sc else 0
            steps.append(Step(f"hold:{gid}", "hold", 0, latency))
            t0 = max(t0, latency)

    if speed_change:
        lo, hi = target.speed_kn.lo, target.speed_kn.hi
        to = min(
            max(state.speed_kn, lo if lo is not None else state.speed_kn),
            hi if hi is not None else state.speed_kn,
        )
        bands = abs(_band_index(ship, to) - _band_index(ship, state.speed_kn))
        steps.append(Step("speed_kn", f"{to:g}", t0, T.speed_band_s.hi * max(bands, 1)))

    want_course = _demands_course(target)
    if want_course == Course.FREE.value and state.course != Course.FREE:
        steps.append(Step("course", Course.FREE.value, t0, 0))
    elif want_course == Course.STEADY.value and state.course != Course.STEADY:
        steps.append(Step("course", Course.STEADY.value, t0, T.course_settle_s.hi))

    want_noise = _demands_noise(target)
    if want_noise == Noise.QUIET.value and state.noise != Noise.QUIET:
        steps.append(Step("noise", Noise.QUIET.value, t0, T.to_quiet_s.hi))
    elif want_noise == Noise.MANOEUVRING.value and state.noise != Noise.MANOEUVRING:
        steps.append(Step("noise", Noise.MANOEUVRING.value, t0, 0))

    if target.deck_machinery_allowed is False and state.deck_machinery_active:
        steps.append(Step("deck_machinery", "off", 0, 0))

    if target.deck == DeckState.CLEAR:
        deck_end = 0.0
        if state.deck != DeckState.CLEAR:
            clear = T.deck_clear_s[crew_key(T.deck_clear_s, crew)]
            if clear is None:
                blockers.append(f"the deck cannot be cleared with {crew} aboard")
            else:
                steps.append(Step("deck", DeckState.CLEAR.value, 0, clear.hi))
                deck_end = clear.hi
        if confirm == "human":
            c = T.confirm_s[crew_key(T.confirm_s, crew)]
            if c is None:
                blockers.append(f"no one aboard to confirm the deck is clear ({crew} crew)")
            else:
                steps.append(Step("confirm:deck", "confirmed", deck_end, c.hi))
        elif confirm == "forbidden":
            blockers.append(f"the action is not permitted with {crew} aboard")

    ready = max((s.end_s for s in steps), default=0.0)
    return TransitionPlan(
        steps=tuple(steps),
        ready_s=ready,
        feasible=not blockers,
        blocker="; ".join(blockers) or None,
    )
