"""Ship state machine (A.6): which guest conditions can hold at the same time.

A conflict is an incompatibility between two guests' conditions on the ship's state — never a
hard-coded pair of guest ids.
"""

from shipos.contracts import Conditions, Course, Noise


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
