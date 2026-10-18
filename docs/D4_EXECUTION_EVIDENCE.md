# Controlled D4 execution evidence

This is a **controlled synthetic input**, not canonical Scenario B and not real weather.
Baseline B's post-trip `no_action` cannot establish an action deadline. These tests use
unchanged main `fcd8c586069ddbfdc9a4b6b2af8f3de61de619e2`, real `shedding.plan`, and the
real `Runner(execution=True)`. No Track A/#43 dependency is imported, no Decision is
canned, and no runtime state is manually changed to imitate application.

## Declared inputs and pre-casualty validity

Input: `tests/sim/inputs/d4_controlled_charging.yaml`. At t=0, G1 is on, G3 and G4 are
already aboard charging, and G5 is on. Both charging clocks start at t=0. This does
not assert a recovery-to-charge transition or that the canonical B vehicle surfaced.
All existing cards and ship limits are loaded unchanged; seed=6, survey priorities,
14 crew, two gensets, 12 kn, steady/quiet, clear deck, normal emission. The synthetic
fixed environment is 24 C, sea state 2, wind 10 kn, unlayered, throughout the horizon.
There is no live weather, battery recovery request, or invented operator confirmation.

All loads are deliberately wired to **port before the casualty**. No bus transfer is
claimed. G1 occupies mast3, G3 bay1, G4 hangar, and G5 bay2 plus hangar_roof. The test
checks exact active-mode pair compatibility and operational state, then uses the
shared kernel for every physical resource/placement constraint, before and after:

| Resource | Pre-casualty demand / capacity | After G5 throttle |
|---|---|---|
| Total power | 106.5 / 200 kW | 69.5 / 100 kW after trip |
| Port line | 106.5 / 150 kW | 69.5 / 150 kW |
| Cooling loop | 105 / 133.333 kW | 68 / 133.333 kW |
| Raw mass | 9.61 / 10 t | unchanged |
| Stability | 9.913 / 12 t-equivalent | unchanged |
| Cores, memory, accelerators | 24/102, 72/409.6 GB, 2/3 | unchanged |
| Mounting | All slot/group/mass/area/manifold/clearance checks pass | unchanged |

No flight-deck/ramp operation or deck machinery is active. G3 charging has no survey
speed/quiet-listening activity, and is explicitly non-interruptible. **Soft findings
are not hidden:** the kernel's worst-link-envelope checks report SATCOM 6.2/0.9 Mbit/s,
radio 5/1 Mbit/s, acoustic 0.003/0.001 Mbit/s; those demands are elastic in the cards.
G3's 60 s acoustic command path exceeds 0.2 s; its declared autonomous hold-depth
fallback makes this check non-blocking. This proof does not establish a 200 ms remote
command or eliminate those capability limitations. All hard pre-casualty checks pass.

## Actual execution, not just computation or planned offsets

At t=10 the constructor timeline trips a genset. The pure callback calls real D4 with
an explicit declared protection policy `{G4}` plus `Context.protected`. This is a D4
policy input, **not a fabricated D3 winner**; no private controller fields are set.
The callback policy is exported alongside the log so replay supplies the same input.

| Observation | Simulation time | Since casualty |
|---|---:|---:|
| Trip, real `shed` decision, G5 still on, corrective task pending | 10 s | 0 s |
| Task actually applied, chosen-action transition on -> low_power, measured G5=8 kW | 11 s | 1 s |
| Entire chosen sequence complete (one action); no corrective task left | 11 s | 1 s |
| Full declared 30-minute casualty horizon observed | 1810 s | 1800 s |

The test links the actual `execution_status.applied` task ID/time to the pending chosen
action and committed mode transition. It asserts actual application equals its due
time, first action <= `ShipSpec.timing.shed_first_s` (5 s), and the last chosen action
<= `shed_full_s` (60 s). It never executes alternatives/armed ladders.

The one-second sustained deficit is 106.5 vs 100 kW; all other hard checks still pass.
It is below the declared one-genset total-plus-peak capacity of 200 kW and lasts less
than the 60 s peak window. The proof does not invent an unreported overdraw profile.
From actual correction through t=1810, **every 1 Hz snapshot passes all hard kernel
checks**, and a fresh D4 horizon evaluation of the corrected snapshot is `no_action`
with all hard checks passing. Full-horizon minimum hard margins are exported.
G4 naturally enters topping at t=1200 using its card; that later charging completion
is not the D4 sequence completion. G3 remains charging throughout the checked horizon.

A separate controlled policy `{G5}` makes real D4 shed G4 to off at t=11 instead.
Protected G5 stays on and G3 is still uninterrupted; power becomes 71.5 kW and heat
70 kW. This non-vacuous counterexample checks the planner respects protection rather
than always throttling G5. Both cases have actual application and execution replay.
D3 winner establishment/retention is not claimed by these explicit-policy cases.

## Wall clock, replay and reproduction

Measured on Windows 11 build 26300, CPython 3.13.5, two independent processes:

| Primary run | Whole decision tick | Casualty processing to first actual commit | To sequence commit |
|---|---:|---:|---:|
| 1 | 3.665 ms | 7.636 ms | 7.636 ms |
| 2 | 3.747 ms | 7.538 ms | 7.538 ms |

These are **unpaced software timings**, including observation/validation overhead
between simulated ticks, not planner-only measurements or real 1 Hz scheduling/hardware
latency guarantees. `perf_counter` telemetry is stored only in `report.json`; it never
enters canonical records. Different timings still yield byte-identical logs in two
independent processes and byte-identical execution replay. Primary final hash:
`30a1e0e6fd2664b3f846d7ee7d54e98a25139527303c22ba3c2fa75c96292397`.
Protection-counterexample hash:
`590f405581034e96e47efcf6a96eb9cebc85c5848eebec313cf7e6a10c75cd56`.

From the repository root, run the regression (including independent processes):

```powershell
py -m uv run --offline pytest tests/sim/test_d4_execution_evidence.py -rs
```

To export a primary trace, start `py -m uv run --offline python` and paste:

```python
import json, runpy
from pathlib import Path
from shipos.sim.runner import replay_jsonl

m = runpy.run_path("tests/sim/test_d4_execution_evidence.py")
out = Path(".pytest_cache/d4-proof")
report = m["run_proof"](out)
policy = json.loads((out / "callback-policy.json").read_text(encoding="utf-8"))
log = (out / "execution.jsonl").read_bytes()
replayed = replay_jsonl(log, decide=m["callback"](policy["protected"]))
assert replayed.jsonl == log
print(report["first_applied_s"], report["sequence_completed_s"], report["wall_unpaced_s"])
print(report["final_hash"], "byte-identical execution replay")
```

The JSONL embeds ship/manifests/initial/timeline/profiles/seed; the adjacent policy JSON
supplies the explicit callback protection input. Same compatible callback, engine,
simulator and assumption registry are required; this is not independently portable.
Canonical chain verification alone does not replace execution replay.

## Limits

This establishes simulated <=5 s first-action and <=60 s full-sequence timing for
these **single-action** corrective cases. It does not establish multi-action sequence
bounds, all failure combinations, human/hardware response, real-time paced scheduling,
D3 protection lifecycle or DEFER application. Exact DEFER producer #43 remains separate
at `ddb32c5d88a9c056a4d74a210e992ca8fb3260f1`; no assembly is needed for this throttle proof.
No production compatibility blocker was encountered. Canonical B and #47 are untouched.
