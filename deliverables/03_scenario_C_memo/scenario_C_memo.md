# Scenario C memo: "Nobody on the flight deck during recovery"

**To:** Fincantieri SDV jury · **From:** Team 6, PoseidonOS · **Date:** 18 October 2026

## The rule the crew enforces by common sense

On today's ships no system knows this rule. A deck officer looks aft, sees the deck is clear, and
tells the bridge the drone may land. PoseidonOS writes it down as a gate in `shipos/crew/policy.py`
(`recovery_gate`), with evidence tracked in `shipos/crew/lifecycle.py`:

- **Trigger:** any guest activity whose conditions include `uses_flight_deck` (G4 recovery or launch).
  In Scenario B this happens at minute 8, when D3 grants the drone right of way.
- **Effect:** recovery may start only when (1) the deck has been cleared, (2) that state has been
  confirmed under the policy for the current crew level, and (3) the confirmation is still valid
  when the operation starts. Re-occupying the deck or changing the crew level invalidates the
  confirmation. A later clear-deck episode needs fresh confirmation. Until then the drone holds
  (keeps flying) and the engine reports the blocker. The governor never invents a landing.
- **Coupled interlock:** deck machinery (G2 winch, hangar doors) may not run while an active mode
  forbids it. This comes from declared card conditions, not a hard-coded pair, so G3 listening
  blocks G4's hangar doors as well as G2's winch.

## The same rule at 14, 6 and 0 aboard

Times are A.6 upper bounds (our Physical Account plans on the slowest value) plus the assumed 3-min drone approach.

| | **14 aboard (patrol)** | **6 aboard (monitoring)** | **0 aboard (night transit)** |
|---|---|---|---|
| Clear the deck | 5 min | 12 min | impossible if occupied |
| Confirmation | human, 1 min (15–60 s) | human, 3 min (1–3 min) | none possible |
| Lead time incl. approach | 9 min | **18 min** | n/a |
| **A human still confirms** | deck clear before recovery; any contested D3/D4 outcome | the same, but slower | nothing |
| **Becomes automatic** | (none) | early clearance from the battery *forecast* | recovery **only** if fresh, same-tick deck evidence shows clear |
| **Becomes impossible** | (none) | reacting to the 20 % alert alone (18 min > 12 min) | clearing an occupied deck; at-sea installation (A.11: logical reconfiguration only) |

**Six crew is the interesting case.** The drone must land within 12 min of the 20 % alert, but 6
crew need 18 min to clear, confirm and recover. Reacting to the alarm is therefore already too late, and
the rule has to move earlier: `early_clearance()` takes a recorded battery forecast and computes
`start_by = deadline − approach − clear − confirm`. For a deadline at t = 1200 s this gives
1200 − 180 − 900 = 120 s, so preparation starts at minute 2. Forecasts that are missing, future-dated or
non-finite are rejected, and weather outside the card's recovery limits (wind ≤ 25 kn, sea state ≤ 4) blocks.

**Zero crew turns the rule from a judgement into evidence.** With no one aboard there is no one
to confirm, so the rule is either automatic or forbidden (A.6). We make it automatic only on fresh
deck-state evidence from the same tick, labelled `simulated_deck_state` in this prototype. Missing,
stale or obstructed evidence blocks the landing, and the decision log says so. Nothing physical can
change, so the governor re-tasks compute instead (A′ at 0 crew).

## What the operator sees and does (shown live)

Scenario B in the dashboard, 14 crew, either priority file, with *Let the simulation carry out the
engine's actions* on:

1. **t = 480 s.** D3 decides in under 2 s: G4 first, G3 holds (survey) or suspends (SAR).
2. **t = 540 s.** G3's acoustic hold completes. **t = 780 s:** deck cleared. **t = 840 s:** preparation done.
3. G4 **stays airborne** until the operator records *Confirm deck is clear*. At t = 841 s recovery starts.
4. At 6 or 0 crew, the same baseline (deck occupied) yields a recorded denial with its blockers, not a landing.

`tests/api/test_execution_acceptance.py` runs all of this under both priority files and 14/6/0 crew,
then re-executes the exported log byte for byte.

## Why this design

- **One rule, three ships.** The rule text never changes. Only the per-crew confirmation policy
  (`human` / `automatic` / `not permitted`) and the A.6 times change, and both are data.
- **Confirmation is input, never inference.** A planner's `confirm="human"` is a requirement, not
  permission. Fast-forwarding the simulator never creates a confirmation token.
- **Fail closed.** At every crew level, no evidence means no landing and a stated reason.

**Boundaries.** Camera and deck-sensor integration is out of scope, so zero-crew evidence is simulated. Times
are planning bounds, not trials. Ditching and diversion are reported as risk; they are never simulated as outcomes.
