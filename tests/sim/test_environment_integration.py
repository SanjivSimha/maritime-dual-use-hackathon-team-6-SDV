import json
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest

from shipos.contracts import Event
from shipos.paths import DATA_DIR, REPO_DIR
from shipos.sim.env import EnvironmentUnavailable, find_installation_windows
from shipos.sim.env_input import EnvironmentInput
from shipos.sim.log import verify_jsonl
from shipos.sim.runner import Runner, load_scenario, replay_jsonl

CACHE = DATA_DIR / "env_cache/strait_of_sicily.json"
SCENARIO = DATA_DIR / "scenarios/B.yaml"
START = datetime(2026, 10, 17, 8, tzinfo=UTC)


@pytest.fixture
def synthetic():
    """Clearly synthetic two-hour boundary data; not provider weather evidence."""
    document = json.loads(CACHE.read_text(encoding="utf-8"))
    document["kind"] = "synthetic_demo"
    document["notes"] = "Synthetic integration test inputs, not Open-Meteo forecasts."
    document.pop("attempted_at", None)
    for source in document["sources"].values():
        source["url"] = source["retrieved_at"] = None
    times = [int(START.timestamp()) + i * 3600 for i in range(3)]
    document["sources"]["marine"]["response"]["hourly"] = {
        "time": times,
        "wave_height": [0.5, 1.5, 0.5],
        "sea_surface_temperature": [21, 22, 23],
    }
    document["sources"]["weather"]["response"]["hourly"] = {
        "time": times.copy(),
        "wind_speed_10m": [10, 21, 10],
    }
    return document


def captured(document, **kwargs):
    return EnvironmentInput(
        document, scenario_start=kwargs.pop("scenario_start", START), layered_water=True, **kwargs
    )


def runner_at(environment, t_s=3599, *, decide=None, timeline=()):
    baseline = load_scenario(SCENARIO)
    initial = baseline.snapshot.model_dump()
    initial["t_s"] = t_s
    initial["guests"] = {}  # isolate environment behavior from equipment/operational policy
    context = baseline.context
    return Runner(
        type(baseline.snapshot).model_validate(initial),
        ship=context.ship,
        manifests=context.manifests,
        priorities=context.priorities,
        seed=6,
        timeline=timeline,
        decide=decide,
        environment=environment,
    )


def state(sim):
    return (
        sim.snapshot,
        sim.context,
        sim._cursor,
        sim._rng.getstate(),
        sim._entered.copy(),
        sim.next_t_s,
        sim.jsonl,
        sim.final_hash,
    )


def test_boundary_reaches_snapshot_callback_and_previous_snapshot(synthetic):
    seen = []

    def callback(snapshot, events, context):
        seen.append(snapshot.env)
        return []

    sim = runner_at(captured(synthetic), decide=callback)
    before = sim.step()
    after = sim.step()
    assert before.env.seawater_c == 21
    assert after.env.seawater_c == 22
    assert (before.env.wind_kn, after.env.wind_kn) == (10, 21)
    assert before.env.sea_state == 2 and after.env.sea_state == 4
    assert before.env.layered_water and after.env.layered_water
    assert seen == [before.env, after.env]
    assert before.env.seawater_c == 21
    after.env.forecast.clear()
    assert sim.snapshot.env.forecast


def test_independent_processes_real_cache_and_replay_match(tmp_path):
    code = (
        "from pathlib import Path; from datetime import datetime; "
        "from shipos.sim.env_input import EnvironmentInput; "
        "from shipos.sim.runner import load_scenario; "
        "e=EnvironmentInput.from_cache(Path('data/env_cache/strait_of_sicily.json'), "
        "scenario_start=datetime.fromisoformat('2026-10-17T08:59:59+00:00'),layered_water=False); "
        "r=load_scenario(Path('data/scenarios/B.yaml'),environment=e); r.run(3); "
        "Path(__import__('sys').argv[1]).write_bytes(r.jsonl); print(r.final_hash)"
    )
    results = []
    for index in range(2):
        path = tmp_path / f"run{index}.jsonl"
        process = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            cwd=REPO_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
        results.append((path.read_bytes(), process.stdout.strip()))
    assert results[0] == results[1]
    data, digest = results[0]
    assert verify_jsonl(data, expected_hash=digest) == digest
    assert replay_jsonl(data).jsonl == data


def test_replay_uses_embedded_cache_after_change_and_removal(tmp_path, synthetic, monkeypatch):
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(synthetic), encoding="utf-8")
    source = EnvironmentInput.from_cache(path, scenario_start=START, layered_water=True)
    sim = runner_at(source)
    sim.run(2)
    evidence = sim.jsonl
    path.write_text("not valid JSON", encoding="utf-8")
    assert replay_jsonl(evidence).jsonl == evidence
    path.unlink()

    def no_file_reads(*args, **kwargs):
        raise AssertionError("replay must not read a mutable cache or scenario")

    monkeypatch.setattr(type(path), "read_text", no_file_reads)
    restored = replay_jsonl(evidence)
    assert restored.jsonl == evidence
    assert restored.environment.forecast == sim.environment.forecast
    header = json.loads(evidence.splitlines()[0])["record"]
    assert header["version"] == 2
    assert header["environment"]["cache"] == synthetic
    assert header["environment"]["layered_water"] is True
    assert header["environment"]["max_age_s"] == 86400
    assert header["environment"]["allow_stale"] is False


@pytest.mark.parametrize("failure", ["sst", "gap", "exhausted"])
def test_environment_failure_is_transactional_with_live_and_timeline_events(synthetic, failure):
    if failure == "sst":
        synthetic["sources"]["marine"]["response"]["hourly"]["sea_surface_temperature"][1] = None
    elif failure == "gap":
        for source in synthetic["sources"].values():
            source["response"]["hourly"]["time"][2] += 3600
    boundary = 7200 if failure == "exhausted" else 3600
    source = captured(synthetic)
    sim = runner_at(source, boundary - 1, timeline=[Event(t_s=boundary, kind="cooling_loss")])
    sim.step()
    before = state(sim)
    for _ in range(2):
        with pytest.raises(EnvironmentUnavailable):
            sim.step([Event(t_s=boundary, kind="crew_change", payload={"crew": 6})])
        assert state(sim) == before


def test_callback_failure_rolls_back_environment_measurements_and_live_events(synthetic):
    fail = False
    seen = []

    def callback(snapshot, events, context):
        seen.append(snapshot.env.seawater_c)
        if fail:
            raise RuntimeError("callback failure")
        return []

    # Use installed guests and genuinely noisy measurement state for rollback coverage.
    source = captured(synthetic, scenario_start=START + timedelta(seconds=3599))
    sim = load_scenario(SCENARIO, environment=source, decide=callback)
    control = load_scenario(SCENARIO, environment=source, decide=lambda *args: [])
    assert sim.step() == control.step()
    before = state(sim)
    batch = [Event(t_s=1, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 17})]
    fail = True
    with pytest.raises(RuntimeError):
        sim.step(batch)
    assert state(sim) == before
    fail = False
    assert sim.step(batch) == control.step(batch)
    assert sim.jsonl == control.jsonl
    assert seen == [21, 22, 22]
    assert replay_jsonl(sim.jsonl, decide=lambda *args: []).jsonl == sim.jsonl


def test_inputs_and_public_copies_cannot_mutate_the_run(synthetic):
    source = captured(synthetic)
    sim = runner_at(source)
    synthetic["sources"]["marine"]["response"]["hourly"]["sea_surface_temperature"][0] = 99
    source.record["cache"].clear()
    sim.environment.record.clear()
    snapshot = sim.step()
    assert snapshot.env.seawater_c == 21
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl


def test_live_injection_order_unchanged_with_environment(synthetic):
    source = captured(synthetic, scenario_start=START + timedelta(seconds=3599))
    baseline = load_scenario(SCENARIO)
    context = baseline.context
    sim = Runner(
        baseline.snapshot,
        ship=context.ship,
        manifests=context.manifests,
        priorities=context.priorities,
        environment=source,
        timeline=[Event(t_s=1, kind="crew_change", payload={"crew": 0})],
    )
    sim.step()
    sim.step(
        [
            Event(t_s=1, kind="genset_trip", payload={"gensets_online": 1}),
            Event(t_s=1, kind="battery_report", payload={"guest_id": "G4", "battery_pct": 18}),
            Event(t_s=1, kind="crew_change", payload={"crew": 6}),
        ]
    )
    assert sim.snapshot.env.seawater_c == 22
    assert sim.snapshot.platform.gensets_online == 1
    assert sim.snapshot.guests["G4"].battery_pct == 18
    assert sim.snapshot.crew == 6
    assert [e["kind"] for e in json.loads(sim.jsonl.splitlines()[-1])["record"]["events"]] == [
        "crew_change",
        "genset_trip",
        "battery_report",
        "crew_change",
    ]
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl


def test_real_cache_snapshot_and_same_captured_forecast_windows(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline path attempted network")

    monkeypatch.setattr(socket, "socket", no_network)

    class NoWallClock(datetime):
        @classmethod
        def now(cls, *args, **kwargs):
            raise AssertionError("offline path read the wall clock")

        @classmethod
        def today(cls):
            raise AssertionError("offline path read the wall clock")

    monkeypatch.setattr("shipos.sim.env.datetime", NoWallClock)
    monkeypatch.setattr("shipos.sim.env_input.datetime", NoWallClock)
    source = EnvironmentInput.from_cache(CACHE, scenario_start=START, layered_water=False)
    sim = load_scenario(SCENARIO, environment=source)
    snapshot = sim.step()
    assert (snapshot.env.seawater_c, snapshot.env.wind_kn) == (24.5, 5.4)
    intervals = find_installation_windows(
        sim.context.ship,
        sim.environment.forecast,
        operation="ramp",
        crew=6,
        speed_kn=6,
        people_available=4,
        local_tz=timezone(timedelta(hours=2)),
    )
    assert intervals[0].duration_s == 2700
    assert intervals[0].start_s == 0
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl


def test_stale_and_naive_inputs_fail_explicitly():
    with pytest.raises(EnvironmentUnavailable, match="stale"):
        EnvironmentInput.from_cache(
            CACHE, scenario_start=START + timedelta(days=1), layered_water=False, max_age_s=0
        )
    source = EnvironmentInput.from_cache(
        CACHE, scenario_start=START, layered_water=False, max_age_s=0, allow_stale=True
    )
    sim = load_scenario(SCENARIO, environment=source)
    sim.step()
    assert sim.environment.forecast.stale
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl
    with pytest.raises(ValueError, match="timezone"):
        EnvironmentInput.from_cache(
            CACHE, scenario_start=START.replace(tzinfo=None), layered_water=False
        )


def test_aware_start_offset_and_explicit_layering_are_captured():
    start = START.astimezone(timezone(timedelta(hours=2)))
    source = EnvironmentInput.from_cache(CACHE, scenario_start=start, layered_water=True)
    assert source.record["scenario_start"] == "2026-10-17T10:00:00+02:00"
    restored = EnvironmentInput.from_record(source.record)
    assert restored.record == source.record
    assert restored.env_at().layered_water
    assert restored.env_at() == source.env_at()


def test_changed_captured_input_changes_replay_evidence(synthetic):
    first = runner_at(captured(synthetic))
    synthetic["sources"]["marine"]["response"]["hourly"]["sea_surface_temperature"][0] += 1
    second = runner_at(captured(synthetic))
    assert first.step().env.seawater_c + 1 == second.step().env.seawater_c
    assert first.final_hash != second.final_hash
    assert replay_jsonl(first.jsonl).jsonl == first.jsonl
    assert replay_jsonl(second.jsonl).jsonl == second.jsonl


def test_no_environment_scenario_retains_baseline_bytes_and_hash():
    sim = load_scenario(SCENARIO)
    sim.run(721)
    assert sim.environment is None
    assert sim.final_hash == "3e43733a289dabdbd77b2cda1bbea4cc176f153bf9e6eb101e205b7b691712d7"
    assert json.loads(sim.jsonl.splitlines()[0])["record"]["version"] == 1
    assert replay_jsonl(sim.jsonl).jsonl == sim.jsonl
