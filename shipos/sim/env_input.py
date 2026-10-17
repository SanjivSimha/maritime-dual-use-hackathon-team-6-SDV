"""Captured, reconstructible offline environment inputs for Runner."""

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Self

from shipos.contracts import Env
from shipos.sim.env import MAX_AGE_S, EnvironmentForecast, _finite, environment_from_data


class EnvironmentInput:
    def __init__(
        self,
        cache: dict,
        *,
        scenario_start: datetime,
        layered_water: bool,
        max_age_s: float = MAX_AGE_S,
        allow_stale: bool = False,
    ):
        if type(layered_water) is not bool or type(allow_stale) is not bool:
            raise ValueError("layered_water and allow_stale must be explicit booleans")
        cache = deepcopy(cache)
        self._forecast = environment_from_data(
            cache,
            scenario_start=scenario_start,
            max_age_s=max_age_s,
            allow_stale=allow_stale,
        )
        self._record = {
            "version": 1,
            "cache": cache,
            "scenario_start": scenario_start.isoformat(),
            "layered_water": layered_water,
            "max_age_s": _finite(max_age_s, "max_age_s"),
            "allow_stale": allow_stale,
        }

    @classmethod
    def from_cache(
        cls,
        path: Path,
        *,
        scenario_start: datetime,
        layered_water: bool,
        max_age_s: float = MAX_AGE_S,
        allow_stale: bool = False,
    ) -> Self:
        """Explicit preparation: read a local cache once; Runner never reads this path."""
        return cls(
            json.loads(path.read_text(encoding="utf-8")),
            scenario_start=scenario_start,
            layered_water=layered_water,
            max_age_s=max_age_s,
            allow_stale=allow_stale,
        )

    @classmethod
    def from_record(cls, record: dict) -> Self:
        """Reconstruct embedded input, without reading the original cache file."""
        if (
            not isinstance(record, dict)
            or set(record)
            != {"version", "cache", "scenario_start", "layered_water", "max_age_s", "allow_stale"}
            or record["version"] != 1
        ):
            raise ValueError("unsupported captured environment record")
        return cls(
            record["cache"],
            scenario_start=datetime.fromisoformat(record["scenario_start"]),
            layered_water=record["layered_water"],
            max_age_s=record["max_age_s"],
            allow_stale=record["allow_stale"],
        )

    @property
    def record(self) -> dict:
        return deepcopy(self._record)

    @property
    def forecast(self) -> EnvironmentForecast:
        return deepcopy(self._forecast)

    def env_at(self, t_s: float = 0) -> Env:
        return self._forecast.env_at(t_s, layered_water=self._record["layered_water"])
