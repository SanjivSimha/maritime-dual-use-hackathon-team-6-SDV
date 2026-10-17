"""Controlled synthetic D4 execution evidence using unchanged cards/planner/Runner."""

import json
import subprocess
import sys
from itertools import combinations
from pathlib import Path
from time import perf_counter

import yaml

from shipos.assumptions import A
from shipos.contracts import Corner, Event, WorldSnapshot
from shipos.data import load_guests, load_priorities, load_ship
from shipos.engine import shedding
from shipos.engine.kernel import Config, GuestConfig, Kernel
from shipos.paths import REPO_DIR
from shipos.physics import power
from shipos.sim.log import verify_jsonl
from shipos.sim.runner import Runner, replay_jsonl

INPUT = Path(__file__).parent / "inputs/d4_controlled_charging.yaml"


def callback(protected):
    """Pure real-engine callback; explicit policy is exported separately from telemetry."""
    protected = frozenset(protected)

    def decide(snapshot, events, context):
        for event in events:
            if event.kind == "genset_trip":
                yield shedding.plan(
                    context.ship,
                    context.manifests,
                    snapshot,
                    context.priorities,
                    protected=protected | context.protected,
                )

    return decide


def physical_checks(kernel, snap):
    """Check exact active modes, not an invented maximum-overlap configuration."""
    active = [(gid, rt.mode) for gid, rt in snap.guests.items()]
    assert all(kernel.compatible(a, b) for a, b in combinations(active, 2))
    for gid, rt in snap.guests.items():
        mode = kernel.guests[gid].mode(rt.mode)
        assert rt.measured_kw is None or rt.measured_kw == mode.power_kw
        assert not mode.deck_machinery
        conditions = mode.conditions
        if conditions:
            for field in ("course", "noise", "deck", "emission"):
                required = getattr(conditions, field)
                assert required is None or getattr(snap.ship, field) == required
            assert conditions.speed_kn is None or conditions.speed_kn.contains(snap.ship.speed_kn)
            assert not conditions.uses_flight_deck and not conditions.uses_ramp
    assert not snap.ship.deck_machinery_active
    assert not snap.ship.flight_deck_in_use and not snap.ship.ramp_in_use
    corner = Corner(
        speed_band=power.speed_band(kernel.ship, snap.ship.speed_kn),
        seawater_c=snap.env.seawater_c,
        gensets_online=snap.platform.gensets_online,
        cooling_degraded=snap.platform.cooling_degraded,
    )
    config = Config(
        tuple(GuestConfig(gid, (rt.mode,), rt.placement) for gid, rt in snap.guests.items())
    )
    return kernel.evaluate(config, corner)


def run_proof(output, *, protected=None):
    """Write canonical execution + explicit callback policy, with separate wall telemetry."""
    case = yaml.safe_load(INPUT.read_text(encoding="utf-8"))
    protected = frozenset(case["protected"] if protected is None else protected)
    decide = callback(protected)
    ship, cards = load_ship(), load_guests()
    kernel = Kernel(ship, cards)
    casualty = Event.model_validate(case["casualty"])
    sim = Runner(
        WorldSnapshot.model_validate(case["initial"]),
        ship=ship,
        manifests=cards,
        priorities=load_priorities(case["priority"]),
        seed=case["seed"],
        timeline=[casualty],
        decide=decide,
        execution=True,
    )
    initial = physical_checks(kernel, sim.snapshot)
    assert all(r.ok for r in initial if r.hard)
    assert not cards["G3"].mode("charging").interruptible
    end_s = int(casualty.t_s + A("shed.horizon_min") * A("time.s_per_min"))
    first_wall = complete_wall = trigger_wall = decision_wall = None
    decision = None
    tasks, applied, horizon_failures = {}, [], []
    restored = None
    worst = {}
    for _ in range(end_s + 1):
        if sim.next_t_s == casualty.t_s:
            trigger_wall = perf_counter()
        sim.step()
        committed_wall = perf_counter()
        snap = sim.snapshot
        results = physical_checks(kernel, snap)
        failures = [r.resource for r in results if r.hard and not r.ok]
        if snap.t_s == casualty.t_s:
            decision_wall = committed_wall - trigger_wall
            record = json.loads(sim.jsonl.splitlines()[-1])["record"]
            (decision,) = record["decisions"]
            assert decision["verdict"] == "shed" and decision["chosen"]["feasible"]
            tasks = {t["id"]: t for t in sim.execution_status["pending"] if t["kind"] == "action"}
            assert len(tasks) == len(decision["chosen"]["actions"])
        for item in sim.execution_status["applied"]:
            if item["task"] in tasks and item not in applied:
                assert item["t_s"] == tasks[item["task"]]["due_s"]
                applied.append(item)
                if first_wall is None:
                    first_wall = committed_wall - trigger_wall
        if tasks and len(applied) == len(tasks) and complete_wall is None:
            complete_wall = committed_wall - trigger_wall
            restored = snap
        if complete_wall is None and snap.t_s >= casualty.t_s:
            assert failures == ["power.total"]
            # The temporary sustained deficit stays below the declared peak reserve.
            total = next(r for r in results if r.resource == "power.total")
            assert total.demand <= total.capacity + power.peak_extra_kw(ship, 1)
        else:
            horizon_failures.extend((snap.t_s, resource) for resource in failures)
        if complete_wall is not None:
            for r in results:
                if r.hard:
                    worst[r.resource] = min(worst.get(r.resource, float("inf")), r.margin)
        assert snap.guests["G3"].mode == "charging"  # non-interruptible throughout this horizon
    assert not horizon_failures and not sim.execution_status["blocked"]
    restored_plan = shedding.plan(
        ship, cards, restored, sim.context.priorities, protected=protected
    )
    assert restored_plan.verdict == "no_action"
    assert all(r.ok for r in restored_plan.checks if r.hard)
    assert len(applied) == len(tasks) > 0
    first_s, complete_s = min(a["t_s"] for a in applied), max(a["t_s"] for a in applied)
    assert first_s - casualty.t_s <= ship.timing.shed_first_s
    assert complete_s - casualty.t_s <= ship.timing.shed_full_s
    assert first_s - casualty.t_s <= ship.power.peak_window_s
    assert all(t["action"]["guest"] not in protected | {"G3"} for t in tasks.values())
    actions = [t["action"] for t in tasks.values()]
    records = [json.loads(line)["record"] for line in sim.jsonl.splitlines()[1:]]
    changes = [t for record in records for t in record["transitions"]]
    chosen_changes = [t for t in changes if t["source"] == "chosen_action"]
    assert [(t["guest_id"], t["to"]) for t in chosen_changes] == [
        (a["guest"], a["to_mode"]) for a in actions
    ]
    assert all(t["t_s"] in [a["t_s"] for a in applied] for t in chosen_changes)
    assert verify_jsonl(sim.jsonl, expected_hash=sim.final_hash) == sim.final_hash
    replay = replay_jsonl(sim.jsonl, decide=decide)
    assert replay.jsonl == sim.jsonl and replay.execution_status == sim.execution_status
    report = {
        "controlled_synthetic": True,
        "protected_policy": sorted(protected),
        "casualty_s": casualty.t_s,
        "decision_s": decision["t_s"],
        "first_applied_s": first_s,
        "sequence_completed_s": complete_s,
        "horizon_end_s": end_s,
        "actions": actions,
        "applied": applied,
        "chosen_changes": chosen_changes,
        "all_changes": changes,
        "initial_checks": [r.model_dump(mode="json") for r in initial],
        "minimum_post_correction_hard_margins": worst,
        "final_hash": sim.final_hash,
        "byte_identical_replay": True,
        "wall_unpaced_s": {
            "decision_tick": decision_wall,
            "first_commit": first_wall,
            "sequence_commit": complete_wall,
        },
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "execution.jsonl").write_bytes(sim.jsonl)
    (output / "callback-policy.json").write_text(
        json.dumps({"protected": sorted(protected)}), encoding="utf-8"
    )
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def test_real_d4_timing_independent_processes_and_replay(tmp_path):
    reports, logs = [], []
    for i in range(2):
        output = tmp_path / str(i)
        code = (
            "import runpy,sys; from pathlib import Path; "
            "m=runpy.run_path('tests/sim/test_d4_execution_evidence.py'); "
            "m['run_proof'](Path(sys.argv[1]))"
        )
        subprocess.run([sys.executable, "-c", code, str(output)], cwd=REPO_DIR, check=True)
        reports.append(json.loads((output / "report.json").read_text(encoding="utf-8")))
        logs.append((output / "execution.jsonl").read_bytes())
    assert logs[0] == logs[1]
    assert reports[0]["final_hash"] == reports[1]["final_hash"]
    assert [(a["guest"], a["kind"], a["to_mode"]) for a in reports[0]["actions"]] == [
        ("G5", "throttle", "low_power")
    ]
    for output in (tmp_path / "0", tmp_path / "1"):
        policy = json.loads((output / "callback-policy.json").read_text(encoding="utf-8"))
        assert (
            replay_jsonl(
                (output / "execution.jsonl").read_bytes(), decide=callback(policy["protected"])
            ).jsonl
            == logs[0]
        )


def test_explicit_protection_changes_real_selection_without_interrupting_charge(tmp_path):
    report = run_proof(tmp_path, protected={"G5"})
    assert [(a["guest"], a["kind"], a["to_mode"]) for a in report["actions"]] == [
        ("G4", "shed", "off")
    ]
