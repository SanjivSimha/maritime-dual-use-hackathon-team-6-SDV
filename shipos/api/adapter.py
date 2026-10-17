"""Public Runner adapter and a versioned, pure callback shared by execution/replay."""

import json
from copy import deepcopy

from shipos.contracts import Decision, Envelope, Event, GuestManifest, WorldSnapshot
from shipos.data import load_envelope, load_guests, load_priorities, load_ship
from shipos.engine import admission, arbitration, shedding
from shipos.paths import DATA_DIR, REPO_DIR
from shipos.sim.log import verify_jsonl
from shipos.sim.runner import Runner, load_scenario, replay_jsonl
from shipos.validation import guest_errors

FIXTURES = REPO_DIR / "tests" / "fixtures"
CALLBACK = "shipos.api.admission-events/v1"
HORIZON_S = 720
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
    errors = guest_errors(card, {**catalogue, card.id: card})
    if errors:
        raise ValueError("; ".join(errors))
    return card


def callback(envelope: Envelope):
    # The runner header captures ship/manifests/priorities, but not this envelope.
    envelope = envelope.model_copy(deep=True)

    def decide(snapshot, events, context):
        cards = [
            GuestManifest.model_validate(e.payload["card"])
            for e in events
            if e.kind == "card_presented"
        ]
        if not cards and any(e.kind in ("priority_swap", "crew_change") for e in events):
            cards = list(context.cards.values())
        decisions = [
            admission.decide(
                context.ship, context.manifests, snapshot, card, context.priorities, envelope
            )
            for card in cards
        ]
        for event in events:
            if event.kind == "battery_report":
                decisions.append(
                    arbitration.decide(
                        context.ship,
                        context.manifests,
                        snapshot,
                        context.priorities,
                        event.payload["guest_id"],
                        "recovery",
                    )
                )
            elif event.kind in ("genset_trip", "cooling_loss"):
                decisions.append(
                    shedding.plan(context.ship, context.manifests, snapshot, context.priorities)
                )
        # NotImplementedError propagates to Runner, which records batch unavailability.
        return decisions

    return decide


class Simulation:
    def __init__(self, scenario="A"):
        self.envelope = load_envelope().model_copy(deep=True)
        decide = callback(self.envelope)
        if scenario == "B":
            self.runner = load_scenario(DATA_DIR / "scenarios" / "B.yaml", decide=decide)
        else:
            initial = WorldSnapshot.model_validate_json(
                (FIXTURES / "snapshot_scenario_a_port.json").read_text()
            )
            self.runner = Runner(
                initial,
                ship=load_ship(),
                manifests=load_guests(),
                priorities=load_priorities("survey_primary"),
                seed=0,
                decide=decide,
            )

    def advance(self, ticks=1, inputs=()):
        if not 1 <= ticks <= MAX_TICKS:
            raise ValueError(f"Use 1–{MAX_TICKS} ticks per command")
        if self.runner.next_t_s + ticks - 1 > HORIZON_S:
            raise ValueError(
                "Demo horizon ends at 720 s; operational prerequisites are unavailable"
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
