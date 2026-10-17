import json

import pytest

from shipos.contracts import Event
from shipos.data import load_guests, load_priorities, load_ship
from shipos.paths import DATA_DIR
from shipos.sim.consumption import Profile
from shipos.sim.log import verify_jsonl
from shipos.sim.runner import Runner, load_scenario, replay_jsonl


@pytest.fixture
def make_runner():
    def make(**kwargs):
        return Runner(
            load_scenario(DATA_DIR / "scenarios/B.yaml").snapshot,
            ship=load_ship(),
            manifests=load_guests(),
            priorities=load_priorities("survey_primary"),
            profiles={"G5": Profile(noise_fraction=0.1)},
            seed=6,
            **kwargs,
        )

    return make


def last_record(sim):
    return json.loads(sim.jsonl.splitlines()[-1])["record"]


def test_inject_trip_and_battery_after_completed_ticks(make_runner):
    seen = []

    def callback(snapshot, events, context):
        seen.append(events)
        return []

    sim = make_runner(decide=callback)
    assert sim.next_t_s == 0
    sim.run(5)
    before = sim.snapshot
    assert sim.next_t_s == 5
    events = [
        Event(t_s=sim.next_t_s, kind="genset_trip", payload={"gensets_online": 1}),
        Event(
            t_s=sim.next_t_s, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 17}
        ),
    ]
    snapshot = sim.step(events=events)
    assert snapshot.t_s == 5
    assert snapshot.platform.gensets_online == 1
    assert snapshot.guests["G4"].battery_pct == 17
    assert before.platform.gensets_online == 2
    assert before.guests["G4"].battery_pct == 30
    assert seen[-1] == tuple(events)
    assert sim.next_t_s == 6
    with pytest.raises(AttributeError):
        sim.next_t_s = 7
    sim.step()
    sim.run(2)
    assert sim.next_t_s == 9


def test_constructor_events_precede_ordered_injected_events(make_runner):
    survey = load_priorities("survey_primary")
    sar = load_priorities("sar_primary")
    timeline = [
        Event(t_s=3, kind="priority_swap", payload={"priorities": sar.model_dump(mode="json")}),
        Event(t_s=3, kind="crew_change", payload={"crew": 0}),
    ]
    injected = [
        Event(t_s=3, kind="priority_swap", payload={"priorities": survey.model_dump(mode="json")}),
        Event(t_s=3, kind="crew_change", payload={"crew": 6}),
        Event(t_s=3, kind="crew_change", payload={"crew": 14}),
    ]
    sim = make_runner(timeline=timeline)
    sim.run(3)
    assert sim.step(injected).crew == 14
    assert sim.context.priorities == survey
    record = last_record(sim)
    assert record["events"] == [e.model_dump(mode="json") for e in timeline + injected]
    assert record["injected_events"] == [e.model_dump(mode="json") for e in injected]
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl


@pytest.mark.parametrize(
    "bad",
    [
        Event(t_s=2, kind="cooling_loss"),
        Event(t_s=4, kind="cooling_loss"),
        Event(t_s=3.5, kind="cooling_loss"),
        Event(t_s=3, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 101}),
        Event(t_s=3, kind="battery_report", payload={"guest_id": "missing", "battery_pct": 10}),
        Event(t_s=3, kind="genset_trip", payload={"gensets_online": 2}),
        Event(t_s=3, kind="crew_change", payload={"crew": 13}),
        Event(t_s=3, kind="unknown"),
        {"t_s": 3, "kind": "cooling_loss"},
    ],
)
def test_failed_batch_rolls_back_everything_and_retry_matches(make_runner, bad):
    timeline = [Event(t_s=3, kind="cooling_loss")]
    sim = make_runner(timeline=timeline)
    control = make_runner(timeline=timeline)
    sim.run(3)
    control.run(3)
    before = (
        sim.snapshot,
        sim.context,
        sim.jsonl,
        sim.final_hash,
        sim.next_t_s,
        sim._rng.getstate(),
        sim._cursor,
        sim._entered.copy(),
    )
    valid = [
        Event(
            t_s=3,
            kind="priority_swap",
            payload={"priorities": load_priorities("sar_primary").model_dump(mode="json")},
        ),
        Event(t_s=3, kind="crew_change", payload={"crew": 6}),
    ]
    for _ in range(2):
        with pytest.raises((ValueError, TypeError)):
            sim.step([*valid, bad])
        assert (
            sim.snapshot,
            sim.context,
            sim.jsonl,
            sim.final_hash,
            sim.next_t_s,
            sim._rng.getstate(),
            sim._cursor,
            sim._entered.copy(),
        ) == before
    assert sim.step(valid) == control.step(valid)
    assert sim.jsonl == control.jsonl


def test_injected_callback_failure_preserves_rng_and_all_inputs(make_runner):
    fail = True

    def callback(*args):
        if fail:
            raise RuntimeError("callback failed after measurement")
        return []

    sim = make_runner(decide=callback)
    control = make_runner(decide=lambda *args: [])
    batch = [Event(t_s=0, kind="crew_change", payload={"crew": 6})]
    before = (
        sim.snapshot,
        sim.context,
        sim.jsonl,
        sim.final_hash,
        sim.next_t_s,
        sim._rng.getstate(),
        sim._cursor,
    )
    with pytest.raises(RuntimeError, match="callback failed"):
        sim.step(batch)
    assert (
        sim.snapshot,
        sim.context,
        sim.jsonl,
        sim.final_hash,
        sim.next_t_s,
        sim._rng.getstate(),
        sim._cursor,
    ) == before
    fail = False
    assert sim.step(batch) == control.step(batch)
    assert sim.jsonl == control.jsonl


def test_caller_payloads_are_copied_before_later_mutation(make_runner):
    sim = make_runner()
    priorities = load_priorities("sar_primary").model_dump(mode="json")
    priorities["overrides"]["G5"] = 7
    report = Event(t_s=0, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 18})
    swap = Event(t_s=0, kind="priority_swap", payload={"priorities": priorities})

    def events():
        yield report
        report.payload["battery_pct"] = 1
        yield swap
        swap.payload["priorities"]["overrides"]["G5"] = 999

    assert sim.step(events()).guests["G4"].battery_pct == 18
    before = sim.jsonl
    assert sim.context.priorities.overrides["G5"] == 7
    priorities["overrides"].clear()
    report.payload.clear()
    swap.payload.clear()
    assert sim.jsonl == before
    sim.run(2)
    assert sim.context.priorities.overrides["G5"] == 7
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl


def test_injected_replay_does_not_duplicate_constructor_trip(make_runner):
    sim = make_runner(timeline=[Event(t_s=4, kind="genset_trip", payload={"gensets_online": 1})])
    sim.run(4)
    sim.step([Event(t_s=4, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 16})])
    sim.run(2)
    sim.step([Event(t_s=7, kind="crew_change", payload={"crew": 6})])
    sim.run(3)
    restored = replay_jsonl(sim.jsonl)
    assert restored.jsonl == sim.jsonl
    assert restored.snapshot == sim.snapshot
    assert restored.context == sim.context
    assert verify_jsonl(restored.jsonl, expected_hash=sim.final_hash) == sim.final_hash


def test_changed_injected_input_changes_replay_evidence(make_runner):
    sims = [make_runner() for _ in range(2)]
    prefixes = []
    for sim, battery in zip(sims, [18, 19], strict=True):
        sim.run(3)
        prefixes.append(sim.jsonl)
        sim.step(
            [
                Event(
                    t_s=3, kind="battery_report", payload={"guest_id": "G4", "battery_pct": battery}
                )
            ]
        )
        assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl
    assert prefixes[0] == prefixes[1]
    assert sims[0].jsonl != sims[1].jsonl
    assert sims[0].final_hash != sims[1].final_hash
    assert sims[0].snapshot.guests["G4"].battery_pct != sims[1].snapshot.guests["G4"].battery_pct


def test_no_injection_preserves_documented_hash_and_omits_extension():
    sim = load_scenario(DATA_DIR / "scenarios/B.yaml")
    sim.run(721)
    assert sim.final_hash == "ca3abd5b32afaf260fd2575b718945c7b6584d5d8263d373d7712acb7ef62a28"
    assert b'"injected_events"' not in sim.jsonl
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl
