"""Heat rejection (A.2): chilled-water loop vs seawater temperature, 1:1 power→heat, inertia."""

from __future__ import annotations

from dataclasses import dataclass

from shipos.assumptions import A
from shipos.contracts import CoolingKind, GuestManifest, ShipSpec


def loop_capacity_kw(ship: ShipSpec, seawater_c: float, degraded: bool) -> float:
    c = ship.cooling
    t = min(max(seawater_c, c.t_lo_c), c.t_hi_c)  # clamp: no extrapolation outside the Annex
    cap = c.cap_lo_kw + (c.cap_hi_kw - c.cap_lo_kw) * (t - c.t_lo_c) / (c.t_hi_c - c.t_lo_c)
    return cap * c.degraded_factor if degraded else cap


def heat_kw(guest: GuestManifest, power_kw: float) -> float:
    """Heat a guest puts into the loop. Air-cooled (mast) guests reject to air instead."""
    if guest.cooling != CoolingKind.WATER:
        return 0
    return power_kw * A("heat.kw_per_kw_electric")


@dataclass(frozen=True)
class InertiaBuffer:
    """+inertia_kw for ≤ inertia_s, once per inertia_period_s (A.2)."""

    last_used_s: float | None = None

    def available(self, ship: ShipSpec, t_s: float) -> bool:
        if self.last_used_s is None:
            return True
        return t_s - self.last_used_s >= ship.cooling.inertia_period_s

    def absorb(
        self, ship: ShipSpec, t_s: float, excess_kw: float, duration_s: float
    ) -> tuple[bool, InertiaBuffer]:
        c = ship.cooling
        if excess_kw > c.inertia_kw or duration_s > c.inertia_s or not self.available(ship, t_s):
            return False, self
        return True, InertiaBuffer(last_used_s=t_s)
