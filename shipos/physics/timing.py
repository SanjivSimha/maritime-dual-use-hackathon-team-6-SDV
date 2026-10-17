"""Response time (A.8) as a budget item: decision ceilings and command-path latency."""

from shipos.contracts import ConstraintResult, GuestManifest, Link, ResourceClass, ShipSpec

MS_PER_S = 1000  # literal-ok: unit conversion

_CEILINGS = {
    "admission": "admission_s",
    "placement": "admission_s",
    "arbitration": "arbitration_s",
    "shed_first": "shed_first_s",
    "shed_full": "shed_full_s",
    "safety": "safety_command_s",
}


def ceiling_s(ship: ShipSpec, kind: str) -> float:
    return getattr(ship.timing, _CEILINGS[kind])


def latency_check(ship: ShipSpec, kind: str, seconds: float) -> ConstraintResult:
    return ConstraintResult(
        cls=ResourceClass.TIME,
        resource=f"time.{kind}",
        capacity=ceiling_s(ship, kind),
        demand=seconds,
        unit="s",
    )


def link_latency_s(ship: ShipSpec, link: Link) -> float:
    """Worst-case one-way latency of a command path."""
    L = ship.links
    if link == Link.ACOUSTIC:
        return L.acoustic_latency_s.hi
    if link == Link.SATCOM:
        return L.satcom_latency_ms.hi / MS_PER_S
    return 0


def safety_command_check(ship: ShipSpec, guest: GuestManifest) -> ConstraintResult | None:
    """Can a stop/hold/secure command reach the guest within the safety ceiling?"""
    sc = guest.safety_commands
    if sc is None:
        return None
    return ConstraintResult(
        cls=ResourceClass.TIME,
        resource=f"time.safety_command.{guest.id}",
        capacity=ship.timing.safety_command_s,
        demand=link_latency_s(ship, sc.link),
        unit="s",
        hard=sc.autonomous_fallback is None,
        contributors={guest.id: link_latency_s(ship, sc.link)},
        facts={"link": sc.link.value, "fallback": sc.autonomous_fallback or "none"},
    )
