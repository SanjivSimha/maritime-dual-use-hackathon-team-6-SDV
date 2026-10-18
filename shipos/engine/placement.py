"""D2 placement: bay, mast, deck, power line and cooling manifold for a set of guests.

Objective (lexicographic, declared in the README):
  1. feasible in every envelope corner (positions, slot mass/area, manifold, mast air-cooling,
     stability, mission lines);
  2. fewest moves of already-installed guests (each move is crane time in port);
  3. largest minimum normalised margin on the shared budgets placement moves (stability and the
     mission lines) — a slot filled to its own limit is a flexibility question, not a margin one;
  4. most future flexibility: catalogue cards not aboard that could still be mounted, then
     standard shapes still free (10-ft bay, 20-ft bay, mast slot, hangar, hangar roof);
  5. most priority-weighted preferred positions (B.0: preferred first, tolerated reported);
  6. most priority-weighted capability after placement effects (G1 beside the radar);
  7. deterministic order of the assignment's name.
Search is exhaustive over slot assignments (a handful of guests, ≤ 3 slots each); mission lines
are balanced greedily by worst-case draw.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import product

from shipos.assumptions import A
from shipos.contracts import (
    Action,
    ActionKind,
    ConstraintResult,
    Decision,
    DecisionKind,
    Envelope,
    GuestManifest,
    Line,
    Option,
    Placement,
    PriorityFile,
    ShipSpec,
    SlotKind,
    Verdict,
)
from shipos.engine.kernel import Config, GuestConfig, Kernel, binding, corners
from shipos.engine.rationale import describe, render
from shipos.physics import positions, power

PLACEMENT_PREFIXES = (
    "deck.",
    "mast.",
    "mass.stability",
    "power.line.",
    "heat.manifold.",
    "heat.mast_air",
)
MARGIN_PREFIXES = ("mass.stability", "power.line.")
BAY_KINDS = (SlotKind.BAY1_WHOLE, SlotKind.BAY1_HALF, SlotKind.BAY2)


@dataclass(frozen=True)
class Candidate:
    name: str
    placement: dict[str, Placement]
    results: tuple[ConstraintResult, ...]
    feasible: bool
    moves: int
    margin_bucket: int
    flexibility: tuple[int, int]
    preferred: float
    capability: float

    def key(self):
        return (
            not self.feasible,
            self.moves,
            -self.margin_bucket,
            tuple(-f for f in self.flexibility),
            -self.preferred,
            -self.capability,
            self.name,
        )


def _free_slots(ship: ShipSpec, kinds: list[SlotKind]) -> list[str]:
    return [s.id for s in ship.slots if s.kind in kinds and s.occupied_by is None]


def _slot_options(
    ship: ShipSpec, g: GuestManifest, fixed: Placement | None, movable: bool
) -> list[dict[str, str]]:
    if fixed is not None and not movable:
        return [dict(fixed.slots)]
    items = [("body", _free_slots(ship, g.allowed))]
    items += [(p.id, _free_slots(ship, p.allowed)) for p in g.parts if p.allowed]
    keys = [k for k, _ in items]
    options = [dict(zip(keys, combo, strict=True)) for combo in product(*(v for _, v in items))]
    if fixed is not None:  # current position first, so "no move" wins ties
        options.sort(key=lambda o: o != dict(fixed.slots))
    return options


def _assign_lines(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    slots: dict[str, dict[str, str]],
    fixed: dict[str, Placement],
    movable: frozenset[str],
) -> dict[str, Placement]:
    load = dict.fromkeys(ship.power.lines, 0.0)
    out: dict[str, Placement] = {}
    for gid in slots:
        if gid in fixed and gid not in movable:
            out[gid] = Placement(slots=slots[gid], line=fixed[gid].line)
            load[fixed[gid].line] += power.max_sustained_kw(guests[gid])
    for gid in sorted(
        (g for g in slots if g not in out), key=lambda g: (-power.max_sustained_kw(guests[g]), g)
    ):
        line = min(ship.power.lines, key=lambda ln: (load[ln], list(Line).index(ln)))
        out[gid] = Placement(slots=slots[gid], line=line)
        load[line] += power.max_sustained_kw(guests[gid])
    return out


def _free_units(ship: ShipSpec, placement: dict[str, Placement]) -> dict[str, int]:
    used = {sid for p in placement.values() for sid in p.slots.values()}
    caps = positions.group_capacity(ship)
    group_used: dict[str, int] = {}
    for s in ship.slots:
        if s.id in used:
            group_used[s.group] = group_used.get(s.group, 0) + s.units
    return {g: caps[g] - group_used.get(g, 0) for g in caps}


def _mountable(ship: ShipSpec, g: GuestManifest, free: dict[str, int]) -> bool:
    """Could `g` still be mounted (body and each separately placed part) in the free space?"""

    def fits(kinds: list[SlotKind]) -> bool:
        return any(
            s.kind in kinds and not s.occupied_by and free[s.group] >= s.units for s in ship.slots
        )

    return fits(g.allowed) and all(fits(p.allowed) for p in g.parts if p.allowed)


def flexibility(
    ship: ShipSpec, placement: dict[str, Placement], catalogue: dict[str, GuestManifest]
) -> tuple[int, int]:
    """(catalogue cards not aboard that still fit, standard shapes still free)."""
    free = _free_units(ship, placement)
    cards = sum(_mountable(ship, g, free) for gid, g in catalogue.items() if gid not in placement)
    caps = positions.group_capacity(ship)
    bay_groups = {s.group for s in ship.slots if s.kind in BAY_KINDS}
    whole = {s.group for s in ship.slots if s.kind == SlotKind.BAY1_WHOLE}
    shapes = [
        any(free[g] > 0 for g in bay_groups),  # a 10-ft bay guest
        any(free[g] >= caps[g] for g in whole),  # a 20-ft bay guest
        any(free[s.group] > 0 for s in ship.slots if s.kind == SlotKind.MAST),
        any(free[s.group] > 0 for s in ship.slots if s.kind == SlotKind.HANGAR),
        any(free[s.group] > 0 for s in ship.slots if s.kind == SlotKind.HANGAR_ROOF),
    ]
    return cards, sum(shapes)


def _preferred_items(g: GuestManifest, p: Placement, ship: ShipSpec) -> list[tuple[str, bool]]:
    kind = {s.id: s.kind for s in ship.slots}
    out = [("body", kind[p.slots["body"]] in g.preferred)]
    for part in g.parts:
        if part.allowed and part.id in p.slots:
            out.append((part.id, kind[p.slots[part.id]] in part.preferred))
    return out


def _name(placement: dict[str, Placement]) -> str:
    def one(gid: str, p: Placement) -> str:
        parts = [f"{k}@{v}" for k, v in p.slots.items() if k != "body"]
        return f"{gid}→{p.slots['body']}" + ("+" + "+".join(parts) if parts else "")

    return ", ".join(one(g, placement[g]) for g in sorted(placement))


def _evaluate(
    kernel: Kernel,
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    placement: dict[str, Placement],
    envelope: Envelope,
    priorities: PriorityFile,
    fixed: dict[str, Placement],
    movable: frozenset[str],
) -> Candidate:
    cfg = Config(
        guests=tuple(
            GuestConfig(id=g, modes=tuple(m.name for m in guests[g].modes), placement=placement[g])
            for g in placement
        )
    )
    results: list[ConstraintResult] = []
    seen = set()
    for corner in corners(ship, envelope):
        for r in kernel.evaluate(cfg, corner):
            if r.resource.startswith(PLACEMENT_PREFIXES):
                key = (r.resource, r.capacity, r.demand)
                if key not in seen:
                    seen.add(key)
                    results.append(r)
    hard = [r for r in results if r.hard]
    feasible = all(r.ok for r in hard)
    quantities = [r.norm_margin for r in hard if r.resource.startswith(MARGIN_PREFIXES)]
    tol = A("placement.margin_tie_tolerance")
    bucket = math.floor(min(quantities, default=1) / tol)
    moves = sum(
        1 for g in placement if g in fixed and g in movable and placement[g].slots != fixed[g].slots
    )
    pref = sum(
        priorities.weight(guests[g]) * ok
        for g, p in placement.items()
        for _, ok in _preferred_items(guests[g], p, ship)
    )
    capf = sum(
        priorities.weight(guests[g]) * positions.capability_factor(ship, guests[g], p)
        for g, p in placement.items()
    )
    return Candidate(
        name=_name(placement),
        placement=placement,
        results=tuple(results),
        feasible=feasible,
        moves=moves,
        margin_bucket=bucket,
        flexibility=flexibility(ship, placement, guests),
        preferred=pref,
        capability=capf,
    )


def search(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    present: list[str],
    fixed: dict[str, Placement],
    priorities: PriorityFile,
    envelope: Envelope,
    movable: frozenset[str] = frozenset(),
    kernel: Kernel | None = None,
) -> list[Candidate]:
    """All candidate placements, best first."""
    kernel = kernel or Kernel(ship, guests)
    ids = sorted(present)
    per_guest = [_slot_options(ship, guests[g], fixed.get(g), g in movable) for g in ids]
    limit = A("placement.max_candidates")
    if math.prod(len(o) for o in per_guest) > limit:
        raise ValueError(f"placement space exceeds {limit} candidates")
    kind_group = {sl.id: (sl.kind.value, sl.group) for sl in ship.slots}
    cands, seen = [], set()
    for combo in product(*per_guest):
        slots = dict(zip(ids, combo, strict=True))
        # interchangeable slots (bay1a / bay1b) give mirror-image candidates: keep one
        signature = tuple(
            (g, tuple(sorted((k, kind_group[v]) for k, v in slots[g].items()))) for g in ids
        )
        used = [v for g in ids for v in slots[g].values()]
        if len(used) == len(set(used)):  # a doubled slot must not hide its valid mirror
            if signature in seen:
                continue
            seen.add(signature)
        placement = _assign_lines(ship, guests, slots, fixed, movable)
        cands.append(
            _evaluate(kernel, ship, guests, placement, envelope, priorities, fixed, movable)
        )
    return sorted(cands, key=Candidate.key)


def _why_worse(c: Candidate, best: Candidate) -> str:
    if not c.feasible:
        return describe(binding(list(c.results)))
    if c.moves > best.moves:
        return f"moves {c.moves} installed guest(s) instead of {best.moves}"
    if c.margin_bucket < best.margin_bucket:
        return f"tighter margin: {describe(binding(list(c.results)))}"
    if c.flexibility[0] < best.flexibility[0]:
        have, best_have = c.flexibility[0], best.flexibility[0]
        return f"leaves {have} catalogue card(s) mountable instead of {best_have}"
    if c.flexibility[1] < best.flexibility[1]:
        return f"leaves {c.flexibility[1]} free guest shapes instead of {best.flexibility[1]}"
    if c.preferred < best.preferred:
        return "fewer priority-weighted preferred positions"
    if c.capability < best.capability:
        return "less capability after placement effects (radar proximity)"
    return "equal score; later in deterministic order"


def decide(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    present: list[str],
    fixed: dict[str, Placement],
    priorities: PriorityFile,
    envelope: Envelope,
    movable: frozenset[str] = frozenset(),
    subject: str | None = None,
) -> Decision:
    """Slots, mast, power line and manifold for `present`; `fixed` kept unless in `movable`."""
    cands = search(ship, guests, present, fixed, priorities, envelope, movable)
    best = cands[0]
    new = [g for g in present if g not in fixed]
    subject = subject or (new[0] if len(new) == 1 else "loadout")
    actions = []
    for gid in sorted(best.placement):
        p = best.placement[gid]
        moved = gid in fixed and p.slots != fixed[gid].slots
        prefs = _preferred_items(guests[gid], p, ship)
        actions.append(
            Action(
                guest=gid,
                kind=ActionKind.MOVE if moved else ActionKind.KEEP,
                facts={
                    **{f"slot.{k}": v for k, v in p.slots.items()},
                    "slot": p.slots["body"],
                    "line": p.line.value,
                    "preferred": "yes" if all(ok for _, ok in prefs) else "no",
                },
            )
        )
    alternatives = [
        Option(
            name=c.name,
            feasible=c.feasible,
            binding=binding(list(c.results)),
            rejected_because=_why_worse(c, best),
        )
        for c in cands[1 : 1 + A("placement.alternatives_reported")]
    ]
    shared = [r for r in best.results if r.resource.startswith(MARGIN_PREFIXES)]
    d = Decision(
        kind=DecisionKind.PLACEMENT,
        subject=subject,
        verdict=Verdict.YES if best.feasible else Verdict.NO,
        binding=binding(shared if best.feasible and shared else list(best.results)),
        chosen=Option(name=best.name, actions=actions, feasible=best.feasible),
        alternatives=alternatives,
        placement=best.placement,
        checks=list(best.results),
    )
    if subject not in best.placement:
        return d.model_copy(
            update={"rationale": render(d.model_copy(update={"subject": "loadout"}))}
        )
    return d.model_copy(update={"rationale": render(d)})
