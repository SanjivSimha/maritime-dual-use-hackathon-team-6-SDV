"""Execution regressions: real Track A decisions plus labelled controlled contract edges."""

import json
import socket
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shipos.contracts import Action, Decision, Event, Option, StateRequest, WorldSnapshot
from shipos.data import load_priorities
from shipos.engine import arbitration, shedding
from shipos.paths import DATA_DIR
from shipos.sim.consumption import Profile
from shipos.sim.deferral import DeferralError
from shipos.sim.env import EnvironmentUnavailable
from shipos.sim.env_input import EnvironmentInput
from shipos.sim.runner import Context, Runner, load_scenario, replay_jsonl


def decide(snapshot, events, context):
    """Pure integration example. API wiring is owned by main."""
    for event in events:
        if event.kind == "battery_report":
            yield arbitration.decide(
                context.ship,
                context.manifests,
                snapshot,
                context.priorities,
                event.payload["guest_id"],
                "recovery",
                confirm=context.recovery_confirm,
            )
        elif event.kind in ("genset_trip", "cooling_loss"):
            yield shedding.plan(
                context.ship,
                context.manifests,
                snapshot,
                context.priorities,
                protected=context.protected,
            )


def base(**changes):
    data = load_scenario(DATA_DIR / "scenarios/B.yaml").snapshot.model_dump()
    data.update(changes)
    return data


def runner(data=None, *, priority="survey_primary", callback=decide, **kwargs):
    reference = load_scenario(DATA_DIR / "scenarios/B.yaml")
    c = reference.context
    return Runner(
        WorldSnapshot.model_validate(data or base()),
        ship=c.ship,
        manifests=c.manifests,
        priorities=load_priorities(priority),
        execution=True,
        decide=callback,
        **kwargs,
    )


def event(sim, kind, **payload):
    return Event(t_s=sim.next_t_s, kind=kind, payload=payload)


def record(sim):
    return json.loads(sim.jsonl.splitlines()[-1])["record"]


def state(sim):
    return deepcopy(
        (
            sim.snapshot,
            sim.context,
            sim.execution_status,
            sim._entered,
            sim._rng.getstate(),
            sim._cursor,
            sim.next_t_s,
            sim.jsonl,
            sim.final_hash,
        )
    )


@pytest.mark.parametrize("priority", ["survey_primary", "sar_primary"])
def test_real_d3_delayed_hold_confirmation_followup_and_replay(priority):
    sim = load_scenario(DATA_DIR / "scenarios/B.yaml", execution=True, decide=decide)
    if priority != sim.context.priorities.id:
        sim.step(
            [
                event(
                    sim,
                    "priority_swap",
                    priorities=load_priorities(priority).model_dump(mode="json"),
                )
            ]
        )
    sim.run(481 - sim.next_t_s)
    chosen = record(sim)["decisions"][0]["chosen"]
    assert chosen["feasible"]
    g3_action = next(a for a in chosen["actions"] if a["guest"] == "G3")
    assert g3_action["kind"] == ("pause" if priority == "survey_primary" else "suspend")
    before = sim.snapshot
    hold = next(t for t in sim.execution_status["pending"] if t.get("variable") == "hold:G3")
    sim.run(hold["due_s"] - sim.next_t_s)
    assert sim.snapshot.guests["G3"].mode == "surveying"
    sim.step()
    assert sim.snapshot.guests["G3"].mode == "holding"
    assert before.guests["G3"].mode == "surveying"
    assert sim.snapshot.ship.speed_kn == 3  # speed preparation still running
    sim.run(841 - sim.next_t_s)
    assert sim.snapshot.ship.speed_kn == 5 and sim.snapshot.ship.deck == "clear"
    assert sim.snapshot.guests["G4"].mode == "flying"
    assert not sim.execution_status["crew_gate"]["permitted"]
    assert any(b["code"] == "confirmation_pending" for b in sim.execution_status["blocked"])
    assert sim.context.protected == frozenset({"G4"})
    deadline = deepcopy(sim.execution_status["deadlines"]["G4"])
    sim.step([event(sim, "operator_confirmation", by="test deck officer")])
    recovery = sim.snapshot.guests["G4"]
    assert recovery.mode == "recovery" and recovery.activity_started_s == 841
    assert sim.execution_status["deadlines"]["G4"] == deadline
    if priority == "sar_primary":
        assert sim.execution_status["reservations"]["G4"]["kw"] == 35
        assert recovery.measured_kw == 2  # a reservation does not consume power
    duration = sim.context.manifests["G4"].activity("recovery").duration_min * 60
    sim.run(int(duration))
    assert sim.snapshot.guests["G4"].mode == "launching"  # actual followed_by, never charge
    launch = sim.context.manifests["G4"].activity("launch").duration_min * 60
    sim.run(int(launch))
    assert sim.snapshot.guests["G4"].mode == "flying"
    assert "G4" not in sim.context.protected
    assert sim.execution_status["deadlines"]["G4"]["land_by_s"] == 1200
    assert sim.execution_status["deadlines"]["G4"]["landed_s"] == 841 + duration
    if priority == "survey_primary":
        resume = next(t for t in sim.execution_status["pending"] if t["kind"] == "resume")
        sim.run(resume["due_s"] + 1 - sim.next_t_s)
        assert sim.snapshot.guests["G3"].mode == "holding"
        prepared = next(t for t in sim.execution_status["pending"] if t["kind"] == "mode")
        sim.run(prepared["due_s"] + 1 - sim.next_t_s)
        assert sim.snapshot.guests["G3"].mode == "surveying"
        assert sim.snapshot.guests["G3"].activity_started_s == prepared["due_s"]
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl
    initial = sim.reset()
    assert initial.t_s == 0 and sim.next_t_s == 0
    assert not sim.execution_status["pending"] and not sim.execution_status["applied"]
    assert sim.execution_status["crew_evidence"]["confirmation"] is None


@pytest.mark.parametrize("priority", ["survey_primary", "sar_primary"])
@pytest.mark.parametrize("crew", [14, 6, 0])
def test_declared_scenario_matrix_no_fabricated_success(priority, crew):
    sim = runner(base(t_s=480, crew=crew), priority=priority)
    sim.step([event(sim, "battery_report", guest_id="G4", battery_pct=18)])
    decision = record(sim)["decisions"][0]
    if crew == 14:
        assert decision["winner"] == "G4"
        sim.run(361)
        assert sim.snapshot.guests["G4"].mode == "flying"  # no manufactured confirmation
    else:
        assert decision["winner"] == "G3"
        assert any(b["code"] == "denied_by_engine" for b in sim.execution_status["blocked"])
        assert sim.snapshot.guests["G4"].mode == "flying"
        assert not sim.execution_status["pending"]
    assert sim.execution_status["deadlines"]["G4"]["land_by_s"] == 1200
    assert sim.execution_status["early_clearance"]["G4"]["blockers"]


def test_deadline_never_slides_and_late_confirmation_cannot_create_success():
    sim = runner(base(t_s=480))
    sim.step([event(sim, "battery_report", guest_id="G4", battery_pct=18)])
    sim.run(1101 - sim.next_t_s)
    sim.step(
        [
            event(sim, "operator_confirmation", by="late operator"),
            event(sim, "battery_report", guest_id="G4", battery_pct=18),
        ]
    )
    assert sim.execution_status["deadlines"]["G4"]["land_by_s"] == 1200
    assert sim.snapshot.guests["G4"].mode == "flying"
    assert any(
        b["code"] == "anchored_landing_deadline_unreachable"
        for b in sim.execution_status["blocked"]
    )


def ready_data(crew=14):
    """Controlled physical input: clear deck, no submerged active survey."""
    data = base(crew=crew)
    data["ship"].update(deck="clear", speed_kn=5, course="free", noise="manoeuvring")
    data["guests"]["G3"].update(mode="standby", activity=None, activity_started_s=None)
    return data


def test_zero_crew_current_tick_simulated_evidence_and_live_injection():
    data = base(crew=0)
    data["ship"]["deck"] = "clear"
    sim = runner(data)
    sim.step([event(sim, "battery_report", guest_id="G4", battery_pct=18)])
    assert sim.context.recovery_confirm == "automatic"
    ready_s = max(t["due_s"] for t in sim.execution_status["pending"] if t["kind"] == "request")
    sim.run(ready_s - sim.next_t_s + 1)
    assert sim.snapshot.guests["G4"].mode == "recovery"
    evidence = sim.execution_status["crew_evidence"]["evidence"]
    assert evidence == {"t_s": ready_s, "deck_clear": True, "source": "simulated_deck_state"}
    sim.step([event(sim, "crew_change", crew=6)])
    assert not sim.execution_status["crew_gate"]["permitted"]
    assert sim.execution_status["crew_evidence"]["evidence"]["t_s"] == ready_s + 1
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


def test_crew_roundtrip_same_tick_invalidates_operator_token():
    sim = runner(ready_data())
    sim.step([event(sim, "operator_confirmation", by="deck officer")])
    assert sim.execution_status["crew_gate"]["permitted"]
    sim.step([event(sim, "crew_change", crew=6), event(sim, "crew_change", crew=14)])
    assert sim.execution_status["crew_evidence"]["confirmation"] is None
    assert not sim.execution_status["crew_gate"]["permitted"]


def controlled(*actions, requests=(), kind="shedding", winner=None):
    """Synthetic contract-edge plan, never a substitute for real decision tests."""

    def callback(snapshot, events, context):
        if snapshot.t_s == 0:
            return [
                Decision(
                    kind=kind,
                    subject="controlled edge",
                    verdict="shed" if kind == "shedding" else "decided",
                    chosen=Option(name="controlled", feasible=True, actions=list(actions)),
                    state_requests=list(requests),
                    winner=winner,
                    t_s=0,
                )
            ]
        return []

    return callback


def test_deck_occupation_invalidates_confirmation_in_intermediate_snapshots():
    requests = [
        StateRequest(variable="deck", to="occupied", start_s=1, duration_s=0),
        StateRequest(variable="deck", to="clear", start_s=2, duration_s=1),
    ]
    sim = runner(ready_data(), callback=controlled(requests=requests))
    sim.step([event(sim, "operator_confirmation", by="operator")])
    sim.run(4)
    assert sim.snapshot.ship.deck == "clear"
    assert sim.execution_status["crew_evidence"]["clear_since_s"] == 3
    assert sim.execution_status["crew_evidence"]["confirmation"] is None


def overlap():
    """Controlled D4 inputs, not baseline Scenario B or an engine-test schedule."""
    data = ready_data()
    data["guests"]["G4"]["mode"] = "charging"
    data["guests"]["G3"]["schedule"] = [
        {"t_s": 60, "mode": "standby"},
        {"t_s": 120, "mode": "charging"},
        {"t_s": 3720, "mode": "standby"},
    ]
    data["guests"]["G4"]["schedule"] = [{"t_s": 300, "mode": "topping"}]
    return data


def observed_decider(seen):
    def callback(snapshot, events, context):
        decisions = list(decide(snapshot, events, context))
        seen.extend(decisions)
        return decisions

    return callback


def step_real_d4_or_verify_missing_metadata(sim, seen):
    before = state(sim)
    try:
        sim.step([event(sim, "genset_trip", gensets_online=1)])
    except DeferralError:
        # The standalone B branch intentionally does not import Track A PR #43.
        # Its old producer must fail closed, never synthesize exact metadata here.
        action = seen[-1].chosen.actions[0]
        assert action.kind == "defer"
        assert not {"schedule_index", "from_t_s", "to_t_s"} <= action.facts.keys()
        assert state(sim) == before
        return False
    return True


@pytest.mark.parametrize("priority", ["survey_primary", "sar_primary"])
def test_real_d4_offsets_and_deferral_target_the_correct_suffix(priority):
    seen = []
    sim = runner(overlap(), priority=priority, callback=observed_decider(seen))
    original = sim.snapshot.guests["G3"].schedule
    if not step_real_d4_or_verify_missing_metadata(sim, seen):
        return
    decision = record(sim)["decisions"][0]
    assert decision["chosen"]["feasible"]
    action = decision["chosen"]["actions"][0]
    assert action["kind"] == ("throttle" if priority == "survey_primary" else "defer")
    assert sim.snapshot.guests["G3"].schedule == original
    assert sim.snapshot.guests["G5"].mode == "on"
    offset = int(action["t_offset_s"])
    sim.run(offset)
    if priority == "survey_primary":
        assert sim.snapshot.guests["G5"].mode == action["to_mode"]
    else:
        delayed = sim.snapshot.guests["G3"].schedule
        assert delayed[0] == original[0]  # earlier unrelated entry untouched
        assert [s.t_s for s in delayed[1:]] == [
            s.t_s + action["facts"]["to_t_s"] - action["facts"]["from_t_s"] for s in original[1:]
        ]
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


def test_unknown_or_ambiguous_semantics_queue_nothing():
    sim = runner(
        ready_data(),
        callback=controlled(
            Action(guest="G5", kind="throttle", to_mode="low_power"),
            Action(guest="G3", kind="retask"),
        ),
    )
    sim.step()
    assert not sim.execution_status["pending"]
    assert sim.snapshot.guests["G5"].mode == "on"
    assert "retask" in sim.execution_status["blocked"][0]["reason"]
    data = overlap()
    data["guests"]["G3"]["schedule"].append({"t_s": 4000, "mode": "charging"})
    sim = runner(
        data, callback=controlled(Action(guest="G3", kind="defer", to_mode="charging", delay_min=5))
    )
    before = state(sim)
    with pytest.raises(DeferralError, match="exact.*metadata"):
        sim.step()
    assert state(sim) == before


@pytest.mark.parametrize("protected", [True, False])
def test_protected_winner_and_noninterruptible_mode_cannot_be_shed(protected):
    data = ready_data()
    if not protected:
        data["guests"]["G3"]["mode"] = "charging"
    gid = "G5" if protected else "G3"
    cb = controlled(
        Action(guest=gid, kind="standby", to_mode="low_power" if protected else "standby"),
        kind="arbitration" if protected else "shedding",
        winner=gid if protected else None,
    )
    sim = runner(data, callback=cb)
    before = sim.snapshot.guests[gid].mode
    sim.step()
    assert sim.snapshot.guests[gid].mode == before
    assert sim.execution_status["blocked"][0]["code"] == (
        "protected_winner" if protected else "noninterruptible_mode"
    )


def test_failed_tick_rolls_back_queue_evidence_context_rng_cursor_and_hash():
    fail = False

    def callback(s, e, c):
        if fail:
            raise RuntimeError("callback failed")
        return list(decide(s, e, c))

    sim = runner(
        base(t_s=480),
        callback=callback,
        profiles={"G5": Profile(noise_fraction=0.1)},
        timeline=[Event(t_s=540, kind="cooling_loss")],
    )
    sim.step([event(sim, "battery_report", guest_id="G4", battery_pct=18)])
    sim.run(59)  # fail exactly when the hold and dependent manoeuvres would commit
    before = state(sim)
    fail = True
    for events in ([event(sim, "crew_change", crew=6)], []):
        with pytest.raises(RuntimeError, match="callback failed"):
            sim.step(events)
        assert state(sim) == before
    with pytest.raises(ValueError):
        sim.step([event(sim, "operator_confirmation", by="operator")])  # occupied
    assert state(sim) == before


def test_repeated_inputs_status_copy_and_old_observation_bytes():
    runs = [runner(base(t_s=480)) for _ in range(2)]
    for sim in runs:
        sim.step([event(sim, "battery_report", guest_id="G4", battery_pct=18)])
        sim.run(65)
    assert runs[0].jsonl == runs[1].jsonl and runs[0].final_hash == runs[1].final_hash
    status = runs[0].execution_status
    status["pending"].clear()
    status["protected"].clear()
    assert runs[0].execution_status["pending"] and runs[0].context.protected
    assert Context.__dataclass_fields__["protected"].default == frozenset()
    observation = load_scenario(DATA_DIR / "scenarios/B.yaml")
    observation.run(721)
    # Published B2 baseline evidence is unchanged.
    assert (
        observation.final_hash == "ca3abd5b32afaf260fd2575b718945c7b6584d5d8263d373d7712acb7ef62a28"
    )
    assert replay_jsonl(observation.jsonl).jsonl == observation.jsonl
    assert not observation.execution_status["enabled"]
    assert "execution" not in record(observation)


def test_supplied_forecast_starts_real_preparation_without_confirming():
    sim = runner(base(crew=6))
    sim.step([event(sim, "battery_forecast", guest_id="G4", issued_s=0.0, land_by_s=1200.0)])
    assert not sim.execution_status["pending"]
    start = int(sim.execution_status["early_clearance"]["G4"]["start_by_s"])
    sim.run(start)
    assert sim.snapshot.ship.deck == "occupied"
    request = next(t for t in sim.execution_status["pending"] if t.get("variable") == "deck")
    assert request["start_s"] == start
    assert request["duration_s"] == sim.context.ship.transitions.deck_clear_s[6].hi
    sim.run(request["due_s"] + 1 - sim.next_t_s)
    assert sim.snapshot.ship.deck == "clear"
    assert not sim.execution_status["crew_gate"]["permitted"]
    sim.run(181)
    assert any(b["code"] == "confirmation_pending" for b in sim.execution_status["blocked"])
    assert sim.snapshot.guests["G4"].mode == "flying"
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


def test_execution_embeds_real_weather_no_cache_read_or_network_on_replay(tmp_path, monkeypatch):
    path = tmp_path / "captured.json"
    path.write_bytes((DATA_DIR / "env_cache/strait_of_sicily.json").read_bytes())
    weather = EnvironmentInput.from_cache(
        path, scenario_start=datetime(2026, 10, 17, 8, tzinfo=UTC), layered_water=True
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("offline execution must not use network or reread its cache")

    sim = runner(ready_data(0), environment=weather)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    sim.run(2)
    path.write_text("cache changed")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    copy = replay_jsonl(sim.jsonl, decide=decide)
    assert copy.jsonl == sim.jsonl and copy.environment.record == weather.record
    sim.reset()
    assert sim.environment.record == weather.record
    assert sim.execution_status["crew_evidence"]["confirmation"] is None


def test_real_engine_no_conflict_keep_does_not_become_proceed():
    sim = runner(ready_data(0))
    sim.step([event(sim, "battery_report", guest_id="G4", battery_pct=18)])
    decision = record(sim)["decisions"][0]
    assert decision["winner"] == "G4"
    assert decision["chosen"]["actions"][0]["kind"] == "keep"
    assert sim.snapshot.guests["G4"].mode == "flying"


def test_real_d3_followup_publishes_declared_future_modes_for_d4():
    data = base(crew=0)
    data["ship"]["deck"] = "clear"
    sim = runner(data)
    sim.step([event(sim, "battery_report", guest_id="G4", battery_pct=18)])
    sim.run(361 - sim.next_t_s)
    runtime = sim.snapshot.guests["G4"]
    assert runtime.mode == "recovery"
    next_activity = sim.context.manifests["G4"].activity("launch")
    assert runtime.schedule[0].mode == next_activity.mode
    assert (
        runtime.schedule[0].t_s
        == runtime.activity_started_s
        + sim.context.manifests["G4"].activity("recovery").duration_min * 60
    )
    # A trip evaluates the actual schedule; armed alternatives never execute.
    sim.step([event(sim, "genset_trip", gensets_online=1)])
    assert record(sim)["decisions"][0]["verdict"] == "no_action"
    assert sim.snapshot.guests["G5"].mode == "on"
    assert sim.context.protected == frozenset({"G4"})


def test_confirmation_invalidated_while_proceed_waits_for_offset():
    cb = controlled(
        Action(guest="G4", kind="proceed", to_mode="recovery", t_offset_s=5),
        requests=[StateRequest(variable="confirm:deck", to="confirmed", start_s=0, duration_s=0)],
        kind="arbitration",
        winner="G4",
    )
    sim = runner(ready_data(), callback=cb)
    sim.step([event(sim, "operator_confirmation", by="operator")])
    sim.step([event(sim, "crew_change", crew=6), event(sim, "crew_change", crew=14)])
    sim.run(4)
    assert sim.snapshot.guests["G4"].mode == "flying"
    assert sim.execution_status["crew_evidence"]["confirmation"] is None
    assert any("confirmation_pending" in b["code"] for b in sim.execution_status["blocked"])


def test_schedule_cannot_override_protected_winner():
    data = ready_data()
    data["guests"]["G5"]["schedule"] = [{"t_s": 1, "mode": "low_power"}]
    sim = runner(
        data, callback=controlled(Action(guest="G5", kind="keep"), kind="arbitration", winner="G5")
    )
    sim.run(2)
    assert sim.snapshot.guests["G5"].mode == "on"
    assert any(b["code"] == "protected_winner" for b in sim.execution_status["blocked"])


@pytest.mark.parametrize("missing_sst", [True, False])
def test_missing_weather_rolls_back_execution_queue_and_evidence(missing_sst):
    # Explicitly synthetic boundary cache; not provider observations or a real forecast.
    document = json.loads((DATA_DIR / "env_cache/strait_of_sicily.json").read_text())
    document["kind"] = "synthetic_demo"
    document["notes"] = "Synthetic missing-coverage execution test."
    for source in document["sources"].values():
        source["url"] = source["retrieved_at"] = None
    start = datetime(2026, 10, 17, 8, tzinfo=UTC)
    stamps = [int(start.timestamp()) + i * 3600 for i in range(4)]
    marine = {
        "time": stamps,
        "wave_height": [0.5] * 4,
        "sea_surface_temperature": [21, None if missing_sst else 21, 21, 21],
    }
    weather = {"time": stamps.copy(), "wind_speed_10m": [10] * 4}
    if not missing_sst:
        marine = {k: [v[0], v[1], v[3]] for k, v in marine.items()}  # unknown one-hour gap
        weather = {k: [v[0], v[1], v[3]] for k, v in weather.items()}
    document["sources"]["marine"]["response"]["hourly"] = marine
    document["sources"]["weather"]["response"]["hourly"] = weather
    environment = EnvironmentInput(document, scenario_start=start, layered_water=False)

    def edge(snapshot, events, context):
        return [
            Decision(
                kind="shedding",
                subject="synthetic queued boundary",
                verdict="shed",
                chosen=Option(
                    name="delayed throttle",
                    feasible=True,
                    actions=[
                        Action(guest="G5", kind="throttle", to_mode="low_power", t_offset_s=10)
                    ],
                ),
                t_s=snapshot.t_s,
            )
        ]

    data = ready_data()
    data["t_s"] = 3599
    sim = runner(data, environment=environment, callback=edge)
    sim.step()
    assert sim.execution_status["pending"]
    before = state(sim)
    for _ in range(2):
        with pytest.raises(EnvironmentUnavailable):
            sim.step()
        assert state(sim) == before


def test_real_d4_deferral_of_generated_boundary_survives_original_duration():
    # Controlled initial modes, actual card durations and real D4. No engine-test schedules.
    data = ready_data()
    data["ship"].update(speed_kn=4, course="steady", noise="normal")
    data["guests"]["G3"]["mode"] = "recovering"
    data["guests"]["G4"]["mode"] = "charging"
    seen = []
    sim = runner(data, priority="sar_primary", callback=observed_decider(seen))
    if not step_real_d4_or_verify_missing_metadata(sim, seen):
        return
    action = record(sim)["decisions"][0]["chosen"]["actions"][0]
    assert action["kind"] == "defer" and action["guest"] == "G3"
    original_due = sim.snapshot.guests["G3"].schedule[0].t_s
    sim.run(int(action["t_offset_s"]))
    delayed_due = sim.snapshot.guests["G3"].schedule[0].t_s
    assert delayed_due == action["facts"]["to_t_s"]
    assert original_due == action["facts"]["from_t_s"]
    sim.run(int(original_due) + 1 - sim.next_t_s)
    assert sim.snapshot.guests["G3"].mode == "recovering"
    sim.run(int(delayed_due) + 1 - sim.next_t_s)
    assert sim.snapshot.guests["G3"].mode == "charging"
    assert replay_jsonl(sim.jsonl, decide=decide).jsonl == sim.jsonl


def test_rejected_schedule_at_exact_boundary_preserves_winner_followup():
    data = ready_data()
    recovery = next(
        a
        for a in load_scenario(DATA_DIR / "scenarios/B.yaml").context.manifests["G4"].activities
        if a.name == "recovery"
    )
    end = int(recovery.duration_min * 60)
    data["guests"]["G4"].update(
        mode="recovery",
        activity="recovery",
        activity_started_s=0,
        schedule=[{"t_s": end, "mode": "off"}],
    )
    sim = runner(
        data, callback=controlled(Action(guest="G4", kind="keep"), kind="arbitration", winner="G4")
    )
    sim.step([event(sim, "operator_confirmation", by="test operator")])
    sim.run(end)
    assert sim.snapshot.guests["G4"].mode == "launching"
    assert sim.context.protected == frozenset({"G4"})
    assert any(b["code"] == "protected_winner" for b in sim.execution_status["blocked"])
