# Performance and browser acceptance evidence

These are measurements from one laptop, taken on merged `main` at
**`fcd8c586069ddbfdc9a4b6b2af8f3de61de619e2`** (PR #46). They are evidence for this machine and
build only. They are not a guarantee for ship hardware, and nothing here is certified.

| | |
|---|---|
| Machine | Razer Blade 15 Advanced (Mid 2021), Intel Core i7-11800H @ 2.30 GHz, 16 logical CPUs, 31.7 GB RAM, Windows 11 (10.0.26300) |
| Runtime | Python 3.13.5, uv 0.13.1 (locked, `--offline`), FastAPI 0.143.3, uvicorn 0.54.0 (`--ws websockets-sansio`), pydantic 2.14.1 |
| Browser | installed Chrome 155.0.8059.84, headless via Playwright 1.64.0 (test-only `--with` overlay, no download); every non-local request blocked |

## Reproduce

```powershell
Set-Location <repo checkout at fcd8c58>
py -m uv sync --locked
py -m uv run --offline --with playwright python -m tools.benchmark_demo --samples 30 --cold 5 --out perf.json
py -m uv run --offline --with playwright pytest tests/api -rs     # browser test suite
```

The demo, as tested: `& "<repo>\tools\demo.ps1" -Port 8000 -OpenBrowser`. Run it from a plain
PowerShell; see finding 2 if a virtual environment is active.

## Method and boundaries

All wall times use `time.perf_counter_ns` (monotonic) in the measuring process. Statistics are
the median, p95 (nearest rank) and maximum.

| Measurement | Starts | Ends |
|---|---|---|
| Engine | the call to `admission.decide` / `arbitration.decide` / `shedding.plan` | it returns. Inputs are built beforehand. **Cold** = first call in a fresh Python process (imports and data loading excluded). **Warm** = repeated calls in one process |
| API | HTTP POST sent, or WebSocket frame sent | full JSON response received, against a real uvicorn started with the launcher's arguments. Includes the server's one simulated tick and the full view (gauges, crew view) |
| Browser | Playwright click in installed Chrome | `wait_for_function` sees the result in the page. Includes the HTTP round trip, rendering and Playwright polling |
| Launch | `tools/demo.ps1` started | `GET /` answers 200. Includes uv's offline locked sync check and server import |
| Sim | n/a | simulated seconds recorded by the simulator; not wall time |

Timing never enters the simulator or replay records. The exported bundle and the canonical JSONL
contain no `latency_ms` in any decision (checked in both API and browser runs). The header's ship
spec legitimately contains link-latency fields (`acoustic_latency_s`, `satcom_latency_ms`).

## Results (ms)

### Engine computation

| Case | Cold n=5: median / p95 / max | Warm n=30: median / p95 / max | Verdict |
|---|---|---|---|
| D1 admission, G4 at 0.9 t (Scenario A) | 80.9 / 83.2 / 83.2 | 81.4 / 94.7 / 97.7 | yes_if |
| D1 admission, G4 at 20 t | 36.2 / 38.5 / 38.5 | 38.6 / 45.8 / 46.6 | no |
| D3 arbitration, t=480 battery report, survey | 0.35 / 0.67 / 0.67 | 0.17 / 0.29 / 0.30 | decided |
| D3 arbitration, t=480, SAR | 0.21 / 0.35 / 0.35 | 0.16 / 0.23 / 0.23 | decided |
| D4 shedding, t=720 baseline trip | 0.27 / 0.30 / 0.30 | 0.18 / 0.20 / 0.21 | no_action |
| D4 shedding, **SYNTHETIC** overload (G3 and G4 charging with G5 on, one generator; not a baseline state) | 0.27 / 0.33 / 0.33 | 0.20 / 0.35 / 0.37 | shed |

### Public API round trip

| Request | n | median / p95 / max |
|---|---|---|
| `python -m uvicorn` start to `GET /` 200 (cold) | 3 | 1098 / 1129 / 1129 |
| First `POST /api/sessions` on a fresh server (cold) | 3 | 62.3 / 63.5 / 63.5 |
| First admit command on a fresh server (cold) | 3 | 96.5 / 112.5 / 112.5 |
| `POST /api/sessions` (warm) | 30 | 38.8 / 42.0 / 46.5 |
| Admit G4: D1 + D2 + 1 tick + view (HTTP) | 30 | 170.9 / 226.8 / 233.0 |
| Admit G4 over WebSocket | 30 | 142.7 / 187.6 / 224.0 |
| D3 query, read-only (Scenario B t=0) | 30 | 19.3 / 28.1 / 34.7 |
| D4 query, read-only | 30 | 22.7 / 38.9 / 46.9 |
| `battery_report` event: 1 tick, real D3 in the callback | 3 | 12.4 / 13.5 / 13.5 |
| Step 1 tick (observation) | 6 | 11.6 / 14.4 / 14.4 |
| Step 120 ticks (observation) | 6 | 237 / 287 / 287 |
| Execution run t=0..840 (7 step commands) | 3 | 2347 / 2388 / 2388 |
| `confirm_deck`: records input; recovery applied in that tick | 3 | 17.5 / 20.0 / 20.0 |

### Browser (action to visible result)

| Action | n | median / p95 / max |
|---|---|---|
| Page load to ship drawn, first page (cold) | 1 | 245 |
| Page load to ship drawn, reload (warm) | 3 | 103 / 109 / 109 |
| Evaluate click to new D1 verdict shown | 10 | 157.7 / 176.5 / 176.5 |
| Advance 1 s with the t=480 battery report to the D3 result shown | 2 | 42.0 / 42.6 / 42.6 |
| Confirm click to recovery shown as an observed change | 2 | 44.0 / 51.0 / 51.0 |

### Launch

| | n | median / p95 / max |
|---|---|---|
| `tools/demo.ps1` start to `GET /` 200 (clean shell environment) | 3 | 2629 / 2748 / 2748 |

### Simulated time (14 crew, Scenario B, execution on)

The battery report at t=480 anchors the landing deadline at t=1200. Preparation (hold, deck clear,
speed, confirmation step) is complete at t=840, which is 360 simulated seconds. Operator
confirmation at t=841 starts recovery, which ends at t=1021 (G4 then launches): 179 s before the
deadline. At 6 and 0 crew, D3 denies the recovery, so no landing time exists. Advancing those 841
simulated seconds took about 2.3 s of wall time through the API.

## Against the A.8 ceilings

| Ceiling | Measured here | Status |
|---|---|---|
| D1 ≤ 30 s (PLAN target < 1 s) | engine ≤ 97.7 ms; API ≤ 233 ms; click to visible ≤ 176.5 ms | well inside on this machine |
| D3 ≤ 2 s | engine ≤ 0.67 ms; event tick ≤ 13.5 ms; click to visible ≤ 42.6 ms | well inside on this machine |
| D4 first action ≤ 5 s, full sequence ≤ 60 s | **computation only**: ≤ 0.37 ms (including the synthetic shed case) | **not demonstrated.** The baseline trip gives `no_action`, which proves nothing about shed execution. The first-action and full-sequence deadlines are simulated execution timings that no baseline run exercises |
| Safety command ≤ 200 ms | not measured | out of scope here: the acoustic link (10–60 s) is a physics finding in the Physical Account, not a software latency |

## Real browser acceptance (installed Chrome, real HTTP and WebSocket, no substituted responses)

The `tools/benchmark_demo.py` browser pass made 18 checks. All passed:

- G4 accepted with conditions and highlighted. At 20 t it is refused, the ship is shown as unaffected, and the limiting slot is outlined.
- Invalid card JSON is rejected with state and log unchanged. An unfamiliar card is imported to real D1 and its name renders as text.
- Top and side views share the selection.
- **Survey and SAR:** real D3 puts G4 first (G3 pause, or suspend). After preparation G4 is still flying and the page asks for the operator. Confirming produces recovery, shown as an observed change.
- Export plus execution replay is byte-identical, both in the page and offline from the downloaded bundle. Records carry no `latency_ms`.
- At 6 and 0 crew, the real D3 denial is shown with its reported risk.
- Reset archives the run, and the `?run=N` download has the same hash.
- Captured weather shows its provenance and replays byte-identically.
- No external requests were made.

The repository browser suite (`--with playwright pytest tests/api`) gave **56 passed, 1 skipped**; the skip is a capability check for an older backend. Plain `pytest` gave 474 passed, 17 skipped; those skips are the browser tests when Playwright is absent.

## Findings

1. **Selection is overridden after an admission.** Owner: Track B static UI (`shipos/api/static/app.js`, `render()`).
   - Repro: evaluate G4, click G2 on the ship, then change any setting (for example crew 14 → 6). The selection jumps back to G4. The page's `selectedGid` goes `G2` → `G4`.
   - Cause: `render()` re-selects the admission subject on every update, including WebSocket echoes, not only when a new decision arrives.
   - Impact: minor UX. No data is wrong.
2. **The launcher aborts when a virtual environment without uv is active.** Owner: Track B launcher (`tools/demo.ps1`, line 34).
   - Repro: `$env:VIRTUAL_ENV = "<repo>\.venv"; .\tools\demo.ps1 -Port 8198`. It exits 1 with a PowerShell NativeCommandError: "No module named uv".
   - Cause: `py -m uv` uses the active venv, and under `$ErrorActionPreference = 'Stop'` its stderr becomes a terminating error. So the launcher never tries `.tools\uv.exe` and never prints its own guidance.
   - Impact: users who launch from an activated venv get a confusing failure. The benchmark therefore starts the launcher with `VIRTUAL_ENV` and `UV*` removed, as a plain shell would have them.
3. **D4 deadlines are unproven.** Real D4 runs in about 0.2 ms, but the 5 s first-action and 60 s full-sequence requirements describe executed shedding. No baseline scenario sheds, and exact D4 schedule metadata is still pending (#43). This needs a declared overload scenario with execution on.

## Limitations

- One machine, one session, with background load uncontrolled. Browser samples are small (n = 1–10), and browser times include Playwright polling.
- Cold engine figures exclude Python start-up and imports. Cold server and launch figures include them.
- The synthetic D4 case is a hand-made snapshot, used only to time a `shed` computation.
- Response time on the real ship's compute rack was not measured.
