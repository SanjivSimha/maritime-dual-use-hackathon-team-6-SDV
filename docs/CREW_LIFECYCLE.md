# Crew-evidence lifecycle

`recovery_gate` ([CREW_POLICY.md](CREW_POLICY.md)) is stateless and sees one snapshot. It cannot
tell that the deck was occupied after a human confirmed it clear. `shipos/crew/lifecycle.py` is
the caller-side state that closes that gap. It is a small frozen `CrewEvidence` value plus pure
functions: no clock, no globals, no actuation. It never clears a deck, changes a guest mode,
operates machinery or authorises a landing. Permission still comes from `recovery_gate`, and
landing-deadline feasibility stays with Track A.

## Interface

```python
CrewEvidence(last_t_s=None, crew=None, clear_since_s=None, confirmation=None, evidence=None)
observe(state: CrewEvidence, snapshot: WorldSnapshot, evidence: DeckEvidence | None = None) -> CrewEvidence
confirm(state: CrewEvidence, confirmation: HumanConfirmation) -> CrewEvidence
gate(ship: ShipSpec, state: CrewEvidence, snapshot: WorldSnapshot) -> RecoveryGate
LifecycleError(ValueError)  # rejected input; the previous state is unchanged and still valid
```

## Rules

| Event | Effect |
|---|---|
| `observe` with an occupied deck | confirmation discarded; the clear-deck episode ends |
| `observe` with a clear deck | episode starts at this `t_s` (or continues); confirmation kept |
| `observe` with a different `crew` | confirmation discarded |
| `observe(..., evidence)` | replaces any earlier reading; omitted means none (readings never carry over a tick) |
| `confirm` | stores the operator's confirmation for the current episode |
| `gate` | `recovery_gate(ship, snapshot, state.confirmation, state.evidence)` |
| `CrewEvidence()` | fresh session or reset: no confirmation, no evidence |

`LifecycleError` is raised, and the old state stays valid, when:
- a snapshot time or evidence time is NaN or ±infinity;
- a snapshot is earlier than the last observed one (a rewind needs a fresh `CrewEvidence()`);
- evidence is dated after its snapshot, or its `source` does not contain `simulated`;
- `confirm` is called before any observation, on an occupied deck, with a non-finite time, after
  the last observed snapshot, or before the current clear episode began (an old confirmation
  cannot be carried into a new episode);
- `gate` is called with a snapshot other than the last one observed (time, crew or deck differ).

A clear deck never produces a confirmation, and nothing creates a camera reading. Both arrive
only as explicit inputs. Evidence dated before the snapshot is kept, and `recovery_gate` reports
it as `stale_deck_evidence`. `confirm` accepts a confirmation at 0 crew, but `recovery_gate`
ignores it there and requires current simulated evidence.

## Caller contract (main / school)

1. **Observe every snapshot in order**, including every occupied-deck tick inside a fast-forward.
   Observing only the final clear snapshot cannot reveal an intervening occupation, and a stale
   confirmation would survive.
2. **Keep one `CrewEvidence` per session or run** in application state, not inside the Runner's
   pure decision callback. Start a new `CrewEvidence()` on reset, scenario switch or replay.
3. **Retain the operator inputs** (each `HumanConfirmation` and the tick it was given at), and
   any simulated `DeckEvidence`, if the application claims to replay crew authorisation.
   Replaying the same snapshots and inputs gives the same states and gates.
4. The state is plain frozen dataclasses: `copy.deepcopy` and `dataclasses.asdict` work, and the
   dict is JSON-serialisable.

## Example

```python
from shipos.crew import (
    CrewEvidence,
    DeckEvidence,
    HumanConfirmation,
    LifecycleError,
    confirm,
    gate,
    observe,
)
from shipos.data import load_ship

ship = load_ship()
state = CrewEvidence()  # new session


def on_tick(state, snapshot, operator_confirmation=None, deck_reading=None):
    """Called by the application for every snapshot, in order."""
    state = observe(state, snapshot, deck_reading)
    if operator_confirmation is not None:
        try:
            state = confirm(state, operator_confirmation)
        except LifecycleError as exc:
            print(f"confirmation rejected: {exc}")  # state unchanged
    return state, gate(ship, state, snapshot)


# For each simulated snapshot `snap`:
#   state, g = on_tick(state, snap, operator_confirmation=HumanConfirmation(t_s=snap.t_s))
#   0 crew: on_tick(state, snap, deck_reading=DeckEvidence(t_s=snap.t_s, deck_clear=True))
#   start recovery only if g.permitted; show g.blockers / g.requests otherwise
```

The tests in `tests/crew/test_lifecycle.py` cover the helper on its own. They do not show that
the API or simulator wire it up correctly.
