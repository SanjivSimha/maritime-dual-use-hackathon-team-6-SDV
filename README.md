# ShipOS: explainable vessel resource governance

Maritime Dual-Use Hackathon 2026, team 6, Fincantieri challenge. ShipOS evaluates
machine-readable equipment cards against eight finite budgets: power, heat, deck,
mast, mass/stability, data links, compute and response time. It is a civil-use,
offline prototype with deterministic decisions and no ML in the decision path.
This submission describes merged baseline `fcd8c58` (PR #46), not full challenge completion.

## Prepare once; launch offline

From the repository root in Windows PowerShell, install Python 3.11+ and uv while
online (`py -m pip install --user uv` if needed), then:

```powershell
.\tools\demo.ps1 -Prepare
.\tools\demo.ps1
```

Open http://127.0.0.1:8000/; Ctrl+C stops the foreground server. Preparation runs
`uv sync --locked`; offline launch uses the populated uv cache and local assets.
No CDN or frontend build is required. The launcher finds uv on PATH, `py -m uv`,
or an existing `.tools/uv.exe`. It does not install uv. A cold cache or busy port
is an error; choose `-Port 8765` for another port. Invoke the script by its path
if outside the repository. If local script policy prevents running it, use the
manual server command below after preparation:

```powershell
uv run --locked --offline python -m uvicorn shipos.api.server:app --host 127.0.0.1 --port 8000 --ws websockets-sansio
```

Use one worker. Sessions and archived runs are in memory; export before stopping.
The locked WebSocket backend is included. No real vessel or camera is connected.

## Demo order: Scenario B, then Scenario A

**B: contention, preparation, explicit permission.** Select LIVE, Scenario B,
14 crew and survey priority; enable simulated execution (an explicit fresh run).
Step/fast-forward, at most 120 ticks per command. The first step emits t=0.
The baseline battery report at t=480 invokes real D3. G3 is paused under survey
priority or suspended under SAR; G4 recovery wins. At t=840 preparation is ready,
but G4 remains flying until the operator records deck-clear confirmation. Confirm
at the next tick, t=841: G4 enters recovery. At t=1021 its declared continuation
enters launching, then flying, **not charging**. Repeat under the other priority;
6/0 crew on the baseline occupied deck produce denial/blockers, not a landing.

The t=720 generator trip invokes D4 but does not itself cause an overload. In the
observation baseline the nominal draw is 49.5 kW, below the 100 kW one-genset
reserve. Synthetic charging-overlap tests are separate from baseline B. Execution
is bounded to 1800 simulated seconds; observation to 720. Neither is a latency claim.

**A: unfamiliar equipment and a changed number.** Reset restores observation-mode
LIVE A, baseline inputs and seed. Select G4: evaluating its declared 0.9 t body mass
returns `yes_if`; 20 t returns `no`, binding on hangar mass. Open the complete-card
JSON editor, load G4, change `id` to `JUDGE_DRONE` and its charging-mode `power_kw`
to 41, then validate/evaluate. Real admission receives the complete card. Proposed
cards remain separate from installed guests; admission never installs equipment.
Malformed/nonfinite input or an installed-ID collision is rejected transactionally.
Not every numeric edit changes a verdict. Export evidence and verify replay.

## Architecture and current evidence

YAML/cards and assumptions -> frozen contracts -> 1 Hz seeded Runner -> pure
D1-D4 engine callback -> optional execution controller -> updated snapshot and
hash-chained JSONL -> FastAPI/WebSocket -> local JS/CSS/SVG illustrated dashboard.

| Component | Merged behavior and evidence boundary |
|---|---|
| D1 admission / D2 placement | Real engine, envelope checks, placement and rationale; API tests cover edits, unfamiliar cards and rollback. |
| D3 arbitration / D4 degradation | Real engines. Observation records recommendations; opt-in execution schedules supported chosen feasible actions. Ambiguous/unsupported semantics remain blocked. |
| D5 crew | Policy and evidence lifecycle are called by API/controller. Human confirmation is received input, never inferred from a planner requirement. Zero-crew evidence is simulated. |
| Environment | UI/API opt-in selects an allowlisted cached source with provenance, scenario start, layering and fixed timezone offset. Windows mean eligibility, not permission; no runtime fetch. |
| D6 Physical Account | [One-page account](docs/PHYSICAL_ACCOUNT.pdf) with registry appendix; [editable source](docs/PHYSICAL_ACCOUNT.md). |

The merged [API acceptance tests](tests/api/test_execution_acceptance.py) exercise
real D3, applied recovery, survey/SAR, crew 14/6/0, captured weather and byte-identical
execution replay. [Simulator tests](tests/sim/) include synthetic contract-edge
and protected-resume cases. Local-Chrome tests cover UI controls and ship rendering;
some control tests substitute responses and prove rendering only. Test success is
not evidence of real hardware operation, universal mission success or certification.

## Export, replay and limits

Download the JSON evidence bundle and canonical JSONL; retain the final hash
separately. Verify by replaying re-executes actual records, not just the hash chain.
The bundle captures ship, manifests, priorities, timeline, seed, profiles, execution
configuration, environment and admission envelope, including imported cards and
operator inputs. Replay still needs compatible callback/engine/controller code,
assumption registry and Python/runtime; it is not an independently portable executable.
Execution/environment changes archive the prior run; Reset archives then restores A.
Export before scenario/mode changes, which start fresh runs.

Exact D4 deferral metadata producer [#43](https://github.com/SanjivSimha/maritime-dual-use-hackathon-team-6-SDV/pull/43)
is separate from this baseline; missing exact facts fail closed. Ambiguous D3
deferrals remain blocked. Winner-protection lifetime is provisional and can retain
protection indefinitely. G6 is absent. Stability, weather mapping and transitions
are engineering proxies, and deck evidence is simulated. No ship trials, mentor
validation or measured decision-latency compliance is claimed here. Performance
measurement is a separate workstream. The proposed extra 40 kW of warm-water cooling
is an engineering proposal, not an optimised or measured result.

Required artifacts and open submission items: [checklist](docs/SUBMISSION_CHECKLIST.md).
[Scenario C memo PDF](docs/scenario_C_memo.pdf) and [source](docs/scenario_C_memo.md).
[Card guide](docs/ADDING_A_CARD.md), [execution semantics](docs/SIM_EXECUTION.md),
[environment](docs/ENVIRONMENT.md), [contracts](CONTRACT.md), [contributor checks](CLAUDE.md).
Some older integration documents describe historical checkpoints; use this baseline
and current code for implementation status. AI-assisted development is disclosed in
PR history; the final submission must retain the organisers' required disclosure.
