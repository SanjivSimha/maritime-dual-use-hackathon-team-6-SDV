import json
import socket
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from shipos.data import load_ship
from shipos.paths import DATA_DIR
from shipos.sim.env import (
    EnvironmentUnavailable,
    find_installation_windows,
    load_environment,
    sea_state,
)
from shipos.sim.env_refresh import refresh_cache

CACHE = DATA_DIR / "env_cache/strait_of_sicily.json"
START = datetime(2026, 10, 17, 8, tzinfo=UTC)
LOCAL = timezone(timedelta(hours=2))


@pytest.fixture
def document():
    return json.loads(CACHE.read_text(encoding="utf-8"))


@pytest.fixture
def synthetic(tmp_path, document):
    """Test edits are synthetic inputs, never relabeled as a provider forecast."""

    def make(*, times=None, wave=0.5, wind=20, temperature=24.5, start=START):
        data = deepcopy(document)
        data["kind"] = "synthetic_demo"
        times = (
            times if times is not None else [int(START.timestamp()) + i * 3600 for i in range(25)]
        )
        for source in data["sources"].values():
            source["url"] = None
            source["retrieved_at"] = None
        marine = data["sources"]["marine"]["response"]["hourly"]
        weather = data["sources"]["weather"]["response"]["hourly"]
        marine.update(
            time=times,
            wave_height=[wave] * len(times),
            sea_surface_temperature=[temperature] * len(times),
        )
        weather.update(time=times.copy(), wind_speed_10m=[wind] * len(times))
        path = tmp_path / "synthetic.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path, load_environment(path, scenario_start=start)

    return make


def windows(forecast, **kwargs):
    return find_installation_windows(
        load_ship(),
        forecast,
        operation="flight_deck",
        crew=kwargs.pop("crew", 14),
        speed_kn=kwargs.pop("speed_kn", 6),
        local_tz=kwargs.pop("local_tz", LOCAL),
        **kwargs,
    )


def test_real_cache_offline_and_repeatable(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network is disabled")

    monkeypatch.setattr(socket, "socket", no_network)
    first = load_environment(CACHE, scenario_start=START)
    second = load_environment(CACHE, scenario_start=START)
    assert first == second
    assert first.kind == "open_meteo_forecast" and not first.stale
    assert first.env_at(layered_water=False) == second.env_at(layered_water=False)
    assert windows(first) == windows(second)
    assert windows(first)
    assert first.env_at(layered_water=False).seawater_c == 24.5
    assert first.env_at(layered_water=False).wind_kn == 5.4
    assert first.env_at(layered_water=False).sea_state == sea_state(0.24)
    assert first.env_at(layered_water=True).layered_water
    assert first.points[0].t_s == -8 * 3600


def test_non_hour_start_and_equivalent_timezones():
    at = START + timedelta(minutes=30)
    forecast = load_environment(CACHE, scenario_start=at)
    other = load_environment(CACHE, scenario_start=at.astimezone(LOCAL))
    assert forecast == other
    assert forecast.env_at(layered_water=False).forecast[0].t_s == 0
    assert forecast.env_at(layered_water=False).forecast[1].t_s == 1800
    assert forecast.env_at(1799, layered_water=False).wind_kn == 5.4
    assert forecast.env_at(1800, layered_water=False).wind_kn == 5.7
    with pytest.raises(ValueError, match="explicit timezone"):
        load_environment(CACHE, scenario_start=at.replace(tzinfo=None))


@pytest.mark.parametrize(
    "height,expected",
    [
        (0, 0),
        (0.1, 1),
        (0.5, 2),
        (1.25, 3),
        (1.250001, 4),
        (2.5, 4),
        (4, 5),
        (6, 6),
        (9, 7),
        (14, 8),
        (14.1, 9),
    ],
)
def test_wave_code_boundaries(height, expected):
    assert sea_state(height) == expected


@pytest.mark.parametrize("height", [-1, float("nan"), float("inf"), True])
def test_invalid_wave_values(height):
    with pytest.raises(ValueError):
        sea_state(height)


@pytest.mark.parametrize(
    "source,variable,unit",
    [
        ("weather", "wind_speed_10m", "km/h"),
        ("marine", "wave_height", "ft"),
        ("marine", "sea_surface_temperature", "\u00b0F"),
    ],
)
def test_wrong_units_rejected(tmp_path, document, source, variable, unit):
    document["sources"][source]["response"]["hourly_units"][variable] = unit
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="units"):
        load_environment(path, scenario_start=START)


def test_misaligned_provider_timestamps_are_not_zipped(tmp_path, document):
    hourly = document["sources"]["weather"]["response"]["hourly"]
    hourly["time"] = [t + 1800 for t in hourly["time"]]
    path = tmp_path / "misaligned.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    forecast = load_environment(path, scenario_start=START)
    assert not forecast.points
    assert windows(forecast) == ()
    with pytest.raises(EnvironmentUnavailable, match="coverage"):
        forecast.env_at(layered_water=False)


def test_nulls_gaps_and_final_sample_do_not_extend_favorable_windows(synthetic):
    times = [int(START.timestamp()) + i * 3600 for i in [0, 1, 3, 4]]
    _, forecast = synthetic(times=times)
    assert [(w.start_s, w.end_s) for w in windows(forecast)] == [(0, 3600), (10800, 14400)]
    for t in [3600, 7200, 14400, 99999]:
        with pytest.raises(EnvironmentUnavailable, match="coverage"):
            forecast.env_at(t, layered_water=False)
    assert len(forecast.env_at(layered_water=False).forecast) == 2
    assert windows(forecast, start_s=3600, end_s=10800) == ()
    path, _ = synthetic()
    data = json.loads(path.read_text())
    data["sources"]["marine"]["response"]["hourly"]["wave_height"][1] = None
    path.write_text(json.dumps(data))
    forecast = load_environment(path, scenario_start=START)
    assert windows(forecast)[0].start_s == 7200


def test_missing_sst_has_no_default_but_does_not_block_wave_wind_window(synthetic):
    path, _ = synthetic(temperature=None)
    forecast = load_environment(path, scenario_start=START)
    assert all(point.seawater_c is None for point in forecast.points)
    assert windows(forecast)
    with pytest.raises(EnvironmentUnavailable, match="temperature"):
        forecast.env_at(layered_water=False)
    data = json.loads(path.read_text())
    del data["sources"]["marine"]["response"]["hourly"]["sea_surface_temperature"]
    del data["sources"]["weather"]["response"]["hourly"]["wind_speed_10m"]
    path.write_text(json.dumps(data))
    assert windows(load_environment(path, scenario_start=START)) == ()


def test_stale_and_out_of_range_are_explicit():
    later = START + timedelta(days=2)
    with pytest.raises(EnvironmentUnavailable, match="stale"):
        load_environment(CACHE, scenario_start=later)
    forecast = load_environment(CACHE, scenario_start=later, allow_stale=True)
    assert forecast.stale
    with pytest.raises(EnvironmentUnavailable):
        forecast.env_at(layered_water=False)
    assert windows(forecast, end_s=3600) == ()
    assert windows(forecast) == ()


def test_freshness_exact_age_boundary_is_deterministic():
    forecast = load_environment(CACHE, scenario_start=START)
    boundary = forecast.retrieved_at + timedelta(seconds=86400)
    assert not load_environment(CACHE, scenario_start=boundary).stale
    with pytest.raises(EnvironmentUnavailable, match="stale"):
        load_environment(CACHE, scenario_start=boundary + timedelta(microseconds=1))


def test_operation_cannot_cross_into_unfavorable_hour(synthetic):
    path, _ = synthetic()
    data = json.loads(path.read_text())
    data["sources"]["weather"]["response"]["hourly"]["wind_speed_10m"][1] = 21
    path.write_text(json.dumps(data))
    forecast = load_environment(path, scenario_start=START)
    assert windows(forecast, start_s=1800, end_s=3600)
    assert windows(forecast, start_s=1801, end_s=3601) == ()


def test_local_timezone_and_operation_inputs_are_explicit(synthetic):
    _, forecast = synthetic()
    with pytest.raises(ValueError, match="explicit timezone"):
        windows(forecast, local_tz=None)
    with pytest.raises(ValueError, match="shorter"):
        windows(forecast, duration_min=29)
    assert windows(forecast, duration_min=60, end_s=3600)[0].duration_s == 3600
    with pytest.raises(ValueError, match="people_available"):
        windows(forecast, people_available=15)


@pytest.mark.parametrize("operation,minutes", [("flight_deck", 30), ("ramp", 45)])
def test_entire_duration_and_threshold_boundaries(synthetic, operation, minutes):
    _, forecast = synthetic(wave=1.25, wind=20)
    ship = load_ship()
    common = dict(operation=operation, crew=14, speed_kn=4, local_tz=LOCAL)
    result = find_installation_windows(ship, forecast, end_s=minutes * 60, **common)
    assert len(result) == 1
    assert result[0].start_s == result[0].latest_start_s == 0
    assert result[0].end_s == result[0].duration_s == minutes * 60
    assert find_installation_windows(ship, forecast, end_s=minutes * 60 - 1, **common) == ()
    assert find_installation_windows(ship, forecast, end_s=minutes * 60, **dict(common, speed_kn=8))
    assert find_installation_windows(ship, forecast, **dict(common, speed_kn=8.01)) == ()
    assert find_installation_windows(ship, forecast, **dict(common, speed_kn=3.99)) == ()
    _, rough = synthetic(wave=1.250001)
    assert find_installation_windows(ship, rough, **common) == ()
    _, windy = synthetic(wind=20.0001)
    assert find_installation_windows(ship, windy, **common) == ()
    people = (
        ship.install_window.people_flight_deck
        if operation == "flight_deck"
        else ship.install_window.people_ramp
    )
    assert find_installation_windows(ship, forecast, people_available=people, **common)
    assert find_installation_windows(ship, forecast, people_available=people - 1, **common) == ()


def test_six_crew_daylight_boundaries_across_days(synthetic):
    start = datetime(2026, 10, 17, 4, tzinfo=UTC)  # local 06:00
    times = [int(start.timestamp()) + i * 3600 for i in range(28)]
    _, forecast = synthetic(times=times, start=start)
    result = windows(forecast, crew=6)
    assert [(w.start_s, w.end_s) for w in result] == [(3600, 46800), (90000, 97200)]
    assert windows(forecast, crew=6, start_s=45000, end_s=46800)[0].latest_start_s == 45000
    assert windows(forecast, crew=6, start_s=45001, end_s=46800) == ()
    assert windows(forecast, crew=6, end_s=3600) == ()
    assert windows(forecast, crew=14, end_s=3600)
    assert windows(forecast, crew=0) == ()


@pytest.mark.parametrize(
    "failure", ["timeout", "http_error", "partial", "units", "gap", "short_horizon"]
)
def test_failed_refresh_preserves_existing_cache(tmp_path, document, failure):
    path = tmp_path / "existing.json"
    before = CACHE.read_bytes()
    path.write_bytes(before)
    calls = []

    def handler(request):
        source = "marine" if "marine-api" in request.url.host else "weather"
        calls.append(source)
        if source == "weather":
            if failure == "timeout":
                raise httpx.ReadTimeout("bounded test timeout", request=request)
            if failure == "http_error":
                return httpx.Response(503)
        body = deepcopy(document["sources"][source]["response"])
        if failure == "short_horizon":
            body["hourly"] = {key: values[:2] for key, values in body["hourly"].items()}
        if source == "weather" and failure == "partial":
            body["hourly"]["wind_speed_10m"][1] = None
        if source == "weather" and failure == "units":
            body["hourly_units"]["wind_speed_10m"] = "km/h"
        if source == "weather" and failure == "gap":
            body["hourly"]["time"][1] += 1
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises((httpx.HTTPError, ValueError)):
            refresh_cache(path, client=client)
    assert calls == ["marine", "weather"]
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_successful_refresh_records_actual_requests_and_units(tmp_path, document):
    def handler(request):
        assert request.url.params["timezone"] == "GMT"
        assert request.url.params["timeformat"] == "unixtime"
        source = "marine" if "marine-api" in request.url.host else "weather"
        if source == "weather":
            assert request.url.params["wind_speed_unit"] == "kn"
        return httpx.Response(200, json=document["sources"][source]["response"])

    path = tmp_path / "new.json"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert refresh_cache(path, client=client) == path
    data = json.loads(path.read_text())
    assert data["kind"] == "open_meteo_forecast"
    assert data["requested"] == {"latitude": 36.5, "longitude": 12.5}
    for source in data["sources"].values():
        assert source["url"].startswith("https://")
        assert datetime.fromisoformat(source["retrieved_at"]).tzinfo is not None
        assert source["response"]["latitude"]
    assert len(load_environment(path, scenario_start=START).points) == 48
