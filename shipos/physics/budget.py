"""1 Hz platform budget: one gauge per class with capacity, committed, degraded capacity."""

from shipos.contracts import (
    ConstraintResult,
    GuestManifest,
    Link,
    ResourceClass,
    ShipSpec,
    SlotKind,
    WorldSnapshot,
)
from shipos.physics import compute, cooling, links, mass, power, timing
from shipos.physics.positions import group_capacity


def _draw_kw(g: GuestManifest, mode: str, measured: float | None) -> float:
    return measured if measured is not None else g.mode(mode).power_kw


def gauges(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    snap: WorldSnapshot,
    last_latency_s: float = 0,
    latency_kind: str = "arbitration",
) -> list[ConstraintResult]:
    """`last_latency_s` is the measured time of the latest `latency_kind` decision (A.8)."""
    present = {gid: guests[gid] for gid in snap.guests}
    draws = {
        gid: _draw_kw(present[gid], rt.mode, rt.measured_kw) for gid, rt in snap.guests.items()
    }
    band = power.speed_band(ship, snap.ship.speed_kn)
    gensets = snap.platform.gensets_online
    one_down = power.full_gensets(ship) - 1

    p = ConstraintResult(
        cls=ResourceClass.POWER,
        resource="power.total",
        capacity=power.usable_kw(ship, band, gensets),
        demand=sum(draws.values()),
        unit="kW",
        contributors=draws,
        facts={"degraded_capacity": power.usable_kw(ship, band, one_down), "band": band.value},
    )
    heat = {gid: cooling.heat_kw(present[gid], kw) for gid, kw in draws.items()}
    h = ConstraintResult(
        cls=ResourceClass.HEAT,
        resource="heat.loop",
        capacity=cooling.loop_capacity_kw(
            ship, snap.env.seawater_c, snap.platform.cooling_degraded
        ),
        demand=sum(heat.values()),
        unit="kW",
        contributors={k: v for k, v in heat.items() if v},
        facts={
            "degraded_capacity": cooling.loop_capacity_kw(ship, snap.env.seawater_c, True),
            "seawater_c": snap.env.seawater_c,
        },
    )

    caps = group_capacity(ship)
    deck_groups = {s.group for s in ship.slots if s.kind != SlotKind.MAST}
    mast_slots = [s for s in ship.slots if s.kind == SlotKind.MAST]
    used_slots = {sid for rt in snap.guests.values() for sid in rt.placement.slots.values()}
    deck_units = sum(caps[gr] for gr in deck_groups)
    deck_used = sum(s.units for s in ship.slots if s.id in used_slots and s.kind != SlotKind.MAST)
    d = ConstraintResult(
        cls=ResourceClass.DECK,
        resource="deck.units",
        capacity=deck_units,
        demand=min(deck_used, deck_units),
        unit="units",
        facts={"degraded_capacity": deck_units},
    )
    m_used = sum(1 for s in mast_slots if s.occupied_by or s.id in used_slots)
    mast_g = ConstraintResult(
        cls=ResourceClass.MAST,
        resource="mast.positions",
        capacity=len(mast_slots),
        demand=m_used,
        unit="positions",
        facts={"degraded_capacity": len(mast_slots)},
    )
    placements = {gid: rt.placement for gid, rt in snap.guests.items()}
    raw, _ = mass.checks(ship, guests, placements)
    raw = raw.model_copy(update={"facts": {"degraded_capacity": raw.capacity}})

    sat = [
        r
        for r in links.checks(ship, list(present.values()), snap.ship, snap.env, worst=False)
        if r.resource == "data.satcom"
    ]
    data = sat[0] if sat else None
    if data is not None:
        worst = links.capacity_mbps(ship, Link.SATCOM, snap.ship, snap.env, worst=True)
        data = data.model_copy(update={"facts": {"degraded_capacity": worst}})
    else:
        data = ConstraintResult(
            cls=ResourceClass.DATA, resource="data.satcom", capacity=0, demand=0, unit="Mbit/s"
        )
    cores = compute.checks(ship, list(present.values()))[0]
    cores = cores.model_copy(update={"facts": {"degraded_capacity": cores.capacity}})
    t = timing.latency_check(ship, latency_kind, last_latency_s)
    t = t.model_copy(update={"facts": {"degraded_capacity": t.capacity}})
    return [p, h, d, mast_g, raw, data, cores, t]
