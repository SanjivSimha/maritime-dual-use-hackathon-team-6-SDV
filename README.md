# ShipOS: resource governor for a software-defined vessel

Maritime Dual-Use Hackathon 2026, team 6 (Fincantieri challenge). A re-configurable 60–70 m
vessel carries swappable mission equipment ("guests" G1–G5 from Annex B) that compete for eight
finite ship resources: power, heat rejection, deck slots, mast positions, mass/stability, data
links, compute and response time. ShipOS sits between the platform and those guests. It decides
whether a new card can be admitted, where it goes, and which numbered limit binds, and every
decision is deterministic and replayable. The prototype is civil-use, offline and uses no ML in
the decision path.

## Run it (Windows PowerShell, from any directory)

```powershell
# one-time, online: needs Python 3.11+ and uv (e.g. py -m pip install --user uv)
.\tools\demo.ps1 -Prepare

# every launch, offline: http://127.0.0.1:8000/  (Ctrl+C stops it)
.\tools\demo.ps1
.\tools\demo.ps1 -Port 8765 -OpenBrowser
```

The launcher uses an existing uv (`uv` on PATH, `py -m uv`, or `.tools\uv.exe`) with
`uv run --locked --offline`. It binds to 127.0.0.1 and runs in the foreground. It never downloads,
upgrades or edits files, and it reports a missing uv, a cold dependency cache or a busy port
rather than working around them. If script execution is disabled, use
`powershell -NoProfile -ExecutionPolicy Bypass -File tools\demo.ps1`; that flag applies to that
one process only. Other platforms: `uv sync --locked`, then
`uv run --locked --offline python -m uvicorn shipos.api.server:app --host 127.0.0.1 --ws websockets-sansio`.
Checks: `make check`, or the commands in [CLAUDE.md](CLAUDE.md).

## Architecture

```
data/ ship (Annex A) · guests G1–G5 · priorities · envelopes · scenarios · assumptions · env_cache
   │  shipos/data.py: YAML → frozen pydantic contracts ("$id" → assumption registry)
   ▼
shipos/sim  Runner: seeded 1 Hz snapshots + timeline events ──► engine callback
   │                                                              │ D1 admission / D2 placement
   │                                                              │ D3 / D4: NotImplementedError
   ▼                                                              ▼
hash-chained JSONL log ◄─────── Decisions (recorded, never applied to the ship)
   │
shipos/api  FastAPI + WebSocket, in-memory sessions ──► local HTML/JS/SVG dashboard
side libraries: shipos/physics (8 budgets) · statemachine (A.6 transitions) · crew (D5) · sim/env
```

## Decisions

| | Component | Declared objective | Status at this checkpoint |
|---|---|---|---|
| D1 admission | `engine/admission.py` | Feasible in every envelope corner; otherwise the cheapest yes-if set by Σ priority weight × capability lost | **Demonstrated** in the UI: catalogue edits and complete judge-card import |
| D2 placement | `engine/placement.py` | Maximise the minimum normalised margin, then future flexibility, then preferred slots; report rejected alternatives | **Demonstrated** with D1 |
| D3 right of way | `engine/arbitration.py` | Utility under the priority file, with deadline filter | **Available** from main: runs on a battery report and on demand (needs G4 aboard, so Scenario B only) |
| D4 degradation | `engine/shedding.py` | Throttle → standby → shed by capability lost | **Available** from main: runs on a generator trip and on demand |
| D5 crew policy | `shipos/crew/policy.py` | Deck clear and confirmed before recovery; winch lock | **Standalone** library plus [evidence-lifecycle helper](docs/CREW_LIFECYCLE.md); not called by the API or Runner; [memo](docs/scenario_C_memo.md), [policy](docs/CREW_POLICY.md) |
| D6 Physical Account | `tools/physical_account.py` | Every number traced to Annex or a reasoned assumption | **Available**: [account](docs/PHYSICAL_ACCOUNT.md), [registry](docs/PHYSICAL_ACCOUNT_REGISTRY.md) |

The brief makes D1 and D3 mandatory. **D3 is still missing.**

## What the demo actually does

- **Scenario A:** real D1/D2 on a proposed card. Editing a number reruns admission. Some edits
  change the verdict or the binding constraint, and many don't. Example: G4 at its declared
  0.9 t gives `yes_if`; set to 20 t it gives `no`, binding on hangar mass. Survey, SAR and
  patrol priorities and crew changes are injected as events and rerun admission.
- **Judge cards:** a complete card JSON with an unfamiliar ID and any edited field
  (`import_card`) goes through contract and reference validation, then real admission. Invalid
  cards are rejected without changing the session, and replay uses the embedded card.
- **Scenario B:** the simulator steps at 1 Hz. The battery report (minute 8) and generator trip
  (minute 12) update the snapshot, and you can inject them yourself. The battery report runs
  real D3 (G4 recovery goes before G3's survey leg) and the trip runs real D4, which answers
  `no_action` because the 49.5 kW draw fits. No overload is invented, and no action is applied.
- **Execution replay:** the session log is re-executed and must match byte for byte. You can
  export it as a JSON bundle or canonical JSONL.

Interaction details and the API are in [shipos/api/README.md](shipos/api/README.md). An earlier
combined checkpoint (PR #25) is recorded in [docs/INTEGRATION_VERIFICATION.md](docs/INTEGRATION_VERIFICATION.md).

## Limitations

- D3 and D4 results are recommendations: nothing applies them to the simulated ship, and their
  integration has only been checked on the Scenario B timeline.
- Decisions are recorded but never applied to the simulated ship.
- The crew policy and its lifecycle helper are libraries the API and Runner don't call yet.
  Deck "camera" evidence is simulated.
- The cached environment ([ENVIRONMENT.md](docs/ENVIRONMENT.md)) is an explicit Runner opt-in
  (`EnvironmentInput`, captured into replay). API sessions and the UI don't use it.
- Physics is a simplified steady-state model: power is the guest reserve, heat is 1:1 for
  water-cooled guests, and stability is a mass × mounting proxy rather than hydrostatics. See
  the Physical Account.
- Sessions live in memory: one server worker, and a restart loses them.
- Replay needs this checkout's code, assumption registry and a compatible Python. It is not a
  portable artifact.
- No measured latency figures, no certification, no ship trials.

## Another week

Implement D3 and D4 against the Scenario B timeline and the priority files, then apply chosen
actions in the simulator. Wire `recovery_gate` with its evidence lifecycle, and the captured
environment, into API sessions and the UI. Add the team's own G6 card once the G1–G5 count tests
are generalised. Add golden end-to-end tests for Scenarios A and B and a "jury changes one
number" sweep.

Plan and arithmetic: [docs/PLAN.md](docs/PLAN.md). Adding cards: [docs/ADDING_A_CARD.md](docs/ADDING_A_CARD.md).
Contributor rules: [CLAUDE.md](CLAUDE.md).
