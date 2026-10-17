# Operational Policy Memo: Flight Deck Recovery and Deck Machinery Gate

**To:** ShipOS Systems Engineering and Mission Operations  
**From:** agy (Noncoding Task Coordinator)  
**Subject:** D5 Crew Policy Specification, Timing Calculations, and Interface Boundaries  
**Status:** Verification Draft (Pending Droid Edge-Case Review and Track A Integration)

---

## 1. Operational Principles and Scope

ShipOS enforces two deterministic crew-safety invariants across flight operations and deck equipment:

1. **Flight Deck Recovery Rule:** The flight deck must be physically clear and explicitly confirmed before initiating VTOL drone recovery.
2. **Deck Machinery Interlock Rule:** Modes operating deck machinery (hangar doors, stern winches) are strictly locked whenever any concurrent active mode prohibits deck machinery.

The crew policy module (`shipos/crew/policy.py`) implements these rules as pure, deterministic functions over frozen contract objects (`ShipSpec`, `WorldSnapshot`, `GuestManifest`). The functions do not mutate state, read system wall clocks, or trigger autonomous physical actions. 

---

## 2. Recovery Gate Policy Across Crew Complements

Recovery feasibility depends directly on the vessel complement mapped through Annex A / A.6 declared crew levels (`14`, `6`, or `0` crew):

| Crew Complement | Flight Deck Occupied | Flight Deck Clear (Unconfirmed) | Flight Deck Clear (Confirmed / Verified) | Failure Mode |
|---|---|---|---|---|
| **14 Crew** | Blocked (`deck_occupied`, `confirmation_pending`) | Blocked (`confirmation_pending`) | **Permitted** (`confirmation="received"`) | Fails closed; waits for human report |
| **6 Crew** | Blocked (`deck_occupied`, `confirmation_pending`) | Blocked (`confirmation_pending`) | **Permitted** (`confirmation="received"`) | Fails closed; requires early pre-clearance |
| **0 Crew (Uncrewed)** | Blocked (`no_crew_to_clear_deck`) | Blocked (`no_deck_evidence` or `stale_deck_evidence`) | **Permitted** (`confirmation="automatic"` via current sensor) | Fails closed (`forbidden`); no diversion destination invented |

### Required vs. Received Human Confirmation
The policy makes a strict architectural distinction between *required* and *received* confirmation:
- `confirm = "human"` indicates that the operational state *demands* human sign-off (populated in transition requests for the Track A planner).
- `confirmation = "received"` indicates that an explicit, valid human report has actually been provided at or prior to the current snapshot time.
Generating a feasible transition plan requiring human confirmation does **not** grant permission to execute recovery. Execution remains blocked (`permitted = False`) until `confirmation="received"`.

---

## 3. Six-Crew Early Clearance and Landing Timing Calculations

### Timing Parameters (Declared Annex A Upper Bounds and Manifest Assumptions)
- **Course Settle:** 60–180 s (A.6 upper bound: 180 s).
- **Speed Band Adjustment:** 120–300 s (A.6 upper bound: 300 s).
- **G4 Landing Approach:** 3 min (180 s; assumption `guests.g4.landing_approach_min`).
- **G4 Battery Thresholds:** Critical at 20%; declared landing window is 12 min (`Battery.land_within_min`).
- **Deck Preparation & Confirmation:**
  - **14 Crew:** Deck clear upper bound = 300 s (5 min); confirmation upper bound = 60 s (1 min). Total lead = 360 s (6 min). Total time to touchdown = 540 s (9 min).
  - **6 Crew:** Deck clear upper bound = 720 s (12 min); confirmation upper bound = 180 s (3 min). Total lead = 900 s (15 min). Total time to touchdown = 1080 s (18 min).

### The Six-Crew Pre-Clearance Requirement
With 6 crew aboard, deck preparation and confirmation require 15 minutes on upper bounds. Adding the 3-minute final approach, total clearance lead is 18 minutes. 

Because the G4 emergency landing window after a critical battery report is 12 minutes, initiating deck clearance *only after* a critical battery alert is mathematically incapable of clearing the deck in time under upper-bound conditions:
$$\text{Elapsed to Touchdown} = 900\,\text{s} + 180\,\text{s} = 1080\,\text{s}\;(18\,\text{min}) > 720\,\text{s}\;(12\,\text{min})$$

`early_clearance()` calculates the required start time:
$$\text{start\_by\_s} = \text{forecast.land\_by\_s} - \text{approach\_s} - \text{lead\_s}$$

For an alert issued at minute 8 ($t = 480\,\text{s}$) with a hard deadline at minute 20 ($t = 1200\,\text{s}$):
$$\text{start\_by\_s} = 1200\,\text{s} - 180\,\text{s} - 900\,\text{s} = 120\,\text{s}\;(\text{minute } 2)$$

If the forecast is not issued until minute 8, $\text{snapshot.t\_s} (480) \ge \text{start\_by\_s} (120)$, and the advice flags `late = True`. The policy continues to request deck clearance immediately, but does **not** falsify deck readiness or compress upper-bound safety margins.

*Note: These figures represent upper-bound planning calculations, not completed real-time mission traces.*

---

## 4. Zero-Crew Operations: Same-Tick Evidence and Fail-Closed Behavior

In an uncrewed configuration (0 crew):
1. **No Physical Deck Clearing:** `deck_clear_s` is `null`. If the deck is occupied, clearing is impossible (`no_crew_to_clear_deck`), and recovery cannot proceed.
2. **Same-Tick Sensor Evidence:** Automatic clearance verification requires a `DeckEvidence` object where `evidence.t_s == snapshot.t_s` and `evidence.deck_clear == True`.
3. **Fail-Closed Verification:**
   - Missing evidence $\rightarrow$ blocked (`no_deck_evidence`).
   - Stale evidence ($t_{\text{evidence}} \ne t_{\text{snapshot}}$) $\rightarrow$ blocked (`stale_deck_evidence`).
   - Sensor reporting obstruction $\rightarrow$ blocked (`evidence_not_clear`).
4. **No Speculative Outcomes:** The policy returns `confirm = "forbidden"`. It does not invent emergency diversion airfields, unverified recovery nets, or automated clearing mechanisms. Contractual default remains `Battery.on_deny = "ditch"`.

---

## 5. Deck-Machinery Interlock

The `machinery_lock()` function evaluates whether a target guest mode requiring deck machinery (`deck_machinery: true`, such as G4 hangar door operations or G2 oceanographic winch handling) conflicts with active guest modes.
- Interlocks are derived purely from mode conditions (`deck_machinery_allowed: false`), never hardcoded guest ID pairs.
- **Operational Case:** While G3 (deep-tow acoustic vehicle) is active in `surveying`, `deck_machinery_allowed` is `False` to prevent acoustic and hydrodynamic interference. G4 recovery and G2 winch activation are locked (`locked = True`, `by = ("G3.surveying",)`).
- When G3 transitions to `standby` (where deck machinery is permitted), the lock disengages automatically without bespoke configuration.

---

## 6. Integration Seams and Operational Caller Responsibilities

Because `WorldSnapshot` is frozen and stateless, calling systems (the simulation runner or API orchestration layer) retain explicit operational responsibilities:

1. **Confirmation Lifecycle Management:** A `HumanConfirmation` token applies strictly to a single clear-deck episode. The caller **must invalidate and discard** confirmation as soon as `snapshot.ship.deck` transitions back to `DeckState.OCCUPIED`.
2. **Sensor Freshness Feeding:** For uncrewed checks, the caller must supply a freshly sampled `DeckEvidence` matching the exact tick timestamp.
3. **Separation of Policy Gate from Execution:** Calling `recovery_gate()` evaluates authorization. It does not actuate equipment, alter ship heading, or move throttles. The caller must inspect `gate.permitted` prior to dispatching state transitions.
4. **Track A Ownership Boundaries:** Crew policy determines whether the flight deck gate permits entry. Track A retains exclusive authority over physical propulsion, speed/course settling feasibility, steering margins, and battery exhaustion calculations.

---

## 7. Current Implementation vs. Integrated Execution

- **Implemented in Code (`shipos/crew/policy.py`):**
  - Pure calculation of `recovery_gate`, `early_clearance`, and `machinery_lock`.
  - Machine-readable blocker codes and deterministic planner condition matching.
  - Full unit test coverage in `tests/crew/test_crew_policy.py` (220 lines, 100% pass rate).
- **Current Integration Status (Gaps):**
  - Neither the simulation runner (`shipos.sim.runner.Runner`) nor the API server (`shipos.api.server`) currently calls the crew policy gate during step execution.
  - The simulator runner applies scheduled mode changes declaratively without gate arbitration.
  - Edge-case review by `droid` is actively underway.
  - Live end-to-end flight deck clearance with event-driven confirmation remains an open integration deliverable.
