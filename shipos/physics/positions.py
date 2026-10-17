"""Deck footprints and mast positions (A.3): discrete, scarce, with per-slot rules."""

from collections import defaultdict

from shipos.assumptions import A
from shipos.contracts import (
    ConstraintResult,
    CoolingKind,
    GuestManifest,
    Placement,
    ResourceClass,
    ShipSpec,
    Slot,
    SlotKind,
)
from shipos.physics.mass import placed_items
from shipos.physics.power import max_sustained_kw


def slot(ship: ShipSpec, slot_id: str) -> Slot:
    for s in ship.slots:
        if s.id == slot_id:
            return s
    raise KeyError(f"no slot {slot_id!r}")


def _cls(s: Slot) -> ResourceClass:
    return ResourceClass.MAST if s.kind == SlotKind.MAST else ResourceClass.DECK


def _violation(cls, resource, gid, **facts) -> ConstraintResult:
    """A rule (not a quantity): capacity 0, demand 1 when broken, 0 when kept."""
    return ConstraintResult(
        cls=cls,
        resource=resource,
        capacity=0,
        demand=1,
        unit="rule",
        contributors={gid: 1},
        facts=facts,
    )


def group_capacity(ship: ShipSpec) -> dict[str, int]:
    cap: dict[str, int] = defaultdict(int)
    for s in ship.slots:
        cap[s.group] = max(cap[s.group], 0 if s.occupied_by else s.units)
    return dict(cap)


def check(
    ship: ShipSpec, guests: dict[str, GuestManifest], placements: dict[str, Placement]
) -> list[ConstraintResult]:
    results: list[ConstraintResult] = []
    used: dict[str, dict[str, int]] = defaultdict(dict)  # group → guest → units
    mass_in: dict[str, dict[str, float]] = defaultdict(dict)  # slot → guest → t
    area_in: dict[str, dict[str, float]] = defaultdict(dict)

    for gid, p in placements.items():
        g = guests[gid]
        parts = {part.id: part for part in g.parts}
        for item, m, sid in placed_items(g, p):
            if sid is None:
                results.append(_violation(ResourceClass.DECK, f"deck.unplaced.{gid}.{item}", gid))
                continue
            s = slot(ship, sid)
            if item == "body":
                allowed, area = g.allowed, g.area_m2
            elif parts[item].allowed:
                allowed, area = parts[item].allowed, parts[item].area_m2
            else:
                allowed, area = None, None  # co-located part rides with the body
            if allowed is not None:
                if s.kind not in allowed:
                    results.append(
                        _violation(
                            _cls(s),
                            f"deck.allowed.{gid}"
                            if item == "body"
                            else f"deck.allowed.{gid}.{item}",
                            gid,
                            slot=sid,
                            kind=s.kind.value,
                        )
                    )
                used[s.group][f"{gid}.{item}"] = s.units
            mass_in[sid][gid] = mass_in[sid].get(gid, 0) + m
            if area is not None:
                area_in[sid][gid] = area_in[sid].get(gid, 0) + area

        body = slot(ship, p.slots["body"])
        if g.cooling == CoolingKind.WATER and not body.cooling_manifold:
            results.append(
                _violation(ResourceClass.HEAT, f"heat.manifold.{gid}", gid, slot=body.id)
            )
        if body.kind == SlotKind.MAST:
            if g.cooling != CoolingKind.AIR:
                results.append(_violation(ResourceClass.HEAT, f"heat.mast_air.{gid}", gid))
            results.append(
                ConstraintResult(
                    cls=ResourceClass.HEAT,
                    resource=f"heat.mast_air_kw.{gid}",
                    capacity=ship.cooling.mast_air_max_kw,
                    demand=max_sustained_kw(g),
                    unit="kW",
                    contributors={gid: max_sustained_kw(g)},
                    facts={"slot": body.id},
                )
            )
        for part in g.parts:
            if part.allowed and p.slots.get(part.id) == "hangar_roof":
                limit = A("positions.hangar_roof_max_antenna_height_m")
                results.append(
                    ConstraintResult(
                        cls=ResourceClass.DECK,
                        resource=f"deck.hangar_roof.clearance.{gid}",
                        capacity=limit,
                        demand=part.height_m or 0,
                        unit="m",
                        contributors={gid: part.height_m or 0},
                        facts={"part": part.id},
                    )
                )

    group_cap = group_capacity(ship)
    group_cls = {s.group: _cls(s) for s in ship.slots}
    for group, by_item in sorted(used.items()):
        contrib: dict[str, float] = defaultdict(float)
        for key, units in by_item.items():
            contrib[key.split(".")[0]] += units
        occupied = [s.occupied_by for s in ship.slots if s.group == group and s.occupied_by]
        results.append(
            ConstraintResult(
                cls=group_cls[group],
                resource=f"{group_cls[group].value}.{group}",
                capacity=group_cap[group],
                demand=sum(by_item.values()),
                unit="units",
                contributors=dict(contrib),
                facts={"occupied_by": occupied[0]} if occupied else {},
            )
        )
    for sid, by_guest in sorted(mass_in.items()):
        s = slot(ship, sid)
        results.append(
            ConstraintResult(
                cls=_cls(s),
                resource=f"{_cls(s).value}.{sid}.mass",
                capacity=s.max_mass_t,
                demand=sum(by_guest.values()),
                unit="t",
                contributors=by_guest,
            )
        )
    for sid, by_guest in sorted(area_in.items()):
        s = slot(ship, sid)
        if s.max_area_m2 is not None:
            results.append(
                ConstraintResult(
                    cls=_cls(s),
                    resource=f"{_cls(s).value}.{sid}.area",
                    capacity=s.max_area_m2,
                    demand=sum(by_guest.values()),
                    unit="m²",
                    contributors=by_guest,
                )
            )
    return results


def capability_factor(ship: ShipSpec, guest: GuestManifest, placement: Placement) -> float:
    """Capability kept after placement effects (G1 next to the ship's own radar loses 30 %)."""
    near = any(slot(ship, sid).near_radar for sid in placement.slots.values())
    return 1 - guest.radar_sensitivity_loss if near else 1
