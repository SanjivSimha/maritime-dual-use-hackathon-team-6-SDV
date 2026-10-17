# Combined System Integration Verification Report

**Checkpoint:** Track B Combined Assembly (`b/system-verification-v1`)  
**Assembly HEAD SHA:** `278c34e31aa5560885e4ce65a08a2bdea604c117`  
**Coordinator:** agy (Noncoding Task Coordinator)  
**Date:** 2026-10-17

---

## 1. Assembled Pull Requests and Exact SHAs

The verification branch combines six published PR heads via local non-fast-forward merges without file conflicts:

| PR | Track B Deliverable | Source Branch | Assembled Commit SHA | CI Status at Capture |
|---|---|---|---|---|
| **#22** | B4: UI / Simulator integration & execution replay | `b/ui-sim-integration-v1` | `57a10a9d65aa60b10728a2559b67f7898378c8fb` | All 6 pass |
| **#16** | B1: Validation, guest schema, patrol priority | `b/cards-validate-v1` | `c323cfabea99ac6d8db08c897d3f1ec6cb3b12be` | All 6 pass |
| **#19** | B5: Crew policy gate, weather & lifecycle semantics | `b/crew-policy-v1` | `abfe471986a3e2ec464e0e77562a6f1affdd0991` | All 6 pass |
| **#21** | B6: Physical Account and assumption registry | `b/physical-account-v1` | `832f77246a83c6a9a60bd3b09260a2008063bad7` | All 6 pass |
| **#23** | B6: Scenario C crew policy memo | `b/crew-memo-v1` | `46c1e1a9da4245069db23b3b9462de0a08298316` | All 6 pass |
| **#24** | B3: Cached environment forecasts & window finder | `b/environment-v1` | `3982ca90bca37fce382d8dfa6fb3bf9c7b4c3a7e` | All 6 pass |

*Note: Base is `origin/main` at `ffe6f3fa8840bdb30aecab70ec92ab0331f9aec0`.*

---

## 2. Combined Check Results and Skips

All checks executed in the isolated worktree using locked dependencies (`uv 0.13.1` / Python 3.13.5):

| Check Command | Result | Notes |
|---|---|---|
| `py -m uv sync --locked` | **Passed** | 27 locked packages synced (including `websockets==17.2`) |
| `py -m uv run ruff check .` | **Passed** | Clean across all 84 formatted repository files |
| `py -m uv run ruff format --check .` | **Passed** | 84 files formatted |
| `py -m uv run python -m tools.check_no_literals` | **Passed** | Guardrail satisfied; 0 disallowed literals in physics/engine |
| `py -m uv run python -m tools.guest_schema --check` | **Passed** | `schema/guest_manifest.schema.json` matches models |
| `py -m uv run shipos validate` | **Passed** | `OK: 1 envelopes, 5 guests, 3 priorities, 1 ship` |
| `py -m uv run python -m tools.physical_account --check` | **Passed** | Verified following mechanical regeneration of environment rows |
| `py -m uv run pytest -rs` | **Passed** | **316 passed, 1 skipped, 1 warning** (27.57s) |
| `git diff --check` | **Passed** | 0 whitespace or formatting anomalies |

### Skips
- `tests/api/test_transport.py::test_browser_offline_edit_reset_and_untrusted_text`: **SKIPPED** (`playwright` not installed in environment). Per project rules, external browser automation packages and binaries were not downloaded. The headless server WebSocket transport test (`test_uvicorn_websocket_round_trip`) passed using locked dependencies.

---

## 3. End-to-End System Verification Flows

### A. Scenario A Admission, Mass Edit, and Reset
- Baseline loadout loaded at $t=0$; G4 evaluated with declared mass (0.9 t) returns verdict `yes_if`.
- Dynamic mass override to 20.0 t triggers deterministic rejection `verdict: no` with binding demand `20.14 t` on hangar mass capacity.
- `op: reset` completely clears session history, reinstates pristine manifests, and clears decision state.

### B. Scenario B Stepping, Live Injection, and Transport Parity
- Switching to Scenario B initializes `shipos.sim.runner.Runner` with minute-0 survey/flying profile.
- Stepping advances simulation time transactionally; injecting `genset_trip` updates `platform.gensets_online: 1`; injecting `battery_report` (18%) updates G4 runtime battery state without mutating catalog manifests.
- Server state emitted over HTTP endpoints and WebSocket subscriptions was verified identical via `tests/api/test_simulation.py`.

### C. Execution Replay and Exact Hashes
- Session evidence exported through `state.simulation.evidence()` and verified via `adapter.replay_evidence()`.
- Replay from embedded input records reproduced the exact initial state, timeline events, and callback evaluations byte-for-byte:
  - **4-tick demo run final hash:** `1d7f8dbc8f9fefd14d9ea2b4bf94f3b9016f7586a6caf601b8de8c28376db99a`
  - **721-tick canonical Scenario B run final hash:** `ca3abd5b32afaf260fd2575b718945c7b6584d5d8263d373d7712acb7ef62a28`
  - **Byte-identical validation:** `True` (verified via `replay_jsonl`).

### D. Offline Environment & Installation-Window Finder
- Loaded `data/env_cache/strait_of_sicily.json` offline without external network requests; extracted 48 hourly forecast points (SST ~24 °C).
- Calculated maximal installation windows for flight deck operations at 6 kn with 14 crew (`duration_min=30`); successfully identified 3 viable operational windows matching daylight and sea-state constraints.

### E. Crew Policy Interface
- Corrected crew policy functions (`recovery_gate`, `early_clearance`, `machinery_lock` at PR #19 `abfe471`) imported and verified against edge-case lifecycle and weather tests. All 286 unit test assertions pass.

---

## 4. Architectural Distinction: Available Modules vs. UI-Wired Features

1. **Wired into Interactive API/UI:**
   - Real Scenario A D1 admission and D2 placement.
   - Real Scenario B 1 Hz stepping and live event injection (`genset_trip`, `battery_report`).
   - Tamper-evident execution replay verification and export.
   - Eight public physics gauges, slot placement visuals, and decision logging.
2. **Available Modules Not Yet Wired to UI Controls:**
   - `shipos.crew.policy`: Pure evaluative gate available as a library; neither `server.py` nor `Runner.step()` currently calls the gate to interlock flight deck recovery.
   - `shipos.sim.env`: Offline cached environment loader and installation-window finder exist as CLI/library modules; not yet exposed via web endpoints or rendered on the ship dashboard.

---

## 5. Actionable Integration Issues

1. **Priority Catalog Omission in API Server (`main`)**
   - **File/Line:** `shipos/api/server.py:24`, `server.py:44`
   - **Reproduction:** Sending command `{"op": "priority", "priority": "patrol"}` returns HTTP 422 Unprocessable Entity because `server.py` hardcodes `PRIORITIES = ("survey_primary", "sar_primary")` and Pydantic validator `Literal["survey_primary", "sar_primary"]`.
   - **Resolution:** Main is actively updating PR #22 to include `patrol` in the schema validator and UI dropdown.

*(Note: Known D3/D4 engine stubs returning `NotImplementedError` and intentional non-application of engine decisions during simulation are established architectural boundaries, not newly introduced defects.)*
