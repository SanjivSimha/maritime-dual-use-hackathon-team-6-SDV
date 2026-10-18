# Validation report: PoseidonOS against the SDV challenge brief

Date: 18 October 2026. The evidence below comes from three independent sources:

1. `make check`: lint, no-literal guardrail and the full pytest suite. **491 passed, 18 skipped.**
   The skips are browser tests that need the optional `playwright` package.
2. `validate.py` (this folder) drives the real engine with expected values computed from the
   Annex A/B arithmetic, never pasted from engine output. **54 / 54 checks pass.** The full log is
   `validation_output.txt`. Rerun with `PYTHONPATH=. uv run python deliverables/05_validation/validate.py`.
3. A live run of the dashboard (`make demo`) in a browser, with screenshots in `screenshots/`.

## Must-have criteria

| Brief requirement | Status | Evidence |
|---|---|---|
| Manifest schema encoding all five Annex B cards with the B.0 fields | ✅ | `schema/guest_manifest.schema.json`, `data/guests/G1–G5.yaml`; MAN-1, MAN-3 |
| Documented way to add a new card | ✅ | `docs/ADDING_A_CARD.md`, `uv run shipos validate <card>`; MAN-5 |
| Platform budget, all eight classes | ✅ | `ResourceClass` has 8 members; all eight gauges on screen; PHY-5 |
| Power–heat coupling 1:1, cooling f(T_sea), degraded state | ✅ | 160 → 140 → 120 kW at 12 / 21 / 30 °C, 72 kW degraded at 30 °C; PHY-2, PHY-3 |
| Power reserve f(speed, gensets) | ✅ | 200 / 100 kW; PHY-1 |
| Scenario A end-to-end: verdict ≤ 30 s against envelope, binding named in one sentence | ✅ | G4 *yes-if* in about 50 ms, binding power.total 101.5 / 100 kW at "slow, 30 °C, one genset down"; G3 *yes-if* on deck.bay1; A-G3, A-G4 |
| …placement, ranked displacement list, one-genset-down statement | ✅ | 5 ranked fixes for G4 and 2 for G3; `holds_one_genset_down` set; A-G4r, A-G3h |
| Scenario B min 8: decision ≤ 2 s, rationale, cost to loser | ✅ | < 1 ms; survey: *G3 holds 15 min, 23 survey-min*; SAR: *G3 suspends, 35 kW reserved, 83 survey-min*; B8-* |
| B: hard constraint first (land ≤ 12 min) | ✅ | binding time.landing_deadline 41 vs 12 min; B8-hard |
| B min 12: explicit shedding sequence that keeps the winner alive and states every guest's loss | ✅ (see finding F3) | overlap case: survey throttles G5 → 8 kW at t+1 s; SAR defers G3's charge 5 min; G4 never touched; B12-* |
| Two priority sets → different, coherent outcomes | ✅ | pause vs suspend at min 8; throttle G5 vs defer G3 at min 12; B8-diff |
| Identifiable engine D1, D3 + D2/D4 with declared objectives in README | ✅ (all four) | `shipos/engine/*.py` docstrings, README table |
| Scenario C memo ≤ 2 pages; Physical Account | ✅ | `03_scenario_C_memo/` (2 pp), `02_physical_account/` (1 p + registry) |

## Should-have criteria

| Requirement | Status | Evidence |
|---|---|---|
| D2 maximising margin, rejected alternatives shown | ✅ | lexicographic objective; alternatives with reasons |
| Jury changes one number live | ✅ | G4 charging 20 → *yes*; 41 → *yes-if* (107.5 kW); 70 → *yes-if, G5 low-power*; mass 20 t → *no* on hangar mass; UI JSON card editor |
| Scenario A′ with installation windows | ⚠️ partial | port-only → *not now, next port call ≤ 14 days*; 0 crew → logical re-task. The window-forecast path exists (`admission.next_window`), but no Annex card installs at sea, so no real card exercises it |
| Cooling and deck driven by real public data | ✅ | Open-Meteo Strait of Sicily cache (`data/env_cache/`), opt-in in the UI, no runtime fetch |
| Guest exceeding its declaration handled | ✅ | +10 % warn, +15 % throttle, 60 s shed; `test_overdraw_*` |
| Scenario C rule at two crew levels, live | ✅ | 14 (human confirm) and 0 (evidence or denial); API acceptance tests |
| Deterministic, replayable; rationale from the binding constraint | ✅ | identical SHA-256 over two runs; replay byte-identical; DET-1/2; `rationale.render` only |

## Constraints

| Constraint | Status | Evidence |
|---|---|---|
| Offline, laptop, single screen | ✅ | no remote assets in the UI (OFFL); `--offline` launch |
| No clock, randomness or I/O in the engine | ✅ | PURE check and CLAUDE.md rule 2 |
| Every decision one plain sentence naming the binding constraint | ✅ | every decision in the log carries `binding` and a rendered rationale |
| Undocumented assumption is a defect | ✅ | literal guardrail in CI; 96 registered assumptions with use sites |
| Civil only; no ML in decisions; no China-based models | ✅ | rule-based engines; civil cards only |

## Findings, and what was done about them

| # | Finding | Severity | Action |
|---|---|---|---|
| F1 | **No card of our own invention.** The brief requires "five cards plus one card of your own invention" | blocker | **Fixed:** the team's original `data/guests/G6.yaml` (towed passive acoustic array, #47: ramp handling, tow speed, quiet, no deck machinery, pausable transect, 17 registered assumptions) is integrated. A second invented card, `G7.yaml` (survivor-care: safety-critical, requires G5, peak), exercises the remaining B.0 flags. The historical G1–G5 baseline hash `ca3abd5…` is kept via a captured-input fixture; a separate test covers the expanded catalog |
| F2 | **D2 could put two guests on one Bay 1 half.** The position check counted units per bay group (2 ≤ 2) but not per slot, and the mirror-image dedupe then hid the valid free half. A light half-bay card was admitted onto G5's half (this is why #47 originally reported G6 as yes-if displacing G5 on a 5.5 t bay1a; with the fix G6 is a clean yes on bay1b) | correctness | **Fixed:** `physics/positions.py` adds a per-slot occupancy check (`deck.<slot>.shared`); `engine/placement.py` dedupes only candidates without doubled slots; `engine/rationale.py` renders the new clause. Two regression tests in `tests/physics/test_mass_positions.py` fail before the fix and pass after. The Annex scenarios are unaffected (baseline hash unchanged by the fix) |
| F3 | **Baseline B does not shed at minute 12.** The drone is still airborne (49.5 kW drawn), so the honest answer is "no shedding needed" plus the armed ladder (G5 throttle → G1 off → G3 standby). Even the brief's own numbers (35 + 45 + 2.5 = 82.5 kW < 100) do not overload | demo risk | Not changed. The engine is right. **In the demo, narrate the armed ladder and then show the charging-overlap case** (106.5 kW) from `validation_output.txt` (B12-sar / B12-survey-on-overlap) or `uv run pytest tests/engine/test_shedding.py -v` |
| F4 | **UI trap.** *Run the mission* keeps *Situation = In port* (Scenario A), so *Report battery* fails silently (HTTP 422: G4 is not aboard) | demo risk | Not changed (UI is outside this task). **Runbook step: select "At sea, running a mission" first** |
| F5 | **Stale submission docs.** Docs were pinned to `fcd8c58`, said "G6 is absent", used the old name ShipOS, gave Windows-only launch steps, and lacked "another week" and the Annex C closing question | submission | **Fixed:** new README (≤ 3 pp), Physical Account in Annex C form with the closing question, memo restructured to the brief's questions, `make demo` |
| F6 | **Brief arithmetic.** "Any four exceed the 10 t margin" is false for {G1,G2,G4,G5} (7.61 t) and {G1,G3,G4,G5} (9.61 t) | note | Recorded in the README and Physical Account; the engine follows the card masses |
| F8 | **Response-time gauge always empty.** Nothing timed the engine; the gauge defaulted to 0 s and the server hid it | demo gap | **Fixed:** the API adapter times every real engine call (`time.perf_counter`, outside the pure engine) and the gauge shows the latest one against its A.8 ceiling (admission 30 s, arbitration 2 s, first shed 5 s, including the scheduled 1 s shed offset). Measured live: 33–49 ms admission, 0.5 ms D3, 0.2 ms D4. Latency stays out of the hashed log, and the pinned replay hashes are unchanged |
| F7 | **A.11 portable ≤ 250 kg** is declared in ship data but not enforced, and no card installs at sea | gap | Listed under limitations and "another week" |

## Files changed in the repository by this validation (uncommitted)

`data/guests/G7.yaml` (new; merged as G6 in #61, renamed when #47 landed), `data/assumptions/other.yaml`, `data/assumptions/compute_time.yaml`,
`docs/PHYSICAL_ACCOUNT_REGISTRY.md` (regenerated), `shipos/physics/positions.py`,
`shipos/engine/placement.py`, `shipos/engine/rationale.py`, `tests/physics/test_mass_positions.py`,
three pinned hashes in `tests/sim/`, the same hash in `docs/SIM_EXECUTION.md`, `docs/ENVIRONMENT.md`
and `shipos/sim/README.md`, plus `README.md` and `Makefile` (`demo` target). For F8: `shipos/api/adapter.py`, `shipos/api/server.py`,
`shipos/api/static/app.js`, `shipos/physics/budget.py`, `tests/api/test_server.py`, `tests/physics/test_budget.py`.
