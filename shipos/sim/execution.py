"""Opt-in deterministic actuation of chosen engine plans, never a decision policy."""

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field

from shipos.assumptions import A
from shipos.contracts import Action, DecisionKind, ShipState, Verdict, WorldSnapshot
from shipos.crew import (
    BatteryForecast,
    CrewEvidence,
    DeckEvidence,
    HumanConfirmation,
    confirm,
    early_clearance,
    gate,
    machinery_lock,
    observe,
)
from shipos.engine.arbitration import deadline_min
from shipos.sim.deferral import DeferralError, apply_deferral, capture_deferral
from shipos.sim.resume import ResumeApproval, manifest_hash
from shipos.statemachine import conflicts, plan_transition


def seconds(value):
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
        or not float(value).is_integer()
    ):
        raise ValueError(f"execution requires nonnegative whole seconds, got {value!r}")
    return int(value)


@dataclass
class Execution:
    pending: list[dict] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    evidence: CrewEvidence = field(default_factory=CrewEvidence)
    winners: set[str] = field(default_factory=set)
    reservations: dict = field(default_factory=dict)
    deadlines: dict = field(default_factory=dict)
    forecasts: dict = field(default_factory=dict)
    caps: dict = field(default_factory=dict)
    done: dict[int, float] = field(default_factory=dict)
    boundaries: set[tuple] = field(default_factory=set)
    generated: dict = field(default_factory=dict)
    _resume_approvals: dict[int, ResumeApproval] = field(default_factory=dict)
    sequence: int = 0
    status: dict = field(default_factory=dict)

    @property
    def protected(self):
        return frozenset(self.winners)

    def block(self, code, **details):
        item = {"code": code, **details}
        if item not in self.blocked:
            self.blocked.append(item)

    def observe(self, data, context):
        snap = WorldSnapshot.model_validate(data)
        reading = DeckEvidence(snap.t_s, snap.ship.deck == "clear", "simulated_deck_state")
        self.evidence = observe(self.evidence, snap, reading)
        result = gate(context.ship, self.evidence, snap)
        self.status = {
            "enabled": True,
            "pending": deepcopy(self.pending),
            "applied": deepcopy(self.applied),
            "blocked": deepcopy(self.blocked),
            "crew_gate": {
                "permitted": result.permitted,
                "confirm": result.confirm,
                "confirmation": result.confirmation,
                "blockers": list(result.blockers),
            },
            "protected": sorted(self.winners),
            "reservations": deepcopy(self.reservations),
            "deadlines": deepcopy(self.deadlines),
            "forecasts": deepcopy(self.forecasts),
            "caps": deepcopy(self.caps),
            "generated_schedule": deepcopy(self.generated),
            "crew_evidence": asdict(self.evidence),
        }
        if self._resume_approvals:
            self.status["resume_approvals"] = {
                str(task_id): approval.record()
                for task_id, approval in sorted(self._resume_approvals.items())
            }
        return snap

    def event(self, event, data, context):
        snap = self.observe(data, context)
        p = event.payload
        if event.kind == "operator_confirmation":
            if not p["by"].strip():
                raise ValueError("operator_confirmation requires a nonempty operator name")
            self.evidence = confirm(self.evidence, HumanConfirmation(event.t_s, p["by"]))
        elif event.kind == "battery_report":
            gid = p["guest_id"]
            limit = deadline_min(context.manifests[gid], p["battery_pct"])
            if limit is not None:
                deadline = event.t_s + limit * 60
                old = self.deadlines.get(gid)
                if old is not None and "landed_s" in old:
                    old = None
                self.deadlines[gid] = {
                    "issued_s": event.t_s if old is None else old["issued_s"],
                    "land_by_s": deadline if old is None else min(old["land_by_s"], deadline),
                }
        elif event.kind == "battery_forecast":
            gid = p["guest_id"]
            if gid not in snap.guests or context.manifests[gid].battery is None:
                raise ValueError("battery_forecast requires an installed battery-equipped guest")
            seconds(p["issued_s"])
            seconds(p["land_by_s"])
            if p["issued_s"] > event.t_s or p["land_by_s"] < p["issued_s"]:
                raise ValueError(
                    "battery_forecast must be issued by this tick with an ordered deadline"
                )
            self.forecasts[gid] = {"issued_s": p["issued_s"], "land_by_s": p["land_by_s"]}
        self.observe(data, context)

    def add(self, kind, due, *, deps=(), **payload):
        task = {
            "id": self.sequence,
            "kind": kind,
            "due_s": seconds(due),
            "deps": list(deps),
            **deepcopy(payload),
        }
        self.sequence += 1
        self.pending.append(task)
        return task["id"]

    def requests(self, requests, data, *, plan, hold_actions=None):
        """Absolute StateRequests; completion is the earliest possible effect time."""
        holds = hold_actions or {}
        hold_end = max(
            (r.start_s + r.duration_s for r in requests if r.variable.startswith("hold:")),
            default=0,
        )
        deck_end = max(
            (r.start_s + r.duration_s for r in requests if r.variable == "deck"), default=0
        )
        ids = []
        for r in requests:
            start, duration = seconds(r.start_s), seconds(r.duration_s)
            variable = r.variable
            if variable.startswith("hold:"):
                gid = variable.split(":", 1)[1]
                if gid not in holds or r.to != "hold":
                    raise ValueError(
                        f"{variable}: hold requires a matching chosen pause/suspend action"
                    )
            elif variable not in {
                "speed_kn",
                "course",
                "noise",
                "deck",
                "emission",
                "deck_machinery",
                "confirm:deck",
            }:
                raise ValueError(f"unknown StateRequest variable {variable!r}")
            if start < data["t_s"]:
                raise ValueError(f"{variable}: request starts in the past")
            if variable in {"speed_kn", "course", "noise", "deck_machinery"} and start < hold_end:
                raise ValueError(f"{variable}: manoeuvre precedes acoustic hold completion")
            if variable == "confirm:deck" and (start < deck_end or r.to != "confirmed"):
                raise ValueError("deck confirmation must follow clearance and target confirmed")
            if variable == "deck_machinery" and r.to != "off":
                raise ValueError("only an explicit deck machinery off request is supported")
            if variable not in {"confirm:deck", "deck_machinery"} and not variable.startswith(
                "hold:"
            ):
                trial = dict(data["ship"])
                trial[variable] = float(r.to) if variable == "speed_kn" else r.to
                ShipState.model_validate(trial)
            ids.append(
                self.add(
                    "request",
                    start + duration,
                    variable=variable,
                    to=r.to,
                    start_s=start,
                    duration_s=duration,
                    plan=plan,
                    crew=data["crew"],
                    hold_mode=holds[gid].hold_mode if variable.startswith("hold:") else None,
                )
            )
        tasks = [t for t in self.pending if t["id"] in ids]
        for task in tasks:
            variable = task["variable"]
            if variable in {"speed_kn", "course", "noise", "deck_machinery"}:
                task["deps"] = [t["id"] for t in tasks if t["variable"].startswith("hold:")]
            elif variable == "confirm:deck":
                task["deps"] = [t["id"] for t in tasks if t["variable"] == "deck"]
        return ids

    def accept(self, decisions, data, context):
        for decision in decisions:
            if decision.kind not in (DecisionKind.ARBITRATION, DecisionKind.SHEDDING):
                continue
            if decision.chosen is None:
                continue
            if not decision.chosen.feasible or decision.chosen.rejected_because:
                self.block("chosen_option_unavailable", subject=decision.subject)
                continue
            if decision.verdict not in (Verdict.DECIDED, Verdict.SHED):
                self.block("verdict_not_executable", subject=decision.subject)
                continue
            # Compile one option atomically: unknown semantics never partially queue it.
            trial = deepcopy(self)
            try:
                trial.compile(decision, data, context)
            except DeferralError:
                raise  # Metadata rejection rolls back the whole Runner tick.
            except (ValueError, KeyError) as exc:
                self.block("ambiguous_plan", subject=decision.subject, reason=str(exc))
            else:
                self.__dict__.update(trial.__dict__)
        self.observe(data, context)

    def compile(self, decision, data, context):
        now = seconds(data["t_s"])
        if decision.t_s != now:
            raise ValueError("decision timestamp does not match this tick")
        actions = decision.chosen.actions
        if (
            decision.kind == DecisionKind.ARBITRATION
            and actions
            and all(a.kind == "keep" for a in actions)
        ):
            self.block("engine_selected_keep_no_activity_start", subject=decision.subject)
        plan = self.sequence
        pauses = {}
        for action in actions:
            gid = action.guest
            if gid not in data["guests"]:
                raise ValueError(f"action guest {gid!r} is not installed")
            if (
                action.when == "degraded"
                and data["platform"]["gensets_online"] >= max(context.ship.power.peak_extra_kw)
                and not data["platform"]["cooling_degraded"]
            ):
                raise ValueError("degraded-only action in an undegraded state")
            seconds(action.t_offset_s)
            if action.to_mode is not None:
                try:
                    context.manifests[gid].mode(action.to_mode)
                except KeyError as exc:
                    if action.kind == "defer":
                        raise DeferralError("DEFER target mode is not declared") from exc
                    raise
            if action.kind in ("pause", "suspend"):
                rt = data["guests"][gid]
                activity = context.manifests[gid].activity(rt["activity"])
                choices = [
                    p
                    for p in activity.preempt
                    if p.name == action.kind and p.hold_mode == action.to_mode
                ]
                if len(choices) != 1:
                    raise ValueError(f"{gid}: no unique declared preemption")
                for request in decision.state_requests:
                    if (
                        request.variable == f"hold:{gid}"
                        and request.start_s < now + action.t_offset_s
                    ):
                        raise ValueError(f"{gid}: hold request starts before its action offset")
                pauses[gid] = choices[0]
        reqs = self.requests(decision.state_requests, data, plan=plan, hold_actions=pauses)
        ready = max((t["due_s"] for t in self.pending if t["id"] in reqs), default=now)
        for action in actions:
            gid = action.guest
            due = now + seconds(action.t_offset_s)
            if action.kind in ("keep", "reserve", "deny"):
                if action.kind == "reserve" and action.to_mode is None:
                    raise ValueError("reservation requires a declared target mode")
            elif action.kind in ("throttle", "standby", "shed", "proceed", "pause", "suspend"):
                if action.to_mode is None:
                    raise ValueError(f"{action.kind}: target mode missing")
            elif action.kind != "defer":
                raise ValueError(f"unsupported action {action.kind.value!r}")
            details = {}
            if action.kind == "defer":
                if decision.kind != DecisionKind.SHEDDING:
                    raise ValueError(
                        "D3 delay_min includes transition/approach; deferred start is unspecified"
                    )
                schedule = data["guests"][gid]["schedule"]
                if (
                    action.to_mode is None
                    or not context.manifests[gid].mode(action.to_mode).deferrable
                ):
                    raise DeferralError("DEFER target mode is missing or not deferrable")
                details = capture_deferral(action, schedule, due)
            if action.kind in ("pause", "suspend"):
                p = pauses[gid]
                rt = data["guests"][gid]
                matching = [
                    t for t in self.pending if t["id"] in reqs and t["variable"] == f"hold:{gid}"
                ]
                due = max(due, max((t["due_s"] for t in matching), default=now))
                details = {
                    "resume_activity": rt["activity"],
                    "resume_s": now + seconds((p.window_min + p.resume_delay_min) * 60),
                    "elapsed_s": 0 if p.rerun else now - rt["activity_started_s"],
                }
            if action.kind == "proceed":
                due = max(due, ready)
            self.add(
                "action",
                due,
                deps=reqs if action.kind == "proceed" else (),
                action=action.model_dump(mode="json"),
                plan=plan,
                decision_kind=decision.kind.value,
                origin_s=now,
                **details,
            )
        if decision.winner and context.priorities.protect_arbitration_winner:
            self.winners.add(decision.winner)

    def change(
        self, gid, mode, data, context, entered, transitions, *, source, activity=None, elapsed=0
    ):
        rt = data["guests"][gid]
        previous = rt["mode"]
        projected = self.generated.pop(gid, None)
        if projected and projected["entry"] in rt["schedule"]:
            rt["schedule"].remove(projected["entry"])
        rt.update(
            mode=mode,
            activity=activity,
            activity_started_s=data["t_s"] - elapsed if activity else None,
            measured_kw=context.manifests[gid].mode(mode).power_kw,
        )
        entered[gid] = data["t_s"] - elapsed
        transitions.append(
            {"guest_id": gid, "from": previous, "to": mode, "t_s": data["t_s"], "source": source}
        )
        self.flags(data, context)
        self.observe(data, context)

    @staticmethod
    def flags(data, context):
        modes = [context.manifests[g].mode(rt["mode"]) for g, rt in data["guests"].items()]
        data["ship"]["flight_deck_in_use"] = any(
            m.conditions and m.conditions.uses_flight_deck for m in modes
        )
        data["ship"]["ramp_in_use"] = any(m.conditions and m.conditions.uses_ramp for m in modes)
        data["ship"]["deck_machinery_active"] = any(m.deck_machinery for m in modes)

    def allowed(self, gid, mode, data, context):
        snap = WorldSnapshot.model_validate(data)
        manifest = context.manifests[gid]
        wanted = manifest.mode(mode)
        lock = machinery_lock(context.manifests, snap, gid, mode)
        if lock.locked:
            return "deck machinery locked by " + ", ".join(lock.by)
        for other, runtime in snap.guests.items():
            if other != gid:
                active = context.manifests[other].mode(runtime.mode)
                found = conflicts(
                    wanted.conditions,
                    active.conditions,
                    wanted.deck_machinery,
                    active.deck_machinery,
                )
                if found:
                    return f"active mode conflict with {other}: {', '.join(found)}"
        # State must already satisfy conditions; preparation is never instantaneous.
        state = snap.ship
        if snap.guests[gid].mode == mode:
            state = state.model_copy(update={"flight_deck_in_use": False, "ramp_in_use": False})
        check = plan_transition(
            context.ship,
            state,
            wanted.conditions,
            snap.crew,
            snap.env,
            guests=context.manifests,
            confirm="automatic",
            deck_machinery=wanted.deck_machinery,
        )
        if not check.feasible or check.ready_s or check.steps:
            return check.blocker or "state preparation incomplete"
        if wanted.conditions and wanted.conditions.uses_flight_deck:
            result = gate(context.ship, self.evidence, snap)
            if not result.permitted:
                return ", ".join(result.blockers)
        limit = self.caps.get(gid)
        if limit and limit["until_s"] > snap.t_s and wanted.power_kw > limit["kw"]:
            return "active shedding cap blocks higher-power mode"
        return None

    def prepare(
        self,
        gid,
        mode,
        activity,
        data,
        context,
        *,
        source,
        elapsed=0,
        retry=False,
        parent_resume_task=None,
    ):
        snap = WorldSnapshot.model_validate(data)
        wanted = context.manifests[gid].mode(mode)
        result = plan_transition(
            context.ship,
            snap.ship,
            wanted.conditions,
            snap.crew,
            snap.env,
            guests=context.manifests,
            confirm="automatic"
            if gate(context.ship, self.evidence, snap).permitted
            else gate(context.ship, self.evidence, snap).confirm,
            deck_machinery=wanted.deck_machinery,
        )
        if not result.feasible:
            if not retry:
                self.add(
                    "prepare",
                    snap.t_s,
                    guest=gid,
                    mode=mode,
                    activity=activity,
                    elapsed_s=elapsed,
                    source=source,
                )
            return f"continuation_unavailable:{result.blocker}"
        from shipos.contracts import StateRequest

        requests = [
            StateRequest(
                variable=s.variable, to=s.to, start_s=snap.t_s + s.start_s, duration_s=s.duration_s
            )
            for s in result.steps
        ]
        ids = self.requests(requests, data, plan=self.sequence)
        mode_task = self.add(
            "mode",
            snap.t_s + result.ready_s,
            deps=ids,
            guest=gid,
            mode=mode,
            activity=activity,
            elapsed_s=elapsed,
            source=source,
        )
        if parent_resume_task in self._resume_approvals:
            self._resume_approvals[mode_task] = self._resume_approvals[parent_resume_task]

    def advance(self, data, context, entered, transitions):
        now = data["t_s"]
        self.flags(data, context)
        self.observe(data, context)
        # Only transient waiting reasons are regenerated; structural blockers persist.
        self.blocked = [b for b in self.blocked if "task" not in b]
        for gid in sorted(data["guests"]):
            rt = data["guests"][gid]
            manifest = context.manifests[gid]
            due_entries = [e for e in rt["schedule"] if e["t_s"] <= now]
            for entry in due_entries:
                rt["schedule"].remove(entry)
                projected = self.generated.get(gid)
                activity = None
                if projected and projected["entry"] == entry:
                    activity = projected["activity"]
                    self.generated.pop(gid)
                if not projected and gid in self.protected:
                    self.block(
                        "protected_winner", guest=gid, mode=entry["mode"], scheduled_s=entry["t_s"]
                    )
                    continue
                self.boundaries.add((gid, entered[gid], rt["mode"]))
                active_activity = manifest.activity(rt["activity"]) if rt["activity"] else None
                if (
                    active_activity
                    and active_activity.name == "recovery"
                    and gid in self.deadlines
                    and active_activity.duration_min is not None
                    and now >= entered[gid] + active_activity.duration_min * 60
                ):
                    self.deadlines[gid]["landed_s"] = now
                self.prepare(
                    gid,
                    entry["mode"],
                    activity,
                    data,
                    context,
                    source="declared_continuation" if projected else "scheduled",
                )
            if gid in self.generated or any(
                t["kind"] == "mode"
                and t["guest"] == gid
                and t["source"] in ("declared_continuation", "declared_resume")
                for t in self.pending
            ):
                continue
            mode = manifest.mode(rt["mode"])
            act = manifest.activity(rt["activity"]) if rt["activity"] else None
            duration = (
                act.duration_min if act and act.duration_min is not None else mode.duration_min
            )
            boundary = entered[gid] + duration * 60 if duration is not None else None
            marker = (gid, entered[gid], rt["mode"])
            if boundary is not None and boundary <= now and marker not in self.boundaries:
                self.boundaries.add(marker)
                if act and act.name == "recovery" and gid in self.deadlines:
                    self.deadlines[gid]["landed_s"] = now
                follow = manifest.activity(act.followed_by) if act and act.followed_by else None
                if follow and mode.next_mode and follow.mode != mode.next_mode:
                    self.block(
                        "contradictory_follow_up",
                        guest=gid,
                        mode=mode.name,
                        next_mode=mode.next_mode,
                        followed_by=follow.mode,
                    )
                elif follow or mode.next_mode:
                    self.prepare(
                        gid,
                        follow.mode if follow else mode.next_mode,
                        follow.name if follow else None,
                        data,
                        context,
                        source="declared_continuation",
                    )
                else:
                    self.block("missing_follow_up", guest=gid, mode=mode.name)
        # A task can add due work. Deterministic IDs order simultaneous completions;
        # hold completions sort first, independent of provider request list order.
        visited = set()
        while True:
            due = [t for t in self.pending if t["due_s"] <= now and t["id"] not in visited]
            if not due:
                break
            task = min(
                due,
                key=lambda t: (t["due_s"], not t.get("variable", "").startswith("hold:"), t["id"]),
            )
            visited.add(task["id"])
            if any(i not in self.done for i in task["deps"]):
                self.block("dependency_pending", task=task["id"])
                continue
            if task["kind"] == "request" and task["deps"]:
                actual_start = max(task["start_s"], *(self.done[i] for i in task["deps"]))
                if actual_start > task["start_s"]:
                    task.setdefault("planned_start_s", task["start_s"])
                    task["start_s"] = actual_start
                    task["due_s"] = actual_start + task["duration_s"]
                    if task["due_s"] > now:
                        continue
            reason = self.execute(task, data, context, entered, transitions)
            if reason:
                self.block(reason, task=task["id"])
                continue
            self.pending.remove(task)
            self._resume_approvals.pop(task["id"], None)
            self.done[task["id"]] = now
            self.applied.append({"task": task["id"], "kind": task["kind"], "t_s": now})
        # Explicit battery forecasts only: never extrapolate a fresh forecast each tick.
        advice = {}
        for gid in sorted(data["guests"]):
            manifest = context.manifests[gid]
            if manifest.battery is None or not any(
                a.name == "recovery" for a in manifest.activities
            ):
                continue
            forecast = BatteryForecast(**self.forecasts[gid]) if gid in self.forecasts else None
            result = early_clearance(
                context.ship, WorldSnapshot.model_validate(data), manifest, forecast
            )
            advice[gid] = {
                "request": result.request,
                "blockers": list(result.blockers),
                "start_by_s": result.start_by_s,
                "late": result.late,
            }
            token = f"forecast:{gid}:{self.forecasts.get(gid)}"
            if (
                result.request
                and not any(t.get("plan") == token for t in self.pending)
                and not any(b.get("forecast") == token for b in self.blocked)
            ):
                self.requests(
                    [r.model_copy(update={"start_s": now + r.start_s}) for r in result.requests],
                    data,
                    plan=token,
                )
                self.block("forecast_preparation_started", forecast=token)
        self.project(data, context, entered)
        self.observe(data, context)
        self.status["early_clearance"] = advice

    def project(self, data, context, entered):
        """Publish only a declared next boundary requiring no additional preparation now.

        These are conditional future intents, checked again when due. Unknown state
        preparation or operator input is never replaced with a guessed completion time.
        """
        for gid, rt in sorted(data["guests"].items()):
            if gid in self.generated or any(
                t.get("guest") == gid
                and t["kind"] == "mode"
                and t["source"] in ("declared_continuation", "declared_resume")
                for t in self.pending
            ):
                continue
            manifest = context.manifests[gid]
            mode = manifest.mode(rt["mode"])
            activity = manifest.activity(rt["activity"]) if rt["activity"] else None
            duration = (
                activity.duration_min
                if activity and activity.duration_min is not None
                else mode.duration_min
            )
            follow = (
                manifest.activity(activity.followed_by)
                if activity and activity.followed_by
                else None
            )
            target = follow.mode if follow else mode.next_mode
            if (
                not target
                or duration is None
                or (follow and mode.next_mode and follow.mode != mode.next_mode)
            ):
                continue
            due = entered[gid] + duration * 60
            if due <= data["t_s"] or any(e["t_s"] <= due for e in rt["schedule"]):
                continue
            wanted = manifest.mode(target)
            check = plan_transition(
                context.ship,
                ShipState.model_validate(data["ship"]),
                wanted.conditions,
                data["crew"],
                WorldSnapshot.model_validate(data).env,
                confirm="automatic",
                deck_machinery=wanted.deck_machinery,
            )
            if not check.feasible or check.steps:
                continue
            entry = {"t_s": seconds(due), "mode": target}
            rt["schedule"].append(entry)
            rt["schedule"].sort(key=lambda e: e["t_s"])
            self.generated[gid] = {
                "entry": deepcopy(entry),
                "activity": follow.name if follow else None,
            }

    def execute(self, task, data, context, entered, transitions):
        now = data["t_s"]
        if task["kind"] == "request":
            variable = task["variable"]
            if task["crew"] != data["crew"]:
                return "crew_changed_replan_required"
            if variable == "confirm:deck":
                return (
                    None
                    if gate(
                        context.ship, self.evidence, WorldSnapshot.model_validate(data)
                    ).permitted
                    else "confirmation_pending"
                )
            if variable.startswith("hold:"):
                gid = variable.split(":", 1)[1]
                if gid in self.protected:
                    return "protected_winner"
                if not context.manifests[gid].mode(data["guests"][gid]["mode"]).interruptible:
                    return "noninterruptible_mode"
                self.change(
                    gid,
                    task["hold_mode"],
                    data,
                    context,
                    entered,
                    transitions,
                    source="acoustic_hold_complete",
                )
                return None
            if variable == "deck_machinery" and any(
                context.manifests[g].mode(rt["mode"]).deck_machinery
                for g, rt in data["guests"].items()
            ):
                return "active_guest_machinery_requires_mode_action"
            ship = dict(data["ship"])
            key = "deck_machinery_active" if variable == "deck_machinery" else variable
            ship[key] = (
                False
                if variable == "deck_machinery"
                else float(task["to"])
                if variable == "speed_kn"
                else task["to"]
            )
            target = ShipState.model_validate(ship)
            for gid, rt in data["guests"].items():
                m = context.manifests[gid].mode(rt["mode"])
                if gid in self.protected or not m.interruptible:
                    check = plan_transition(
                        context.ship,
                        target,
                        m.conditions,
                        data["crew"],
                        WorldSnapshot.model_validate(data).env,
                        confirm="automatic",
                    )
                    if not check.feasible or check.steps:
                        return f"state_request_conflicts_with_protected_or_noninterruptible:{gid}"
            data["ship"] = ship
            self.observe(data, context)
            return None
        if task["kind"] in ("resume", "prepare"):
            approval = self._resume_approvals.get(task["id"])
            if task["kind"] == "resume" and approval is None:
                return "unapproved_resume_task"
            if approval is not None:
                reason = approval.blocker(task, data, context.manifests[task["guest"]], entered)
                if reason:
                    return reason
            return self.prepare(
                task["guest"],
                task["mode"],
                task["activity"],
                data,
                context,
                source=task.get("source", "declared_resume"),
                elapsed=task["elapsed_s"],
                retry=True,
                parent_resume_task=task["id"] if approval is not None else None,
            )
        if task["kind"] == "mode":
            gid, mode = task["guest"], task["mode"]
            rt = data["guests"][gid]
            active = context.manifests[gid].mode(rt["mode"])
            approval = self._resume_approvals.get(task["id"])
            if approval is not None:
                reason = approval.blocker(task, data, context.manifests[gid], entered)
                if reason:
                    return reason
            if (
                gid in self.protected
                and task["source"] != "declared_continuation"
                and approval is None
            ):
                return "protected_winner"
            if (
                not active.interruptible
                and now < entered[gid] + (active.duration_min or float("inf")) * 60
            ):
                return "noninterruptible_mode"
            reason = self.allowed(gid, mode, data, context)
            if reason:
                return reason
            self.change(
                gid,
                mode,
                data,
                context,
                entered,
                transitions,
                source=task["source"],
                activity=task["activity"],
                elapsed=task["elapsed_s"],
            )
            if task["activity"] is None:
                self.winners.discard(gid)
                self.reservations.pop(gid, None)
            return None
        action = Action.model_validate(task["action"])
        gid, rt = action.guest, data["guests"][action.guest]
        manifest = context.manifests[gid]
        if action.kind == "keep":
            return None
        if action.kind == "deny":
            self.block("denied_by_engine", guest=gid, facts=action.facts)
            return None
        if action.kind == "reserve":
            self.reservations[gid] = {
                "mode": action.to_mode,
                "kw": manifest.mode(action.to_mode).power_kw,
            }
            return None
        if action.kind == "proceed" and rt["mode"] == action.to_mode:
            return None  # Incumbent proceeds; preserve its original activity clock.
        replacement = None
        if action.kind == "defer":
            replacement = apply_deferral(task, rt["schedule"], now)
        if gid in self.protected and action.kind != "proceed":
            return "protected_winner"
        if not manifest.mode(rt["mode"]).interruptible:
            return "noninterruptible_mode"
        if manifest.safety_critical and task["decision_kind"] == "shedding":
            return "safety_critical_requires_separate_authority"
        if action.kind == "defer":
            schedule = rt["schedule"]
            suffix = task["planned_schedule"][task["schedule_index"] :]
            projected = self.generated.get(gid)
            projected_entry = None
            if projected and projected["entry"] in suffix:
                index = suffix.index(projected["entry"])
                projected_entry = deepcopy(task["shifted_suffix"][index])
            # Every check and replacement is complete before mutating either container.
            schedule[:] = replacement
            if projected_entry is not None:
                projected["entry"] = projected_entry
            return None
        activity = None
        if action.kind == "proceed":
            matches = [a for a in manifest.activities if a.mode == action.to_mode]
            if len(matches) != 1:
                return "proceed_requires_unique_declared_activity"
            activity = matches[0]
            deadline = self.deadlines.get(gid)
            if deadline and now + (activity.duration_min or 0) * 60 > deadline["land_by_s"]:
                return "anchored_landing_deadline_unreachable"
        reason = self.allowed(gid, action.to_mode, data, context)
        if reason:
            return reason
        if action.kind in ("pause", "suspend"):
            original = manifest.activity(task["resume_activity"])
            resume_task = self.add(
                "resume",
                task["resume_s"],
                guest=gid,
                mode=original.mode,
                activity=original.name,
                elapsed_s=task["elapsed_s"],
            )
            self._resume_approvals[resume_task] = ResumeApproval(
                origin_task=task["id"],
                guest=gid,
                activity=original.name,
                mode=original.mode,
                hold_mode=action.to_mode,
                hold_entered_s=now,
                not_before_s=task["resume_s"],
                elapsed_s=task["elapsed_s"],
                manifest_sha256=manifest_hash(manifest),
            )
        if action.kind in ("throttle", "standby", "shed"):
            self.caps[gid] = {
                "kw": manifest.mode(action.to_mode).power_kw,
                "until_s": task["origin_s"] + A("shed.horizon_min") * 60,
            }
        self.change(
            gid,
            action.to_mode,
            data,
            context,
            entered,
            transitions,
            source="chosen_action",
            activity=activity.name if activity else None,
        )
        return None
