"""Transactional 1 Hz stepping and a deliberately policy-free engine callback seam."""

from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from random import Random

from shipos.contracts import (
    Decision,
    Event,
    GuestManifest,
    PriorityFile,
    ShipSpec,
    WorldSnapshot,
)
from shipos.data import load_guests, load_priorities, load_ship, load_yaml
from shipos.sim.consumption import Profile
from shipos.sim.env_input import EnvironmentInput
from shipos.sim.execution import Execution
from shipos.sim.log import ZERO_HASH, chain_line, verify_jsonl
from shipos.sim.timeline import tick_time, validate_event


@dataclass(frozen=True)
class Context:
    ship: ShipSpec
    manifests: dict[str, GuestManifest]
    priorities: PriorityFile
    cards: dict[str, GuestManifest]
    protected: frozenset[str] = frozenset()
    recovery_confirm: str = "human"


Decide = Callable[[WorldSnapshot, tuple[Event, ...], Context], Iterable[Decision]]


class Runner:
    def __init__(
        self,
        initial: WorldSnapshot,
        *,
        ship: ShipSpec,
        manifests: dict[str, GuestManifest],
        priorities: PriorityFile,
        timeline: Iterable[Event] = (),
        seed: int = 0,
        profiles: dict[str, Profile] | None = None,
        decide: Decide | None = None,
        environment: EnvironmentInput | None = None,
        execution: bool = False,
    ):
        if environment is not None and not isinstance(environment, EnvironmentInput):
            raise TypeError("environment must be a captured EnvironmentInput")
        if type(execution) is not bool:
            raise TypeError("execution must be a bool")
        self._execution = Execution() if execution else None
        self._environment = deepcopy(environment)
        self._snapshot = initial.model_copy(deep=True)
        self._context = deepcopy(Context(ship, manifests, priorities, {}))
        self._timeline = tuple(sorted(map(validate_event, timeline), key=lambda event: event.t_s))
        self._profiles = deepcopy(profiles or {})
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        self._rng = Random(seed)
        self._decide = decide
        self._next_s = tick_time(initial.t_s)
        self._cursor = 0
        self._entered = {}
        self._validate_inputs()
        if self._execution is not None:
            self._execution.observe(self._snapshot.model_dump(), self._context)
        self._lines: list[bytes] = []
        self._hash = ZERO_HASH
        header = {
            "type": "initial",
            "version": 1,
            "initial": initial.model_dump(mode="json"),
            "ship": ship.model_dump(mode="json"),
            "manifests": {k: v.model_dump(mode="json") for k, v in manifests.items()},
            "priorities": priorities.model_dump(mode="json"),
            "timeline": [event.model_dump(mode="json") for event in self._timeline],
            "seed": seed,
            "profiles": {k: v.model_dump(mode="json") for k, v in self._profiles.items()},
        }
        if self._environment is not None:
            header.update(version=2, environment=self._environment.record)
        if execution:
            header.update(version=3, execution=True)
        self._append(header)

    def _validate_inputs(self):
        if self._snapshot.crew not in self._context.ship.crew_levels:
            raise ValueError("initial crew must be a declared ship crew level")
        if any(
            self._snapshot.platform.gensets_online not in values
            for values in self._context.ship.power.reserve_kw.values()
        ):
            raise ValueError("initial genset count is not represented in ship reserves")
        if set(self._profiles) - set(self._snapshot.guests):
            raise ValueError("profile refers to an uninstalled guest")
        if any(event.t_s < self._next_s for event in self._timeline):
            raise ValueError("timeline event precedes initial snapshot")
        for key, manifest in self._context.manifests.items():
            if key != manifest.id:
                raise ValueError("manifest key must match its id")
            for mode in manifest.modes:
                if mode.next_mode:
                    manifest.mode(mode.next_mode)
                if mode.duration_min is not None and (
                    mode.duration_min <= 0 or not (mode.duration_min * 60).is_integer()
                ):
                    raise ValueError("mode duration must be positive whole seconds")
        for key, runtime in self._snapshot.guests.items():
            if key != runtime.id or key not in self._context.manifests:
                raise ValueError("runtime key/id must match a supplied manifest")
            manifest = self._context.manifests[key]
            manifest.mode(runtime.mode)
            entered = self._next_s
            if runtime.activity is not None:
                activity = manifest.activity(runtime.activity)
                if activity.mode != runtime.mode or runtime.activity_started_s is None:
                    raise ValueError("initial activity requires matching mode and start time")
                entered = tick_time(runtime.activity_started_s)
                if entered > self._next_s:
                    raise ValueError("activity starts after initial snapshot")
            self._entered[key] = entered
            for change in runtime.schedule:
                if tick_time(change.t_s) < self._next_s:
                    raise ValueError("schedule precedes initial snapshot")
                manifest.mode(change.mode)

    @property
    def snapshot(self) -> WorldSnapshot:
        return self._snapshot.model_copy(deep=True)

    @property
    def context(self) -> Context:
        return deepcopy(self._context)

    @property
    def environment(self) -> EnvironmentInput | None:
        """A defensive copy of the captured input, including its window-finder forecast."""
        return deepcopy(self._environment)

    @property
    def execution_status(self) -> dict:
        """Defensive JSON-compatible execution evidence; never an actuation API."""
        if self._execution is None:
            return {
                "enabled": False,
                "pending": [],
                "applied": [],
                "blocked": [],
                "crew_gate": None,
            }
        return deepcopy(self._execution.status)

    def reset(self) -> WorldSnapshot:
        """Restore captured construction inputs, including weather, without reading files."""
        import json

        fresh = _from_header(json.loads(self._lines[0])["record"], self._decide)
        self.__dict__ = fresh.__dict__
        return self.snapshot

    @property
    def next_t_s(self) -> int:
        """Time of the next tick, including the initial tick before the first step."""
        return self._next_s

    @property
    def jsonl(self) -> bytes:
        return b"".join(self._lines)

    @property
    def final_hash(self) -> str:
        return self._hash

    def _append(self, record: dict):
        line, self._hash = chain_line(record, len(self._lines), self._hash)
        self._lines.append(line)

    def step(self, events: Iterable[Event] = ()) -> WorldSnapshot:
        """Step one second; apply timeline events first, then this tick's ordered live batch."""
        t_s = self._next_s
        injected = []
        for event in events:
            if not isinstance(event, Event):
                raise TypeError("injected events must be shared Event objects")
            event = validate_event(event)
            if event.t_s != t_s:
                raise ValueError(f"injected event at {event.t_s:g}s must target next tick {t_s}s")
            injected.append(event)
        data = self._snapshot.model_dump(mode="python")
        data["t_s"] = float(t_s)
        if self._environment is not None:
            data["env"] = self._environment.env_at(t_s).model_dump(mode="python")
        data["mission_days_left"] = max(
            0, data["mission_days_left"] - (t_s - self._snapshot.t_s) / 86400
        )
        context = self.context
        entered = self._entered.copy()
        cursor = self._cursor
        batch = []
        while cursor < len(self._timeline) and self._timeline[cursor].t_s == t_s:
            batch.append(self._timeline[cursor])
            cursor += 1
        batch.extend(injected)
        execution = deepcopy(self._execution)
        if execution is not None:
            execution.observe(data, context)
        for event in batch:
            if execution is None and event.kind in ("operator_confirmation", "battery_forecast"):
                raise ValueError(f"{event.kind} requires execution=True")
            context = self._apply(event, data, context)
            if execution is not None:
                execution.event(event, data, context)
        transitions = []
        rng = Random(0)
        rng.setstate(self._rng.getstate())
        if execution is not None:
            execution.advance(data, context, entered, transitions)
            context = replace(
                context,
                protected=execution.protected,
                recovery_confirm=execution.status["crew_gate"]["confirm"],
            )
        for key in sorted(data["guests"]):
            guest = data["guests"][key]
            manifest = context.manifests[key]
            # Explicit schedules at this second override an automatic boundary.
            due = [] if execution is not None else [s for s in guest["schedule"] if s["t_s"] == t_s]
            if execution is None:
                guest["schedule"] = [s for s in guest["schedule"] if s["t_s"] > t_s]
            for change in due:
                self._transition(key, guest, change["mode"], t_s, entered, transitions, "scheduled")
            mode = manifest.mode(guest["mode"])
            while (
                execution is None and mode.duration_min is not None and mode.next_mode is not None
            ):
                boundary = entered[key] + mode.duration_min * 60
                if boundary > t_s:
                    break
                self._transition(
                    key, guest, mode.next_mode, boundary, entered, transitions, "duration"
                )
                mode = manifest.mode(guest["mode"])
            guest["measured_kw"] = self._profiles.get(key, Profile()).measure(
                mode.power_kw, t_s, rng
            )
        candidate = WorldSnapshot.model_validate(data)
        decisions = []
        status = "no_callback"
        if self._decide is not None:
            try:
                decisions = list(
                    self._decide(
                        candidate.model_copy(deep=True), deepcopy(tuple(batch)), deepcopy(context)
                    )
                )
                status = "available"
            except NotImplementedError:
                decisions = []
                status = "engine_not_implemented"
        serialized = []
        for decision in decisions:
            if not isinstance(decision, Decision):
                raise TypeError("decide callback must return only shared Decision objects")
            serialized.append(decision.model_dump(mode="json", exclude={"latency_ms"}))
        if execution is not None:
            execution.accept(decisions, data, context)
            # Newly chosen zero-offset work may complete at this same tick.
            execution.advance(data, context, entered, transitions)
            context = replace(
                context,
                protected=execution.protected,
                recovery_confirm=execution.status["crew_gate"]["confirm"],
            )
            for key, guest in data["guests"].items():
                if guest["mode"] != candidate.guests[key].mode:
                    guest["measured_kw"] = context.manifests[key].mode(guest["mode"]).power_kw
            candidate = WorldSnapshot.model_validate(data)
        # Serialization must succeed before any simulator state (including RNG) is committed.
        record = {
            "type": "tick",
            "snapshot": candidate.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in batch],
            "transitions": transitions,
            "decisions": serialized,
            "decision_status": status,
        }
        if execution is not None:
            record["execution"] = execution.status
        if injected:
            record["injected_events"] = [e.model_dump(mode="json") for e in injected]
        self._append(record)
        self._execution = execution
        self._snapshot = candidate
        self._context = context
        self._entered = entered
        self._cursor = cursor
        self._rng.setstate(rng.getstate())
        self._next_s += 1
        return self.snapshot

    @staticmethod
    def _transition(key, guest, mode, t_s, entered, transitions, source):
        transitions.append(
            {"guest_id": key, "from": guest["mode"], "to": mode, "t_s": t_s, "source": source}
        )
        guest.update(mode=mode, activity=None, activity_started_s=None)
        entered[key] = t_s

    @staticmethod
    def _apply(event: Event, data: dict, context: Context) -> Context:
        payload = event.payload
        if event.kind == "genset_trip":
            count = payload["gensets_online"]
            if count >= data["platform"]["gensets_online"]:
                raise ValueError("genset_trip must reduce online generators")
            if any(count not in values for values in context.ship.power.reserve_kw.values()):
                raise ValueError("genset_trip count is not represented in ship reserves")
            data["platform"]["gensets_online"] = count
        elif event.kind == "cooling_loss":
            data["platform"]["cooling_degraded"] = True
        elif event.kind == "battery_report":
            key = payload["guest_id"]
            if key not in data["guests"] or context.manifests[key].battery is None:
                raise ValueError(f"battery_report requires installed battery-equipped guest: {key}")
            data["guests"][key]["battery_pct"] = payload["battery_pct"]
        elif event.kind == "crew_change":
            if payload["crew"] not in context.ship.crew_levels:
                raise ValueError("crew_change must use a declared ship crew level")
            data["crew"] = payload["crew"]
        elif event.kind == "card_presented":
            card = GuestManifest.model_validate(payload["card"])
            context.cards[card.id] = card
        elif event.kind == "priority_swap":
            context = replace(
                context, priorities=PriorityFile.model_validate(payload["priorities"])
            )
        return context

    def run(self, ticks: int) -> WorldSnapshot:
        """Run quickly without sleeping; ticks=1 emits the initial second."""
        if type(ticks) is not int or ticks < 0:
            raise ValueError("ticks must be a nonnegative integer")
        for _ in range(ticks):
            self.step()
        return self.snapshot


def load_scenario(
    path: Path,
    *,
    decide: Decide | None = None,
    environment: EnvironmentInput | None = None,
    execution: bool = False,
) -> Runner:
    """Load a scenario's explicit snapshot, seed, profiles and sibling timeline YAML."""
    from shipos.sim.timeline import load_timeline

    data = load_yaml(path)
    if set(data) != {"initial", "priorities", "timeline", "seed", "profiles"}:
        raise ValueError("scenario requires initial, priorities, timeline, seed, profiles")
    return Runner(
        WorldSnapshot.model_validate(data["initial"]),
        ship=load_ship(),
        manifests=load_guests(),
        priorities=load_priorities(data["priorities"]),
        timeline=load_timeline(path.parent / data["timeline"]),
        seed=data["seed"],
        profiles={k: Profile.model_validate(v) for k, v in data["profiles"].items()},
        decide=decide,
        environment=environment,
        execution=execution,
    )


def replay_jsonl(data: bytes, *, decide: Decide | None = None) -> Runner:
    """Re-run embedded inputs and demand byte-identical output; inject the same pure callback."""
    import json

    verify_jsonl(data)
    lines = data.splitlines()
    header = json.loads(lines[0])["record"]
    runner = _from_header(header, decide)
    for line in lines[1:]:
        record = json.loads(line)["record"]
        runner.step(Event.model_validate(e) for e in record.get("injected_events", ()))
    if runner.jsonl != data:
        raise ValueError("replay differs: callback or simulator implementation changed")
    return runner


def _from_header(header: dict, decide: Decide | None) -> Runner:
    if header["type"] != "initial" or header["version"] not in (1, 2, 3):
        raise ValueError("unsupported replay header")
    if header["version"] == 3 and header.get("execution") is not True:
        raise ValueError("replay version 3 requires explicit execution=True")
    if header["version"] != 3 and "execution" in header:
        raise ValueError("execution requires replay version 3")
    environment = None
    if "environment" in header and header["version"] in (2, 3):
        environment = EnvironmentInput.from_record(header.get("environment"))
    elif "environment" in header:
        raise ValueError("captured environment requires replay version 2")
    return Runner(
        WorldSnapshot.model_validate(header["initial"]),
        ship=ShipSpec.model_validate(header["ship"]),
        manifests={k: GuestManifest.model_validate(v) for k, v in header["manifests"].items()},
        priorities=PriorityFile.model_validate(header["priorities"]),
        timeline=[Event.model_validate(e) for e in header["timeline"]],
        seed=header["seed"],
        profiles={k: Profile.model_validate(v) for k, v in header["profiles"].items()},
        decide=decide,
        environment=environment,
        execution=header.get("execution", False),
    )
