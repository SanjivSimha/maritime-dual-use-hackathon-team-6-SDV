# B2 simulator integration

`Runner(initial, *, ship, manifests, priorities, timeline=(), seed=0,
profiles=None, decide=None)` accepts existing frozen contract objects. Import it
from `shipos.sim.runner`. `load_scenario(Path('data/scenarios/B.yaml'))` loads
the example through the existing data loaders. No shared contracts change.

* `step() -> WorldSnapshot`: first call emits `initial.t_s`; subsequent calls
  advance one simulated second. `run(ticks) -> WorldSnapshot` steps without sleep.
  Thus `run(721)` from zero includes the minute-12 event at second 720.
* `snapshot` and `context` return deep copies. Every returned snapshot owns its
  nested containers. Inputs, callback arguments, and previous snapshots remain
  separate from runner state even though contract freezing is shallow.
* `jsonl -> bytes`, `final_hash -> str`: complete canonical replay log and final
  chain digest, including the initial replay-input record.
* `clock.paced(iterable_of_snapshots)` optionally presents snapshots at 1 Hz.
  For example, `paced(r.step() for _ in range(721))`. Pacing never enters records.

## Tick and mode semantics

Times must be nonnegative whole seconds. Timeline entries sort by time, stably;
equal times retain input/file order. Each tick applies all its events in order,
then scheduled modes, then elapsed manifest durations, then measurements, then
the callback. A schedule at an automatic mode boundary wins. Same-time schedules
retain their list order, including repeated entries for one guest; the last wins.
Schedules consumed in that tick are removed from the new snapshot only.

Durations use `Mode.duration_min * 60` and `next_mode`. A continuous mode, or one
without `next_mode`, stays unchanged. Initial mode entry is the initial snapshot
time unless its matching activity provides `activity_started_s`; elapsed chains
are caught up from that start. Initial schedules may not precede the snapshot.
Entering a mode resets its duration clock and clears the previous activity fields.
Mission days count down by elapsed simulated seconds, floored at zero.

These are declarative profile transitions, not authorizations or a ship-state
planner. The runner does not infer recovery, depletion, ditching, crew approvals,
interlocks, equipment installation, bus transfers, or safety actions. A caller
must supply a physically valid initial state/schedule and obtain required engine
and crew approvals. In particular `holding` never implies aboard/charging.
Longer operational Scenario B schedules remain unavailable pending Track A and
crew integration. Do not use the example beyond its documented 12-minute horizon
as a demonstration of physical feasibility.

## Timeline YAML and events

`load_timeline(path)` accepts a list of exactly `{t, event, payload}` rows and
returns existing `Event(t_s, kind, payload)` objects. Assumption references resolve
using the existing YAML loader. For direct Python callers, supply `Event` objects.
Payload keys are strict; missing/extra fields, nonfinite reports, out-of-range
values, unknown kinds, and fractional times fail with `ValueError`.

| Event | Payload | Effect before callback |
|---|---|---|
| `genset_trip` | `{gensets_online: 1}` | Must reduce current count to a count supported by ship reserve tables. Updates platform; does not transfer lines or shed guests. |
| `cooling_loss` | `{}` | Sets `platform.cooling_degraded = True`; repeated loss is idempotent. |
| `battery_report` | `{guest_id: G4, battery_pct: 18}` | Sets 0–100 percentage on an installed battery-equipped guest; no inferred recovery. |
| `card_presented` | `{card: <complete GuestManifest mapping>}` | Replaces `context.cards[card.id]`, a proposed card. Does not install it or change the incumbent manifest. |
| `priority_swap` | `{priorities: <complete PriorityFile mapping>}` | Replaces callback priority input. Embed actual loaded data, not a filesystem-dependent name. |
| `crew_change` | `{crew: 6}` | Sets crew to a level declared by the ship (currently 14/6/0). No crew policy runs. |

```yaml
- t: 480
  event: battery_report
  payload: {guest_id: G4, battery_pct: 18}
- t: 720
  event: genset_trip
  payload: {gensets_online: 1}
```

Structural payload validation happens at construction. State-dependent validation
happens transactionally during stepping: if any event, measurement serialization,
or callback fails, snapshot, context, RNG, log, event cursor and clock stay at the
previous completed tick. Earlier ticks remain committed. Callback external side
effects cannot be rolled back; callbacks must be pure.

`Profile(noise_fraction=..., pulses=[Pulse(start_s=..., end_s=...,
multiplier=...)])` configures each installed guest's synthetic measurements.
Measurement is declared mode kW × active pulse multipliers × uniform noise factor.
Pulse intervals are `[start_s, end_s)`; overlapping multipliers multiply. Guests
are sampled in sorted id order using a runner-local seeded RNG. Default profiles
are noiseless. A `1.15` pulse explicitly scripts +15% measurement overdraw; it is
never implicitly injected into Scenario B. The example's G5 ±2% noise and initial
battery value are illustrative input parameters, not new physical assumptions.

## Local engine seam

```python
def decide(snapshot: WorldSnapshot, events: tuple[Event, ...], context: Context):
    # context: ship, manifest catalog, current priorities, proposed cards
    return iterable_of_existing_Decision_objects
```

The callback runs once after the whole tick; events and context preserve all
input changes. It can inspect individual card events when multiple cards with the
same id arrive in a tick (context retains the last). Capture an existing loaded
envelope in an admission adapter and call the frozen entry point:

```python
from shipos.engine import admission
from shipos.contracts import GuestManifest


def decide(snapshot, events, context):
    return [
        admission.decide(
            context.ship,
            context.manifests,
            snapshot,
            GuestManifest.model_validate(event.payload["card"]),
            context.priorities,
            envelope,
        )
        for event in events
        if event.kind == "card_presented"
    ]
```

Each event's card is used so repeated card ids are evaluated separately.
Integration chooses which existing engine
calls to make on a trip/priority change and how to apply approved actions later.
Runner only records decisions; it never converts a result to new policy.
No callback logs `decision_status: no_callback`. `NotImplementedError` logs
`engine_not_implemented` and an empty decision list for that callback batch.
Other exceptions fail the tick. An empty successful result is `available`.
An adapter must not catch missing engine work and return fabricated decisions.
At audited main `ffe6f3f`, A6 arbitration and A7 shedding are unimplemented.

## Canonical logging, verification, replay

Each UTF-8 line is sorted-key JSON with compact separators, ASCII escaping,
finite numbers, and LF. The wrapper contains `index`, `previous_hash`, `record`,
`hash`. Starting with 32 zero bytes:

`hash = SHA256(bytes.fromhex(previous_hash) + canonical(record)).hexdigest()`

The first record embeds the initial snapshot, resolved ship/manifests/priorities,
entire sorted timeline, seed, and profiles. Tick records contain snapshot, events,
mode transitions, decisions, and availability status. `Decision.latency_ms` is
excluded. Physical link latency limits in ship specifications remain replay inputs.
No wall clock, execution timestamp, or measured performance enters canonical data.
If an integration measures latency, retain it separately outside `runner.jsonl`.

`verify_jsonl(bytes, expected_hash=...)` checks exact encoding, indices, continuity,
and digests. Retain the expected final hash separately to detect suffix truncation
or an entirely rewritten chain. This is tamper detection, not authentication.
`replay_jsonl(bytes, decide=...) -> Runner` additionally reruns embedded inputs and
compares every byte. Supply the same pure callback and implementation; callback
code is not serialized. Replay does not read current YAML data. Determinism is
for the same Python/runtime implementation, inputs and callback, not an assertion
of cross-version floating-point or RNG compatibility.

Run these exact PowerShell commands from the repository root (with `uv` on PATH).
On school’s Windows machine, first add the existing installation to this session:

```powershell
$env:PATH = 'C:\Users\asagb\AppData\Roaming\Python\Python313\Scripts;' + $env:PATH
```

They add no CLI entry point. The directory is excluded by existing `.gitignore`:

```powershell
New-Item -ItemType Directory -Force .pytest_cache/sim-demo | Out-Null
uv run python -c "from pathlib import Path; from shipos.sim.runner import load_scenario; r=load_scenario(Path('data/scenarios/B.yaml')); r.run(721); Path('.pytest_cache/sim-demo/B.jsonl').write_bytes(r.jsonl); Path('.pytest_cache/sim-demo/B.sha256').write_text(r.final_hash); print(r.final_hash)"
uv run python -c "from pathlib import Path; from shipos.sim.log import verify_jsonl; from shipos.sim.runner import replay_jsonl; p=Path('.pytest_cache/sim-demo'); b=(p/'B.jsonl').read_bytes(); print(verify_jsonl(b,expected_hash=(p/'B.sha256').read_text())); r=replay_jsonl(b); print('byte-identical replay:',r.jsonl==b)"
```

Observed on Python 3.13.5: two independent 721-tick runs produced identical 722-line
logs, and replay reproduced every byte. Final hash:
`ca3abd5b32afaf260fd2575b718945c7b6584d5d8263d373d7712acb7ef62a28`.
The full repository suite passed 168 tests; lint, formatting, the numeric-literal
guard and the B2 ownership check also passed.

## Scenario B physical findings for Sanjiv

`B.yaml` reproduces the real minute-0 survey/flying loadout at 3 kn, 24°C, 14 crew,
occupied deck. At minute 8, G4 reports 18%; at minute 12, one generator trips.
No arbitration or shedding outcome is supplied. G3 remains `surveying` (1 kW),
G4 `flying` (2 kW), G5 `on` (45 kW nominal), G1 `on` (1.5 kW): 49.5 kW nominal.
There is no 106.5 kW total-power overload at this trip. The lost stbd line still
needs real engine evaluation; the absence of a total overload is not a claim
that the degraded loadout is feasible.

The uninterrupted G3 mode chain has a charge lower bound of minute **55**:
40 survey + 15 recovering. Recovery also requires 4–8 kn and steady course;
survey requires 2.5–3.5 kn. With the existing A5 upper-bound planner invoked
after the survey at minute 40, speed preparation takes 5 minutes, or 6 minutes
including `hold_first=['G3']`, putting charge at minute **60 or 61**. Which safety
prerequisites apply is Sanjiv's decision. These are conditional timings, not
an automatically executed recovery plan. A hold/pause changes this sequence;
the data does not define a `holding -> charging` transition.

For positive charge overlap, G4's 20-minute charge must still be active when
G3 reaches charge: its start must be later than minute 35 for the bare 55-minute
bound, or later than 40/41 for those planner-qualified 60/61-minute starts.
G4 `recovery` itself has no mode duration or next mode; its 3-minute approach is
activity metadata. Therefore **the present inputs establish no unique earliest
physically valid overlap**. A coordinated recovery/charge schedule is missing.
No overlap is reachable in the planned minute-0–12 demo from these inputs.

Reproduce the planner timings without editing Track A:

```powershell
uv run python -c "from pathlib import Path; from shipos.sim.runner import load_scenario; from shipos.statemachine import plan_transition; r=load_scenario(Path('data/scenarios/B.yaml')); c=r.context; target=c.manifests['G3'].mode('recovering').conditions; print([(hold,plan_transition(c.ship,r.snapshot.ship,target,14,env=r.snapshot.env,hold_first=hold,guests=c.manifests).ready_s) for hold in [[],['G3']]])"
```

Additional existing A5 evidence: from the example state at the minute-8 report,
G4 recovery readiness is 360 s with 14 crew and 900 s with 6 crew (hold first),
then a 180 s approach: touchdown at minute 17 or 26. The battery deadline is
minute 20. That leaves 6 crew infeasible without earlier preparation. The acoustic
hold path is 60 s versus a 0.2 s safety ceiling; the declared autonomous hold
fallback matters. B2 neither resolves that safety policy nor changes it.
