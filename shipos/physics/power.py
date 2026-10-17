"""Electrical power (A.1, A.9): reserve by speed band and gensets, peaks, mission lines."""

from shipos.contracts import GuestManifest, Line, ShipSpec, SpeedBand


def speed_band(ship: ShipSpec, speed_kn: float) -> SpeedBand:
    for band in SpeedBand:
        if ship.speed_bands_kn[band].contains(speed_kn):
            return band
    raise ValueError(f"speed {speed_kn} kn is in no band")


def full_gensets(ship: ShipSpec) -> int:
    return max(max(by_gensets) for by_gensets in ship.power.reserve_kw.values())


def reserve_kw(ship: ShipSpec, band: SpeedBand, gensets: int) -> float:
    return ship.power.reserve_kw[band][gensets]


def usable_kw(ship: ShipSpec, band: SpeedBand, gensets: int) -> float:
    """Reserve the governor may allocate: with a genset down, keep the spinning reserve free."""
    held = ship.power.degraded_spinning_reserve_kw if gensets < full_gensets(ship) else 0
    return reserve_kw(ship, band, gensets) - held


def peak_extra_kw(ship: ShipSpec, gensets: int) -> float:
    return ship.power.peak_extra_kw[gensets]


def surviving_lines(ship: ShipSpec, gensets: int) -> list[Line]:
    lines = list(ship.power.lines)
    if gensets < full_gensets(ship):
        lines.remove(ship.power.line_lost_on_trip)
    return lines


def line_capacity_kw(ship: ShipSpec, line: Line, gensets: int) -> float:
    return ship.power.lines[line] if line in surviving_lines(ship, gensets) else 0


def mode_kw(guest: GuestManifest, mode: str) -> float:
    return guest.mode(mode).power_kw


def max_sustained_kw(guest: GuestManifest, modes: list[str] | None = None) -> float:
    """Highest sustained draw (peaks ≤ peak_s excluded) over the allowed modes."""
    names = modes if modes is not None else [m.name for m in guest.modes]
    return max(mode_kw(guest, n) for n in names)
