"""Power and heat numbers from Annex A, and the Annex B arithmetic (PLAN §1)."""

import pytest

from shipos.contracts import Line, SpeedBand
from shipos.physics import cooling, power


@pytest.mark.parametrize(
    ("kn", "band"),
    [
        (0, SpeedBand.STOPPED),
        (3, SpeedBand.SLOW),
        (5, SpeedBand.SLOW),
        (12, SpeedBand.CRUISE),
        (20, SpeedBand.FAST),
    ],
)
def test_speed_band(ship, kn, band):
    assert power.speed_band(ship, kn) == band


def test_reserve_cruise_and_degraded(ship):
    assert power.reserve_kw(ship, SpeedBand.CRUISE, 2) == 200
    assert power.reserve_kw(ship, SpeedBand.CRUISE, 1) == 100
    assert power.usable_kw(ship, SpeedBand.SLOW, 1) == 100  # spinning reserve default 0
    assert power.peak_extra_kw(ship, 2) == 250 and power.peak_extra_kw(ship, 1) == 100


def test_lines_after_trip(ship):
    assert power.line_capacity_kw(ship, Line.PORT, 2) == 150
    assert power.line_capacity_kw(ship, Line.STBD, 1) == 0  # lost with genset 2
    assert power.surviving_lines(ship, 1) == [Line.PORT]


def test_all_five_fit_at_cruise(ship, guests):
    # Annex B arithmetic covers G1-G5 only; later catalog cards (e.g. G6) are not in it.
    worst = sum(power.max_sustained_kw(guests[gid]) for gid in ("G1", "G2", "G3", "G4", "G5"))
    assert worst == pytest.approx(126.5)  # incl. the 20 kW winch
    no_winch = 1.5 + 6 + 25 + 35 + 45
    assert no_winch == pytest.approx(112.5)
    assert worst <= power.reserve_kw(ship, SpeedBand.CRUISE, 2)


def test_degraded_does_not_fit(ship, guests):
    g = guests
    demand = g["G5"].mode("on").power_kw + g["G4"].mode("charging").power_kw
    demand += g["G3"].mode("charging").power_kw
    assert demand == 105 > power.reserve_kw(ship, SpeedBand.CRUISE, 1)


@pytest.mark.parametrize(
    ("t", "degraded", "kw"),
    [
        (12, False, 160),
        (30, False, 120),
        (24, False, 133.333),
        (30, True, 72),
        (24, True, 80),
        (35, False, 120),
        (5, False, 160),
    ],
)
def test_loop_capacity(ship, t, degraded, kw):
    assert cooling.loop_capacity_kw(ship, t, degraded) == pytest.approx(kw, abs=1e-3)


def test_heat_coupling(guests):
    assert cooling.heat_kw(guests["G5"], 45) == 45  # water-cooled: 1 kW electric = 1 kW heat
    assert cooling.heat_kw(guests["G1"], 1.5) == 0  # air-cooled mast guest: not on the loop


def test_inertia_buffer_once_per_period(ship):
    buf = cooling.InertiaBuffer()
    ok, buf = buf.absorb(ship, t_s=0, excess_kw=25, duration_s=50)
    assert ok
    ok, _ = buf.absorb(ship, t_s=300, excess_kw=10, duration_s=10)
    assert not ok  # inside the 10-min period
    ok, _ = buf.absorb(ship, t_s=601, excess_kw=10, duration_s=10)
    assert ok
    ok, _ = cooling.InertiaBuffer().absorb(ship, t_s=0, excess_kw=31, duration_s=10)
    assert not ok  # above +30 kW
