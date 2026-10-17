"""Reachable protected resumes: actual D3 decisions over labelled synthetic card variants."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from shipos.contracts import Event, GuestManifest, WorldSnapshot
from shipos.engine import arbitration
from shipos.paths import DATA_DIR
from shipos.sim.env_input import EnvironmentInput
from shipos.sim.runner import Runner, load_scenario, replay_jsonl
from tests.sim.test_execution import state


def decide(snapshot, events, context):
    for event in events:
        if event.kind == "battery_report":
            yield arbitration.decide(
                context.ship,
                context.manifests,
                snapshot,
                context.priorities,
                "G4",
                "launch" if event.t_s == 0 else "recovery",
                confirm=context.recovery_confirm,
            )


def case(
    *,
    external_mode=None,
    machinery=False,
    target_conditions=None,
    environment=None,
    callback=decide,
):
    """Synthetic installed G4 variant, not baseline B or fabricated engine decisions.

    Launch is compatible with vehicle hold. This variant's 15-minute recovery has
    an explicit hold interlock and misses the later battery deadline, so real D3
    protects the held incumbent after previously approving its pause/resume.
    """
    baseline = load_scenario(DATA_DIR / "scenarios/B.yaml")
    context = baseline.context
    raw = context.manifests["G4"].model_dump(mode="json")
    next(a for a in raw["activities"] if a["name"] == "recovery")["duration_min"] = 15
    raw["excludes"].append(
        {
            "kind": "conditional",
            "mode": "recovery",
            "target": "G3",
            "target_mode": "holding",
            "reason": "Synthetic recovery/hold interlock",
        }
    )
    context.manifests["G4"] = GuestManifest.model_validate(raw)
    data = baseline.snapshot.model_dump()
    data["guests"]["G4"]["mode"] = "standby"
    if external_mode:
        data["guests"]["G3"]["schedule"] = [{"t_s": 1201, "mode": external_mode}]
    if machinery:
        raw = context.manifests["G5"].model_dump(mode="json")
        next(m for m in raw["modes"] if m["name"] == "low_power")["deck_machinery"] = True
        context.manifests["G5"] = GuestManifest.model_validate(raw)
        data["guests"]["G5"]["schedule"] = [
            {"t_s": 1000, "mode": "low_power"},
            {"t_s": 1201, "mode": "on"},
        ]
    if target_conditions:
        raw = context.manifests["G3"].model_dump(mode="json")
        next(m for m in raw["modes"] if m["name"] == "surveying")["conditions"].update(
            target_conditions
        )
        context.manifests["G3"] = GuestManifest.model_validate(raw)
    timeline = [
        Event(t_s=0, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 30}),
        Event(t_s=360, kind="operator_confirmation", payload={"by": "synthetic test officer"}),
        Event(t_s=600, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 18}),
    ]
    return Runner(
        WorldSnapshot.model_validate(data),
        ship=context.ship,
        manifests=context.manifests,
        priorities=context.priorities,
        timeline=timeline,
        decide=callback,
        execution=True,
        environment=environment,
    )


def records(sim):
    return [json.loads(line)["record"] for line in sim.jsonl.splitlines()[1:]]


def test_real_d3_internal_resume_is_reachable_and_preserves_protection():
    sim = case()
    sim.run(601)
    decisions = [d for r in records(sim) for d in r["decisions"]]
    assert len(decisions) == 2
    assert decisions[0]["winner"] == "G4"
    assert decisions[0]["chosen"]["actions"][0]["kind"] == "pause"
    assert decisions[1]["winner"] == "G3"
    assert decisions[1]["chosen"]["actions"][1]["kind"] == "deny"
    assert sim.snapshot.guests["G4"].mode == "flying"
    assert sim.context.protected == frozenset({"G3"})
    root = next(t for t in sim.execution_status["pending"] if t["kind"] == "resume")
    assert root["due_s"] == 900
    receipt = sim.execution_status["resume_approvals"][str(root["id"])]
    assert receipt["version"] == 1 and receipt["hold_entered_s"] == 60
    assert receipt["mode"] == "surveying" and receipt["activity"] == "survey_leg"
    assert any(a["task"] == receipt["origin_task"] for a in sim.execution_status["applied"])
    sim.run(300)
    assert sim.snapshot.guests["G3"].mode == "holding"
    child = next(t for t in sim.execution_status["pending"] if t["kind"] == "mode")
    assert child["due_s"] == 1200 and child["source"] == "declared_resume"
    assert sim.execution_status["resume_approvals"] == {str(child["id"]): receipt}
    sim.run(299)
    assert sim.snapshot.guests["G3"].mode == "holding"  # full preparation still required
    before = sim.snapshot
    sim.step()
    assert sim.snapshot.guests["G3"].mode == "surveying"
    assert sim.snapshot.guests["G3"].activity == "survey_leg"
    assert sim.snapshot.guests["G3"].activity_started_s == 1200  # declared rerun
    assert before.guests["G3"].mode == "holding"
    assert sim.context.protected == frozenset({"G3"})  # no new release policy
    replayed = replay_jsonl(sim.jsonl, decide=decide)
    assert replayed.jsonl == sim.jsonl and replayed.execution_status == sim.execution_status
    assert "resume_approvals" not in sim.execution_status
    sim.reset()
    assert "resume_approvals" not in sim.execution_status and not sim.context.protected


def test_conflicting_external_schedule_remains_blocked_after_internal_resume():
    sim = case(external_mode="standby")
    sim.run(1202)
    assert sim.snapshot.guests["G3"].mode == "surveying"
    assert sim.context.protected == frozenset({"G3"})
    assert any(
        b["code"] == "protected_winner" and b.get("mode") == "standby"
        for b in sim.execution_status["blocked"]
    )
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


def test_approved_resume_still_waits_for_active_interlock():
    sim = case(machinery=True)
    sim.run(1201)
    assert sim.snapshot.guests["G3"].mode == "holding"
    assert sim.snapshot.guests["G5"].mode == "low_power"
    assert any("deck_machinery" in b["code"] for b in sim.execution_status["blocked"])
    sim.step()  # the declared external machinery activity ends
    assert sim.snapshot.guests["G3"].mode == "surveying"
    assert sim.context.protected == frozenset({"G3"})
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


def test_approved_resume_does_not_bypass_crew_bound_preparation():
    sim = case()
    sim.run(1000)
    sim.step([Event(t_s=1000, kind="crew_change", payload={"crew": 6})])
    sim.run(201)
    assert sim.snapshot.guests["G3"].mode == "holding"
    assert any(b["code"] == "crew_changed_replan_required" for b in sim.execution_status["blocked"])
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


@pytest.fixture(scope="module")
def awaiting_internal_resume():
    sim = case()
    sim.run(901)
    return sim


@pytest.mark.parametrize(
    "tamper, expected",
    [
        ("source_only", "protected_winner"),
        ("payload", "approved_resume_payload_changed"),
        ("manifest", "approved_resume_manifest_changed"),
        ("time", "approved_resume_not_due"),
        ("mode", "approved_resume_episode_changed"),
        ("episode", "approved_resume_episode_changed"),
    ],
)
def test_isolated_guard_rejects_spoofed_or_stale_approval(
    awaiting_internal_resume, tamper, expected
):
    # Negative guard isolation only. The positive case above reaches this state
    # using real decisions and an internally generated resume, never injected tasks.
    sim = awaiting_internal_resume
    before = state(sim)
    controller = deepcopy(sim._execution)
    task = deepcopy(next(t for t in controller.pending if t["kind"] == "mode"))
    data = sim.snapshot.model_dump()
    context = sim.context
    entered = sim._entered.copy()
    if tamper == "source_only":
        task["id"] = controller.sequence  # no receipt for this external task ID
        task["source"] = "declared_resume"  # a convenient label grants no permission
    elif tamper == "payload":
        task["mode"] = "standby"
    elif tamper == "manifest":
        context.manifests["G3"] = context.manifests["G3"].model_copy(update={"name": "changed"})
    elif tamper == "time":
        data["t_s"] = 899
    elif tamper == "mode":
        data["guests"]["G3"]["mode"] = "standby"
    else:
        entered["G3"] += 1  # another entry into the same named hold is not this episode
    assert controller.execute(task, data, context, entered, []) == expected
    assert state(sim) == before


def test_approved_resume_weather_is_still_checked():
    # Explicit synthetic hourly cache; no provider forecast or measurement claim.
    document = json.loads((DATA_DIR / "env_cache/strait_of_sicily.json").read_text())
    document["kind"] = "synthetic_demo"
    document["notes"] = "Synthetic protected-resume weather boundary."
    for source in document["sources"].values():
        source["url"] = source["retrieved_at"] = None
    start = datetime(2026, 10, 17, 8, tzinfo=UTC)
    stamps = [int(start.timestamp()) + i * 3600 for i in range(3)]
    document["sources"]["marine"]["response"]["hourly"] = {
        "time": stamps,
        "wave_height": [0.5] * 3,
        "sea_surface_temperature": [21] * 3,
    }
    document["sources"]["weather"]["response"]["hourly"] = {
        "time": stamps.copy(),
        "wind_speed_10m": [22, 30, 22],
    }
    weather = EnvironmentInput(
        document, scenario_start=start.replace(minute=45), layered_water=False
    )
    sim = case(target_conditions={"wind_max_kn": 25}, environment=weather)
    sim.run(901)
    assert sim.snapshot.guests["G3"].mode == "holding"
    assert sim.snapshot.env.wind_kn == 30
    assert any("wind 30" in b["code"] for b in sim.execution_status["blocked"])
    assert next(t for t in sim.execution_status["pending"] if t["kind"] == "resume")
    assert sim.context.protected == frozenset({"G3"})
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


def test_callback_failure_rolls_back_resume_receipt_and_applied_state():
    fail = True

    def callback(snapshot, events, context):
        if snapshot.t_s == 1200 and fail:
            raise RuntimeError("controlled callback failure after resume")
        return list(decide(snapshot, events, context))

    sim = case(callback=callback)
    sim.run(1200)
    before = state(sim)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="after resume"):
            sim.step()
        assert state(sim) == before
    fail = False
    sim.step()
    assert sim.snapshot.guests["G3"].mode == "surveying"
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl
