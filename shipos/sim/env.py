"""Offline forecast conversion and pure installation-window calculation."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from itertools import pairwise
from pathlib import Path
from typing import Literal

from shipos.assumptions import A
from shipos.contracts import Env, ForecastPoint, ShipSpec

HOLD_S = int(A("environment.sample_hold_s"))
MAX_AGE_S = float(A("environment.cache_max_age_s"))
DAYLIGHT_START = int(A("environment.daylight_start_hour"))
DAYLIGHT_END = int(A("environment.daylight_end_hour"))


class EnvironmentUnavailable(ValueError):
    """Unknown coverage, temperature, or stale data; never replaced with favorable defaults."""


def _finite(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must have an explicit timezone")
    return value.astimezone(UTC)


def sea_state(wave_height_m: float) -> int:
    """WMO 3700 height guidance; exact bounds belong to the lower code figure."""
    height = _finite(wave_height_m, "wave_height_m")
    if height < 0:
        raise ValueError("wave_height_m cannot be negative")
    # Published boundaries, not invented physical assumptions. See ENVIRONMENT.md.
    for code, upper in enumerate((0, 0.1, 0.5, 1.25, 2.5, 4, 6, 9, 14)):
        if height <= upper:
            return code
    return 9


def _series(document: dict, source: str, variable: str, unit: str) -> dict[int, float | None]:
    response = document["sources"][source]["response"]
    if response["utc_offset_seconds"] != 0 or response["timezone"] not in ("GMT", "UTC"):
        raise ValueError("cache responses must use UTC/GMT with zero offset")
    units, hourly = response["hourly_units"], response["hourly"]
    if units["time"] != "unixtime":
        raise ValueError("cache times must be Unix seconds")
    times = hourly["time"]
    if any(type(t) is not int for t in times) or times != sorted(set(times)):
        raise ValueError("cache times must be unique increasing integer Unix seconds")
    values = hourly.get(variable, [None] * len(times))
    if variable in hourly and units.get(variable) != unit:
        raise ValueError(f"{variable} requires units {unit!r}")
    if len(values) != len(times):
        raise ValueError(f"{variable} length differs from timestamps")
    result = {}
    for t, value in zip(times, values, strict=True):
        if value is not None:
            value = _finite(value, variable)
            if variable != "sea_surface_temperature" and value < 0:
                raise ValueError(f"{variable} cannot be negative")
        result[t] = value
    return result


def _samples(document: dict, *, complete: bool = False) -> list[tuple]:
    if document["version"] != 1 or document["kind"] not in (
        "open_meteo_forecast",
        "synthetic_demo",
    ):
        raise ValueError("unsupported environment cache format")
    waves = _series(document, "marine", "wave_height", "m")
    temperatures = _series(document, "marine", "sea_surface_temperature", "\u00b0C")
    winds = _series(document, "weather", "wind_speed_10m", "kn")
    if complete and (
        len(waves) < 2
        or waves.keys() != winds.keys()
        or any(v is None for series in (waves, temperatures, winds) for v in series.values())
        or any(b - a != HOLD_S for a, b in pairwise(waves))
    ):
        raise EnvironmentUnavailable("partial or gapped refresh; existing cache retained")
    return [
        (t, sea_state(waves[t]), winds[t], temperatures[t])
        for t in sorted(waves.keys() & winds.keys())
        if waves[t] is not None and winds[t] is not None
    ]


@dataclass(frozen=True)
class EnvironmentForecast:
    scenario_start: datetime
    kind: str
    retrieved_at: datetime | None
    stale: bool
    points: tuple[ForecastPoint, ...]

    def segments(self):
        """Only consecutive known hourly endpoints establish a held interval."""
        for left, right in pairwise(self.points):
            if right.t_s - left.t_s == HOLD_S:
                yield left, right.t_s

    def env_at(self, t_s: float = 0, *, layered_water: bool) -> Env:
        if type(layered_water) is not bool:
            raise ValueError("layered_water must be an explicit boolean scenario input")
        t_s = _finite(t_s, "t_s")
        if t_s < 0:
            raise ValueError("t_s cannot be negative")
        for index, (left, right) in enumerate(pairwise(self.points)):
            if left.t_s <= t_s < right.t_s and right.t_s - left.t_s == HOLD_S:
                if left.seawater_c is None:
                    raise EnvironmentUnavailable("sea-surface temperature unavailable at this time")
                future = [
                    ForecastPoint(
                        t_s=t_s,
                        sea_state=left.sea_state,
                        wind_kn=left.wind_kn,
                        seawater_c=left.seawater_c,
                    )
                ]
                # A contract forecast cannot encode unknowns: expose only this contiguous block.
                for previous, point in pairwise(self.points[index:]):
                    if point.t_s - previous.t_s != HOLD_S:
                        break
                    future.append(point.model_copy(deep=True))
                return Env(
                    seawater_c=left.seawater_c,
                    sea_state=left.sea_state,
                    wind_kn=left.wind_kn,
                    layered_water=layered_water,
                    forecast=future,
                )
        raise EnvironmentUnavailable("no complete hourly coverage at requested simulation time")


def load_environment(
    path: Path,
    *,
    scenario_start: datetime,
    max_age_s: float = MAX_AGE_S,
    allow_stale: bool = False,
) -> EnvironmentForecast:
    """Read only local data; freshness is relative to scenario_start, never the wall clock."""
    start = _utc(scenario_start)
    max_age_s = _finite(max_age_s, "max_age_s")
    if max_age_s < 0:
        raise ValueError("max_age_s cannot be negative")
    document = json.loads(path.read_text(encoding="utf-8"))
    try:
        samples = _samples(document)
        retrieved = None
        if document["kind"] == "open_meteo_forecast":
            retrieved = min(
                _utc(datetime.fromisoformat(document["sources"][s]["retrieved_at"]))
                for s in ("marine", "weather")
            )
        stale = retrieved is not None and (start - retrieved).total_seconds() > max_age_s
        if stale and not allow_stale:
            raise EnvironmentUnavailable(
                "stale cache relative to scenario start; refresh or allow_stale"
            )
        points = tuple(
            ForecastPoint(
                t_s=(datetime.fromtimestamp(t, UTC) - start).total_seconds(),
                sea_state=state,
                wind_kn=wind,
                seawater_c=temp,
            )
            for t, state, wind, temp in samples
        )
        return EnvironmentForecast(start, document["kind"], retrieved, stale, points)
    except (KeyError, TypeError) as exc:
        raise ValueError("malformed environment cache") from exc


@dataclass(frozen=True)
class InstallationWindow:
    start_s: float
    end_s: float
    duration_s: float

    @property
    def latest_start_s(self) -> float:
        return self.end_s - self.duration_s


def find_installation_windows(
    ship: ShipSpec,
    forecast: EnvironmentForecast,
    *,
    operation: Literal["flight_deck", "ramp"],
    crew: int,
    speed_kn: float,
    local_tz: tzinfo,
    start_s: float = 0,
    end_s: float | None = None,
    people_available: int | None = None,
    duration_min: float | None = None,
) -> tuple[InstallationWindow, ...]:
    """Pure maximal eligible intervals; fixed speed/personnel must hold for the whole operation."""
    if operation not in ("flight_deck", "ramp"):
        raise ValueError("operation must be flight_deck or ramp")
    if type(crew) is not int or crew not in ship.crew_levels:
        raise ValueError("crew must be a declared ship crew level")
    people = crew if people_available is None else people_available
    if type(people) is not int or not 0 <= people <= crew:
        raise ValueError("people_available must be an integer between zero and crew")
    if local_tz is None or forecast.scenario_start.astimezone(local_tz).utcoffset() is None:
        raise ValueError("local_tz must be an explicit timezone")
    speed_kn, start_s = _finite(speed_kn, "speed_kn"), _finite(start_s, "start_s")
    end_s = (
        max(start_s, forecast.points[-1].t_s if forecast.points else start_s)
        if end_s is None
        else _finite(end_s, "end_s")
    )
    if start_s < 0 or end_s < start_s:
        raise ValueError("horizon requires 0 <= start_s <= end_s")
    limits = ship.install_window
    required = limits.people_flight_deck if operation == "flight_deck" else limits.people_ramp
    minimum = limits.drone_swap_min if operation == "flight_deck" else limits.vehicle_swap_min
    duration = minimum if duration_min is None else _finite(duration_min, "duration_min")
    if duration < minimum or duration <= 0:
        raise ValueError("duration_min cannot be shorter than the ship operation minimum")
    if not crew or people < required or not limits.speed_kn.contains(speed_kn):
        return ()
    intervals = []
    for point, stop in forecast.segments():
        if point.sea_state > limits.sea_state_max or point.wind_kn > limits.wind_max_kn:
            continue
        begin, stop = max(start_s, point.t_s), min(end_s, stop)
        if begin >= stop:
            continue
        if crew == 6:
            day = (forecast.scenario_start + timedelta(seconds=begin)).astimezone(local_tz).date()
            while True:
                opening = datetime.combine(day, time(DAYLIGHT_START), tzinfo=local_tz)
                closing = datetime.combine(day, time(DAYLIGHT_END), tzinfo=local_tz)
                lo = (opening.astimezone(UTC) - forecast.scenario_start).total_seconds()
                hi = (closing.astimezone(UTC) - forecast.scenario_start).total_seconds()
                if lo >= stop:
                    break
                if max(begin, lo) < min(stop, hi):
                    intervals.append((max(begin, lo), min(stop, hi)))
                day += timedelta(days=1)
        else:
            intervals.append((begin, stop))
    merged = []
    for begin, stop in intervals:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(stop, merged[-1][1]))
        else:
            merged.append((begin, stop))
    need_s = duration * 60
    return tuple(
        InstallationWindow(begin, stop, need_s) for begin, stop in merged if stop - begin >= need_s
    )
