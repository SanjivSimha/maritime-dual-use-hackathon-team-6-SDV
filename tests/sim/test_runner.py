import json
import random
import subprocess
import sys

import pytest

from shipos.contracts import Decision, Event, ScheduledMode, WorldSnapshot
from shipos.data import load_envelope, load_guests, load_priorities, load_ship
from shipos.engine import admission, arbitration, shedding
from shipos.paths import DATA_DIR, REPO_DIR
from shipos.sim.consumption import Profile, Pulse
from shipos.sim.log import verify_jsonl
from shipos.sim.runner import Runner, load_scenario, replay_jsonl
from shipos.sim.timeline import load_timeline


@pytest.fixture
def initial():
    return load_scenario(DATA_DIR / "scenarios/B.yaml").snapshot


def runner(initial, **kwargs):
    return Runner(
        initial,
        ship=load_ship(),
        manifests=load_guests(),
        priorities=load_priorities("survey_primary"),
        **kwargs,
    )


def records(sim):
    return [json.loads(line)["record"] for line in sim.jsonl.splitlines()]


def test_independent_processes_identical_bytes_and_hash(tmp_path):
    code = (
        "from pathlib import Path; from shipos.sim.runner import load_scenario; "
        "r=load_scenario(Path('data/scenarios/B.yaml')); r.run(723); "
        "Path(__import__('sys').argv[1]).write_bytes(r.jsonl); print(r.final_hash)"
    )
    outputs = []
    for index in range(2):
        path = tmp_path / f"run{index}.jsonl"
        process = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            cwd=REPO_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append((path.read_bytes(), process.stdout.strip()))
    assert outputs[0] == outputs[1]
    data, digest = outputs[0]
    assert verify_jsonl(data, expected_hash=digest) == digest
    assert replay_jsonl(data).jsonl == data


def test_seed_changes_measurements_not_event_order(initial):
    events = [Event(t_s=1, kind="crew_change", payload={"crew": crew}) for crew in [6, 0, 14]]
    state = random.getstate()
    sims = [
        runner(initial, seed=seed, profiles={"G5": Profile(noise_fraction=0.1)}, timeline=events)
        for seed in [1, 2]
    ]
    for sim in sims:
        sim.run(3)
    assert random.getstate() == state
    assert sims[0].snapshot.guests["G5"].measured_kw != sims[1].snapshot.guests["G5"].measured_kw
    assert records(sims[0])[2]["events"] == records(sims[1])[2]["events"]
    assert [e["payload"]["crew"] for e in records(sims[0])[2]["events"]] == [6, 0, 14]
    assert sims[0].snapshot.crew == 14


def test_events_snapshots_and_callback_inputs(initial):
    guests = load_guests()
    card = guests["G2"].model_dump(mode="json")
    card["modes"][0]["power_kw"] += 7
    sar = load_priorities("sar_primary")
    events = [
        Event(t_s=0, kind="genset_trip", payload={"gensets_online": 1}),
        Event(t_s=0, kind="cooling_loss"),
        Event(t_s=0, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 11}),
        Event(t_s=0, kind="card_presented", payload={"card": card}),
        Event(t_s=0, kind="priority_swap", payload={"priorities": sar.model_dump(mode="json")}),
        Event(t_s=0, kind="crew_change", payload={"crew": 6}),
    ]
    seen = []

    def callback(snapshot, events, context):
        seen.append((snapshot, events, context))
        return []

    sim = runner(initial, timeline=events, decide=callback)
    snapshot = sim.step()
    assert snapshot.platform.gensets_online == 1
    assert snapshot.platform.cooling_degraded
    assert snapshot.guests["G4"].battery_pct == 11
    assert snapshot.crew == 6
    assert seen[0][2].priorities == sar
    assert seen[0][2].cards["G2"].modes[0].power_kw == guests["G2"].modes[0].power_kw + 7
    assert "G2" not in snapshot.guests  # presenting a card is not admission/installation
    assert [e.kind for e in seen[0][1]] == [e.kind for e in events]
    assert initial.platform.gensets_online == 2
    assert initial.guests["G4"].battery_pct == 30


@pytest.mark.parametrize(
    "event",
    [
        Event(t_s=0, kind="crew_change", payload={"crew": -1}),
        Event(t_s=0, kind="crew_change", payload={"crew": True}),
        Event(t_s=0, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 101}),
        Event(
            t_s=0, kind="battery_report", payload={"guest_id": "G4", "battery_pct": float("nan")}
        ),
        Event(t_s=0, kind="cooling_loss", payload={"unexpected": 1}),
        Event(t_s=0, kind="card_presented", payload={"card": {"id": "G6"}}),
        Event(t_s=0, kind="priority_swap", payload={"priorities": {}}),
        Event(t_s=0.5, kind="cooling_loss"),
        Event(t_s=0, kind="invented"),
    ],
)
def test_bad_payloads_rejected(initial, event):
    with pytest.raises(ValueError):
        runner(initial, timeline=[event])


@pytest.mark.parametrize(
    "bad",
    [
        Event(t_s=1, kind="battery_report", payload={"guest_id": "missing", "battery_pct": 20}),
        Event(t_s=1, kind="battery_report", payload={"guest_id": "G3", "battery_pct": 20}),
        Event(t_s=1, kind="crew_change", payload={"crew": 13}),
        Event(t_s=1, kind="genset_trip", payload={"gensets_online": 2}),
    ],
)
def test_invalid_later_event_rolls_back_entire_tick(initial, bad):
    sim = runner(
        initial,
        profiles={"G5": Profile(noise_fraction=0.1)},
        timeline=[Event(t_s=1, kind="cooling_loss"), bad],
    )
    sim.step()
    before = (sim.snapshot, sim.jsonl, sim.final_hash, sim._rng.getstate(), sim._next_s)
    for _ in range(2):
        with pytest.raises(ValueError):
            sim.step()
        assert (sim.snapshot, sim.jsonl, sim.final_hash, sim._rng.getstate(), sim._next_s) == before


def test_nested_containers_and_callbacks_are_isolated(initial):
    saved = initial.model_dump(mode="json")

    def hostile_callback(snapshot, events, context):
        snapshot.guests["G5"].placement.slots["body"] = "changed"
        context.manifests.clear()
        context.ship.crew_levels.clear()
        context.cards.clear()
        return []

    sim = runner(initial, decide=hostile_callback)
    first = sim.step()
    first_saved = first.model_dump(mode="json")
    second = sim.step()
    assert first.model_dump(mode="json") == first_saved
    second.guests["G5"].placement.slots.clear()
    sim.snapshot.guests.clear()
    sim.context.manifests.clear()
    assert sim.snapshot.guests["G5"].placement.slots["body"] == "bay2"
    assert initial.model_dump(mode="json") == saved


def test_mode_duration_and_scheduled_boundary(initial):
    data = initial.model_dump()
    data["guests"]["G3"].update(mode="standby", activity=None, activity_started_s=None)
    data["guests"]["G4"].update(
        mode="charging",
        schedule=[
            ScheduledMode(t_s=1200, mode="standby").model_dump(),
            ScheduledMode(t_s=1200, mode="launching").model_dump(),
        ],
    )
    sim = runner(WorldSnapshot.model_validate(data))
    sim.run(1200)
    before = sim.snapshot
    assert sim.snapshot.t_s == 1199
    assert sim.snapshot.guests["G4"].mode == "charging"
    snapshot = sim.step()
    assert snapshot.guests["G4"].mode == "launching"
    assert snapshot.guests["G4"].schedule == []
    assert len(before.guests["G4"].schedule) == 2
    changes = records(sim)[-1]["transitions"]
    assert [c["to"] for c in changes] == ["standby", "launching"]
    assert all(c["source"] == "scheduled" for c in changes)
    launch_s = load_guests()["G4"].mode("launching").duration_min * 60
    sim.run(int(launch_s))
    assert sim.snapshot.guests["G4"].mode == "flying"


def test_manifest_duration_chain_and_prior_snapshots(initial):
    data = initial.model_dump()
    data["t_s"] = 3299
    sim = runner(WorldSnapshot.model_validate(data))
    before = sim.step()
    assert before.guests["G3"].mode == "recovering"
    after = sim.step()
    assert after.guests["G3"].mode == "charging"
    assert after.guests["G3"].measured_kw == load_guests()["G3"].mode("charging").power_kw
    assert before.guests["G3"].mode == "recovering"
    assert records(sim)[1]["transitions"][0]["t_s"] == 2400
    assert records(sim)[2]["transitions"][0]["t_s"] == 3300


def test_scripted_overdraw_is_an_explicit_half_open_measurement_profile(initial):
    profile = Profile(pulses=[Pulse(start_s=1, end_s=3, multiplier=1.15)])
    sim = runner(initial, profiles={"G5": profile})
    values = [sim.step().guests["G5"].measured_kw for _ in range(4)]
    nominal = load_guests()["G5"].mode("on").power_kw
    assert values == [nominal, nominal * 1.15, nominal * 1.15, nominal]
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl


def test_changed_installed_manifest_changes_snapshot_measurement(initial):
    manifests = load_guests()
    original = runner(initial).step()
    card = manifests["G5"].model_dump()
    next(m for m in card["modes"] if m["name"] == "on")["power_kw"] += 1
    manifests["G5"] = type(manifests["G5"]).model_validate(card)
    sim = Runner(
        initial, ship=load_ship(), manifests=manifests, priorities=load_priorities("survey_primary")
    )
    assert sim.step().guests["G5"].measured_kw == original.guests["G5"].measured_kw + 1


def test_callback_failure_rolls_back_rng_events_and_context(initial):
    fail = True

    def callback(*args):
        if fail:
            raise RuntimeError("integration error")
        return []

    kwargs = dict(
        timeline=[Event(t_s=0, kind="crew_change", payload={"crew": 6})],
        profiles={"G5": Profile(noise_fraction=0.1)},
    )
    sim = runner(initial, decide=callback, **kwargs)
    before = sim.jsonl
    with pytest.raises(RuntimeError, match="integration error"):
        sim.step()
    assert sim.jsonl == before
    assert sim.snapshot == initial
    fail = False
    baseline = runner(initial, decide=lambda *args: [], **kwargs)
    assert sim.step() == baseline.step()
    assert sim.jsonl == baseline.jsonl


def test_log_detects_tampering_truncation_and_wrong_callback(initial):
    sim = runner(initial)
    sim.run(2)
    edited = sim.jsonl.replace(b'"crew":14', b'"crew":6')
    with pytest.raises(ValueError, match="integrity"):
        verify_jsonl(edited)
    with pytest.raises(ValueError, match="expected_hash"):
        verify_jsonl(sim.jsonl.splitlines(keepends=True)[0], expected_hash=sim.final_hash)
    with pytest.raises(ValueError):
        verify_jsonl(sim.jsonl.rstrip(b"\n"))
    with pytest.raises(ValueError, match="callback"):
        replay_jsonl(sim.jsonl, decide=lambda *args: [])


def test_latency_is_excluded_and_engine_unavailability_is_explicit(initial):
    decision = json.loads((REPO_DIR / "tests/fixtures/decision_arbitration.json").read_text())
    sims = []
    for latency in [1, 999]:

        def callback(*args, latency=latency):
            return [Decision.model_validate(dict(decision, latency_ms=latency))]

        sim = runner(initial, decide=callback)
        sim.step()
        sims.append(sim)
    assert sims[0].jsonl == sims[1].jsonl
    assert "latency_ms" not in records(sims[0])[-1]["decisions"][0]
    # D3 / D4 are real engines now: their Decisions are recorded and replay byte-identically.
    for call in [
        lambda snap, events, ctx: [
            arbitration.decide(ctx.ship, ctx.manifests, snap, ctx.priorities, "G4", "recovery")
        ],
        lambda snap, events, ctx: [shedding.plan(ctx.ship, ctx.manifests, snap, ctx.priorities)],
    ]:
        sim = runner(initial, decide=call)
        sim.step()
        assert records(sim)[-1]["decision_status"] == "available"
        assert len(records(sim)[-1]["decisions"]) == 1
        assert replay_jsonl(sim.jsonl, decide=call).jsonl == sim.jsonl

    # An engine that raises NotImplementedError is still recorded explicitly, never substituted.
    def unavailable(snap, events, ctx):
        raise NotImplementedError("engine unavailable")

    sim = runner(initial, decide=unavailable)
    sim.step()
    assert records(sim)[-1]["decision_status"] == "engine_not_implemented"
    assert records(sim)[-1]["decisions"] == []
    assert replay_jsonl(sim.jsonl, decide=unavailable).jsonl == sim.jsonl


def test_changed_card_reaches_real_admission(initial):
    initial = WorldSnapshot.model_validate(
        json.loads((REPO_DIR / "tests/fixtures/snapshot_scenario_a_port.json").read_text())
    )
    results = []
    for power in [35, 135]:
        card = load_guests()["G4"].model_dump(mode="json")
        next(m for m in card["modes"] if m["name"] == "charging")["power_kw"] = power

        def callback(snapshot, events, ctx):
            return [
                admission.decide(
                    ctx.ship,
                    ctx.manifests,
                    snapshot,
                    ctx.cards["G4"],
                    ctx.priorities,
                    load_envelope(),
                )
            ]

        sim = runner(
            initial,
            timeline=[Event(t_s=0, kind="card_presented", payload={"card": card})],
            decide=callback,
        )
        sim.step()
        results.append(records(sim)[-1]["decisions"])
    assert results[0] != results[1]


def test_yaml_order_and_validation(tmp_path):
    path = tmp_path / "timeline.yaml"
    path.write_text(
        "- {t: 2, event: crew_change, payload: {crew: 6}}\n"
        "- {t: 1, event: cooling_loss, payload: {}}\n"
        "- {t: 2, event: crew_change, payload: {crew: 0}}\n"
    )
    events = load_timeline(path)
    assert [e.t_s for e in events] == [1, 2, 2]
    assert [e.payload["crew"] for e in events[1:]] == [6, 0]
    path.write_text("- {t: true, event: cooling_loss, payload: {}}")
    with pytest.raises(ValueError):
        load_timeline(path)


def test_scenario_b_reports_actual_modes_and_charge_bound(initial):
    sim = load_scenario(DATA_DIR / "scenarios/B.yaml")
    sim.run(721)
    assert sim.snapshot.guests["G3"].mode == "surveying"
    assert sim.snapshot.guests["G4"].mode == "flying"
    assert sim.snapshot.platform.gensets_online == 1
    guests = load_guests()
    charge_start = sum(guests["G3"].mode(m).duration_min for m in ["surveying", "recovering"])
    assert charge_start > 12
    assert sum(g.measured_kw for g in sim.snapshot.guests.values()) < 100
    assert records(sim)[-1]["decision_status"] == "no_callback"
    assert guests["G3"].mode("holding").next_mode is None
    assert guests["G4"].mode("recovery").next_mode is None
