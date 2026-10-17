"""Explicit optional network refresh; never imported or called by offline evaluation."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from shipos.sim.env import EnvironmentUnavailable, _finite, _samples

ENDPOINTS = {
    "marine": "https://marine-api.open-meteo.com/v1/marine",
    "weather": "https://api.open-meteo.com/v1/forecast",
}


def refresh_cache(
    path: Path,
    *,
    latitude: float = 36.5,
    longitude: float = 12.5,
    timeout_s: float = 10,
    client: httpx.Client | None = None,
) -> Path:
    """Two bounded GETs, no retries. Validate both full responses before atomic replacement."""
    latitude, longitude = _finite(latitude, "latitude"), _finite(longitude, "longitude")
    timeout_s = _finite(timeout_s, "timeout_s")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or timeout_s <= 0:
        raise ValueError("invalid coordinates or timeout_s")
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "forecast_days": 2,
        "timezone": "GMT",
        "timeformat": "unixtime",
        "cell_selection": "sea",
    }
    document = {
        "version": 1,
        "kind": "open_meteo_forecast",
        "requested": {"latitude": latitude, "longitude": longitude},
        "sources": {},
    }
    owned_client = client is None
    client = httpx.Client() if client is None else client
    try:
        for source, endpoint in ENDPOINTS.items():
            variables = (
                {"hourly": "wave_height,sea_surface_temperature"}
                if source == "marine"
                else {"hourly": "wind_speed_10m", "wind_speed_unit": "kn"}
            )
            response = client.get(
                endpoint, params={**params, **variables}, timeout=timeout_s, follow_redirects=False
            )
            response.raise_for_status()
            body = response.json()
            for name, lower, upper in (("latitude", -90, 90), ("longitude", -180, 180)):
                if not lower <= _finite(body[name], name) <= upper:
                    raise ValueError(f"invalid returned {name}")
            document["sources"][source] = {
                "url": str(response.request.url),
                "retrieved_at": datetime.now(UTC).isoformat(),
                "response": body,
            }
        if len(_samples(document, complete=True)) != params["forecast_days"] * 24:
            raise EnvironmentUnavailable("partial refresh horizon; existing cache retained")
        encoded = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, suffix=".tmp", delete=False
            ) as output:
                temporary = Path(output.name)
                output.write(encoded.encode("utf-8"))
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return path
    finally:
        if owned_client:
            client.close()
