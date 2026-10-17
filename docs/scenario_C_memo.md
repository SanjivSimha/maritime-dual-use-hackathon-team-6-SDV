# Operational Policy Memo: Flight Deck Recovery and Deck Machinery Gate

**To:** ShipOS Systems Engineering and Mission Operations  
**From:** agy (Noncoding Task Coordinator)  
**Subject:** D5 Crew Policy Specification, Timing Calculations, and Interface Boundaries  
**Status:** Operational Specification (Audited at PR #19 Commit `abfe471`)

---

## 1. Operational Principles and Scope

ShipOS specifies two deterministic crew-safety invariants across flight operations and deck equipment:

1. **Flight Deck Recovery Rule:** The flight deck must be physically clear and explicitly confirmed before initiating VTOL drone recovery.
2. **Deck Machinery Interlock Rule:** Modes operating deck machinery (hangar doors, stern winches) are strictly locked whenever any concurrent active mode prohibits deck machinery.

The crew policy module (`shipos/crew/policy.py`, audited at PR #19 `abfe471`) implements these rules as pure, deterministic evaluative functions over frozen contract objects (`ShipSpec`, `WorldSnapshot`, `GuestManifest`). The functions evaluate gates and interlocks without mutating inputs, reading wall clocks, or actuating vessel equipment. Integrated runtime callers (the simulation runner and API server) do not yet enforce these gates automatically during live stepping.

---

## 2. Recovery Gate Policy Across Crew Complements

Recovery feasibility depends directly on the vessel complement mapped through Annex A / A.6 declared crew levels (`14`, `6`, or `0` crew):

| Crew Complement | Flight Deck Occupied | Flight Deck Clear (Unconfirmed) | Flight Deck Clear (Confirmed / Verified) | Evaluated Gate Status |
|---|---|---|---|---|
| **14 Crew** | Blocked (`deck_occupied`, `confirmation_pending`) | Blocked (`confirmation_pending`) | **Permitted** (`confirmation="received"`) | Fails closed; requires human confirmation |
| **6 Crew** | Blocked (`deck_occupied`, `confirmation_pending`) | Blocked (`confirmation_pending`) | **Permitted** (`confirmation="received"`) | Fails closed; requires early pre-clearance |
| **0 Crew (Uncrewed)** | Blocked (`no_crew_to_clear_deck`) | Blocked (`no_deck_evidence` or `stale_deck_evidence`) | **Permitted** (`confirmation="automatic"` via simulated sensor) | Fails closed (`forbidden`); no diversion invented |

### Required vs. Received Human Confirmation
The policy maintains a strict architectural distinction between *required* and *received* confirmation:
- `confirm = "human"` indicates that the operational state *demands* human sign-off (passed through to the Track A transition planner).
- `confirmation = "received"` indicates that an explicit, valid human report has actually been provided at or before the snapshot timestamp.
Generating a feasible transition plan requiring human confirmation does **not** grant permission to execute recovery. Evaluated recovery remains blocked (`permitted = False`) until `confirmation="received"`.

---

## 3. Six-Crew Early Clearance, Weather, and Landing Timing Calculations

### Declared Timing Parameters (Annex A Upper Bounds & Manifest Assumptions)
- **Course Settle:** 60–180 s (A.6 upper bound: 180 s).
- **Speed Band Adjustment:** 120–300 s (A.6 upper bound: 300 s).
- **G4 Landing Approach:** 3 min (180 s; assumption `guests.g4.landing_approach_min`).
- **G4 Battery Thresholds:** Critical at 20%; declared landing window is 12 min (`Battery.land_within_min`).
- **Deck Preparation & Confirmation:**
  - **14 Crew:** Deck clear upper bound = 300 s (5 min); confirmation upper bound = 60 s (1 min). Total lead = 360 s (6 min). Total calculated time to touchdown = 540 s (9 min).
  - **6 Crew:** Deck clear upper bound = 720 s (12 min); confirmation upper bound = 180 s (3 min). Total lead = 900 s (15 min). Total calculated time to touchdown = 1080 s (18 min).

### The Six-Crew Pre-Clearance Requirement
With 6 crew aboard, deck preparation and confirmation require 15 minutes on upper bounds. Adding the 3-minute final approach, total clearance lead is 18 minutes. 

Because the G4 emergency landing window after a critical battery report is 12 minutes, initiating deck clearance *only after* a critical battery alert cannot clear the deck in time under upper-bound calculations:
$$\text{Elapsed to Touchdown} = 900\,\text{s} + 180\,\text{s} = 1080\,\text{s}\;(18\,\text{min}) > 720\,\text{s}\;(12\,\text{min})$$

`early_clearance()` evaluates the required start time:
$$\text{start\_by\_s} = \text{forecast.land\_by\_s} - \text{approach\_s} - \text{lead\_s}$$

For a landing deadline at minute 20 ($t = 1200\,\text{s}$), clearance must start by minute 2 ($t = 120\,\text{s}$).

### Weather, Invalid Forecast, and Late Semantics (PR #19 `abfe471`)
- **Environmental Gating:** `early_clearance()` passes `snapshot.env` to the Track A planner. Wind (> 25 kn) or sea state (> 4) exceeding G4 card limits adds `recovery_state_blocked` and records `planner_blocker`. This suppresses `request` (`False`) and clears `requests` (`()`), while preserving `lead_s` and `start_by_s` as timing references.
- **Forecast Validation:** Missing or future-dated forecasts return `no_forecast`. Non-finite values (NaN or $\pm\infty$) in `issued_s` or `land_by_s` return `invalid_forecast` with no timing outputs.
- **Late Evaluation:** `late = True` indicates that preparation starts after `start_by_s` (whether the forecast arrived late or was evaluated after `start_by_s`). Clearance is still requested immediately, but `late` serves as an operational warning, not a definitive missed-deadline verdict.

*Note: These figures represent upper-bound planning calculations, not demonstrated real-time mission traces.*

---

## 4. Zero-Crew Operations: Simulated Evidence and Fail-Closed Behavior

In an uncrewed configuration (0 crew):
1. **No Physical Deck Clearing:** `deck_clear_s` is `null`. If the deck is occupied, clearing is impossible (`no_crew_to_clear_deck`), and recovery cannot proceed.
2. **Simulated Sensor Evidence:** Automatic clearance verification requires a `DeckEvidence` reading from the current tick (`evidence.t_s == snapshot.t_s`) with `deck_clear == True`. In this prototype, camera/sensor inputs are strictly **simulated** (`source="simulated_camera"`); no real camera hardware is integrated.
3. **Fail-Closed Verification:**
   - Missing evidence $\rightarrow$ blocked (`no_deck_evidence`).
   - Stale evidence ($t_{\text{evidence}} \ne t_{\text{snapshot}}$) $\rightarrow$ blocked (`stale_deck_evidence`).
   - Sensor reporting obstruction $\rightarrow$ blocked (`evidence_not_clear`).
4. **No Speculative Outcomes:** The policy returns `confirm = "forbidden"`. It does not invent emergency diversion airfields, unverified recovery nets, or automated clearing mechanisms. Contractual default remains `Battery.on_deny = "ditch"`.

---

## 5. Deck-Machinery Interlock

The `machinery_lock()` function evaluates whether a target guest mode requiring deck machinery (`deck_machinery: true`, such as G4 hangar door operations or G2 oceanographic winch handling) conflicts with active guest modes.
- Interlocks are derived dynamically from condition conflicts (`deck_machinery_allowed: false`), never hardcoded guest pairs.
- **Operational Case:** While G3 (underwater vehicle plus shipboard cradle) is active in `surveying`, `deck_machinery_allowed` is `False` to prevent acoustic interference. G4 recovery and G2 winch activation are evaluated as locked (`locked = True`, `by = ("G3.surveying",)`).
- When G3 transitions to `standby` (where deck machinery is permitted), the lock disengages automatically.

---

## 6. Integration Seams and Caller Responsibilities

Because `WorldSnapshot` is frozen and stateless, calling systems (the simulation runner or API orchestration layer) retain explicit operational responsibilities:

1. **Confirmation Lifecycle Management:** A `HumanConfirmation` token applies strictly to a single clear-deck episode. Because the policy is stateless, the caller **must discard** the confirmation token as soon as `snapshot.ship.deck` becomes `DeckState.OCCUPIED`. A reopened deck requires fresh human confirmation.
2. **Sensor Freshness Feeding:** For uncrewed checks, the caller must supply simulated `DeckEvidence` matching the exact tick timestamp.
3. **Separation of Policy Gate from Execution:** Calling `recovery_gate()` evaluates authorization. It does not actuate equipment, alter ship heading, or move throttles. The caller must inspect `gate.permitted` prior to dispatching state transitions.
4. **Track A Ownership Boundaries:** Crew policy determines whether the flight deck gate permits entry. Track A retains exclusive authority over physical propulsion, speed/course settling feasibility, steering margins, and battery exhaustion calculations.

---

## 7. Implementation Status vs. Integrated Execution

- **Implemented in Code (`shipos/crew/policy.py` at `abfe471`):**
  - Pure evaluation of `recovery_gate`, `early_clearance`, and `machinery_lock`.
  - Machine-readable blocker codes and weather-aware transition planner integration.
  - Focused edge-case review completed by `droid`; corrections owned and merged by Claude into PR #19. Unit tests pass in `tests/crew/test_crew_policy.py`.
- **Current Integration Status (Gaps):**
  - Neither the simulation runner (`shipos.sim.runner.Runner`) nor the API server (`shipos.api.server`) currently calls the crew policy gate during live tick execution.
  - The simulator runner applies scheduled mode changes declaratively without gate arbitration.
  - Live end-to-end flight deck clearance with event-driven confirmation remains an open integration deliverable.
