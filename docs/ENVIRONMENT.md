# B3: offline environment and installation windows

The demo coordinate is **36.5 N, 12.5 E**, offshore in the Strait of Sicily.
It is an illustrative operating point, not a surveyed cable route. The committed
`data/env_cache/strait_of_sicily.json` contains **real cached model forecasts**,
not measurements or synthetic 24 C values. Offline evaluation makes no network
calls and never refreshes automatically.

## Provenance

Both official Open-Meteo requests succeeded with HTTP 200 on 2026-10-17:

| Source | Retrieved UTC | Returned grid centre (lat, lon) |
|---|---|---|
| Marine | 06:41:55.612436 | 36.541664, 12.4583435 |
| Weather | 06:41:56.305580 | 36.478764, 12.522832 |

Each response has 48 hourly valid timestamps: **2026-10-17 00:00 UTC through
2026-10-18 23:00 UTC**. Requests explicitly use GMT, Unix seconds, two forecast
days, and sea-cell selection. The distinct grid centres are retained; there is
no assertion that wave, SST and wind share one grid cell. The cache stores exact
request URLs, requested and returned coordinates, units, per-source retrieval
times, original hourly values, and provider metadata. Provider processing latency
is retained only as raw provenance; conversion does not use it.

* [Marine API documentation](https://open-meteo.com/en/docs/marine-weather-api):
  `https://marine-api.open-meteo.com/v1/marine`, hourly `wave_height` (significant
  height, metres) and `sea_surface_temperature` (degrees Celsius).
* [Weather API documentation](https://open-meteo.com/en/docs):
  `https://api.open-meteo.com/v1/forecast`, hourly `wind_speed_10m`, explicitly
  requested in knots with `wind_speed_unit=kn`.

Data attribution: Open-Meteo and its upstream forecast providers, including DWD
as acknowledged in the Marine API documentation. Default provider model selection
was used; the response does not identify a single underlying model/run, so this
cache does not claim one. Refreshes can change model inputs; retain the exact
cache for reproducibility.

## Public interfaces

```python
# shipos.sim.env_refresh (the only module which performs network I/O)
refresh_cache(path: Path, *, latitude=36.5, longitude=12.5,
              timeout_s=10, client: httpx.Client | None = None) -> Path

# shipos.sim.env (offline)
load_environment(path: Path, *, scenario_start: datetime,
                 max_age_s=86400, allow_stale=False) -> EnvironmentForecast
forecast.env_at(t_s=0, *, layered_water: bool) -> Env
sea_state(wave_height_m: float) -> int
find_installation_windows(
    ship: ShipSpec, forecast: EnvironmentForecast, *,
    operation="flight_deck" or "ramp", crew: int, speed_kn: float,
    local_tz: tzinfo, start_s=0, end_s=None,
    people_available=None, duration_min=None,
) -> tuple[InstallationWindow, ...]
```

The signatures above describe the API; executable examples follow below.
`EnvironmentForecast` exposes `scenario_start` normalized to UTC, `kind`,
`retrieved_at`, `stale`, and immutable `points: tuple[ForecastPoint, ...]`.
`Env` and every `ForecastPoint` use the existing contracts. Water layering cannot
be inferred from surface data, so `env_at` requires the caller's explicit
`layered_water` scenario input. There is no implicit temperature substitute.

### Alignment, coverage and freshness

* `t_s = valid_timestamp_UTC - scenario_start_UTC`, in seconds. The start must be
  timezone-aware. Equivalent instants with different explicit offsets produce
  equal results. Forecast samples before simulation zero retain negative times.
* Series join on **exact timestamps**, never array indices. Units are validated,
  not guessed. Wrong units, duplicate/unordered timestamps, nonfinite values,
  negative wave/wind values, and malformed lengths fail clearly.
* Only consecutive known hourly wave/wind endpoints establish an interval.
  Within that interval, values are held at the left endpoint over `[left, right)`.
  This is a declared demonstration approximation, not a guarantee about conditions
  between model samples. There is no interpolation, last-sample extension, or
  bridging of a missing hour. A null/missing wave or wind removes that sample;
  both adjacent intervals consequently become unknown.
* Missing SST remains `None` in a `ForecastPoint`. Window screening needs only
  wave/wind, but `env_at` raises `EnvironmentUnavailable` when current SST is
  unknown because `Env.seawater_c` is mandatory. `env_at` also rejects uncovered
  times and the final sample instant, which has no following coverage endpoint.
  Its `Env.forecast` contains only the current contiguous block, beginning at the
  requested simulated time. The terminal sample is a boundary, not an extension.
* Freshness compares the **earlier source download time** with the explicit
  scenario start, not the computer clock. The default permitted age is 24 hours;
  equality passes. Older data raises `EnvironmentUnavailable` unless
  `allow_stale=True`, in which case `forecast.stale` stays true. Archived replay
  may start before download, but that does not claim the forecast was available
  to a real ship at that earlier instant. Age is a run-start policy; forecast
  coverage still bounds every query.
* Edited/synthetic inputs must use `kind="synthetic_demo"` and must not claim
  provider request/retrieval provenance. No synthetic cache is needed for this
  PR; synthetic tests live in temporary files. UI integration should display
  `kind`/`stale` from the forecast beside any values it presents.

### Wave height to sea-state code

Use [NOAA's WMO code table 3700](https://www.nodc.noaa.gov/gtspp/document/codetbls/wmocodes/table3700.html)
as height guidance. Exact boundaries go to the **lower code**:

| Code | Wave height m |
|---|---|
| 0 | 0 |
| 1 | (0, 0.1] |
| 2 | (0.1, 0.5] |
| 3 | (0.5, 1.25] |
| 4 | (1.25, 2.5] |
| 5 | (2.5, 4] |
| 6 | (4, 6] |
| 7 | (6, 9] |
| 8 | (9, 14] |
| 9 | >14 |

Applying height guidance to forecast significant wave height is a proxy for the
contract's sea-state code. It does not supply a visual observation, represent
the largest wave, or fully describe swell, currents, and wave direction. The
convention and invented policies are recorded only in the new
`data/assumptions/environment.yaml`; existing assumption files are untouched.

### Installation-window semantics

The finder is pure: no file/network access, current time, state mutation,
admission verdicts, or crew decisions. It uses `ShipSpec.install_window` for
inclusive sea-state/wind/speed limits and operation-specific people and duration.
Current ship data requires sea state <=3, wind <=20 kn, speed 4-8 kn; flight-deck
handling needs 3 people/30 minutes, ramp handling 4 people/45 minutes.

`people_available` defaults to all aboard and may be reduced explicitly. It
cannot exceed crew. Crew must be a declared ship level (currently 14/6/0).
Zero crew always yields no physical window. `duration_min` can lengthen the
operation but cannot shorten the ship's minimum. Speed and available personnel
are explicit commitments assumed constant across the requested horizon; changing
them requires another call with the appropriate horizon.

At six crew, intersect coverage with local **07:00-19:00** on every date. This
is PLAN's clock restriction, not calculated sunrise/sunset. `local_tz` is always
explicit. The example uses fixed UTC+02:00 as a demonstration input for these
October dates; it must not be reused blindly across a daylight-saving change.
An aware IANA timezone can be supplied if its data is installed by the caller;
this PR adds no timezone dependency or reliance on the machine's local timezone.

Results are maximal eligible half-open intervals `[start_s, end_s)` long enough
for the **entire** operation. Each result also has `duration_s` and
`latest_start_s = end_s - duration_s`. Starting exactly at `latest_start_s` is
valid; finishing exactly at 19:00 or a bad/unknown interval's start is allowed.
Unknown gaps split windows. No operation may overrun known coverage. A valid
requested horizon outside coverage returns no window; malformed horizons raise.

## Offline example and integration

Run Python from the repository root through `uv run python` (or `py -m uv run
python`), then use:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shipos.data import load_ship
from shipos.sim.env import find_installation_windows, load_environment

forecast = load_environment(
    Path("data/env_cache/strait_of_sicily.json"),
    scenario_start=datetime.fromisoformat("2026-10-17T08:00:00+00:00"),
)
env = forecast.env_at(0, layered_water=False)  # explicit demonstration input
windows = find_installation_windows(
    load_ship(),
    forecast,
    operation="ramp",
    crew=6,
    speed_kn=6,
    local_tz=timezone(timedelta(hours=2)),
)
print(env.seawater_c, env.wind_kn, windows)
```

The optional Runner integration below supplies actual forecast-backed snapshots.
Use this finder for coverage-aware physical windows.
`Env.forecast` cannot express missing-data masks or a validity duration, so do
not infer extra coverage from the terminal `ForecastPoint` or a separate engine's
window heuristic. No Track A signature or implementation is changed here.

## Captured Runner environment

This additive integration imports the published dependencies without changing
their branches: B2 PR #18 at `0023d1e6295fd75f954fe14440f0df127bbb6ad0` and B3
PR #24 at `3982ca90bca37fce382d8dfa6fb3bf9c7b4c3a7e`. The common main base is
`ffe6f3fa8840bdb30aecab70ec92ab0331f9aec0`. No API, crew-policy or unrelated
unmerged changes are imported.

The public input in `shipos.sim.env_input` is:

```python
EnvironmentInput(cache: dict, *, scenario_start: datetime, layered_water: bool,
                 max_age_s=86400, allow_stale=False)
EnvironmentInput.from_cache(path: Path, *, scenario_start: datetime,
                            layered_water: bool, max_age_s=86400,
                            allow_stale=False) -> EnvironmentInput
EnvironmentInput.from_record(record: dict) -> EnvironmentInput
```

`from_cache` is explicit preparation that reads the local cache once; passing a
dict avoids that read. `record` and `forecast` properties return defensive copies.
`env_at(t_s=0) -> Env` uses the captured layering input. A new
`environment_from_data(document, *, scenario_start, max_age_s=86400,
allow_stale=False)` offline helper uses precisely the existing B3 conversion rules
without file I/O. Disallowed stale data and naive starts fail during preparation.

Pass `environment=input` to `Runner(...)` or
`load_scenario(path, *, decide=None, environment=None)`. Existing signatures and
fixed-environment scenarios remain compatible. Before its first completed tick,
`runner.snapshot` is the supplied initial snapshot. Each `step` then computes
the actual Env at the upcoming simulation time in a separate candidate snapshot,
before applying constructor events, ordered injected events, measurements and
the existing callback. That Env reaches both callback and committed snapshot.

Missing current SST, unknown intervals or exhausted coverage raise
`EnvironmentUnavailable`. A failed step, including a callback failure, commits
nothing: snapshot, context, mode clocks, timeline cursor, RNG, next tick, log and
hash stay unchanged. There is no obsolete-Env fallback or automatic refresh.
`runner.environment` returns a defensive copy for public inspection/window use.

### Replay evidence and compatibility

No environment input means **version 1**, byte-identical records and the original
Scenario B hash `3e43733a289dabdbd77b2cda1bbea4cc176f153bf9e6eb101e205b7b691712d7`.
No baseline scenario file or chronology is changed.

Opting in writes **version 2** and an `environment` field in the initial record.
The input subrecord has version 1 and embeds full cache JSON values (including
source URLs, retrieval timestamps, units and coordinates), the aware start as
ISO 8601 with its explicit offset, layering, `max_age_s`, and `allow_stale`.
This captures JSON content, not file whitespace, path names or filesystem state.
Cache provenance timestamps are fixed inputs; no runtime wall-clock reads are
made. Source cache mutation/removal cannot change an existing run or its replay.
Constructor/live event ordering and latency exclusion remain unchanged.

`replay_jsonl` reads both runner versions and reconstructs version 2 entirely
from embedded data. It does not reread the original cache or scenario files.
The same code, runtime, pure callback, and applicable assumption registry remain
required, including the hourly-hold convention and wave mapping. The cache and
freshness options are captured; independent portability across implementation or
registry changes has not been proved. Installation-window queries are separate
calculations; retain their operation/crew/speed/people/timezone inputs if replaying
those results. They are not silently added to Runner decisions or its log.

### Copy-paste offline reproduction for main

From the repository root in `py -m uv run python`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shipos.sim.env import find_installation_windows
from shipos.sim.env_input import EnvironmentInput
from shipos.sim.runner import load_scenario, replay_jsonl

source = EnvironmentInput.from_cache(
    Path("data/env_cache/strait_of_sicily.json"),
    scenario_start=datetime.fromisoformat("2026-10-17T08:59:59+00:00"),
    layered_water=False,
)
runner = load_scenario(Path("data/scenarios/B.yaml"), environment=source)
first, second = runner.step(), runner.step()
print(first.env.wind_kn, second.env.wind_kn)  # real cached values: 5.4 -> 5.7 kn
windows = find_installation_windows(
    runner.context.ship,
    runner.environment.forecast,
    operation="ramp",
    crew=6,
    speed_kn=6,
    people_available=4,
    local_tz=timezone(timedelta(hours=2)),
)
print(windows)
assert replay_jsonl(runner.jsonl).jsonl == runner.jsonl
print(runner.final_hash)
```

The window query explicitly plans for six crew, four available handlers and a
constant 6 kn. It does not reconfigure the scenario's actual 14 crew/3 kn, grant
admission/crew permission, or execute an installation. Eligibility remains
conditional on the declared planning inputs. The fixed UTC+02:00 demonstration
timezone and `layered_water=False` are explicit caller inputs, not new provider
observations. Main can opt in with the keyword above and let existing callbacks
receive the actual per-tick environment; no private runner fields are needed.

## Explicit refresh and failure behavior

```python
from pathlib import Path
from shipos.sim.env_refresh import refresh_cache

refresh_cache(Path("data/env_cache/refreshed.json"), timeout_s=10)
```

Refresh issues two official GETs, with explicit per-phase HTTPX timeouts, no
application retries, and redirects disabled. An injected `httpx.Client` supports
offline tests; an internally created client is closed on return. Both responses
must cover all 48 requested hours with aligned, consecutive timestamps and complete
wave/SST/wind data
in the requested units. Missing SST or any partial/error response rejects the
refresh rather than filling values. Offline loading is more permissive about
known missing values so it can report their limitations honestly.

Only after validation are new bytes written to a temporary sibling file and
atomically replaced with `os.replace`. Download, decode, validation or replacement
failure leaves an existing cache untouched and raises to the caller. Temporary
files are cleaned up. The caller may keep using a previously loaded valid cache;
no failed refresh silently changes its provenance or freshness status.

Focused checks: `uv run pytest tests/sim/test_env.py`. Tests disable network for
cache evaluation and use HTTPX mock transport for refresh success/failure paths.
