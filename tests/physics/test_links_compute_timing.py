import pytest

from shipos.contracts import Course, DeckState, Emission, Env, Link, Noise, ShipState
from shipos.physics import compute, links, timing


def state(**kw):
    base = dict(
        speed_kn=3,
        course=Course.STEADY,
        noise=Noise.QUIET,
        deck=DeckState.CLEAR,
        emission=Emission.NORMAL,
    )
    base.update(kw)
    return ShipState(**base)


ENV = Env(seawater_c=24, sea_state=3, wind_kn=10)


def test_satcom_worst_case_and_silence(ship):
    assert links.capacity_mbps(ship, Link.SATCOM, None, None, worst=True) == pytest.approx(0.9)
    silent = state(emission=Emission.SILENT)
    assert links.capacity_mbps(ship, Link.SATCOM, silent, ENV, worst=False) == 0
    assert links.capacity_mbps(ship, Link.RADIO, silent, ENV, worst=False) == 0


def test_acoustic_drops_on_manoeuvre_machinery_layering(ship):
    assert links.acoustic_up(state(), ENV)
    assert not links.acoustic_up(state(noise=Noise.MANOEUVRING), ENV)
    assert not links.acoustic_up(state(deck_machinery_active=True), ENV)
    assert not links.acoustic_up(
        state(), Env(seawater_c=24, sea_state=3, wind_kn=10, layered_water=True)
    )


def test_compute_allocatable(ship):
    cap = compute.allocatable(ship)
    assert cap.cores == 102 and cap.mem_gb == pytest.approx(409.6) and cap.accelerators == 3


def test_safety_command_cannot_cross_acoustic_link(ship, guests):
    r = timing.safety_command_check(ship, guests["G3"])
    assert r.demand == 60 and r.capacity == pytest.approx(0.2)
    assert not r.ok  # 60 s acoustic latency really exceeds the 200 ms safety ceiling ...
    assert not r.hard  # ... but the autonomous hold_depth fallback covers it, so it does not block
    assert r.facts["fallback"] == "hold_depth"


def test_latency_ceiling(ship):
    assert timing.ceiling_s(ship, "arbitration") == 2
    assert not timing.latency_check(ship, "arbitration", 2.5).ok
