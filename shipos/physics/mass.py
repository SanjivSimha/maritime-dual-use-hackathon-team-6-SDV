"""Mass margin and stability budget (A.4): weight high up costs more."""

from shipos.contracts import (
    ConstraintResult,
    GuestManifest,
    Placement,
    ResourceClass,
    ShipSpec,
)


def raw_t(manifests: list[GuestManifest]) -> float:
    return sum(g.total_mass_t for g in manifests)


def placed_items(guest: GuestManifest, placement: Placement) -> list[tuple[str, float, str | None]]:
    """(item id, mass t, slot id) for the body and each part.

    Co-located parts follow the body; a separately placed part with no slot yet gets None.
    """
    body_slot = placement.slots["body"]
    items: list[tuple[str, float, str | None]] = [("body", guest.mass_t, body_slot)]
    for part in guest.parts:
        slot = placement.slots.get(part.id) if part.allowed else body_slot
        items.append((part.id, part.mass_t, slot))
    return items


def _multiplier(ship: ShipSpec, guest: GuestManifest, item: str, slot: str | None) -> float:
    if slot is not None:
        return next(s.stability_multiplier for s in ship.slots if s.id == slot)
    part = next(p for p in guest.parts if p.id == item)  # unplaced: charge the worst allowed slot
    return max(s.stability_multiplier for s in ship.slots if s.kind in part.allowed)


def stability_t(
    ship: ShipSpec, guests: dict[str, GuestManifest], placements: dict[str, Placement]
) -> float:
    total = 0.0
    for gid, placement in placements.items():
        g = guests[gid]
        for item, m, slot in placed_items(g, placement):
            total += m * _multiplier(ship, g, item, slot)
    return total


def checks(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    placements: dict[str, Placement],
    corner: str = "",
) -> list[ConstraintResult]:
    present = [guests[g] for g in placements]
    raw = ConstraintResult(
        cls=ResourceClass.MASS,
        resource="mass.margin",
        capacity=ship.mass.margin_t,
        demand=raw_t(present),
        unit="t",
        corner=corner,
        contributors={g.id: g.total_mass_t for g in present},
    )
    contrib = {
        gid: sum(
            m * _multiplier(ship, guests[gid], item, s)
            for item, m, s in placed_items(guests[gid], p)
        )
        for gid, p in placements.items()
    }
    stab = ConstraintResult(
        cls=ResourceClass.MASS,
        resource="mass.stability",
        capacity=ship.mass.stability_budget_t,
        demand=sum(contrib.values()),
        unit="t-eq",
        corner=corner,
        contributors=contrib,
    )
    return [raw, stab]
