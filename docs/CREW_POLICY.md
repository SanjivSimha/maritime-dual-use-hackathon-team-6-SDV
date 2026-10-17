# Crew policy (D5)

Rule: **the flight deck is clear and confirmed before drone recovery.** Second rule: **deck
machinery is locked while another active mode forbids it.** Code: `shipos/crew/policy.py`, pure
functions over contract objects. They read no clock, mutate no input and return frozen results.

## Interface

```python
from shipos.crew import (
    recovery_gate,
    early_clearance,
    machinery_lock,
    HumanConfirmation,
    DeckEvidence,
    BatteryForecast,
)

gate = recovery_gate(ship, snapshot, confirmation=None, evidence=None)  # RecoveryGate
advice = early_clearance(ship, snapshot, guests["G4"], forecast)  # ClearanceAdvice
lock = machinery_lock(guests, snapshot, "G2", "winch")  # MachineryLock
```

| Result | Fields |
|---|---|
| `RecoveryGate` | `permitted`, `confirm` (planner value), `confirmation` (what actually happened), `blockers`, `requests` |
| `ClearanceAdvice` | `request`, `start_by_s`, `lead_s`, `approach_s`, `late`, `blockers`, `requests`, `planner_blocker` |
| `MachineryLock` | `locked`, `by` (e.g. `("G3.surveying",)`) |

`requests` are contract `StateRequest`s from the Track A planner, with `start_s` relative to
`snapshot.t_s`. `blockers` are codes (`deck_occupied`, `confirmation_pending`,
`no_crew_to_clear_deck`, `no_deck_evidence`, `stale_deck_evidence`, `evidence_not_clear`,
`no_forecast`, `invalid_forecast`, `recovery_state_blocked`), not prose.

## Evidence the caller supplies

`WorldSnapshot` is frozen and carries none of this, so it comes in as arguments:

| Input | Meaning | Who supplies it |
|---|---|---|
| `HumanConfirmation(t_s, by)` | a crew member confirmed the deck clear at `t_s` | the UI operator action, or a scripted sim event |
| `DeckEvidence(t_s, deck_clear, source="simulated_camera")` | a deck camera / sensor reading. **Simulated**; there is no real camera integration | the simulator, each tick |
| `BatteryForecast(issued_s, land_by_s)` | the drone must be on deck by `land_by_s` | the simulator's battery model / Track A deadline logic |

A confirmation covers one clear-deck episode. The policy is stateless and sees one snapshot, so
it **cannot detect** that the deck was occupied in between: the caller must discard the
`HumanConfirmation` as soon as `snapshot.ship.deck` becomes `occupied`, and a reopened deck then
needs a new one (`test_confirmation_lifecycle_is_the_callers` models this). That test only shows
the policy's side; enforcing the lifecycle in the API/simulator is the integration task. A
confirmation or forecast dated after `snapshot.t_s` is ignored. Deck evidence
counts only if `evidence.t_s == snapshot.t_s` (a reading from this tick).

## Crew levels

The crew level is A.6's: `statemachine.crew_key` maps any count to 14, 6 or 0. Humans can confirm
when `ship.transitions.confirm_s[level]` is not null.

| Crew | Deck occupied | Deck clear |
|---|---|---|
| 14 / 6 | blocked: `deck_occupied` + `confirmation_pending`; requests `deck` then `confirm:deck` | blocked until a `HumanConfirmation` is received; then permitted |
| 0 | blocked: `no_crew_to_clear_deck` (no one can clear it) | permitted only with current `DeckEvidence(deck_clear=True)`; otherwise blocked, fail closed |

Required is not received: `confirm="human"` says a human must confirm; only
`confirmation="received"` says one did.

### Early clearance from the battery forecast (6 crew)

`early_clearance` asks the planner for the time to reach the drone's recovery conditions from
the current ship state, on A.6 upper bounds (`lead_s`). The deck is cleared first, then a human
confirms; speed and course settle at the same time. `approach_s` is the recovery activity's
duration from the G4 card. Clearance must start by

```
start_by_s = forecast.land_by_s - approach_s - lead_s
```

and is requested once `snapshot.t_s >= start_by_s`. With 6 crew, A.6 gives 720 s to clear the
deck plus 180 s to confirm, so the request has to go out about 18 min before touchdown. With 14
crew it is 300 + 60 s.

- **Weather:** the planner gets `snapshot.env`. Wind or sea state beyond the recovery mode's
  card limits (G4: 25 kn, sea state 4), or any other planner blocker, adds
  `recovery_state_blocked` and the planner's own text in `planner_blocker`. `request` is then
  false and `requests` empty. `lead_s` / `start_by_s` are still reported as timing information.
- **`request` / `requests`** are advice to start deck preparation, not a feasibility verdict or an
  authorised recovery plan. They are empty whenever any blocker other than `deck_occupied`
  applies, or before `start_by_s`.
- **`late=True`** means preparation starts after `start_by_s`. That happens when the forecast
  arrived late, or when it arrived on time but was first evaluated after `start_by_s` (exactly at
  `start_by_s` is not late). Clearance is still requested. `late` is not proof that the landing
  deadline is missed.
- **Forecast missing or issued after `snapshot.t_s`:** `no_forecast`. **NaN or ±infinity in
  either timestamp:** `invalid_forecast`. Both give no request and no timing outputs.

None of these change the deck state: `recovery_gate` stays blocked until the deck is clear and
confirmed. Whether the drone can still
land in time is Track A's deadline calculation, not this policy's. `Battery.on_deny` (ditch) is
unchanged, and no diversion destination is invented.

## Mapping to `plan_transition(confirm=...)`

| `gate.confirm` | When | Planner effect |
|---|---|---|
| `"human"` | 14 or 6 aboard | adds the A.6 `confirm:deck` step after clearing |
| `"automatic"` | 0 aboard, deck clear, current simulated evidence shows clear | no confirmation step |
| `"forbidden"` | 0 aboard, otherwise | plan infeasible |

Pass `gate.confirm` through unchanged. The planner still applies its own deck, wind, sea-state
and hold checks. A feasible plan with `"human"` does **not** mean confirmation was received.
Check `gate.permitted` before starting recovery.

## Deck-machinery lock

`machinery_lock` locks a mode with `deck_machinery: true` when another guest's **current** mode
(`snapshot.guests[gid].mode`) has `deck_machinery_allowed: false`. It uses
`statemachine.conflicts`, so there are no guest-pair exceptions. G2's winch and G4's hangar
doors are both locked while G3 is `surveying`, and both are free when G3 is in `standby`.
