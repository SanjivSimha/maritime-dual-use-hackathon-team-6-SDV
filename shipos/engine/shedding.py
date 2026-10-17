"""D4 degradation (first action ≤ 5 s, full sequence ≤ 60 s): restore the budget in declared order.

Objective (declared in the README):
  * Feasibility over a forecast horizon (shed.horizon_min): the power reserve and the cooling
    loop must hold at every instant of the guests' scheduled modes — an overlap that has not
    started yet (e.g. a non-interruptible recharge) is caught before it trips the last genset.
  * Order, lexicographic: (1) fewest mission-critical guests touched (B.0: mission-critical is
    resolved against the priority file and goes last); (2) the lowest ladder tier that works —
    defer or throttle to a low-power mode, then standby, then switch off; (3) least
    priority-weighted capability-minutes lost; (4) fewest actions.
  * Never: safety-critical guests (human confirmation only), the protected arbitration winner,
    or a guest in a non-interruptible mode.
When nothing needs to go, the decision still states every guest's loss (none) and the armed
ladder — the order the governor would shed in if the margin closes.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from shipos.assumptions import A
from shipos.contracts import (
    Action,
    ActionKind,
    ConstraintResult,
    Corner,
    Cost,
    Decision,
    DecisionKind,
    GuestManifest,
    ModeTier,
    Option,
    PriorityFile,
    ResourceClass,
    ShipSpec,
    Verdict,
    WorldSnapshot,
)
from shipos.engine.kernel import binding
from shipos.engine.rationale import fmt, render
from shipos.physics import cooling, power, timing

LADDER = [(ActionKind.DEFER, ActionKind.THROTTLE), (ActionKind.STANDBY,), (ActionKind.SHED,)]
TIER = {kind: rung for rung, kinds in enumerate(LADDER) for kind in kinds}
CAP_TIER = {
    ActionKind.THROTTLE: ModeTier.LOW_POWER,
    ActionKind.STANDBY: ModeTier.STANDBY,
    ActionKind.SHED: ModeTier.OFF,
}


@dataclass(frozen=True)
class _Move:
    guest: str
    kind: ActionKind
    to_mode: str | None = None
    cap_kw: float | None = None  # throttle / standby / off: draw ≤ cap at every instant
    defer_index: int | None = None  # schedule entry shifted
    delay_s: float = 0.0
    cost: float = 0.0
    factor: float = 1.0  # capability factor kept


def _timeline(snap: WorldSnapshot, gid: str, moves: dict[str, _Move], horizon_end: float):
    rt = snap.guests[gid]
    events = [(snap.t_s, rt.mode)]
    m = moves.get(gid)
    for i, e in enumerate(rt.schedule):
        t = e.t_s
        if m is not None and m.kind == ActionKind.DEFER and m.defer_index is not None:
            if i >= m.defer_index:
                t += m.delay_s
        if snap.t_s < t < horizon_end:
            events.append((t, e.mode))
    return sorted(events)


def _mode_at(events, t: float) -> str:
    mode = events[0][1]
    for te, me in events:
        if te <= t:
            mode = me
    return mode


def _draw(g: GuestManifest, snap: WorldSnapshot, gid: str, mode: str, t: float, m) -> float:
    rt = snap.guests[gid]
    kw = rt.measured_kw if (t == snap.t_s and mode == rt.mode and rt.measured_kw) else None
    kw = g.mode(mode).power_kw if kw is None else kw
    if m is not None and m.cap_kw is not None:
        kw = min(kw, m.cap_kw)
    return kw


def _peaks(guests, snap, moves, horizon_end):
    """(power peak, heat peak) as (value, instant, contributors) over the horizon."""
    lines = {gid: _timeline(snap, gid, moves, horizon_end) for gid in snap.guests}
    instants = sorted({t for ev in lines.values() for t, _ in ev})
    best_p, best_h = (-1.0, 0.0, {}), (-1.0, 0.0, {})
    for t in instants:
        p_contrib, h_contrib = {}, {}
        for gid, ev in lines.items():
            g = guests[gid]
            kw = _draw(g, snap, gid, _mode_at(ev, t), t, moves.get(gid))
            p_contrib[gid] = kw
            heat = cooling.heat_kw(g, kw)
            if heat:
                h_contrib[gid] = heat
        p, h = sum(p_contrib.values()), sum(h_contrib.values())
        if p > best_p[0]:
            best_p = (p, t, p_contrib)
        if h > best_h[0]:
            best_h = (h, t, h_contrib)
    return best_p, best_h


def _capacities(ship: ShipSpec, snap: WorldSnapshot) -> tuple[float, float, Corner]:
    band = power.speed_band(ship, snap.ship.speed_kn)
    corner = Corner(
        speed_band=band,
        seawater_c=snap.env.seawater_c,
        gensets_online=snap.platform.gensets_online,
        cooling_degraded=snap.platform.cooling_degraded,
    )
    usable = power.usable_kw(ship, band, snap.platform.gensets_online)
    loop = cooling.loop_capacity_kw(ship, snap.env.seawater_c, snap.platform.cooling_degraded)
    return usable, loop, corner


def _results(snap, peaks, usable, loop, corner) -> list[ConstraintResult]:
    (p, tp, pc), (h, th, hc) = peaks
    to_min = A("time.s_per_min")
    return [
        ConstraintResult(
            cls=ResourceClass.POWER,
            resource="power.total",
            capacity=usable,
            demand=round(p, A("display.kw_decimals")),
            unit="kW",
            corner=corner.label,
            contributors={g: v for g, v in pc.items() if v},
            facts={"at_min": fmt((tp - snap.t_s) / to_min)},
        ),
        ConstraintResult(
            cls=ResourceClass.HEAT,
            resource="heat.loop",
            capacity=loop,
            demand=round(h, A("display.kw_decimals")),
            unit="kW",
            corner=corner.label,
            contributors=hc,
            facts={
                "at_min": fmt((th - snap.t_s) / to_min),
                "seawater_c": snap.env.seawater_c,
                "loop_degraded": str(snap.platform.cooling_degraded),
            },
        ),
    ]


def _moves_for(gid, g, snap, prio, horizon_s, protected) -> list[_Move | None]:
    rt = snap.guests[gid]
    if gid in protected or g.safety_critical or not g.mode(rt.mode).interruptible:
        return [None]
    w = prio.weight(g)
    h_min = horizon_s / A("time.s_per_min")
    out: list[_Move | None] = [None]
    for kind, tier in CAP_TIER.items():
        target = [m for m in g.modes if m.tier == tier]
        if not target:
            continue
        m = max(target, key=lambda x: x.capability_factor)
        out.append(
            _Move(
                gid,
                kind,
                m.name,
                cap_kw=m.power_kw,
                cost=w * (1 - m.capability_factor) * h_min,
                factor=m.capability_factor,
            )
        )
    others = sorted(
        {e.t_s for o, ort in snap.guests.items() if o != gid for e in ort.schedule}
        | {snap.t_s + horizon_s}
    )
    for i, e in enumerate(rt.schedule):
        if e.t_s <= snap.t_s or not g.mode(e.mode).deferrable:
            continue
        for tau in others:
            if tau > e.t_s:
                delay = tau - e.t_s
                out.append(
                    _Move(
                        gid,
                        ActionKind.DEFER,
                        e.mode,
                        defer_index=i,
                        delay_s=delay,
                        cost=w * delay / A("time.s_per_min"),
                    )
                )
    return out


def _key(combo: tuple[_Move, ...], guests, prio):
    tiers = [TIER[m.kind] for m in combo]
    mc = sum(1 for m in combo if prio.mission_critical(guests[m.guest]))
    names = [f"{m.guest}:{m.kind.value}" for m in combo]
    return (mc, max(tiers, default=-1), sum(m.cost for m in combo), len(combo), names)


def plan(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    snapshot: WorldSnapshot,
    priorities: PriorityFile,
    protected: frozenset[str] = frozenset(),
    event: str | None = None,
) -> Decision:
    """Ordered throttle → standby → shed sequence restoring the budget over the forecast horizon."""
    snap = snapshot
    horizon_s = A("shed.horizon_min") * A("time.s_per_min")
    end = snap.t_s + horizon_s
    usable, loop, corner = _capacities(ship, snap)
    if event is None:
        event = (
            "genset_trip"
            if snap.platform.gensets_online < power.full_gensets(ship)
            else "cooling_loss"
            if snap.platform.cooling_degraded
            else "budget_check"
        )
    before = _peaks(guests, snap, {}, end)
    results = _results(snap, before, usable, loop, corner)
    b = binding(results)
    ids = sorted(snap.guests)
    per_guest = [_moves_for(g, guests[g], snap, priorities, horizon_s, protected) for g in ids]

    if all(r.ok for r in results):
        ladder = sorted(
            (m for opts in per_guest for m in opts if m is not None and m.kind != ActionKind.DEFER),
            key=lambda m: (
                priorities.mission_critical(guests[m.guest]),
                TIER[m.kind],
                m.cost / max(_relief(guests, snap, m, before), A("shed.min_relief_kw")),
                m.guest,
            ),
        )
        first_per_guest, seen = [], set()
        for m in ladder:
            if m.guest not in seen:
                seen.add(m.guest)
                first_per_guest.append(m)
        alts = [
            Option(
                name=f"armed {i + 1}: {m.kind.value} {m.guest}",
                actions=[_action(m, guests, snap, before, 0)],
                feasible=True,
                cost=m.cost,
                rejected_because=f"not needed: {fmt(b.margin)} {b.unit} margin",
            )
            for i, m in enumerate(first_per_guest)
        ]
        d = Decision(
            kind=DecisionKind.SHEDDING,
            subject=event,
            verdict=Verdict.NO_ACTION,
            binding=b,
            alternatives=alts,
            costs=_costs(guests, snap, {}, horizon_s),
            checks=results,
            t_s=snap.t_s,
        )
        return d.model_copy(update={"rationale": render(d)})

    best = None
    for combo in product(*per_guest):
        moves = tuple(m for m in combo if m is not None)
        if best is not None and _key(moves, guests, priorities) >= best[0]:
            continue
        mv = {m.guest: m for m in moves}
        after = _peaks(guests, snap, mv, end)
        if all(r.ok for r in _results(snap, after, usable, loop, corner)):
            best = (_key(moves, guests, priorities), moves, after)

    if best is None:  # everything allowed goes; report what is left
        moves = tuple(
            next((m for m in opts if m is not None and m.kind == ActionKind.SHED), None)
            for opts in per_guest
        )
        moves = tuple(m for m in moves if m is not None)
        after = _peaks(guests, snap, {m.guest: m for m in moves}, end)
    else:
        _, moves, after = best

    ordered = sorted(
        moves, key=lambda m: (TIER[m.kind], -_relief(guests, snap, m, before), m.guest)
    )
    first, step = A("shed.first_action_s"), A("shed.step_interval_s")
    actions = [_action(m, guests, snap, before, first + i * step) for i, m in enumerate(ordered)]
    after_results = _results(snap, after, usable, loop, corner)
    checks = results + after_results
    if actions:
        checks += [
            timing.latency_check(ship, "shed_first", actions[0].t_offset_s),
            timing.latency_check(ship, "shed_full", actions[-1].t_offset_s),
        ]
    d = Decision(
        kind=DecisionKind.SHEDDING,
        subject=event,
        verdict=Verdict.SHED,
        binding=b,
        chosen=Option(
            name=" → ".join(f"{a.kind.value} {a.guest}" for a in actions),
            actions=actions,
            feasible=best is not None,
            cost=sum(m.cost for m in moves),
            binding=binding(after_results),
            rejected_because=None if best is not None else "residual deficit: human decision",
        ),
        costs=_costs(guests, snap, {m.guest: m for m in moves}, horizon_s),
        checks=checks,
        t_s=snap.t_s,
    )
    return d.model_copy(update={"rationale": render(d)})


def _relief(guests, snap, m: _Move, before) -> float:
    (_, t_peak, contrib), _ = before
    end = snap.t_s + A("shed.horizon_min") * A("time.s_per_min")
    after = _peaks(guests, snap, {m.guest: m}, end)
    return max(sum(contrib.values()) - after[0][0], 0.0) if contrib else 0.0


def _action(m: _Move, guests, snap, before, t_offset) -> Action:
    return Action(
        guest=m.guest,
        kind=m.kind,
        to_mode=m.to_mode,
        when="now",
        capability_lost=m.cost,
        kw_recovered=round(_relief(guests, snap, m, before), A("display.kw_decimals")),
        delay_min=round(m.delay_s / A("time.s_per_min"), A("display.minute_decimals")),
        t_offset_s=t_offset,
    )


def _costs(guests, snap, moves: dict[str, _Move], horizon_s: float) -> list[Cost]:
    h = fmt(horizon_s / A("time.s_per_min"))
    out = []
    for gid in sorted(snap.guests):
        m = moves.get(gid)
        mode = snap.guests[gid].mode
        if m is None:
            out.append(Cost(guest=gid, amount=0, unit="% capability", what=f"keeps {mode}"))
        elif m.kind == ActionKind.DEFER:
            out.append(
                Cost(
                    guest=gid,
                    amount=round(m.delay_s / A("time.s_per_min"), A("display.minute_decimals")),
                    unit="min",
                    what=f"{m.to_mode} deferred",
                )
            )
        else:
            lost = round((1 - m.factor) * A("units.percent"))
            out.append(
                Cost(guest=gid, amount=lost, unit="% capability", what=f"{m.to_mode} for {h} min")
            )
    return out


def overdraw(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    snapshot: WorldSnapshot,
    priorities: PriorityFile,
    persisted_s: dict[str, float],
) -> Decision:
    """A guest drawing more than declared: warn, then throttle, then shed if it persists."""
    snap = snapshot
    worst = None
    for gid in sorted(snap.guests):
        rt = snap.guests[gid]
        declared = guests[gid].mode(rt.mode).power_kw
        if rt.measured_kw is None or declared <= 0:
            continue
        ratio = rt.measured_kw / declared
        if worst is None or ratio > worst[1]:
            worst = (gid, ratio, declared, rt.measured_kw)
    if worst is None:
        return Decision(
            kind=DecisionKind.SHEDDING,
            subject="overdraw_check",
            verdict=Verdict.NO_ACTION,
            rationale="Overdraw check: no measured draws reported.",
            t_s=snap.t_s,
        )
    gid, ratio, declared, measured = worst
    g = guests[gid]
    b = ConstraintResult(
        cls=ResourceClass.POWER,
        resource=f"power.overdraw.{gid}",
        capacity=declared,
        demand=round(measured, A("display.kw_decimals")),
        unit="kW",
        contributors={gid: measured},
        facts={"guest": gid, "persisted_s": persisted_s.get(gid, 0)},
    )
    held = persisted_s.get(gid, 0)
    act = None
    if (
        ratio >= A("overdraw.throttle_ratio")
        and held >= A("overdraw.warn_after_s")
        and not g.safety_critical
    ):
        tier = ModeTier.OFF if held >= A("overdraw.shed_after_s") else ModeTier.LOW_POWER
        target = [m for m in g.modes if m.tier == tier] or [
            m for m in g.modes if m.tier == ModeTier.STANDBY
        ]
        if target:
            m = target[0]
            kind = {ModeTier.OFF: ActionKind.SHED, ModeTier.LOW_POWER: ActionKind.THROTTLE}.get(
                m.tier, ActionKind.STANDBY
            )
            act = Action(
                guest=gid,
                kind=kind,
                to_mode=m.name,
                when="now",
                kw_recovered=round(measured - m.power_kw, A("display.kw_decimals")),
                t_offset_s=A("shed.first_action_s"),
            )
    if act is None:
        subject = "overdraw_warning" if ratio >= A("overdraw.warn_ratio") else "overdraw_check"
        d = Decision(
            kind=DecisionKind.SHEDDING,
            subject=subject,
            verdict=Verdict.NO_ACTION,
            binding=b,
            t_s=snap.t_s,
        )
    else:
        d = Decision(
            kind=DecisionKind.SHEDDING,
            subject="overdraw",
            verdict=Verdict.SHED,
            binding=b,
            chosen=Option(name=f"{act.kind.value} {gid}", actions=[act], feasible=True),
            t_s=snap.t_s,
        )
    return d.model_copy(update={"rationale": render(d)})
