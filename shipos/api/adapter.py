"""Public Runner adapter and a versioned, pure callback shared by execution/replay."""

import json
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from shipos.contracts import Decision, Envelope, Event, GuestManifest, WorldSnapshot
from shipos.crew import lifecycle
from shipos.crew.policy import HumanConfirmation
from shipos.data import load_envelope, load_guests, load_priorities, load_ship
from shipos.engine import admission, arbitration, shedding
from shipos.paths import DATA_DIR, REPO_DIR
from shipos.sim.consumption import Profile
from shipos.sim.env import find_installation_windows
from shipos.sim.env_input import EnvironmentInput
from shipos.sim.log import verify_jsonl
from shipos.sim.runner import Runner, load_scenario, replay_jsonl
from shipos.validation import guest_errors

FIXTURES = REPO_DIR / "tests" / "fixtures"
CALLBACK = "shipos.api.admission-events/v1"
HORIZON_S = 720  # observation: the Scenario B baseline timeline ends at its t=720 s trip
# Execution runs past the baseline so a chosen plan can complete: in Scenario B the battery
# report at t=480 anchors a landing deadline at t=1200 s and, with 14 crew, preparation is
# ready at t=840, recovery starts on an operator confirmation at 841 and ends at 1021
# (docs/SIM_EXECUTION.md). 1800 s keeps that deadline plus ten minutes of follow-on activity.
EXECUTION_HORIZON_S = 1800
MAX_TICKS = 120


def parse_card(text: str, catalogue: dict[str, GuestManifest]) -> GuestManifest:
    def reject_constant(value):
        raise ValueError(f"Nonfinite number {value} is not supported")

    def check_refs(value):
        if isinstance(value, str) and value.startswith("$"):
            raise ValueError("Supply resolved JSON values; assumption references are not accepted")
        if isinstance(value, dict):
            for item in value.values():
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    raw = json.loads(text, parse_constant=reject_constant)
    check_refs(raw)
    json.dumps(raw, allow_nan=False)  # Also rejects overflow such as 1e999.
    card = GuestManifest.model_validate(raw)
    json.dumps(card.model_dump(mode="json"), allow_nan=False)  # Includes coerced numeric strings.
    if not card.id.strip():
        raise ValueError("A nonempty guest ID is required")
    errors = guest_errors(card, {**catalogue, card.id: card})
    if errors:
        raise ValueError("; ".join(errors))
    return card


# A.8 ceiling each decision kind is judged against (physics.timing names).
LATENCY_KIND = {"admission": "admission", "arbitration": "arbitration", "shedding": "shed_first"}


def measure(engine_call, *args, **kwargs) -> Decision:
    """Run one real engine call and stamp its wall-clock time on the Decision.

    The clock is read here, outside the pure engine. `latency_ms` is excluded from the
    hash-chained log by the Runner, so measuring never changes replay bytes.
    """
    started = time.perf_counter()
    decision = engine_call(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return decision.model_copy(update={"latency_ms": elapsed_ms})


def latency_record(decision: Decision) -> dict | None:
    """Response time for the TIME gauge: engine time, plus the scheduled first-shed offset."""
    if decision.latency_ms is None or decision.kind.value not in LATENCY_KIND:
        return None
    seconds = decision.latency_ms / 1000
    offset = 0.0
    if decision.kind.value == "shedding" and decision.chosen and decision.chosen.actions:
        offset = min(a.t_offset_s or 0.0 for a in decision.chosen.actions)
    return {
        "kind": LATENCY_KIND[decision.kind.value],
        "decision": decision.kind.value,
        "subject": decision.subject,
        "engine_ms": round(decision.latency_ms, 3),
        "scheduled_offset_s": offset,
        "seconds": seconds + offset,
        "t_s": decision.t_s,
    }


class EngineCallback:
    """The Runner's decide callback; remembers the measured time of its latest decision."""

    def __init__(self, envelope: Envelope):
        # The runner header captures ship/manifests/priorities, but not this envelope.
        self.envelope = envelope.model_copy(deep=True)
        self.latest: dict | None = None
        self.count = 0

    def __call__(self, snapshot, events, context):
        envelope = self.envelope
        cards = [
            GuestManifest.model_validate(e.payload["card"])
            for e in events
            if e.kind == "card_presented"
        ]
        if not cards and any(e.kind in ("priority_swap", "crew_change") for e in events):
            cards = list(context.cards.values())
        decisions = [
            measure(
                admission.decide,
                context.ship,
                context.manifests,
                snapshot,
                card,
                context.priorities,
                envelope,
            )
            for card in cards
        ]
        for event in events:
            if event.kind == "battery_report":
                decisions.append(
                    measure(
                        arbitration.decide,
                        context.ship,
                        context.manifests,
                        snapshot,
                        context.priorities,
                        event.payload["guest_id"],
                        "recovery",
                        confirm=getattr(context, "recovery_confirm", "human"),
                    )
                )
            elif event.kind in ("genset_trip", "cooling_loss"):
                decisions.append(
                    measure(
                        shedding.plan,
                        context.ship,
                        context.manifests,
                        snapshot,
                        context.priorities,
                        protected=getattr(context, "protected", frozenset()),
                    )
                )
        # NotImplementedError propagates to Runner, which records batch unavailability.
        for decision in decisions:
            record = latency_record(decision)
            if record is not None:
                self.latest, self.count = record, self.count + 1
        return decisions


def callback(envelope: Envelope) -> EngineCallback:
    return EngineCallback(envelope)


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = "scenario"
    scenario_start: datetime | None = None
    layered_water: StrictBool | None = None
    timezone_offset_min: int | None = Field(default=None, ge=-840, le=840, strict=True)
    allow_stale: StrictBool = False

    @model_validator(mode="after")
    def validate_source(self):
        if self.source not in ("scenario", "strait_of_sicily"):
            raise ValueError("Select an allowed cached source; paths are not accepted")
        if self.source == "scenario":
            if (
                any(
                    v is not None
                    for v in (self.scenario_start, self.layered_water, self.timezone_offset_min)
                )
                or self.allow_stale
            ):
                raise ValueError("Scenario source does not accept cache configuration")
        else:
            if (
                self.scenario_start is None
                or self.scenario_start.utcoffset() is None
                or self.layered_water is None
                or self.timezone_offset_min is None
            ):
                raise ValueError(
                    "Cache requires timezone-aware scenario_start, layering and timezone"
                )
        return self


class Simulation:
    def __init__(
        self, scenario="A", *, execution=False, environment=None, priority=None, crew=None
    ):
        self.envelope = load_envelope().model_copy(deep=True)
        self.execution = execution
        self.environment_config = environment or EnvironmentConfig()
        self.environment_input = None
        if self.environment_config.source != "scenario":
            self.environment_input = EnvironmentInput.from_cache(
                DATA_DIR / "env_cache" / "strait_of_sicily.json",
                scenario_start=self.environment_config.scenario_start,
                layered_water=self.environment_config.layered_water,
                allow_stale=self.environment_config.allow_stale,
            )
        if self.environment_input is not None:
            self.environment_input.env_at(0)  # Reject unusable initial cache coverage before reset.
        if execution and not hasattr(Runner, "execution_status"):
            raise ValueError(
                "Execution unavailable: school's public execution seam is not imported"
            )
        kwargs = {"environment": self.environment_input}
        if hasattr(Runner, "execution_status"):
            kwargs["execution"] = execution
        decide = self.decide = callback(self.envelope)
        if scenario == "B":
            self.runner = load_scenario(DATA_DIR / "scenarios" / "B.yaml", decide=decide, **kwargs)
        else:
            initial = WorldSnapshot.model_validate_json(
                (FIXTURES / "snapshot_scenario_a_port.json").read_text(encoding="utf-8")
            )
            self.runner = Runner(
                initial,
                ship=load_ship(),
                manifests=load_guests(),
                priorities=load_priorities("survey_primary"),
                seed=0,
                decide=decide,
                **kwargs,
            )
        # Reconstruct the baseline's public captured inputs, with explicit initial controls.
        # No private runner state is read, and no simulator mechanics are duplicated.
        if priority is not None or crew is not None:
            header = json.loads(self.runner.jsonl.splitlines()[0])["record"]
            context = self.runner.context
            initial = self.runner.snapshot
            if crew is not None:
                initial = initial.model_copy(update={"crew": crew})
            self.runner = Runner(
                initial,
                ship=context.ship,
                manifests=context.manifests,
                priorities=priority or context.priorities,
                timeline=[Event.model_validate(e) for e in header["timeline"]],
                seed=header["seed"],
                profiles={k: Profile.model_validate(v) for k, v in header["profiles"].items()},
                decide=decide,
                **kwargs,
            )

    def execution_view(self):
        status = self.runner.execution_status if hasattr(self.runner, "execution_status") else None
        return {"enabled": self.execution, "status": status}

    def crew_view(self):
        status = self.execution_view()["status"]
        if self.execution and status and status.get("crew_gate") is not None:
            result = deepcopy(status["crew_gate"])
            result["request_time_base"] = "snapshot_relative"
            return result
        evidence = lifecycle.CrewEvidence()
        records = [json.loads(line)["record"] for line in self.runner.jsonl.splitlines()]
        initial = WorldSnapshot.model_validate(records[0]["initial"])
        evidence = lifecycle.observe(evidence, initial)
        for record in records[1:]:
            snap = WorldSnapshot.model_validate(record["snapshot"])
            evidence = lifecycle.observe(evidence, snap)
            for event in record["events"]:
                if event["kind"] == "operator_confirmation":
                    evidence = lifecycle.confirm(
                        evidence, HumanConfirmation(t_s=event["t_s"], by=event["payload"]["by"])
                    )
        gate = lifecycle.gate(self.runner.context.ship, evidence, self.runner.snapshot)
        return {
            "permitted": gate.permitted,
            "confirm": gate.confirm,
            "confirmation": gate.confirmation,
            "blockers": list(gate.blockers),
            "requests": [r.model_dump(mode="json") for r in gate.requests],
            "request_time_base": "snapshot_relative",
        }

    def environment_view(self):
        result = {
            "configuration": self.environment_config.model_dump(mode="json"),
            "provenance": None,
            "windows": {},
            "eligibility_only": True,
        }
        if self.environment_input is not None:
            record = self.environment_input.record
            forecast = self.environment_input.forecast
            result["provenance"] = {
                "kind": forecast.kind,
                "stale": forecast.stale,
                "retrieved_at": forecast.retrieved_at.isoformat()
                if forecast.retrieved_at
                else None,
                "sources": {
                    k: {key: value for key, value in v.items() if key != "response"}
                    for k, v in record["cache"]["sources"].items()
                },
            }
            snap = self.runner.snapshot
            for operation in ("flight_deck", "ramp"):
                windows = find_installation_windows(
                    self.runner.context.ship,
                    forecast,
                    operation=operation,
                    crew=snap.crew,
                    speed_kn=snap.ship.speed_kn,
                    local_tz=timezone(
                        timedelta(minutes=self.environment_config.timezone_offset_min)
                    ),
                    start_s=snap.t_s,
                )
                result["windows"][operation] = [
                    {
                        "start_s": w.start_s,
                        "end_s": w.end_s,
                        "duration_s": w.duration_s,
                        "latest_start_s": w.latest_start_s,
                    }
                    for w in windows
                ]
        return result

    @property
    def horizon_s(self):
        return EXECUTION_HORIZON_S if self.execution else HORIZON_S

    def advance(self, ticks=1, inputs=()):
        if not 1 <= ticks <= MAX_TICKS:
            raise ValueError(f"Use 1-{MAX_TICKS} ticks per command")
        if self.runner.next_t_s + ticks - 1 > self.horizon_s:
            raise ValueError(
                f"The {'execution' if self.execution else 'observation'} run ends at "
                f"{self.horizon_s} s; start a fresh run to continue"
            )
        before = len(self.runner.jsonl.splitlines())
        events = [
            Event(t_s=self.runner.next_t_s, kind=kind, payload=deepcopy(payload))
            for kind, payload in inputs
        ]
        self.runner.step(events=events)
        self.runner.run(ticks - 1)
        return [json.loads(line)["record"] for line in self.runner.jsonl.splitlines()[before:]]

    def evidence(self):
        return {
            "runtime": {
                "execution": self.execution,
                "environment": self.environment_config.model_dump(mode="json"),
            },
            "callback": CALLBACK,
            "envelope": self.envelope.model_dump(mode="json"),
            "jsonl": self.runner.jsonl.decode("utf-8"),
            "final_hash": self.runner.final_hash,
        }


def replay_evidence(evidence: dict) -> dict:
    """Verify chain AND rerun records with exported inputs and the same callback.

    Requires this callback/engine/runtime implementation; code is not serialized.
    Scenario/envelope YAML is not reloaded. Engine assumption registry and code
    remain runtime prerequisites, so this is not an independently portable artifact.
    """
    if evidence.get("callback") != CALLBACK:
        raise ValueError("Unsupported callback implementation")
    envelope = Envelope.model_validate(evidence["envelope"])
    data = evidence["jsonl"].encode("utf-8")
    verify_jsonl(data, expected_hash=evidence["final_hash"])
    replayed = replay_jsonl(data, decide=callback(envelope))
    return {
        "byte_identical": replayed.jsonl == data,
        "final_hash": replayed.final_hash,
        "records": len(data.splitlines()),
        "snapshot": replayed.snapshot.model_dump(mode="json"),
    }


def preview(engine: str) -> Decision:
    name = {"arbitration": "decision_arbitration", "shedding": "decision_shedding"}[engine]
    return Decision.model_validate_json((FIXTURES / f"{name}.json").read_text())
