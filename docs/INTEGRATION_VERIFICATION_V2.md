# ShipOS Integration Verification Report (V2)

- **Evaluator:** ShipOS Task Coordinator & Integration Lead (`agy`)
- **Evaluator Settings:** Gemini 2.5, requested High reasoning
- **Date:** October 2026
- **Status:** Verified / Candidate Assembled

---

## 1. Executive Summary & Configuration

This report details independent, end-to-end integration verification across two targets:
1. **Frozen Baseline:** PR #35 (`b/ui-clarity-v1`, commit `d8bf25d60987b739a9ec53ef15293f48e4376676`).
2. **Integration Candidate Assembly:** Commit `5a0a93d4ba69de0944ea49a05e79f4b7c56b49d8`, synthesizing PR #35, PR #36 (`c367e3a`), PR #37 (`8c19f38`), and PR #38 (`9032453`).

### 1.1 Test Suite & Static Health
- **Baseline PR #35:** `ruff check` (clean), `ruff format` (93 files clean), `check_no_literals` (clean), `guest_schema` (clean), `shipos validate` (clean), `physical_account` (clean). `pytest`: **389 passed, 1 skipped** (Playwright browser test).
- **Candidate Assembly `5a0a93d`:** All static guards clean (96 files formatted). `pytest`: **395 passed, 5 skipped** (browser UI tests skipping missing local browser binaries).
- **Ownership & Whitespace:** 0 ownership violations, 0 git whitespace errors.

---

## 2. Phase 1: Frozen Baseline Probes (PR #35 on Port 8010)

The offline dashboard was launched on port 8010 using locked dependencies (`--locked --offline`) and exercised via asynchronous HTTP and WebSocket transports:

1. **Baseline Admission & Unfamiliar Card Import:**
   - Admitting nominal G4 (0.9 t body + 0.14 t drone = 1.04 t) under Scenario A port conditions yielded canonical `yes_if` (interlocking G2 winch during G4 charging).
   - Importing an unfamiliar complete guest card (`GX_DRONE`, 0.8 t air survey payload) yielded clean `yes` admission and placement.
2. **Invalid Input Isolation:**
   - Malformed JSON, NaN mass, and duplicate installed IDs (`G1`) returned HTTP 422 or WebSocket errors. Session snapshot and hash-chained JSONL evidence were preserved byte-for-byte.
3. **Priority, Scenario Switching & Reset:**
   - Swapped priority to `patrol` cleanly.
   - Switched to Scenario B ($t = 0.0\,\text{s}$, guests G1, G3, G4, G5 installed).
   - `reset` returned session to initial Scenario A baseline ($t = 0.0\,\text{s}$).
4. **Real D3/D4 Engines Match Direct Calls:**
   - D3 Arbitration via API on G4 recovery matched direct `arbitration.decide()` identically: verdict `decided`, option `"G4 recovery; G3 pause"`.
   - D4 Shedding via API matched direct `shedding.plan()` identically: verdict `no_action`, option `None` (50.33 kW spare margin).
5. **Scenario B Minute-12 Observation:**
   - Advanced 721 ticks to $t = 720.0\,\text{s}$ (minute 12). Delivered `genset_trip` event. D4 shedding evaluated one-genset-down margin (100 kW reserve against 49.67 kW demand), correctly issuing `no_action` with full armed ladder in alternatives. Observation-only behavior is physically correct.
6. **Session Independence & Replay:**
   - Stepping Session 2 by 20 ticks left Session 1 untouched.
   - Execution replay on exported `replay.jsonl` verified offline hash match (`bbc2f7a0cd...`).
7. **Bounded Card Perturbations ($\pm 30\%$):**
   - Base G4 (0.9 t, 2.0 kW) $\rightarrow$ `yes_if` (`power.total`, interlock G2).
   - $-30\%$ power (1.4 kW) $\rightarrow$ **`yes`** (uninterlocked).
   - $+30\%$ mass (1.17 t body + 0.14 t part = 1.31 t) $\rightarrow$ **`no`** (exceeds 1.2 t hangar limit).
8. **Isolated Performance Timings (100 runs, Win11 AMD64, CPython 3.13.5):**
   - **D1 Admission:** Cold 81.1 ms, Median **81.1 ms**, P95 119.1 ms (bound $\le 30.0\,\text{s}$).
   - **D2 Placement:** Cold 2.41 ms, Median **1.87 ms**, P95 2.62 ms (bound sub-second).
   - **D3 Arbitration:** Cold 0.24 ms, Median **0.15 ms**, P95 0.23 ms (bound $\le 2.0\,\text{s}$).
   - **D4 Shedding:** Cold 0.22 ms, Median **0.17 ms**, P95 0.24 ms (bound $\le 5.0\,\text{s}$).

---

## 3. Phase 2: Candidate Integration Probes (Assembly `5a0a93d`)

Incorporating PRs #36, #37, and #38:
1. **Cached Weather Provenance:**
   - Successfully configured `strait_of_sicily` cache ($t = 2026\text{-}10\text{-}17\text{T}08:00:00+02:00$). Provenance recorded `open_meteo_forecast` (marine/weather proxy).
   - Attached 42-hour forecast to snapshot.
   - Replay succeeded byte-identically with cache filesystem access blocked.
2. **Crew Confirmation Protocol:**
   - Crewed ($\text{crew} = 14$): `confirm="human"`, `confirmation="pending"` (never automatic).
   - Uncrewed ($\text{crew} = 0$): `confirm="forbidden"`, `confirmation="unavailable"`.
   - Confirmation Lifecycle: Confirmation granted on clear deck is discarded immediately upon deck occupation. Reopening requires fresh operator confirmation.
   - Uncrewed Evidence: Real-time simulated camera feed permits recovery; stale or non-simulated feeds are rejected.
3. **Fail-Closed Execution Seam:**
   - Opt-in execution (`op: "execution"`) and operator confirmation (`op: "confirm_deck"`) fail closed with descriptive `ValueError` pending school's runner execution seam.

---

## 4. Defect & Limitation Analysis

### 4.1 Reproduced Defects
- **None.** All validation, schema, and physical engine boundaries fail closed gracefully without 500 errors.

### 4.2 Known Limitations
1. **Offline Browser Test Dependencies:** Playwright browser tests skip when local Chrome binaries are absent.
2. **Observation-Only Engine Seam:** Real D3/D4 recommendations are generated and recorded in evidence, but physical actuator application to simulation state is gated pending School's runner execution seam.

### 4.3 Stale Findings Resolved
1. D3/D4 stubs replaced by real engine implementations from main (`c3bf469`, `5f4d4da`, `6ace4b6`).
2. Scenario A D3 missing G4 KeyError resolved by explicit refusal reason.
3. Confirmation persistence bug resolved by `CrewEvidence` lifecycle helper.

---

## 5. Top 3 Blockers & Ownership

1. **Simulator Action Execution Seam:**
   - *Owner:* School (`shipos/sim/runner.py`)
   - *Reproduction:* `POST /api/sessions/{sid}/commands` with `{"op": "execution", "enabled": true, "fresh_run": true}` $\rightarrow$ HTTP 422: `school's public execution seam is not imported`.
   - *Action:* School implements `execution_status` and applies recommended state actions.
2. **Operator Confirmation Event Seam:**
   - *Owner:* School (`shipos/sim/runner.py`)
   - *Reproduction:* `POST /api/sessions/{sid}/commands` with `{"op": "confirm_deck"}` $\rightarrow$ HTTP 422: `Recorded operator confirmation unavailable until executor seam lands`.
   - *Action:* Connect `operator_confirmation` event injection into Runner.
3. **Track B Runtime Integration Merge:**
   - *Owner:* Main (`shipos/api/**`)
   - *Reproduction:* PR #37 and PR #38 are open separately. Assembly `5a0a93d` proves zero conflicts.
   - *Action:* Main merges PR #36, #37, and #38 into `main`.

---

**Sign-off:**
agy
