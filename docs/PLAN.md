# Plan v2 — Software Defined Vessel Resource Governor ("ShipOS")

## 0. Context
Fincantieri challenge, Maritime Dual-Use Hackathon SF, 16–18 Oct 2026. We build a **resource governor** that runs on the ship's fixed compute rack, between platform automation and mission systems. It owns 8 finite budgets (power, heat, deck, mast, mass/stability, data links, compute, response time) and makes decisions **D1 admission (≤30 s)**, **D3 right-of-way (≤2 s)**, plus **D2 placement** and **D4 degradation** (we do both: Scenario B needs D4 anyway). Also required: D5 crew memo, D6 Physical Account.

What the jury scores: honest physics (power→heat 1:1, cooling binding in warm water, mass, mast scarcity, degraded state), deterministic and explainable rules, rationale built *from the binding constraint* (no canned strings), a priority file that can be swapped, robustness when the jury changes a number, and a sharp Physical Account. A plain demo with a sharp Physical Account beats a polished demo with an empty one.

Repo state: empty (`.git`, `.claude` only). Two people, each running coding agents, collaborating through a GitHub repo.

---

## 1. Arithmetic checked against the Annexes (this drives the design)
I recomputed the brief's numbers. Several statements in the brief don't hold, and noticing that is a selling point ("we checked the arithmetic").

**Guest masses.** Raw mass first, then stability-equivalent (deck ×1, hangar ×1.2, mast ×3).

| Guest | Raw mass | Stability-eq |
|---|---|---|
| G1 | 0.045 t | 0.135 t (mast) |
| G2 | 3.5 t | 3.5 t |
| G3 | 5.5 t | 5.5 t |
| G4 | 1.04 t | 1.248 t |
| G5 | 3.0 t + 0.025 t antenna | 3.0 t + 0.075 t (antenna on mast) or 0.03 t (antenna on hangar roof) |

**Loadout checks:**

| Loadout | Raw mass vs 10 t | Positions | Verdict |
|---|---|---|---|
| G1, G2, G5 | 6.57 t | OK | OK |
| G2, G3, G4 (brief's alternative "installed" set) | **10.04 t**, already over by 40 kg | — | Infeasible as installed |
| G2, G3, G5 together | — | **Impossible**: G3 takes all of Bay 1, and G2 and G5 both need Bay 2 or half of Bay 1, so only one of them fits | Infeasible in any combination |
| G2, G3, G4, G5 (what the brief's Scenario B narrative implies, since it sheds "node + ocean module") | 13.07 t | Bay conflict as above | Infeasible on both mass and bays. Our admission engine will refuse it, and we show that on stage. |
| G1, G3, G4, G5 (our Scenario B loadout) | 9.61 t (390 kg margin) | G3 Bay 1, G5 Bay 2, G4 hangar, G1 mast, G5 antenna on hangar roof | OK. Stability-eq 9.91 ≤ 12 |

- The brief says "any four exceed the 10 t mass margin". That is false: {G1,G2,G4,G5} = 7.61 t and {G1,G3,G4,G5} = 9.61 t. It holds only for sets that contain G3 together with G2 or G5.

**Power.**
- All five at cruise: 1.5 + 6 + 25 + 35 + 45 = 112.5 kW (126.5 kW with the winch) ≤ 200 kW. ✓
- One genset down: G5 + G4 + G3 charging = 105 kW > 100 kW. ✓ This matches the brief.

**Cooling.** Capacity is `cap(T) = 160 − 2.222·(T−12)` kW, multiplied by 0.6 when the loop is degraded.

| Seawater | Nominal | Degraded |
|---|---|---|
| 24 °C | 133.3 kW | 80 kW |
| 30 °C | 120 kW | 72 kW |

- The mast has no manifold, so G1 is air-cooled and is excluded from the loop's load.

**Scenario A (installed G1, G2, G5).** G1 is on the mast, G2 in Bay 2, G5 in half of Bay 1 with its antenna on the hangar roof (the mast slot is taken).
- **Jury card G4:**
  - Mass 7.61 t ✓.
  - With one genset down, the sustained draw is 87.5 kW, which fits 100 kW. Add the G2 winch (20 kW for 5 min) and it becomes 101.5 kW, which doesn't fit.
  - **Verdict: yes-if.** Interlock the winch against drone charging while a genset is down (cheapest capability loss).
  - If the jury widens the envelope to a degraded loop at 30 °C: 86 kW of water-cooled load against 72 kW. **Cooling binds**, and power doesn't.
- **Jury card G3:**
  - Needs all of Bay 1, and mass would reach 12.07 t.
  - **Verdict: yes-if, displace G5** (low weight for survey; frees half of Bay 1 and 3.025 t → 9.045 t).
  - Alternative: displace G2 (8.57 t), then re-install G5 in Bay 2 (6 h of crane time). This ranks lower under survey priority.

**Scenario B (loadout G1, G3, G4, G5; 24 °C; 3 kn).**
- The deficit after the trip appears only when G4 charging (35 kW) and G3 charging (25 kW) overlap, on top of G5 (45 kW): 35 + 25 + 45 + 1.5 = 106.5 kW > 100 kW.
- **As built, baseline B does not reach that overlap.** The cards declare G4 recovery → launch → flying (no charging after landing), and G3 pause/suspend → holding → rerun (no forced recharge). At the t=720 trip the real D4 sees 49.5 kW against 100 kW and answers no-action. That is the correct answer for these inputs; we do not script an overload into baseline B.
- **D4 corrective action is demonstrated on a separate, labelled controlled input** (`tests/engine/test_shedding.py`): the same ship and cards, with G4 charging 17–37 min and G3 charging from 32 min declared as schedule inputs, run through the real planner.
  - **Survey primary:** D4 throttles G5 to low power (37 kW recovered, 45 → 8 kW), leaving 69.5 kW.
  - **SAR primary:** G5 keeps 45 kW; D4 defers G3's charge by 5 min until G4 finishes (not interruptible once started, but deferrable). The cost is survey delay.
  - Without the G3 recharge the survey path draws 82.5 kW: no action, shed ladder armed with G5 first and the protected winner excluded.
- Making the overlap arise in baseline B would need a card change (e.g. G4 recovery followed by charging) agreed with Track A, not a sim script.
- The two priority files give two different, coherent outcomes, both computed rather than scripted.
- Power binds in B and cooling can bind in A: the same code names a different binding class in each.

**Timing (critical path at minute 8: land within 12 min).**
- Hold command to the vehicle over the acoustic link: ≤60 s.
- Speed 3 kn → 5–12 kn: 2–5 min. Course settle: 1–3 min.
- Deck clear: 3–5 min with 14 crew, 8–12 min with 6.
- Human confirmation: 15–60 s with 14 crew, 1–3 min with 6.
- Landing approach: 3 min (assumed).
- With 14 crew: about 10 min ≤ 12 min ✓.
- With 6 crew: up to 18 min > 12 min ✗, so the governor must pre-clear the deck from a battery forecast. This is our D5 story.
- With 0 crew: possible only if the deck is already clear.

**Safety command vs. acoustic link.** A 200 ms safety command ("hold") cannot cross a 10–60 s acoustic link. So G3 must carry autonomous "link-loss → hold depth" behaviour, and the governor must send the hold at least 60 s before any manoeuvre. This is a real response-time finding; it goes in the Physical Account.

All of the above become **golden tests**. The expected values are derived from Annex numbers inside the tests, so changing a card number moves the expected outcome too.

---

## 2. Architecture

```
          ┌──────────── inputs (YAML, swappable) ─────────────┐
          │ ship.yaml  guests/*.yaml  priorities/*.yaml       │
          │ envelope.yaml  crew_policy.yaml  scenario.yaml    │
          └───────────────────────┬───────────────────────────┘
                                  ▼
  sim/ (clock, timeline, consumption, env)  ──►  WorldSnapshot (immutable, 1 Hz)
                                  │
                                  ▼
        ┌────────────── engine/ (pure, deterministic) ──────────────┐
        │  kernel.evaluate(config, corner) → [ConstraintResult]     │  ← one feasibility kernel
        │     ├─ D1 admission.decide()   enumerate displacements    │
        │     ├─ D2 placement.decide()   enumerate slot assignments │
        │     ├─ D3 arbitration.decide() hard filter → utility      │
        │     └─ D4 shedding.plan()      ordered throttle/shed      │
        │  rationale.render(Decision)  ← built only from BindingConstraint│
        │  crew.policy.gate(action, crew_level)                    │
        └───────────────────────────┬───────────────────────────────┘
                                    ▼
             Decision log (JSONL, append-only, hash-chained) ──► api/ (FastAPI+WS) ──► UI
```

### Key architectural decisions (with reasons)
1. **One feasibility kernel, four search strategies.** `kernel.evaluate(config, corner)` returns, for each class and sub-resource (line port/stbd, loop, Bay 1, mast slot, cores…), a `ConstraintResult(cls, resource, capacity, demand, margin, unit, contributors[], corner)`. D1–D4 differ only in *what they search over* and *what they optimise*. This keeps the physics in one place, so the rationale is consistent across decisions, and a change to one card number propagates everywhere.
2. **Exhaustive enumeration, not a MIP solver.** The problem is tiny: 5 guests × ≤4 modes = 1,024 configurations, × 4 envelope corners, × ~10 placement slots. Python enumerates it in milliseconds. Enumeration is deterministic, needs no external solver, and gives the full ranked list of alternatives that D1 and D2 must report. A guard (`assumptions: solver.max_states = 200 000`, `solver.timeout_s = 5`) falls back to a greedy search ordered by capability-per-kW if the space grows. We name OR-Tools CP-SAT as the scale-up path in "another week".
3. **The engine is pure.** No wall clock, no randomness, no I/O inside `engine/`. Time comes from `WorldSnapshot.t`. A wrapper measures wall-clock latency and logs it against the ceilings (30 s / 2 s / 5 s / 60 s / 200 ms), but latency never affects the decision. Replay means re-running the timeline with the same seed and checking the log hash matches.
4. **Hard vs. elastic resource classes.**
   - **Hard** (a violation means no, or yes-if): power (total and per line), heat, positions, mass, stability, compute accelerators (discrete: floor(4 × 0.8) = 3).
   - **Elastic** (a violation means yes, with a stated capability loss): SATCOM/radio bandwidth, CPU share above minimum.
   - Data links are modelled honestly with their worst-case envelope (SATCOM 2 Mbit/s × 70 % availability; zero when silent).
5. **The admission envelope is a set of corners, and the verdict must hold in all of them.** Default corners: {slow, cruise} × 30 °C × {2 gensets, 1 genset (stbd line lost)}. A degraded cooling loop is *not* in the A.10 default envelope, so it is a `envelope.yaml` flag (default off; the jury can widen it). Every verdict also prints a **contingency line**: a dry-run of D4 for "loop degraded at 30 °C".
6. **Demand is profile-based, so worst-case overlap is checked.** Each guest declares *modes* with sustained draw and duration (G3: survey 1 kW for 40 min, then charge 25 kW for 60 min, not interruptible once started; G4: charge 35 kW for 20 min, then 10 kW; standby 2 kW).
   - Admission assumes each guest is at its max sustained mode at the same time, *unless* a declared exclusion or interlock prevents the overlap.
   - Proposing a new interlock is a legitimate yes-if option. Its cost is a delay, not a capability loss.
   - Peaks (≤60 s) are checked separately against the peak reserve and the cooling inertia token (+30 kW for 60 s, once per 10 min).
7. **Every number lives in a registry.** Annex values go in `data/ship/*.yaml`. Every invented or narrowed number goes in `data/assumptions/*.yaml` as `{id, value, unit, range, why, source}`. Code reads `A("power.reserve.fast.two_gensets")` and never uses a literal (a lint test greps for numeric literals in `engine/` and `physics/`). `tools/physical_account.py` fills the Annex C table automatically, including the "where used" column (file:line from a usage scan).
8. **Rationale is rendered from data.** A `Decision` carries `binding: ConstraintResult`, `alternatives[]`, `costs_to_loser[]`, `state_requests[]`. `rationale.render()` turns those fields into one sentence through a handful of grammar templates per verdict type, e.g. *"Yes-if: with one genset down, the 100 kW reserve has 12.5 kW free; G4 charging needs 35 kW while G2's winch runs — interlock the winch during charging (G2 loses ≤20 min of profiling)."* A test fails if any decision's sentence lacks the binding numbers.
9. **The priority file is pure data.**
   - `priorities/<mission>.yaml` holds per-guest capability weights, an asset-loss penalty (e.g. losing the G4 drone for 14 days), a `protect_winner` flag, and shed-order tie-breaks.
   - Mission-criticality is *derived* from the weights by a threshold, never stored as a bare boolean.
   - A test asserts that swapping the file changes the D3 winner and the D4 sequence in Scenario B.
10. **Ship state is a state machine with timed transitions.** Variables: course, speed band, noise, deck, emission, gensets, cooling (A.6). A conflict is detected by intersecting the guests' condition sets on these variables (no hard-coded pairs). A transition plan is the critical path through those variables, with durations taken from A.6 and scaled by crew level.

### Declared objectives (go in the README)
- **D1 admission:** feasible in every envelope corner. Otherwise, the displacement or throttle or interlock set that minimises `Σ priority_weight × capability_lost`, tie-broken by fewest guests touched, then by guest id. Verdicts:
  - **yes**
  - **yes-if** (ranked list)
  - **not-now-and-when**: physically OK but no installation window; gives the next window from the forecast. At crew 0, proposes a logical reconfiguration instead.
  - **no**: an absolute exclusion, or nothing removable makes it feasible.
- **D2 placement:** maximise the minimum normalised margin across classes and corners. Then maximise *future flexibility* (how many of these standard shapes stay admittable: 10-ft bay guest, 20-ft bay guest, mast guest ≤60 kg, hangar guest). Then prefer `preferred` positions. Report the top 3 rejected alternatives and why each lost.
- **D3 right of way:**
  1. Filter out options that break a hard constraint (deadline, safety-critical, envelope).
  2. Maximise `Σ w(g) × capability_retained(g) − asset_loss_penalty × P(loss)`.
  3. Tie-break on fewest ship-state changes.
  4. Report the cost to the loser in its natural unit (survey minutes, drone risk).
- **D4 shedding:** restore feasibility over a forecast horizon (`A("shed.horizon_min") = 30`) while maximising retained weighted capability.
  - Throttle to low-power before standby, and standby before shedding.
  - Order within each tier by lost capability per kW recovered.
  - Never auto-shed safety-critical guests; protect the D3 winner; shed part-of assemblies as one unit.
  - First action ≤5 s, full sequence ≤60 s.
  - Overdraw rule: warn at +10 % for 10 s, throttle at +15 %, shed if it persists for 60 s (thresholds go in the assumption registry).

---

## 3. Repository layout
```
pyproject.toml  (uv; deps: pydantic>=2, pyyaml, fastapi, uvicorn, httpx, pytest, ruff)
Makefile        (demo, test, lint, account, schema)
CLAUDE.md       (shared agent conventions — see §5)
.github/workflows/ci.yml   .github/pull_request_template.md   .github/CODEOWNERS
shipos/
  contracts.py      ← ALL shared types (Manifest, Mode, Conditions, WorldSnapshot,
                      ConstraintResult, Decision, Event, ShedStep). Frozen after PR #2.
  assumptions.py    ← loader + A() accessor
  physics/          power.py cooling.py mass.py positions.py links.py compute.py timing.py
  engine/           kernel.py admission.py placement.py arbitration.py shedding.py rationale.py
  statemachine.py   ship state + transition planner
  crew/policy.py    D5 gate: human_confirms | automatic | not_permitted, + delays
  sim/              clock.py timeline.py consumption.py env.py runner.py
  api/              server.py  static/index.html static/app.js static/style.css
  cli.py            `shipos demo|run|admit|validate|account|replay`
data/ ship/ guests/G1..G6.yaml priorities/ envelopes/ crew/ scenarios/ assumptions/ env_cache/
schema/guest_manifest.schema.json   (generated)
docs/ README.md PHYSICAL_ACCOUNT.md scenario_C_memo.md(+.pdf) architecture.svg
tests/ physics/ engine/ scenarios/ (golden) test_no_literals.py test_determinism.py
```

---

## 4. Build sequence and team split
Two tracks run in parallel after a short bootstrap. **Person A (Sanjiv)** owns physics and the decision engine, the critical path. **Person B (teammate)** owns data, simulator, UI, crew policy and docs. Each track is a series of small PRs.

### Phase 0: bootstrap (H0–H2, together, done by A while B sets up)
- **PR #1 `chore/bootstrap` (A):** pyproject, Makefile, ruff, pytest, CI workflow, PR template, CODEOWNERS, CLAUDE.md, empty package. Turn on branch protection for `main` (CI required, squash merge only).
- **PR #2 `feat/contracts` (A writes; B must review):** `shipos/contracts.py` with all shared pydantic types and engine function signatures as stubs. Also `tests/fixtures/*.json`: a sample WorldSnapshot, a Decision for each verdict type, a ShedPlan. *From this point B can build the UI and sim against fixtures without waiting for the engine.*
- **At the same time (B):** check that Claude is on the organisers' authorised AI list (rules ban China-based models); register for Copernicus if wanted (activation can take hours); pick the real site (recommend the **Strait of Sicily cable corridor**: SST about 24 °C in mid-October, which fits Scenario B).

### Track A — physics and engine (Person A + their agents)
| PR | Content | Done when |
|---|---|---|
| A1 `feat/physics-budget` | All 8 classes. Power per line and peak window; cooling(T, loop) with inertia token; mass and stability; slots; links (silence → 0, acoustic drop on manoeuvre/deck machinery/layering); compute with discrete accelerators; response-time ledger | Unit tests reproduce every number in §1 |
| A2 `feat/kernel` | `evaluate(config, corner)`, envelope corner generator, binding selection (most negative normalised margin, else the tightest) | Kernel tests per class |
| A3 `feat/d1-admission` | Enumeration of displacement, throttle and interlock options; four verdicts; genset-down statement; contingency dry-run | Golden: G4 → yes-if (interlock); G3 → yes-if (displace G5); G2+G3+G4+G5 → no (mass, Bay 1) |
| A4 `feat/rationale` | Renderer + "no canned strings" test | Each sentence contains its binding numbers |
| A5 `feat/statemachine` | Transitions and critical-path planner, scaled by crew | B min-8 timing matches §1 |
| A6 `feat/d3-arbitration` | Condition-set conflict detection, deadline filter, utility, loser cost | Two priority files give two winners; <2 s |
| A7 `feat/d4-shedding` | Horizon forecast, tiers, protect winner, overdraw handling | Survey: G5 → 8 kW. SAR: G3 charge deferred, G5 kept |
| A8 `feat/d2-placement` | Slot enumeration, flexibility score, rejected alternatives | Scenario A placements + 3 alternatives |

### Track B — data, simulation, UI, crew, docs (Person B + their agents)
| PR | Content | Done when |
|---|---|---|
| B1 `feat/data-cards` | **Annex A ship + G1–G5 cards, survey/sar priorities, default envelope and assumption YAMLs already landed in PR #2.** Remaining: **G6 own card** (recommend towed passive hydrophone array on the ramp, 4 kW, 2.5 t, needs quiet, excludes winch — civil, creates new contention), `patrol` priority file, `shipos validate`, generated JSON Schema, "how to add a card" doc | All cards validate; schema generated in CI |
| B2 `feat/sim` | Seeded 1 Hz runner, timeline YAML `(t, event, payload)`, consumption profiles (nominal ± noise, scripted +15 % overdraw), events: genset_trip, battery_report, card_presented, priority_swap, crew_change | Same seed gives an identical log hash |
| B3 `feat/env-real-data` | Open-Meteo Marine (wave height → sea state, SST) + forecast wind for the chosen site, cached to `env_cache/`, offline fallback, installation-window finder for A′ (sea state ≤3, wind ≤20 kn, ≥30 or 45 min long, crew counts; with 6 crew, windows only in daylight 07–19, an assumption) | Works with wifi off |
| B4 `feat/ui` | FastAPI + WebSocket + vanilla JS/SVG (no build step). Ship schematic (Bay 1/2, 3 mast slots, flight deck, ramp, port/stbd lines), 8 gauges (capacity / committed / free / degraded marker), ship-state strip, decision log with rationale and latency badge. Controls: scenario, priority file, crew level, an editable card number, inject buttons. Initially driven by fixtures | Runs `make demo` against the stub engine |
| B5 `feat/crew-policy` | D5 rule: **"flight deck clear and confirmed before drone recovery"**. At 14 crew: human confirms. At 6: automatic pre-clear triggered by the battery forecast, human confirms. At 0: automatic only if cameras show the deck already clear, otherwise not permitted (the drone diverts). Second rule: winch auto-locked while G3 listens | Live at 14 and 6 |
| B6 `docs/*` | README (≤3 pages), Scenario C memo (≤2 pages, PDF), Physical Account generator, closing answer. Draft answer to verify with runs: **+40 kW of heat rejection at 30 °C**, i.e. a loop sized at 160 kW for 30 °C, because cooling binds before power in warm water | Docs fit the page limits |

### Integration and sync points
- **H6 (first merge point):** A1 + B1 + B2 merged, so the simulator ticks, 3 guests consume, and gauges move in the CLI. This is the brief's own hour-4 health check.
- **H12:** A3 + A4 merged, the UI is switched from fixtures to the real engine, and Scenario A runs end to end.
- **H20:** A5 + A6 + A7 + B5 merged, so Scenario B runs with both priority files.
- **H28:** A8 + B3 merged (A′ windows, placement alternatives). Feature freeze.
- **H28–end:** docs, golden tests, a "jury changes one number" fuzz test (change each card number by ±30 % and check there's no crash and each rationale names its numbers), rehearsal with wifi off, backup recording.

---

## 5. Working in parallel with coding agents and GitHub PRs

### Shared conventions (CLAUDE.md, committed in PR #1, read by both people's agents)
- `shipos/contracts.py` is frozen. Changing it needs its own PR, reviewed by both people.
- No numeric literals in `engine/` or `physics/`. Use `A("id")`, and add the YAML entry with `why`.
- The engine is pure: no `time.time()`, `random`, or I/O. The clock is injected.
- Every Decision must set `binding`. Rationale only goes through `rationale.render`.
- Every new behaviour comes with a test. Golden tests compute expected values from the Annex inputs and never hard-code outputs.
- Branch names: `a/<topic>` or `b/<topic>`. One agent task = one branch = one PR, ideally under 400 lines.

### Running several agents per person
- Each person runs Claude Code in their own clone. To run two agents at once on one machine, use separate worktrees so they never share files:
  `git worktree add ../sdv-sim -b b/sim origin/main` and `git worktree add ../sdv-ui -b b/ui origin/main`. Start one Claude Code session in each.
- Give each agent a self-contained task: the PR row from §4, the CLAUDE.md rules, the contract types, and the "done when" test. The agent writes the failing test first, implements, runs `make test lint`, then opens the PR.
- Prompt for the teammate's first agent:
  > "Read CLAUDE.md and shipos/contracts.py. Implement PR B1: encode Annex A as data/ship/reference_vessel.yaml and G1–G5 from Annex B (attached PDF) as data/guests/*.yaml conforming to GuestManifest; add `shipos validate`; generate schema/guest_manifest.schema.json; every value not in the Annex goes into data/assumptions/ with a why. Tests first."

### PR flow (GitHub, using the `gh` CLI)
1. `git fetch && git switch -c b/sim origin/main`, then the agent works and commits.
2. `git fetch && git rebase origin/main`, then `make test lint`, then push, then `gh pr create --fill --base main`.
3. CI must pass (`.github/workflows/ci.yml` runs ruff, pytest, schema generation diff, the no-literals check, and golden scenarios).
4. Review:
   - **Contracts, kernel and golden-test changes:** the other person approves.
   - **Everything else:** the other person runs `/code-review` locally on the PR (or has an agent review it), then approves. If they're busy and CI is green, self-merge after 30 min is OK, but only in a track you own (enforced by CODEOWNERS).
5. Squash-merge, delete the branch, and post the PR link in team chat.
6. Avoid conflicts:
   - Each person owns separate directories: A owns `physics/`, `engine/` and `statemachine.py`; B owns `data/`, `sim/`, `api/`, `crew/` and `docs/`.
   - Assumptions are split into one YAML file per class, so two PRs rarely touch the same file.
   - `pyproject.toml` dependencies are added only in PR #1, or by a tiny dedicated PR.
   - Rebase at least every 3–4 h and never leave a branch open longer than half a day.
- Hand-offs where B's work depends on A's (or the reverse) go through fixtures in `tests/fixtures/`: whoever changes an output shape updates the fixture in the same PR.

---

## 6. Demo script (what the code must support)
1. **Scenario B, 90 s**, with the priority file the jury picks:
   - Gauges are steady at minute 0.
   - Minute 8: battery report. The UI shows the two condition sets clashing on the ship-state strip. A decision arrives in under 2 s with the critical path: hold sent, deck clear, speed change. It shows the rationale and the cost to the loser.
   - Minute 12: genset trip. Baseline B: D4 runs and answers no-action (49.5 kW fits under 100 kW). Say so plainly: a trip is not an overload.
   - Then the **controlled D4 input** (labelled as such): the declared G3 + G4 charging overlap, 106.5 kW > 100 kW. The real planner shows each step's timestamp and what each guest lost: G5 throttled under survey, G3's charge deferred under SAR.
   - Replay with the other priority file and show the diff.
2. **Scenario A**: the jury picks a card and edits one number in the UI. The verdict shows the binding class, placement plus rejected alternatives, the yes-if list, and "holds with one genset down?". Then flip to A′ (under way) for a not-now-and-when, and to crew 0 for a logical reconfiguration.
3. **Bonus beat:** load the brief's implied B loadout (G2+G3+G4+G5) and show the governor refusing it on mass and Bay 1.

## 7. Verification
- `make test`:
  - Physics reproduces every §1 number.
  - Determinism: the same seed gives the same log hash, checked twice.
  - Latency ceilings: D1 <30 s (target <1 s), D3 <2 s, first shed <5 s simulated and wall-clock.
  - The priority swap changes the D3 winner and the D4 sequence.
  - A one-number card change moves the verdict or rationale.
  - The no-literals lint and the no-canned-strings test pass.
- `make demo SCENARIO=B PRIORITY=survey_primary` vs `PRIORITY=sar_primary`: diff the JSONL logs.
- `uv run shipos admit --scenario A --card G3 --set G3.mass_t=4.0`: the verdict changes coherently (mass stops binding, Bay 1 still does).
- Turn off wifi: the env falls back to the cache, and the full demo still runs.
- `make account` regenerates the Physical Account. Check that every assumption id has a "where used" entry.
