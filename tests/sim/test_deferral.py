"""Synthetic consumer edges; real producer assembly is explicitly marked below."""

from copy import deepcopy

import pytest

from shipos.contracts import Action, Decision, Event, Option
from shipos.sim.consumption import Profile
from shipos.sim.deferral import DeferralError
from shipos.sim.runner import replay_jsonl
from tests.sim.test_execution import (
    decide,
    event,
    observed_decider,
    overlap,
    record,
    runner,
    state,
)


def synthetic_data():
    data = overlap()
    data["guests"]["G3"]["schedule"] = [
        {"t_s": 20, "mode": "charging"},
        {"t_s": 100, "mode": "charging"},
        {"t_s": 200, "mode": "standby"},
    ]
    return data


def action(**updates):
    values = {
        "guest": "G3",
        "kind": "defer",
        "to_mode": "charging",
        "delay_min": 1.7,
        "t_offset_s": 5,
        "facts": {"schedule_index": 1.0, "from_t_s": 100.0, "to_t_s": 201.0},
    }
    values.update(updates)
    return Action(**values)


def synthetic_callback(actions_by_tick):
    """Controlled contract inputs, not claimed engine outcomes or real scenarios."""

    def callback(snapshot, events, context):
        actions = actions_by_tick.get(snapshot.t_s)
        if actions is None:
            return []
        return [
            Decision(
                kind="shedding",
                subject="synthetic DEFER consumer",
                verdict="shed",
                chosen=Option(name="synthetic selected option", feasible=True, actions=actions),
                t_s=snapshot.t_s,
            )
        ]

    return callback


def test_exact_index_selects_second_duplicate_mode_and_replays():
    cb = synthetic_callback({0: [action()]})
    sims = [runner(synthetic_data(), callback=cb) for _ in range(2)]
    for sim in sims:
        original = sim.snapshot
        sim.run(6)
        schedule = sim.snapshot.guests["G3"].schedule
        assert [(e.t_s, e.mode) for e in schedule] == [
            (20, "charging"),
            (201, "charging"),
            (301, "standby"),
        ]
        assert original.guests["G3"].schedule[1].t_s == 100
        assert record(sim)["execution"]["applied"][-1]["t_s"] == 5
        assert sim.execution_status["pending"] == []
        assert replay_jsonl(sim.jsonl, decide=cb).jsonl == sim.jsonl
    assert sims[0].jsonl == sims[1].jsonl and sims[0].final_hash == sims[1].final_hash
    # Exact 101 seconds differs from display 1.7 minutes = 102 seconds.
    assert action().facts["to_t_s"] - action().facts["from_t_s"] == 101
    assert action().delay_min * 60 == 102


@pytest.mark.parametrize(
    "facts",
    [
        {},
        {"schedule_index": 1, "from_t_s": 100},
        {"schedule_index": -1, "from_t_s": 100, "to_t_s": 201},
        {"schedule_index": 3, "from_t_s": 100, "to_t_s": 201},
        {"schedule_index": 1.5, "from_t_s": 100, "to_t_s": 201},
        {"schedule_index": "1", "from_t_s": 100, "to_t_s": 201},
        {"schedule_index": float("nan"), "from_t_s": 100, "to_t_s": 201},
        {"schedule_index": 1, "from_t_s": float("inf"), "to_t_s": 201},
        {"schedule_index": 1, "from_t_s": 100, "to_t_s": float("inf")},
        {"schedule_index": 1, "from_t_s": 101, "to_t_s": 201},
        {"schedule_index": 1, "from_t_s": 100.1, "to_t_s": 201},
        {"schedule_index": float("inf"), "from_t_s": 100, "to_t_s": 201},
        {"schedule_index": 1, "from_t_s": 100, "to_t_s": "201"},
        {"schedule_index": 2, "from_t_s": 200, "to_t_s": 301},  # wrong original mode
        {"schedule_index": 1, "from_t_s": 100, "to_t_s": 100},
        {"schedule_index": 1, "from_t_s": 100, "to_t_s": 99},
        {"schedule_index": 1, "from_t_s": 100, "to_t_s": 201.1},
    ],
)
def test_malformed_metadata_rejects_entire_tick_without_state_or_evidence_change(facts):
    cb = synthetic_callback(
        {0: [Action(guest="G5", kind="throttle", to_mode="low_power"), action(facts=facts)]}
    )
    sim = runner(synthetic_data(), callback=cb, profiles={"G5": Profile(noise_fraction=0.1)})
    before = state(sim)
    for _ in range(2):
        with pytest.raises(DeferralError, match="DEFER"):
            sim.step([event(sim, "crew_change", crew=6)])
        assert state(sim) == before


def test_late_action_offset_is_rejected_before_queuing():
    sim = runner(synthetic_data(), callback=synthetic_callback({0: [action(t_offset_s=100)]}))
    before = state(sim)
    with pytest.raises(DeferralError, match="execution time"):
        sim.step()
    assert state(sim) == before


def test_naturally_consumed_prefix_preserves_selected_suffix_identity():
    data = synthetic_data()
    data["guests"]["G3"]["schedule"][0] = {"t_s": 1, "mode": "standby"}
    cb = synthetic_callback({0: [action()]})
    sim = runner(data, callback=cb)
    sim.run(6)
    assert [(e.t_s, e.mode) for e in sim.snapshot.guests["G3"].schedule] == [
        (201, "charging"),
        (301, "standby"),
    ]
    assert replay_jsonl(sim.jsonl, decide=cb).jsonl == sim.jsonl


@pytest.mark.parametrize("changed_prefix", [False, True])
def test_stale_schedule_rolls_back_actions_rng_context_and_evidence(changed_prefix):
    # A separately valid, later plan changes the schedule before the old action is due.
    cb = synthetic_callback(
        {
            0: [Action(guest="G5", kind="throttle", to_mode="low_power", t_offset_s=5), action()],
            1: [
                action(
                    t_offset_s=0,
                    facts={
                        "schedule_index": 0 if changed_prefix else 1,
                        "from_t_s": 20 if changed_prefix else 100,
                        "to_t_s": 30 if changed_prefix else 160,
                    },
                )
            ],
        }
    )
    sim = runner(synthetic_data(), callback=cb, profiles={"G5": Profile(noise_fraction=0.1)})
    sim.run(5)
    assert sim.snapshot.guests["G5"].mode == "on"
    before = state(sim)
    for _ in range(2):
        with pytest.raises(DeferralError, match="schedule identity changed"):
            sim.step([event(sim, "crew_change", crew=6)])
        assert state(sim) == before


def test_suffix_identity_is_copied_from_caller_input():
    data = synthetic_data()
    before = deepcopy(data)
    cb = synthetic_callback({0: [action()]})
    sim = runner(data, callback=cb)
    sim.step()
    data["guests"]["G3"]["schedule"][1]["t_s"] = 999
    sim.run(5)
    assert sim.snapshot.guests["G3"].schedule[1].t_s == 201
    assert before["guests"]["G3"]["schedule"][1]["t_s"] == 100


def test_real_published_producer_to_consumer_exact_metadata():
    """Run with real #43 in a separate assembly; skip explicitly on the older producer.

    Initial modes/schedules are controlled synthetic physical inputs, not baseline B.
    No producer outputs or outcomes are fabricated here.
    """
    data = overlap()
    data["guests"]["G3"]["schedule"] = [
        {"t_s": 60, "mode": "standby"},
        {"t_s": 120, "mode": "charging"},
        {"t_s": 240, "mode": "charging"},
        {"t_s": 3840, "mode": "standby"},
    ]
    data["guests"]["G4"]["schedule"] = [{"t_s": 151, "mode": "topping"}]
    seen = []
    sim = runner(data, priority="sar_primary", callback=observed_decider(seen))
    before = state(sim)
    try:
        sim.step([Event(t_s=0, kind="genset_trip", payload={"gensets_online": 1})])
    except DeferralError:
        assert seen[-1].chosen.actions[0].facts == {}
        assert state(sim) == before
        pytest.skip("Real producer metadata requires Track A #43; exercised separately in assembly")
    selected = seen[-1].chosen.actions[0]
    assert selected.facts == {"schedule_index": 1, "from_t_s": 120, "to_t_s": 151}
    assert selected.delay_min == 0.5  # display 30 seconds; actual exact delay is 31 seconds
    sim.step()
    assert [e.t_s for e in sim.snapshot.guests["G3"].schedule] == [60, 151, 271, 3871]
    sim.run(150)
    assert sim.snapshot.guests["G3"].mode == "charging"
    assert sim.snapshot.guests["G4"].mode == "topping"
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


@pytest.mark.parametrize("mode", [None, "standby", "unrecognized_mode"])
def test_missing_or_invalid_defer_mode_preserves_tick(mode):
    cb = synthetic_callback({0: [action(to_mode=mode)]})
    sim = runner(synthetic_data(), callback=cb)
    before = state(sim)
    with pytest.raises(DeferralError, match="DEFER"):
        sim.step()
    assert state(sim) == before


def test_shifted_timestamp_that_contract_float_cannot_represent_is_not_rounded():
    data = synthetic_data()
    data["guests"]["G3"]["schedule"][-1]["t_s"] = 2**53
    cb = synthetic_callback(
        {0: [action(facts={"schedule_index": 1, "from_t_s": 100, "to_t_s": 101})]}
    )
    sim = runner(data, callback=cb)
    before = state(sim)
    with pytest.raises(DeferralError, match="exact whole"):
        sim.step()
    assert state(sim) == before
