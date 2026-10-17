"""D1 admission (≤ 30 s): yes / no / yes-if / not-now-and-when, judged on the mission envelope.

Objective (declared in the README):
  * The card is admitted only if the configuration holds in *every* envelope corner (A.10:
    speed bands × one genset down × hottest water), never against pier-side conditions.
  * If it does not, the engine searches the fixes that make it hold and ranks them by
    priority-weighted capability lost: permanent losses (displacing a guest — port only) first,
    then contingent ones (throttle / standby / switch-off / interlock, applied only in the
    corners that need them), then the number of actions touched.
  * yes-if only when the cheapest fix costs less capability than the card returns; otherwise no.
  * At sea a port-only card is "not now", with the next window; uncrewed, only a logical
    reconfiguration is proposed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations

from shipos.assumptions import A
from shipos.contracts import (
    Action,
    ActionKind,
    ConstraintResult,
    Corner,
    Cost,
    Decision,
    DecisionKind,
    Envelope,
    GuestManifest,
    ModeTier,
    Option,
    Placement,
    PriorityFile,
    ResourceClass,
    ShipSpec,
    Situation,
    Verdict,
    WorldSnapshot,
)
from shipos.engine import placement as d2
from shipos.engine.kernel import Config, GuestConfig, Kernel, binding, contingency_corner, corners
from shipos.engine.rationale import fmt, render
from shipos.physics import compute

TIER_ORDER = [ModeTier.NORMAL, ModeTier.LOW_POWER, ModeTier.STANDBY, ModeTier.OFF]


@dataclass(frozen=True)
class Restriction:
    kind: ActionKind  # THROTTLE / STANDBY / SHED / INTERLOCK
    guest: str
    modes: tuple[str, ...] = ()  # allowed modes after the restriction
    pair: tuple[str, str] | None = None  # interlock ("G2.winch", "G4.charging")
    cost: float = 0.0
    kw: float = 0.0

    @property
    def name(self) -> str:
        return f"{self.kind.value}:{self.guest}" + (f":{self.pair[1]}" if self.pair else "")


# ---------------------------------------------------------------------------- restrictions


def _tier_restriction(
    g: GuestManifest, kind: ActionKind, tier: ModeTier, weight: float
) -> Restriction | None:
    allowed_tiers = TIER_ORDER[TIER_ORDER.index(tier) :]
    target = [m for m in g.modes if m.tier == tier]
    if not target:
        return None
    modes = tuple(m.name for m in g.modes if m.tier in allowed_tiers)
    best_left = max(m.capability_factor for m in target)
    top_kw = max(m.power_kw for m in g.modes)
    return Restriction(
        kind=kind,
        guest=g.id,
        modes=modes,
        cost=weight * (1 - best_left),
        kw=top_kw - max(m.power_kw for m in g.modes if m.name in modes),
    )


def _candidates(
    kernel: Kernel,
    cfg: Config,
    card: str,
    weights: dict[str, float],
) -> list[Restriction]:
    out: list[Restriction] = []
    for gc in cfg.guests:
        g = kernel.guests[gc.id]
        if gc.id == card or g.safety_critical:
            continue
        for kind, tier in (
            (ActionKind.THROTTLE, ModeTier.LOW_POWER),
            (ActionKind.STANDBY, ModeTier.STANDBY),
            (ActionKind.SHED, ModeTier.OFF),
        ):
            r = _tier_restriction(g, kind, tier, weights[gc.id])
            if r is not None:
                out.append(r)
    pairs: set[tuple[str, str]] = set()
    # the worst coincident combination does not depend on the corner, only on the modes
    for objective in ("power", "heat"):
        _, combo = kernel.worst(cfg, objective)
        pairs_of = combinations(sorted(combo.items()), 2)  # literal-ok: pairs
        for (ga, ma), (gb, mb) in pairs_of:
            for (gx, mx), (gy, my) in (((ga, ma), (gb, mb)), ((gb, mb), (ga, ma))):
                # defer a deferrable mode only around a time-limited one; waiting on a
                # continuous mode would be a permanent ban, i.e. a throttle in disguise
                partner_limited = kernel.guests[gy].mode(my).duration_min is not None
                if kernel.guests[gx].mode(mx).deferrable and gx != card and partner_limited:
                    pairs.add((f"{gx}.{mx}", f"{gy}.{my}"))
    frac = A("admission.interlock_capability_fraction")
    for a, b in sorted(pairs):
        gx = a.split(".")[0]
        out.append(
            Restriction(kind=ActionKind.INTERLOCK, guest=gx, pair=(a, b), cost=weights[gx] * frac)
        )
    return sorted(out, key=lambda r: (r.cost, r.name))


def _apply(cfg: Config, rs: tuple[Restriction, ...]) -> Config:
    modes = {r.guest: r.modes for r in rs if r.kind != ActionKind.INTERLOCK}
    guests = tuple(replace(gc, modes=modes[gc.id]) if gc.id in modes else gc for gc in cfg.guests)
    interlocks = cfg.interlocks | frozenset(r.pair for r in rs if r.pair)
    return Config(guests=guests, interlocks=interlocks)


def _passes(kernel: Kernel, cfg: Config, cs: list[Corner], silence: bool) -> bool:
    return all(all(r.ok for r in kernel.evaluate(cfg, c, silence) if r.hard) for c in cs)


def cheapest_fixes(
    kernel: Kernel,
    cfg: Config,
    failing: list[Corner],
    card: str,
    weights: dict[str, float],
    silence: bool,
) -> list[tuple[Restriction, ...]]:
    """Restriction sets (≤ admission.max_restrictions) that make every failing corner pass."""
    cands = _candidates(kernel, cfg, card, weights)
    found = []
    for size in range(1, A("admission.max_restrictions") + 1):
        for combo in combinations(cands, size):
            if len({r.guest for r in combo if r.kind != ActionKind.INTERLOCK}) < sum(
                r.kind != ActionKind.INTERLOCK for r in combo
            ):
                continue  # one tier restriction per guest
            if _passes(kernel, _apply(cfg, combo), failing, silence):
                if not any(set(f) <= set(combo) for f in found):  # keep minimal sets only
                    found.append(combo)
    return sorted(found, key=lambda c: (sum(r.cost for r in c), len(c), [r.name for r in c]))


def _restriction_action(r: Restriction, when: str) -> Action:
    if r.kind == ActionKind.INTERLOCK:
        mode = r.pair[0].split(".", 1)[1]
        return Action(
            guest=r.guest,
            kind=r.kind,
            to_mode=mode,
            other=r.pair[1],
            when=when,
            capability_lost=r.cost,
        )
    return Action(
        guest=r.guest,
        kind=r.kind,
        to_mode=r.modes[0] if r.modes else None,
        when=when,
        capability_lost=r.cost,
        kw_recovered=r.kw,
    )


# ---------------------------------------------------------------------------- installation


def _install_binding(card: GuestManifest, resource: str, **facts) -> ConstraintResult:
    return ConstraintResult(
        cls=ResourceClass.DECK,
        resource=resource,
        capacity=0,
        demand=1,
        unit="rule",
        contributors={card.id: 1},
        facts={"guest": card.id, **facts},
    )


def _logical_reconfig(
    ship: ShipSpec, guests: dict[str, GuestManifest], snap: WorldSnapshot, card: GuestManifest
) -> list[Action]:
    """Uncrewed: nothing physical can change; propose re-tasking what is already aboard."""
    actions = []
    for gid in sorted(snap.guests):
        g = guests[gid]
        overlap = sorted(set(g.capability.tags) & set(card.capability.tags))
        if g.logical_reconfig or overlap:
            detail = g.logical_reconfig or f"raise its {', '.join(overlap)} tasking"
            actions.append(Action(guest=gid, kind=ActionKind.RETASK, facts={"detail": detail}))
    cap = compute.allocatable(ship)
    used = sum(guests[g].compute.cores for g in snap.guests)
    if cap.cores - used > 0:
        actions.append(
            Action(
                guest="rack",
                kind=ActionKind.RETASK,
                facts={
                    "detail": f"{fmt(cap.cores - used)} free cores re-allocated to aboard guests"
                },
            )
        )
    return actions


def _not_now(
    ship: ShipSpec, guests: dict[str, GuestManifest], snap: WorldSnapshot, card: GuestManifest
) -> Decision | None:
    if snap.platform.situation == Situation.PORT or card.installation.where == "logical":
        return None
    days = fmt(snap.mission_days_left)
    chosen = None
    if snap.crew == 0:
        chosen = Option(
            name="logical reconfiguration",
            feasible=True,
            actions=_logical_reconfig(ship, guests, snap, card),
        )
    if card.installation.where == "port_only":
        b = _install_binding(
            card,
            "install.port_only",
            people=card.installation.people,
            hours=card.installation.hours,
        )
        window = f"next port call, within {days} days"
    else:
        need = card.installation.people
        if snap.crew < need:
            b = ConstraintResult(
                cls=ResourceClass.DECK,
                resource="install.crew",
                capacity=snap.crew,
                demand=need,
                unit="people",
                facts={"guest": card.id},
            )
            window = f"next port call, within {days} days"
        else:
            nxt = next_window(ship, snap, card)
            if nxt is not None and nxt <= snap.t_s:
                return None  # a window is open now: judge it physically
            b = _install_binding(card, "install.window", reason="sea state or wind above limits")
            window = (
                f"forecast window at T+{fmt((nxt - snap.t_s) / A('time.s_per_h'))} h"
                if nxt is not None
                else f"none in the forecast; next port call within {days} days"
            )
    d = Decision(
        kind=DecisionKind.ADMISSION,
        subject=card.id,
        verdict=Verdict.NOT_NOW,
        binding=b,
        chosen=chosen,
        next_window=window,
        t_s=snap.t_s,
    )
    return d.model_copy(update={"rationale": render(d)})


def next_window(ship: ShipSpec, snap: WorldSnapshot, card: GuestManifest) -> float | None:
    """First forecast time an at-sea installation window (A.11) opens for long enough."""
    w = ship.install_window
    minutes = (
        card.at_sea_swap.minutes
        if card.at_sea_swap
        else card.installation.hours * A("time.min_per_h")
    )
    need_s = minutes * A("time.s_per_min")
    pts = sorted(snap.env.forecast, key=lambda p: p.t_s)
    for i, p in enumerate(pts):
        if p.sea_state > w.sea_state_max or p.wind_kn > w.wind_max_kn:
            continue
        end = p.t_s
        for q in pts[i:]:
            if q.sea_state > w.sea_state_max or q.wind_kn > w.wind_max_kn:
                break
            end = q.t_s
        if end - p.t_s >= need_s or p is pts[-1]:
            return max(p.t_s, snap.t_s)
    return None


# ---------------------------------------------------------------------------- decide


@dataclass
class _Option:
    removed: tuple[str, ...]
    placement: dict[str, Placement] | None
    fixes: tuple[Restriction, ...]
    when: str
    feasible: bool
    binding: ConstraintResult | None
    moves: tuple[str, ...]
    cost: float
    cost_contingent: float
    checks: list[ConstraintResult]
    needs_fix_one_genset_down: bool = False

    def key(self):
        return (
            self.cost,
            self.cost_contingent,
            len(self.removed) + len(self.fixes) + len(self.moves),
            self.removed,
            [r.name for r in self.fixes],
        )


def decide(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    snapshot: WorldSnapshot,
    card: GuestManifest,
    priorities: PriorityFile,
    envelope: Envelope,
) -> Decision:
    """yes / no / yes-if / not-now-and-when for `card`, judged on every envelope corner."""
    catalogue = {**guests, card.id: card}
    if card.id in snapshot.guests:
        raise ValueError(f"{card.id} is already aboard")
    nn = _not_now(ship, catalogue, snapshot, card)
    if nn is not None:
        return nn

    kernel = Kernel(ship, catalogue)
    silence = envelope.silence_expected
    weights = {g: priorities.weight(m) for g, m in catalogue.items()}
    installed = sorted(snapshot.guests)
    fixed = {g: snapshot.guests[g].placement for g in installed}
    in_port = snapshot.platform.situation == Situation.PORT
    removable = [g for g in installed if in_port and not catalogue[g].safety_critical]
    cs = corners(ship, envelope)

    options: list[_Option] = []
    base_binding: ConstraintResult | None = None
    for k in range(len(removable) + 1):
        for removed in combinations(removable, k):
            opt = _evaluate(
                ship,
                catalogue,
                kernel,
                card,
                installed,
                removed,
                fixed,
                priorities,
                envelope,
                cs,
                weights,
                silence,
                in_port,
            )
            options += opt
            if not removed:
                base_binding = opt[0].binding

    feasible = sorted((o for o in options if o.feasible), key=_Option.key)
    # a removal set that strictly contains one which already works as-is is never worth listing
    clean = [set(o.removed) for o in feasible if not o.fixes]
    feasible = [o for o in feasible if not any(c < set(o.removed) for c in clean)]
    gain = weights[card.id]
    best = feasible[0] if feasible else None

    if best is not None and not best.removed and not best.fixes and not best.moves:
        verdict = Verdict.YES
    elif best is not None and best.cost < gain:
        verdict = Verdict.YES_IF
    else:
        verdict = Verdict.NO

    chosen = best
    shown_binding = binding(best.checks) if verdict == Verdict.YES else base_binding
    holds = best is not None and not best.needs_fix_one_genset_down
    contingency: list[Action] = []
    if chosen is not None and not envelope.include_cooling_degraded:
        contingency = _contingency(kernel, ship, envelope, chosen, card.id, weights, silence)

    costs = [Cost(guest="card", amount=gain, unit="capability", what=f"{card.id} returned")]
    if chosen is not None:
        costs += _option_costs(chosen, catalogue, weights)
    ranked = [o for o in feasible if o is not chosen]
    infeasible = [o for o in options if not o.feasible and o.removed]
    alternatives = [_to_option(o, catalogue, rejected=_why(o, chosen)) for o in ranked] + [
        _to_option(o, catalogue, rejected="still infeasible") for o in infeasible[:1]
    ]
    alternatives = alternatives[: A("admission.alternatives_reported")]

    d = Decision(
        kind=DecisionKind.ADMISSION,
        subject=card.id,
        verdict=verdict,
        binding=shown_binding,
        chosen=_to_option(chosen, catalogue)
        if chosen is not None and verdict != Verdict.YES
        else None,
        alternatives=alternatives,
        placement=chosen.placement or {} if chosen is not None and verdict != Verdict.NO else {},
        holds_one_genset_down=holds,
        contingency=contingency,
        costs=costs if verdict != Verdict.YES else [],
        checks=chosen.checks if chosen is not None else [],
        t_s=snapshot.t_s,
    )
    return d.model_copy(update={"rationale": render(d)})


def _evaluate(
    ship,
    catalogue,
    kernel,
    card,
    installed,
    removed,
    fixed,
    priorities,
    envelope,
    cs,
    weights,
    silence,
    in_port,
) -> list[_Option]:
    """Options for one removal set: as-is if it holds, else one per minimal fix set."""
    present = [g for g in installed if g not in removed] + [card.id]
    keep_fixed = {g: fixed[g] for g in present if g in fixed}
    movable = frozenset(keep_fixed) if in_port else frozenset()
    cands = d2.search(ship, catalogue, present, keep_fixed, priorities, envelope, movable, kernel)
    top = cands[0]
    cost_removed = sum(weights[g] for g in removed)
    moves = tuple(g for g in keep_fixed if top.placement[g].slots != keep_fixed[g].slots)
    all_modes = tuple(
        GuestConfig(
            id=g, modes=tuple(m.name for m in catalogue[g].modes), placement=top.placement[g]
        )
        for g in present
    )
    cfg = Config(guests=all_modes)

    def option(feasible, b, fixes=(), when="always", fix_cost=0.0, checks=(), placed=True):
        contingent = when == "degraded"
        genset_down = any(c.gensets_online < max(envelope.gensets_online) for c in failing)
        return _Option(
            removed=removed,
            placement=top.placement if placed else None,
            fixes=tuple(fixes),
            when=when,
            feasible=feasible,
            binding=b,
            moves=moves,
            cost=cost_removed + (0 if contingent else fix_cost),
            cost_contingent=fix_cost if contingent else 0,
            checks=list(checks),
            needs_fix_one_genset_down=bool(fixes) and genset_down,
        )

    failing: list[Corner] = []
    if not top.feasible:
        unplaced = Config(guests=tuple(replace(gc, placement=None) for gc in all_modes))
        loads = [r for c in cs for r in kernel.evaluate(unplaced, c, silence)]
        b = binding([r for r in list(top.results) + loads if r.hard and not r.ok])
        return [option(False, b, placed=False)]

    per_corner = {c: kernel.evaluate(cfg, c, silence) for c in cs}
    failing = [c for c, rs in per_corner.items() if any(r.hard and not r.ok for r in rs)]
    results = [r for rs in per_corner.values() for r in rs]
    if not failing:
        return [option(True, binding(results), checks=results)]
    b = binding([r for c in failing for r in per_corner[c]])
    fixes = cheapest_fixes(kernel, cfg, failing, card.id, weights, silence)
    if not fixes:
        return [option(False, b, checks=results)]
    when = "degraded" if all(c.degraded for c in failing) else "always"
    out = []
    for fix in fixes[: A("admission.alternatives_reported")]:
        fixed_cfg = _apply(cfg, fix)
        checked = [
            r for c in cs for r in kernel.evaluate(fixed_cfg if c in failing else cfg, c, silence)
        ]
        out.append(option(True, b, fix, when, sum(r.cost for r in fix), checked))
    return out


def _contingency(kernel, ship, envelope, chosen: _Option, card, weights, silence) -> list[Action]:
    """Pre-computed answer to an out-of-envelope fault: chilled-water loop degraded at 30 °C."""
    corner = contingency_corner(ship, envelope)
    cfg = Config(
        guests=tuple(
            GuestConfig(id=g, modes=tuple(m.name for m in kernel.guests[g].modes), placement=p)
            for g, p in chosen.placement.items()
        )
    )
    cfg = _apply(cfg, chosen.fixes)
    if _passes(kernel, cfg, [corner], silence):
        return []
    fixes = cheapest_fixes(kernel, cfg, [corner], card, weights, silence)
    return [_restriction_action(r, "degraded") for r in fixes[0]] if fixes else []


def _option_costs(o: _Option, catalogue, weights) -> list[Cost]:
    costs = [
        Cost(guest=g, amount=weights[g], unit="capability", what=f"{catalogue[g].capability.name}")
        for g in o.removed
    ]
    costs += [
        Cost(
            guest=r.guest,
            amount=r.cost,
            unit="capability",
            what=("with one genset down" if o.when == "degraded" else "always"),
        )
        for r in o.fixes
    ]
    return costs


def _to_option(o: _Option, catalogue, rejected: str | None = None) -> Option:
    actions = [Action(guest=g, kind=ActionKind.REMOVE, capability_lost=0) for g in o.removed]
    actions += [
        Action(guest=g, kind=ActionKind.MOVE, facts={"slot": o.placement[g].slots["body"]})
        for g in o.moves
        if o.placement
    ]
    actions += [_restriction_action(r, o.when) for r in o.fixes]
    name = " + ".join(a.kind.value + " " + a.guest for a in actions) or "as is"
    return Option(
        name=name,
        actions=actions,
        feasible=o.feasible,
        cost=o.cost,
        cost_contingent=o.cost_contingent,
        binding=o.binding,
        rejected_because=rejected,
    )


def _why(o: _Option, chosen: _Option | None) -> str:
    if chosen is None:
        return "not chosen"
    extra = (o.cost + o.cost_contingent) - (chosen.cost + chosen.cost_contingent)
    if o.cost > chosen.cost:
        return f"costs {fmt(o.cost)} permanent capability against {fmt(chosen.cost)}"
    return f"costs {fmt(extra)} more capability"
