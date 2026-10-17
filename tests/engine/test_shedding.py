"""D4 degradation (Scenario B minute 12): forecast the budget, shed in a declared order."""

import time

from shipos.contracts import ActionKind, GuestRuntime, ScheduledMode, Verdict
from shipos.engine import shedding
from tests.engine.scenario import scenario_b

MIN = 60


def survey_path(**kw):
    """After D3 (survey primary): G3 holds then re-runs; G4 recovers, charges 20 min, tops up."""
    return scenario_b(
        t_s=12 * MIN,
        gensets=1,
        modes={"G3": "holding"},
        schedules={
            "G4": [
                ScheduledMode(t_s=14 * MIN, mode="recovery"),
                ScheduledMode(t_s=17 * MIN, mode="charging"),
                ScheduledMode(t_s=37 * MIN, mode="topping"),
            ],
            "G3": [ScheduledMode(t_s=23 * MIN, mode="surveying")],
        },
        **kw,
    )


def sar_path(**kw):
    """After D3 (SAR primary): G3 suspended, recovered on the ramp after G4, then recharges."""
    return scenario_b(
        t_s=12 * MIN,
        gensets=1,
        modes={"G3": "holding"},
        schedules={
            "G4": [
                ScheduledMode(t_s=14 * MIN, mode="recovery"),
                ScheduledMode(t_s=17 * MIN, mode="charging"),
                ScheduledMode(t_s=37 * MIN, mode="topping"),
            ],
            "G3": [
                ScheduledMode(t_s=17 * MIN, mode="recovering"),
                ScheduledMode(t_s=32 * MIN, mode="charging"),
            ],
        },
        **kw,
    )


def test_survey_path_fits_and_arms_the_ladder(ship, guests, survey):
    d = shedding.plan(ship, guests, survey_path(), survey, protected=frozenset({"G4"}))
    assert d.verdict == Verdict.NO_ACTION
    assert d.binding.capacity == 100 and d.binding.demand == 82.5
    armed = [a.actions[0].guest for a in d.alternatives]
    assert armed[0] == "G5" and "G4" not in armed  # declared order; winner protected
    assert {c.guest for c in d.costs} == {"G1", "G3", "G4", "G5"}  # every guest's loss stated


def test_sar_path_defers_the_vehicle_charge(ship, guests, sar):
    d = shedding.plan(ship, guests, sar_path(), sar, protected=frozenset({"G4"}))
    assert d.verdict == Verdict.SHED
    assert d.binding.demand == 106.5 and d.binding.capacity == 100
    (step,) = d.chosen.actions
    assert step.guest == "G3" and step.kind == ActionKind.DEFER and step.delay_min == 5
    assert next(c for c in d.costs if c.guest == "G5").amount == 0  # node kept for the rescue


def test_same_overlap_under_survey_throttles_the_node(ship, guests, survey):
    d = shedding.plan(ship, guests, sar_path(), survey, protected=frozenset({"G4"}))
    (step,) = d.chosen.actions
    assert step.guest == "G5" and step.kind == ActionKind.THROTTLE and step.to_mode == "low_power"
    assert step.kw_recovered == 37


def test_spinning_reserve_forces_the_brief_sequence(ship, guests, survey):
    held = ship.model_copy(
        update={"power": ship.power.model_copy(update={"degraded_spinning_reserve_kw": 20})}
    )
    d = shedding.plan(held, guests, survey_path(), survey, protected=frozenset({"G4"}))
    assert d.verdict == Verdict.SHED
    assert [(a.guest, a.to_mode) for a in d.chosen.actions] == [("G5", "low_power")]


def test_timing_ceilings(ship, guests, sar):
    d = shedding.plan(ship, guests, sar_path(), sar, protected=frozenset({"G4"}))
    first = min(a.t_offset_s for a in d.chosen.actions)
    last = max(a.t_offset_s for a in d.chosen.actions)
    assert first <= ship.timing.shed_first_s and last <= ship.timing.shed_full_s
    assert all(r.ok for r in d.checks if r.resource.startswith("time.shed"))


def test_throttle_before_shed_and_mission_critical_last(ship, guests, sar):
    # nothing deferrable: a deep deficit must be met by throttling before switching off
    snap = sar_path(battery=18).model_copy(update={"platform": sar_path().platform})
    held = ship.model_copy(
        update={"power": ship.power.model_copy(update={"degraded_spinning_reserve_kw": 40})}
    )
    d = shedding.plan(held, guests, snap, sar, protected=frozenset({"G4"}))
    tiers = [a.kind for a in d.chosen.actions]
    order = {ActionKind.DEFER: 0, ActionKind.THROTTLE: 0, ActionKind.STANDBY: 1, ActionKind.SHED: 2}
    assert [order[k] for k in tiers] == sorted(order[k] for k in tiers)


def test_safety_critical_never_shed_automatically(ship, guests, survey):
    g5 = guests["G5"].model_copy(update={"safety_critical": True})
    held = ship.model_copy(
        update={"power": ship.power.model_copy(update={"degraded_spinning_reserve_kw": 20})}
    )
    d = shedding.plan(
        held, {**guests, "G5": g5}, survey_path(), survey, protected=frozenset({"G4"})
    )
    assert all(a.guest != "G5" for a in d.chosen.actions)


def test_overdraw_detected_and_handled(ship, guests, survey):
    snap = survey_path()
    g5 = snap.guests["G5"].model_copy(update={"measured_kw": 45 * 1.16})
    snap = snap.model_copy(update={"guests": {**snap.guests, "G5": g5}})
    d = shedding.overdraw(ship, guests, snap, survey, persisted_s={"G5": 15})
    assert d.verdict == Verdict.SHED and d.chosen.actions[0].kind == ActionKind.THROTTLE
    assert "52.2 kW" in d.rationale and "45 kW" in d.rationale
    warn = shedding.overdraw(
        ship,
        guests,
        snap.model_copy(
            update={
                "guests": {**snap.guests, "G5": g5.model_copy(update={"measured_kw": 45 * 1.11})}
            }
        ),
        survey,
        persisted_s={"G5": 15},
    )
    assert warn.verdict == Verdict.NO_ACTION and "warn" in warn.rationale


def test_fast(ship, guests, sar):
    t0 = time.perf_counter()
    shedding.plan(ship, guests, sar_path(), sar, protected=frozenset({"G4"}))
    assert time.perf_counter() - t0 < ship.timing.shed_first_s


def test_runtime_import():
    assert GuestRuntime


def test_armed_ladder_follows_the_priority_file(ship, guests, survey, sar):
    """B.G5: first to shed under degradation — or last in disaster relief."""
    order = {
        p.id: [
            a.actions[0].guest
            for a in shedding.plan(
                ship, guests, survey_path(), p, protected=frozenset({"G4"})
            ).alternatives
        ]
        for p in (survey, sar)
    }
    assert order["survey_primary"][0] == "G5" and order["sar_primary"][-1] == "G5"


def _overdrawn(snap, gid, kw, mode=None):
    rt = snap.guests[gid].model_copy(update={"measured_kw": kw, **({"mode": mode} if mode else {})})
    return snap.model_copy(update={"guests": {**snap.guests, gid: rt}})


def test_overdraw_never_touches_the_protected_winner(ship, guests, survey):
    snap = _overdrawn(survey_path(), "G4", 2 * 1.5)
    d = shedding.overdraw(
        ship, guests, snap, survey, persisted_s={"G4": 120}, protected=frozenset({"G4"})
    )
    assert d.verdict == Verdict.NO_ACTION


def test_overdraw_never_interrupts_a_non_interruptible_mode(ship, guests, survey):
    snap = _overdrawn(survey_path(), "G3", 25 * 1.3, mode="charging")
    d = shedding.overdraw(ship, guests, snap, survey, persisted_s={"G3": 120})
    assert d.verdict == Verdict.NO_ACTION


def test_search_is_bounded_with_long_schedules(ship, guests, sar):
    long = [
        ScheduledMode(t_s=12 * MIN + 30 * i, mode="charging" if i % 2 else "recovering")
        for i in range(1, 400)
    ]
    snap = sar_path()
    g3 = snap.guests["G3"].model_copy(update={"schedule": long})
    g4_long = [
        ScheduledMode(t_s=12 * MIN + 45 * i, mode="charging" if i % 2 else "topping")
        for i in range(1, 200)
    ]
    g4 = snap.guests["G4"].model_copy(update={"schedule": g4_long})
    snap = snap.model_copy(update={"guests": {**snap.guests, "G3": g3, "G4": g4}})
    t0 = time.perf_counter()
    d = shedding.plan(ship, guests, snap, sar, protected=frozenset({"G4"}))
    assert time.perf_counter() - t0 < ship.timing.shed_first_s
    assert d.verdict in (Verdict.SHED, Verdict.NO_ACTION)
