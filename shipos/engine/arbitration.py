"""D3 right of way (≤ 2 s): two admitted guests need incompatible ship states.

Objective (declared in the README):
  1. Hard constraints first. A deadline (battery below critical: land within N min or ditch)
     prunes every option that misses it; an option that loses an asset is chosen only if no
     option avoids it, and is then reported as risk accepted.
  2. Among the rest, minimise priority-weighted capability-minutes lost over the arbitration
     horizon: the loser's lost activity (elapsed work that must be re-run + the window it
     yields) plus the time the winner's follow-on activity stays blocked by the loser.
  3. Ties: fewer ship-state changes, then option name.
The conflict itself is found by intersecting the two guests' conditions on the ship's state
(statemachine.conflicts) — no pair of guests is hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shipos.assumptions import A
from shipos.contracts import (
    Action,
    ActionKind,
    Conditions,
    ConstraintResult,
    Cost,
    Course,
    Decision,
    DecisionKind,
    GuestManifest,
    Noise,
    Option,
    PriorityFile,
    ResourceClass,
    ShipSpec,
    ShipState,
    StateRequest,
    Verdict,
    WorldSnapshot,
)
from shipos.engine.kernel import Kernel
from shipos.engine.rationale import fmt, render
from shipos.statemachine import TransitionPlan, conflicts, plan_transition


@dataclass
class _Opt:
    name: str
    proceeds: bool  # challenger goes first
    feasible: bool
    cost: float
    asset_loss: bool = False
    actions: list[Action] = field(default_factory=list)
    costs: list[Cost] = field(default_factory=list)
    rejected: str | None = None
    changes: int = 0


def _minutes(seconds: float) -> float:
    return seconds / A("time.s_per_min")


def deadline_min(guest: GuestManifest, battery_pct: float | None) -> float | None:
    """Minutes until the guest must have landed (B.G4: below 20 % land within 12 min or ditch)."""
    b = guest.battery
    if b is None or battery_pct is None:
        return None
    if battery_pct < b.critical_pct:
        return b.land_within_min
    to_critical = (battery_pct - b.critical_pct) / A("units.percent") * b.endurance_min
    return to_critical + b.land_within_min


def _state_after(state: ShipState, c: Conditions, plan: TransitionPlan) -> ShipState:
    speed = next((float(s.to) for s in plan.steps if s.variable == "speed_kn"), state.speed_kn)
    return state.model_copy(
        update={
            "speed_kn": speed,
            "course": Course.FREE if c.needs_manoeuvre_freedom else (c.course or state.course),
            "noise": Noise.MANOEUVRING if c.needs_manoeuvre_freedom else (c.noise or state.noise),
            "deck": c.deck or state.deck,
        }
    )


def _conflicting(kernel: Kernel, snap: WorldSnapshot, challenger: str, mode: str) -> dict:
    out = {}
    cm = kernel.guests[challenger].mode(mode)
    for gid in sorted(snap.guests):
        if gid == challenger:
            continue
        im = kernel.guests[gid].mode(snap.guests[gid].mode)
        found = conflicts(cm.conditions, im.conditions, cm.deck_machinery, im.deck_machinery)
        if found or not kernel.compatible((challenger, mode), (gid, im.name)):
            out[gid] = found or ["exclusion"]
    return out


def decide(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    snapshot: WorldSnapshot,
    priorities: PriorityFile,
    challenger: str,
    challenger_activity: str,
    confirm: str = "human",
) -> Decision:
    """Resolve `challenger` starting `challenger_activity` against running guests' conditions."""
    snap, t = snapshot, snapshot.t_s
    kernel = Kernel(ship, guests)
    ch = guests[challenger]
    act = ch.activity(challenger_activity)
    mode = ch.mode(act.mode)
    w = {g: priorities.weight(m) for g, m in guests.items()}
    horizon = A("arbitration.horizon_min")
    mission_min = snap.mission_days_left * A("time.min_per_day")

    clash = _conflicting(kernel, snap, challenger, act.mode)
    inc = next(iter(clash), None)  # one incumbent per decision; ids in deterministic order
    inc_g = guests[inc] if inc else None
    inc_rt = snap.guests[inc] if inc else None
    inc_act = inc_g.activity(inc_rt.activity) if inc and inc_rt.activity else None
    elapsed = (
        _minutes(t - inc_rt.activity_started_s)
        if inc_act and inc_rt.activity_started_s is not None
        else 0.0
    )
    remaining = (inc_act.duration_min or 0) - elapsed if inc_act else 0.0

    hold = [inc] if inc_g is not None and inc_g.safety_commands is not None else []
    plan = plan_transition(
        ship,
        snap.ship,
        mode.conditions,
        snap.crew,
        snap.env,
        hold,
        guests,
        confirm,
        mode.deck_machinery,
    )
    finish = _minutes(plan.ready_s) + (act.duration_min or 0)
    deadline = deadline_min(ch, snap.guests[challenger].battery_pct)
    conflict_txt = ", ".join(clash.get(inc, [])) if inc else ""

    reserve: list[Action] = []
    if priorities.protect_arbitration_winner and priorities.mission_critical(ch):
        top = max(ch.modes, key=lambda m: m.power_kw)
        reserve = [
            Action(
                guest=challenger,
                kind=ActionKind.RESERVE,
                to_mode=top.name,
                kw_recovered=top.power_kw,
            )
        ]

    options: list[_Opt] = []
    proceed_ok = plan.feasible and (deadline is None or finish <= deadline)
    for p in inc_act.preempt if inc_act else []:
        name = f"{challenger} {act.name}; {inc} {p.name}"
        lost = (elapsed if p.rerun else 0) + p.window_min + p.resume_delay_min
        back = plan_transition(
            ship,
            _state_after(snap.ship, mode.conditions, plan),
            inc_g.mode(inc_act.mode).conditions,
            snap.crew,
            snap.env,
            guests=guests,
        )
        needed = finish + _minutes(back.ready_s)
        blocked = 0.0
        follow = ch.activity(act.followed_by) if act.followed_by else None
        if follow is not None:
            fm, im = ch.mode(follow.mode), inc_g.mode(inc_act.mode)
            if conflicts(fm.conditions, im.conditions, fm.deck_machinery, im.deck_machinery):
                start = p.window_min + p.resume_delay_min
                busy = (start, start + (inc_act.duration_min or 0))
                blocked = max(0.0, min(busy[1], horizon) - max(busy[0], finish))
        o = _Opt(
            name=name,
            proceeds=True,
            feasible=proceed_ok and needed <= p.window_min,
            cost=w[inc] * lost + w[challenger] * blocked,
            changes=len(plan.steps),
            actions=[
                Action(
                    guest=inc,
                    kind=ActionKind.PAUSE if p.name == "pause" else ActionKind.SUSPEND,
                    to_mode=p.hold_mode,
                    facts={"window_min": p.window_min, "activity": inc_act.name},
                ),
                Action(guest=challenger, kind=ActionKind.PROCEED, to_mode=act.mode),
                *reserve,
            ],
            costs=[
                Cost(
                    guest=inc,
                    amount=round(lost, A("display.minute_decimals")),
                    unit=f"{inc_act.name} min",
                    what=f"{fmt(elapsed)} min of the leg re-run + {fmt(p.window_min)}-min {p.name}"
                    if p.rerun
                    else f"{fmt(p.window_min)}-min {p.name}",
                )
            ]
            + (
                [
                    Cost(
                        guest=challenger,
                        amount=round(blocked, A("display.minute_decimals")),
                        unit=f"{follow.name} min",
                        what=f"blocked while {inc} re-runs",
                    )
                ]
                if blocked
                else []
            ),
        )
        if not plan.feasible:
            o.rejected = f"{challenger} cannot {act.name}: {plan.blocker}"
        elif deadline is not None and finish > deadline:
            o.rejected = f"ready in {fmt(finish)} min, after the {fmt(deadline)}-min deadline"
        elif needed > p.window_min:
            o.rejected = f"needs {fmt(needed)} min, longer than the {fmt(p.window_min)}-min window"
        options.append(o)

    wait = remaining + finish
    waits_ok = deadline is None or wait <= deadline
    any_safe = any(o.feasible for o in options)
    wait_opt = _Opt(
        name=f"{challenger} waits for the end of {inc} {inc_act.name}" if inc_act else "wait",
        proceeds=False,
        feasible=waits_ok or not any_safe,
        asset_loss=not waits_ok,
        cost=w[challenger] * (mission_min if not waits_ok else wait),
        actions=[
            Action(guest=inc, kind=ActionKind.PROCEED, to_mode=inc_rt.mode)
            if inc
            else Action(guest=challenger, kind=ActionKind.KEEP),
            Action(
                guest=challenger,
                kind=ActionKind.DENY,
                facts={"outcome": f"{ch.battery.on_deny}: risk accepted"},
            )
            if not waits_ok and ch.battery
            else Action(guest=challenger, kind=ActionKind.DEFER, to_mode=act.mode, delay_min=wait),
        ],
        costs=[
            Cost(
                guest=challenger,
                amount=1,
                unit="drone-risk",
                what=f"cannot land within {fmt(deadline)} min",
            )
            if not waits_ok
            else Cost(
                guest=challenger,
                amount=round(wait, A("display.minute_decimals")),
                unit=f"{act.name} min",
                what=f"waits for {inc}",
            )
        ],
    )
    if not waits_ok and any_safe:
        wait_opt.rejected = (
            f"violates the {fmt(deadline)}-min landing deadline: waiting takes {fmt(wait)} min"
        )
    options.append(wait_opt)

    feasible = [o for o in options if o.feasible]
    safe = [o for o in feasible if not o.asset_loss] or feasible
    chosen = min(safe, key=lambda o: (o.cost, o.changes, o.name))
    for o in options:
        if o is not chosen and o.rejected is None:
            o.rejected = f"costs {fmt(o.cost)} weighted capability-min against {fmt(chosen.cost)}"

    facts = {"guest": challenger, "conflict": conflict_txt}
    if not proceed_ok and (not plan.feasible or deadline is not None):
        b = ConstraintResult(
            cls=ResourceClass.TIME,
            resource="time.ready",
            capacity=deadline if deadline is not None else horizon,
            demand=round(finish, A("display.minute_decimals")),
            unit="min",
            facts={
                **facts,
                **({"blocker": plan.blocker} if plan.blocker else {}),
                "activity": act.name,
            },
        )
    elif deadline is not None:
        b = ConstraintResult(
            cls=ResourceClass.TIME,
            resource="time.landing_deadline",
            capacity=deadline,
            demand=round(wait, A("display.minute_decimals")),
            unit="min",
            facts={**facts, "waiting_for": f"the end of {inc} {inc_act.name}" if inc_act else "-"},
        )
    else:
        b = ConstraintResult(
            cls=ResourceClass.TIME,
            resource="time.ready",
            capacity=horizon,
            demand=round(finish, A("display.minute_decimals")),
            unit="min",
            facts={**facts, "activity": act.name},
        )

    winner, loser = (challenger, inc) if chosen.proceeds else (inc or challenger, challenger)
    requests = (
        [
            StateRequest(
                variable=s.variable, to=s.to, start_s=t + s.start_s, duration_s=s.duration_s
            )
            for s in sorted(
                plan.steps,
                key=lambda s: (s.start_s, not s.variable.startswith("hold:"), s.variable),
            )
        ]
        if chosen.proceeds
        else []
    )
    d = Decision(
        kind=DecisionKind.ARBITRATION,
        subject=f"{challenger} vs {inc}" if inc else challenger,
        verdict=Verdict.DECIDED,
        binding=b,
        winner=winner,
        loser=loser if inc else None,
        chosen=Option(
            name=chosen.name,
            actions=chosen.actions,
            feasible=True,
            cost=chosen.cost,
            costs=chosen.costs,
        ),
        alternatives=[
            Option(
                name=o.name,
                actions=o.actions,
                feasible=o.feasible,
                cost=o.cost,
                rejected_because=o.rejected,
                costs=o.costs,
            )
            for o in options
            if o is not chosen
        ],
        state_requests=requests,
        costs=chosen.costs,
        t_s=t,
    )
    return d.model_copy(update={"rationale": render(d)})
