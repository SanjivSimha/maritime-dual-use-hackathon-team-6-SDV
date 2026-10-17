"""Data links (A.5): backbone, SATCOM and radio (zero in silence), acoustic (fragile)."""

from shipos.contracts import (
    ConstraintResult,
    Emission,
    Env,
    GuestManifest,
    Link,
    Noise,
    ResourceClass,
    ShipSpec,
    ShipState,
)

KBPS_PER_MBPS = 1000  # literal-ok: unit conversion


def acoustic_up(state: ShipState, env: Env) -> bool:
    """The acoustic link breaks with manoeuvre, deck machinery or layered water (A.5)."""
    return not (
        state.noise == Noise.MANOEUVRING or state.deck_machinery_active or env.layered_water
    )


def capacity_mbps(
    ship: ShipSpec, link: Link, state: ShipState | None, env: Env | None, worst: bool
) -> float:
    """Usable capacity for guests. `worst` = envelope worst case (admission); else best case."""
    L = ship.links
    silent = state is not None and state.emission == Emission.SILENT
    if link == Link.BACKBONE:
        return L.backbone_mbps
    if link == Link.SATCOM:
        if silent:
            return 0
        raw = (
            L.satcom_mbps.lo * L.satcom_availability.lo
            if worst
            else L.satcom_mbps.hi * L.satcom_availability.hi
        )
        return max(raw - L.ship_satcom_reserved_mbps, 0)
    if link == Link.RADIO:
        if silent:
            return 0
        return L.radio_mbps.lo if worst else L.radio_mbps.hi
    if state is not None and env is not None and not acoustic_up(state, env):
        return 0
    return (L.acoustic_kbps.lo if worst else L.acoustic_kbps.hi) / KBPS_PER_MBPS


def checks(
    ship: ShipSpec,
    manifests: list[GuestManifest],
    state: ShipState | None,
    env: Env | None,
    worst: bool,
    corner: str = "",
) -> list[ConstraintResult]:
    """One result per link; elastic demands make the result non-blocking (capability loss)."""
    results = []
    for link in Link:
        demands = [(g.id, d) for g in manifests for d in g.data if d.link == link]
        if not demands:
            continue
        results.append(
            ConstraintResult(
                cls=ResourceClass.DATA,
                resource=f"data.{link.value}",
                capacity=capacity_mbps(ship, link, state, env, worst),
                demand=sum(d.mbps for _, d in demands),
                unit="Mbit/s",
                corner=corner,
                hard=not all(d.elastic for _, d in demands),
                contributors={gid: d.mbps for gid, d in demands},
                facts={"basis": "envelope worst case" if worst else "current"},
            )
        )
    return results
