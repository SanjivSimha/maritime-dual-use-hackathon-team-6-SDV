# Deterministic simulator execution

Execution is an explicit, offline Track B controller outside the pure decision callback.
It applies real chosen D3/D4 plans. It does not install admitted cards, select options,
execute alternatives/armed ladders/contingencies, or simulate unprovided operator input.

## Provisional protection: indefinite retention and blocked resumes

**Sanjiv's protection-lifetime ruling is pending. This task changes no release policy.**
Successful plan compilation adds the named winner when the priority protection flag
is true, before delayed execution begins; winners accumulate. The implemented release
predicate is a successful queued `mode` task with `activity=None`, or reset. For G4,
recovery -> launch retains protection and launching -> flying releases it. This predicate
does not infer terminal-mode semantics for every card.

There is no timeout or release on crew/priority changes, KEEP/DENY, blocked execution
or missing confirmation. **A winner can remain protected indefinitely.** Protected
non-PROCEED actions (including DEFER), holds, external schedule overrides, conflicting
state requests and mode tasks other than `declared_continuation` remain blocked.
**A protected `declared_resume` can remain blocked indefinitely.** Main/Sanjiv must
resolve this provisional policy; exact DEFER metadata does not override protection.

## Public integration interface

```python
Runner(initial, *, ship, manifests, priorities, timeline=(), seed=0,
       profiles=None, decide=None, environment=None, execution: bool = False)
load_scenario(path, *, decide=None, environment=None, execution: bool = False)
runner.execution_status  # defensive JSON-compatible dict
runner.reset()           # restores captured construction inputs; returns WorldSnapshot
```

The callback Context adds `protected: frozenset[str] = frozenset()` and
`recovery_confirm: str = "human"`. Main must pass these to Track A's `protected=`
and `confirm=` arguments respectively. The controller never modifies the callback
or any Track A signature. With execution disabled these defaults retain old calls.

`execution_status` contains `enabled`, `pending`, `applied`, `blocked`, `crew_gate`,
protected IDs, reservations, anchored deadlines, supplied forecasts, shedding caps,
crew evidence and generated-schedule identities. `pending` includes absolute due
seconds, request start/duration, dependencies and chosen action payloads. `applied`
is the completed-task audit for this run. `blocked` includes persistent semantic
blockers and current task waits; task waits are refreshed each tick. It is evidence,
not a way to edit the queue. Returned dictionaries and snapshots are defensive copies.

Inputs use the existing Event contract and ordered public `step(events=())`:

```python
Event(t_s=runner.next_t_s, kind="operator_confirmation", payload={"by": "deck officer"})
Event(
    t_s=runner.next_t_s,
    kind="battery_forecast",
    payload={"guest_id": "G4", "issued_s": 0.0, "land_by_s": 1200.0},
)
```

These event kinds require execution mode. An operator name must be nonempty.
Battery forecasts are explicit caller-supplied planning inputs, not measurements
or inferred weather. They must identify installed battery-equipped equipment, be
finite whole seconds, already issued, and have land_by_s >= issued_s. Without one,
early clearance reports the existing crew policy's unavailable/no_forecast result.

## Tick order, crew and transactions

Constructor-timeline events precede the ordered injected batch, unchanged from B2.
Each event observes its intermediate snapshot: a same-tick crew change 14 -> 6 -> 14
still invalidates a confirmation. Pending requests/actions and declared continuations
then advance before the callback. New chosen zero-offset actions may apply after it.
The canonical tick snapshot is the committed result; decisions describe their actual
pre-action callback input. Every intermediate state, including fast-forwarded seconds,
is passed through CrewEvidence.observe. A failed callback, invalid input or unavailable
captured weather leaves snapshot, controller/queue, context, evidence, RNG, mode clocks,
event cursor, next tick, log and hash unchanged.

Preparation StateRequests have absolute start times and take effect only after their
full duration. Acoustic hold completion applies the matching chosen, declared hold
mode before dependent manoeuvres. Confirmation steps wait for both their modeled
completion time and an explicit valid token; they never create a token. An operator
input on a tick whose clearance has not yet committed is rejected, even if clearance
will finish later in that tick. Occupation and crew changes discard tokens. Pending
requests planned for a different crew report crew_changed_replan_required; a fresh
engine plan is needed. The actual gate is checked again before flight-deck operations
start, even if an earlier confirmation request already completed.

Zero-crew evidence is reconstructed from each actual simulated deck state, stamped
at that tick, with source `simulated_deck_state`. It is never a real sensor claim.
An occupied zero-crew deck cannot be cleared by this controller. Human confirmation
is not auto-generated by fast-forward, early clearance or a planned confirmation step.

The landing deadline is `originating battery_report.t_s + Track A deadline_min(...) * 60`.
Later reports cannot extend an outstanding deadline. Recovery must finish by that
anchored deadline. Completion is recorded, after which a genuinely later report can
anchor a new flight's deadline. A denial records the engine's outcome/risk; there is no
invented ditch/removal state or fabricated completed landing.

## Executable semantics and fail-closed boundaries

Only `chosen.feasible` D3/D4 options with an executable verdict and no rejection are
compiled. Unsupported/ambiguous plans queue nothing and record their exact reason.
This is separate from a transaction failure: a valid engine decision can commit a
non-actuating semantic blocker. All execution times must fit the simulator's 1 Hz grid.

- keep preserves the incumbent clock. proceed starts a uniquely declared activity
  after request dependencies, action offset, actual conditions and crew permission.
- pause/suspend use the installed activity's matching PreemptOption, hold mode,
  window, resume delay and rerun semantics. Resume preparation uses the existing
  Track A planner; it does not invent an immediate return to surveying or charging.
- throttle/standby/shed enter the explicit target mode at the action offset and hold
  its power ceiling for the existing `shed.horizon_min` planning horizon. Future higher
  modes wait under that cap; no automatic post-cap restoration is invented.
- D4 defer requires exact `Action.facts`: `schedule_index`, `from_t_s`, `to_t_s`
  from Track A PR #43 (`8d4ae7d85ebd9295ecb7d52c9da3f8c878e2a4bb`).
  The finite integral index identifies the original list entry, even with repeated
  modes. Its original timestamp and mode must match. Source, destination and every
  shifted suffix timestamp must be finite nonnegative whole seconds; destination
  must be later than source, and the affected suffix must still be future when the
  action executes. Every entry at/after the chosen original index moves by exactly
  `to_t_s - from_t_s`. Earlier entries remain unchanged. `delay_min` is display only.
  Missing metadata has no fallback to the first mode match or rounded display delay.
  The full planned schedule is copied. Normal consumption of earlier prefix entries
  is allowed; changes to the remaining prefix or suffix, insertions or reordering
  require a new plan. `DeferralError` (a ValueError) rejects the whole Runner tick,
  preserving snapshot, queue, RNG, context, crew evidence, cursor, log and hash even
  if an earlier action in that tick was staged. Fractional timestamps are not rounded.
  This strict consumer requires #43 for real D4 deferral. The unpatched producer's
  missing facts fail closed; its code is not imported into this B branch.
- reserve records a declared-mode power reservation. It adds neither consumption nor
  charging. Protection follows the provisional predicate documented prominently
  above, including indefinite-retention and blocked-resume cases. A protected winner
  or non-interruptible active mode cannot be shed or overridden by a schedule.
- StateRequest variables supported: speed_kn, course, noise, deck, emission,
  confirm:deck, a matching hold:<guest>, and deck_machinery=off. Machinery cannot simply
  be labelled off while an active guest mode still declares it running. Unknown
  variables, placement/removal/retask/interlock semantics and ambiguous activity starts
  remain explicit blockers. A crew change does not silently retime preparation.

Future schedules come only from caller inputs, selected actions and declared
Mode.next_mode / Activity.followed_by. A next boundary is published to GuestRuntime
only if its current state needs no additional preparation; it is a conditional intent,
revalidated when due. Unknown preparation/confirmation completion is not guessed.
Explicit schedules override a natural boundary at the same tick. Deferred generated
entries retain their identity. Natural continuations needing preparation use the
planner at completion and can be delayed/blocked. No engine-test schedule is imported.

## Scenario B findings / precise Track A questions

Baseline B inputs are unchanged. At t=480, 18% G4 battery anchors land_by_s=1200.
With 14 crew, real D3 chooses G3 pause under survey and suspend under SAR. Acoustic
hold completes at 540, the deck clears at 780, and speed/confirmation preparation is
ready at 840. Without operator input G4 remains flying. Explicit confirmation at 841
starts recovery; its declared 3-minute activity finishes at 1021. With the baseline
occupied deck, 6 and 0 crew produce real D3 deny/risk outcomes, not a fabricated success.
A separate clear-deck zero-crew test exercises real D3 with automatic simulated evidence.

1. **No conflicting incumbent:** starting from B at t=0, set G3 to standby with no
   activity, deck clear, speed=5, course=free, noise=manoeuvring, crew=0, then report
   G4 battery=18. Real arbitration.decide(..., challenger='G4', challenger_activity='recovery',
   confirm='automatic') returns winner G4 but chosen actions [keep G4, defer G4].
   The defer delay includes approach time and does not identify a recovery start.
   The controller blocks that ambiguous D3 deferral instead of turning KEEP into PROCEED.
   Sanjiv must clarify/correct this output if recovery should start directly.
2. **D3 deferral:** delay_min is remaining incumbent time + transition + approach, while
   the action carries only to_mode. It is not an unambiguous activity start timestamp.
   Such options fail closed. A specified start/meaning is needed from Sanjiv.
3. **D4 identity:** PR #43 now exports exact index/from/to facts, consumed without
   mode-name search or display-delay arithmetic. Both repeated-mode entries and
   their suffixes are supported when metadata and planned identity match. Missing,
   stale or fractional metadata remains unsupported and rejects the tick. Producer
   verification is performed in a separate detached assembly containing #43; no
   Track A changes are published on this consumer branch.
4. **Physical follow-ups:** G4 recovery is followed_by launch; launch enters flying.
   It does not charge after landing in the current card. G3 pause AND suspend declare
   holding then rerun, not a forced recovery/charging path. Thus baseline t=720 does
   not magically produce the brief's charging overlap. Recovery-to-charge behavior or
   different suspension semantics needs a card/Track A clarification, not a sim script.

Controlled synthetic contract-edge tests explicitly label their inputs. The separate
D4 tests create declared future charging inputs locally and invoke the real planner;
these are not presented as baseline B or real weather evidence.

## Exact deferral example (synthetic consumer input)

Given schedule `[(20, charging), (100, charging), (200, standby)]`, a chosen D4 action
with target charging, `delay_min=1.7`, offset 5 and facts
`{"schedule_index": 1, "from_t_s": 100, "to_t_s": 201}` produces
`[(20, charging), (201, charging), (301, standby)]` at t=5.
The second charging entry is selected; the exact shift is 101 seconds, while the
rounded display would imply 102. Controlled consumer inputs are explicitly synthetic.

The separate real-#43 regression uses a synthetic initial charging overlap and the
actual shedding planner: it emits index 1, from=120, to=151, display delay=0.5 min.
The consumer shifts 31 seconds, preserving an earlier standby entry and moving all
later entries; actual snapshots reach charging/topping at t=151, and execution replay
matches byte-for-byte. On the standalone B branch this integration test explicitly
skips because the installed Track A producer predates #43, after verifying rejection.

## Replay and offline reproduction

Observation mode retains v1/v2 bytes and the published 721-tick Scenario B hash
`ca3abd5b32afaf260fd2575b718945c7b6584d5d8263d373d7712acb7ef62a28`.
Execution uses header version 3, execution=true, optional embedded captured weather,
and controller evidence in every tick. Operator/forecast inputs are recorded at their
exact ticks and reinjected automatically on execution replay. No cache path, network
request, computer timezone or wall clock is read during stepping, reset or replay.
Same compatible code, callback and assumption registry remain required; this is not
an independently portable executable. Old execution records that relied on DEFER without
exact facts require their original compatible consumer; this strict consumer rejects
those inputs. Observation v1/v2 behavior and the no-deferral execution example below
remain unchanged. Missing forecast weather coverage still fails
transactionally; a reservation is not proof of future resource feasibility.

Run from the repository root with `py -m uv run --offline python`, then paste:

```python
from pathlib import Path
from shipos.contracts import Event
from shipos.engine import arbitration, shedding
from shipos.sim.runner import load_scenario, replay_jsonl


def decide(snapshot, events, context):
    for event in events:
        if event.kind == "battery_report":
            yield arbitration.decide(
                context.ship,
                context.manifests,
                snapshot,
                context.priorities,
                event.payload["guest_id"],
                "recovery",
                confirm=context.recovery_confirm,
            )
        elif event.kind in ("genset_trip", "cooling_loss"):
            yield shedding.plan(
                context.ship,
                context.manifests,
                snapshot,
                context.priorities,
                protected=context.protected,
            )


runner = load_scenario(Path("data/scenarios/B.yaml"), decide=decide, execution=True)
runner.run(841)  # through t=840: preparation complete; operator confirmation still pending
assert runner.snapshot.guests["G4"].mode == "flying"
runner.step(
    [Event(t_s=runner.next_t_s, kind="operator_confirmation", payload={"by": "demo deck officer"})]
)
assert runner.snapshot.guests["G4"].mode == "recovery"
runner.run(180)  # follows the declared recovery -> launch, never invented charging
assert runner.snapshot.guests["G4"].mode == "launching"
output = Path(".pytest_cache/execution.jsonl")
output.parent.mkdir(exist_ok=True)
output.write_bytes(runner.jsonl)
replayed = replay_jsonl(output.read_bytes(), decide=decide)
assert replayed.jsonl == runner.jsonl
assert replayed.execution_status == runner.execution_status
print(runner.final_hash, "byte-identical execution replay")
```

To add captured weather, pass the existing `environment=EnvironmentInput(...)` to
Runner/loading; v3 embeds its entire record, including provenance, scenario start,
layering and freshness choices. No original cache file is required for replay or reset.
Main owns API wiring and Claude owns static UI; this PR edits neither.