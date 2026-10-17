# ShipOS Track B Release Validation & System Evidence Report

- **Document ID:** `docs/RELEASE_VALIDATION.md`
- **Author:** ShipOS Task Coordinator & Integration Lead (`agy`)
- **Branch:** `b/release-evidence-v1`
- **Base Assembly SHA:** `1b0e9b4bfca94cc5ac997e3af45f4eec7ea65141`
- **Date:** October 2026
- **Status:** Complete / Verified

---

## 1. Executive Summary

This report establishes comprehensive empirical evidence for the Track B release candidate of ShipOS. All core components across API, UI adapters, simulation runner, environmental cache, and crew safety lifecycle helpers have been assembled, verified against locked dependencies, and subjected to rigorous multi-dimensional probes.

The platform demonstrates:
- **100% Determinism & Offline Operation:** Pure offline simulation, replay, and validation without external network calls or live LLMs.
- **Physical Integrity Across 8 Resources:** Enforces mass, space, electrical power, chilled water, cooling air, data links, crew hours, and acoustic signatures.
- **Sub-100 ms Decision Performance:** D1 admission completes in **79.6 ms** (median) against a $\le 30\,\text{s}$ requirement; D2 placement completes in **1.89 ms** (median).
- **Execution-Level Tamper Resistance:** Cryptographic JSONL hashing verified, with full engine execution replay proving detection of modified records even when hash chains are recomputed.
- **Fail-Closed Crew & Flight Deck Safety:** Formalized lifecycle helper ensures confirmations expire immediately upon deck occupation, and uncrewed operations strictly reject stale or unverified sensor telemetry.

---

## 2. Integrated Branch Provenance & Repository Health

### 2.1 Branch Composition
The release assembly was constructed from fresh `origin/b/system-verification-v1` by integrating published Track B PR branches:
* **Base Integration:** `origin/b/system-verification-v1` (PR #25, commit `6a646baa`)
* **API / UI & Judge Cards:** `origin/b/ui-sim-integration-v1` (PR #22, commit `39edd4c7`)
* **Simulator & Environmental Integration:** `origin/b/sim-environment-integration-v1` (PR #26, commit `905c585c`)
* **Crew Evidence Lifecycle Helper:** `origin/b/crew-lifecycle-v1` (PR #27, commit `ce0b211a`)

Assembly HEAD SHA: `1b0e9b4bfca94cc5ac997e3af45f4eec7ea65141`.

### 2.2 Locked Dependency Sync
Dependency synchronization executed strictly against `uv.lock`:
```powershell
py -m uv sync --locked
```
Result: 27 packages installed cleanly, zero unpinned dependencies.

### 2.3 Static Analysis & Test Suite
All six canonical repository health checks executed cleanly:
1. **Linting:** `py -m uv run ruff check .` $\rightarrow$ **0 errors**.
2. **Formatting:** `py -m uv run ruff format --check .` $\rightarrow$ **90 files inspected, 0 changes needed**.
3. **Literal Guard:** `py -m uv run python -m tools.check_no_literals` $\rightarrow$ **Passed** (0 unreferenced constants).
4. **Schema Guard:** `py -m uv run python -m tools.guest_schema --check` $\rightarrow$ **Passed**.
5. **Physical Account:** `py -m uv run python -m tools.physical_account --check` $\rightarrow$ **Passed** (registry up to date).
6. **Card Validation:** `py -m uv run shipos validate` $\rightarrow$ **All guest cards valid**.
7. **Git Diff Hygiene:** `git diff --check` $\rightarrow$ **0 whitespace errors**.
8. **Automated Tests:** `py -m uv run pytest` $\rightarrow$ **356 passed, 1 skipped** (optional Playwright browser test), 1 warning (Starlette lifespan deprecation).

---

## 3. Admission Robustness Probe (Phase 2)

An automated robustness probe (`probe_robustness.py`) evaluated 130 distinct test cases against the pure engine and API boundary:
- **Seed:** 42
- **Unhandled Exceptions / Crashes:** **0**
- **Outcome Distribution:**
  - `yes`: 6
  - `yes_if`: 58
  - `no`: 50
  - `VALIDATION_PASSED`: 6
  - `VALIDATION_REJECTED`: 10

### 3.1 Probe Categories & Binding Constraints
1. **Mass Demand Scaling:** 42 cases probing hangar, bay 1, and bay 2 mass limits.
   - Result: 16 `yes_if`, 26 `no`.
   - Binding resources: `deck.hangar.mass`, `deck.bay1.mass`.
2. **Power Demand Scaling:** 24 cases varying continuous and peak kW across operational modes.
   - Result: 6 `yes`, 10 `yes_if`, 8 `no`.
   - Binding resources: `power.total`, `deck.bay1`.
3. **Degraded Operational Corners:** 48 cases crossing speed bands (10–22 kn), single genset trips, and 30 °C Mediterranean seawater.
   - Result: 32 `yes_if`, 16 `no`.
   - Binding resources: `heat.manifold.cw`, `power.total`, `deck.bay1`.
4. **Validation Boundary & Malformed Inputs:** 16 cases testing boundary inputs against the REST/CLI parser.
   - Result: 10 rejected cleanly (`VALIDATION_REJECTED`), 6 valid boundary cases accepted (`VALIDATION_PASSED`).
   - Guarded conditions: NaN power values, infinite dimensions, negative mass, duplicate guest IDs, missing mode tiers, and out-of-order coordinate declarations. All rejected via Pydantic or `guest_errors` without internal 500 errors.

---

## 4. Replay & State Isolation Probe (Phase 3)

The state isolation probe (`probe_replay_isolation.py`) verified simulation integrity under varied seeds, event injections, and replayed executions:

```json
{
  "seed_repeatable": true,
  "seed_diverges": true,
  "hash_seed0": "43c808fe2914b19c7ae2ec6b17723deca6dab39764ac96a9cd87a0112709c3ff",
  "hash_seed42": "4c9ab865a6617b4bd90b349371bc38e0166b1f9260e078bafd3b9855b06bf0ca",
  "transactional_rollback": true,
  "valid_injection_applied": true,
  "reset_and_repeat_hash_identical": true,
  "reset_and_repeat_bytes_identical": true,
  "weather_stepped_to_s": 3601.0,
  "weather_at_3601s_present": true,
  "replay_standalone_byte_identical": true,
  "replay_standalone_hash": "aa73bd36639b4bd7e2c8fdc19d65dd6877a2f599867b2b30badc828d6f2e368f",
  "tamper_hash_integrity_verified": "a4440463cc431d298efa60662ca908e1ed091f77be031e0c04919618ac4b4f0c",
  "tamper_execution_replay_detected_mismatch": true,
  "lifecycle_unconfirmed_permitted": false,
  "lifecycle_confirmed_permitted": true,
  "lifecycle_deck_occupied_permitted": false,
  "lifecycle_reopened_without_new_confirm_permitted": false,
  "lifecycle_future_confirmation_rejected": true,
  "lifecycle_future_evidence_rejected": true,
  "lifecycle_unsimulated_source_rejected": true,
  "lifecycle_stale_simulated_evidence_rejected_by_gate": true,
  "lifecycle_valid_uncrewed_simulated_evidence_permitted": true
}
```

### 4.1 Key Findings
1. **True Determinism:** Re-running simulation runs with the same seed yields byte-for-byte identical output and identical SHA-256 hashes (`reset_and_repeat_hash_identical: true`). Different seeds produce divergent physical runs.
2. **Transactional Event Injection:** Injecting an invalid event (e.g. timestamp before the current snapshot time) triggers rollback to the previous valid snapshot without state corruption (`transactional_rollback: true`).
3. **Environmental Boundary Stepping:** Advancing the simulation across the 3600-second boundary smoothly interpolates hourly metocean observations (Strait of Sicily cache) without discontinuity.
4. **Tamper Resistance:** Modifying a record's decision verdict and recomputing SHA-256 hash chains allows `verify_jsonl` to pass cryptographic checks, but `replay_jsonl` re-runs the deterministic physics engine, detects the decision divergence, and raises `ValueError: replay differs: callback or simulator implementation changed`.
5. **Crew Evidence Lifecycle Safety:**
   - Unconfirmed clear deck $\rightarrow$ recovery forbidden (`confirmation_pending`).
   - Valid operator confirmation $\rightarrow$ recovery permitted.
   - Deck occupied $\rightarrow$ recovery forbidden.
   - Deck becomes clear again $\rightarrow$ previous confirmation is dropped automatically; recovery forbidden until a new confirmation is observed.
   - Future confirmations ($t > t_{\text{sim}}$) and unverified/stale simulated cameras $\rightarrow$ strictly rejected.

---

## 5. Performance Benchmarks (Phase 4)

Benchmarking was conducted on the release assembly under controlled execution:
* **Host Platform:** AMD64 (Windows 11 Build 26300)
* **Processor:** Intel64 Family 6 Model 141 Stepping 1
* **Runtime:** CPython 3.13.5 (64-bit)

### 5.1 Benchmark Results

| Metric | Warm-Up First Call | Warm-Up Median | Measured Median (100 runs) | Measured Mean | Measured Min | Measured Max | Measured P95 | Target Bound |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **D1 Admission** (G4 on Scenario A, 10 corners) | 151.5 ms | 80.1 ms | **79.6 ms** | 82.0 ms | 74.0 ms | 127.2 ms | 94.5 ms | $\le 30.0\,\text{s}$ |
| **D2 Placement** (Exhaustive 6-slot search, G1+G2+G4) | 1.94 ms | 1.85 ms | **1.89 ms** | 1.97 ms | 1.79 ms | 2.91 ms | 2.40 ms | Sub-second |

*Notes on Track A Components:*
- **D3 Dynamic Scheduling:** `UNIMPLEMENTED_STUB` (Track A owned; pending Sanjiv).
- **D4 Rationale Generation:** `TEMPLATE_ONLY` (templated rendering via `render(d)` in `rationale.py`; full dynamic trade-space search deferred to Track A).

---

## 6. Deliverable Artifacts Matrix

The complete release validation package includes the following persistent documentation:
1. `docs/RELEASE_VALIDATION.md`: This comprehensive verification report.
2. `docs/REQUIREMENTS_EVIDENCE.csv`: 14-point traceability matrix linking PLAN/CONTRACT requirements to verifiable commits, paths, and test metrics.
3. `docs/MENTOR_TECHNICAL_BRIEF.md`: Technical brief presenting 5 deep clarification questions for mentors and judges regarding operational chronology, battery margins, thermal limits, layered water fidelity, and uncrewed confirmation protocols.
4. `docs/scenario_C_memo.md`: Scenario C operational memo detailing uncrewed flight deck safety and fail-closed interlocks.

---

## 7. Release Recommendation

**Verdict: READY FOR MERGE & INTEGRATION**

Track B is internally consistent, fully tested, and strictly isolated within assigned file ownership boundaries. All changes are ready to merge into `main` and hand off to Sanjiv for Track A integration.
