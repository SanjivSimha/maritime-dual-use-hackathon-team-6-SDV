Recommended launch: `.\tools\demo.ps1 -Port 8001` (see the root README). It runs
the equivalent of the commands below with any existing uv, offline and locked.
Manual form, from the repository root in PowerShell:

```powershell
.\.tools\uv.exe sync --locked
.\.tools\uv.exe run --locked --offline python -m uvicorn shipos.api.server:app --host 127.0.0.1 --port 8001 --ws websockets-sansio
```

Open http://127.0.0.1:8001/. The browser uses local JS/CSS/SVG, no build step or
external requests. `.tools/uv.exe` is this worktree's verified local installer;
replace it with `uv` where installed on PATH. Sessions are isolated and in memory;
use one server worker. Restarting loses sessions. Canonical exports contain no
session IDs or wall-clock latency. Snapshot response-time gauge remains unavailable.

Complete judge-card import example:

In LIVE Scenario A select G4 and open **Advanced: import a complete card (JSON)** and click **Load selected card**. The editor
contains the complete resolved manifest. Set `id` to `JUDGE_DRONE` and change
the `charging` mode's `power_kw` from 35 to 41, then click **Validate and
evaluate**. This unfamiliar ID and non-mass edit reach real admission through
`card_presented`; the original G4 manifest and installed guests remain unchanged.
The same complete example can be generated for pasting with:

```powershell
.\.tools\uv.exe run --locked python -c "import json; from shipos.data import load_guests; c=load_guests()['G4'].model_dump(mode='json'); c['id']='JUDGE_DRONE'; next(m for m in c['modes'] if m['name']=='charging')['power_kw']=41; print(json.dumps(c,indent=2))"
```

Click **Verify by replaying**, then export the evidence bundle. It embeds
the full unfamiliar card, so replay does not load a repository file for that card.
Reset removes it. The API command is `{"op":"import_card","card_json":"<complete JSON text>"}`.
Malformed JSON, missing fields, invalid references/structures, nonfinite numbers,
unresolved `$...` references, blank IDs, installed-ID collisions and `import_card`
commands that also carry `card`/`value` (use `admit` for the quick mass edit) return readable errors
without committing snapshot/proposed-card/history/log changes. Validation uses
GuestManifest and the existing guest_errors helper; it does not certify new
physics or guarantee that every edit changes the verdict. No new assumptions are
accepted or resolved by this JSON path. The quick body-mass editor still works.

Tested interaction sequence:

1. Start in LIVE Scenario A. Select G4, evaluate body mass 0.9 t: real D1 returns
   yes_if with placement and engine evidence. Change it to 20 t: no, binding on
   the hangar mass limit. Incumbent manifests and installed equipment are unchanged.
2. Select SAR/patrol priority or crew 6. Priority options come from validated YAML
   entries in the fixed repository `data/priorities/` directory. Selection only
   accepts an existing catalog key; unknown names and path traversal are rejected
   without committing session state or evidence. Each change becomes a priority_swap/crew_change
   event and reruns admission for proposed cards. Complete loaded PriorityFile and
   GuestManifest mappings are recorded, not just file names or changed numbers.
3. Reset restores LIVE A, original inputs, seed 0, empty proposed cards/history,
   initial runner log/hash, priority and crew. Selecting a scenario or preview mode
   also starts a fresh runner and replay. Mode-only changes preserve the selected
   scenario: B → SAMPLE → LIVE remains B, with a fresh B baseline each time.
   Explicit Reset still restores A. B's documented YAML seed is 6.
4. Select B (LIVE simulator), enter battery report 13, inject battery report.
   G4's actual snapshot percentage changes at t=0; its mode remains flying.
   Real D3 runs on the report (G4 recovery before G3's survey leg); nothing is applied. First step emits initial.t_s; later steps advance
   one simulated second. Every injected event targets public runner.next_t_s.
5. Click Step (t=1), fast-forward 3 ticks (t=4), then inject genset trip (t=5).
   Platform status becomes one generator. Real D4 answers no_action (49.5 kW fits). HTTP and WebSocket
   receive the same updated snapshots. No action-applier or crew policy runs, and
   the session Runner does not use the cached environment (`EnvironmentInput` is an
   explicit opt-in for direct Runner use only).
6. Click Verify by replaying: integrity is checked and the complete run is
   re-executed with the same callback and captured admission envelope. Every actual
   record must match byte-for-byte. A valid recomputed chain containing a false
   engine verdict fails execution replay.
7. Download Export evidence (JSON bundle) and Log (JSONL). The bundle
   includes JSONL, final hash, captured envelope, and versioned callback identifier.
   The JSONL endpoint also returns X-ShipOS-Final-Hash. Reset clears injected events,
   and repeating the same inputs produces the same bytes and hash.

The B baseline is bounded to t=720 s; each fast-forward is 1-120 ticks, and a
command exceeding the horizon is rejected as a whole. Unmodified B: six 120-tick
commands plus one step emit t=0..720. Minute 8 reports the battery; minute 12 trips
the generator. The minute-12 nominal draw is 49.5 kW, with synthetic G5 noise in
measurements. There is no invented 106.5 kW overload, recovery, charge overlap,
shedding or bus transfer. Lost-line feasibility still requires real engine work.
Injecting a trip early means the baseline trip at 720 would no longer reduce the
count; the runner rejects that tick transactionally. Reset to replay the untouched
baseline. No baseline event is silently suppressed.

D3/D4 are real engines on main. If an engine raises NotImplementedError, Runner
records engine_not_implemented;
progression is LIVE, with no SAMPLE fallback. Runner discards decisions for that
whole callback batch if any engine call raises NotImplementedError. Unavailability
is retained in the UI log even if later ticks have no events. Other failures reject
the API command without committing its snapshot, context, RNG, history or log.
Direct D3/D4 buttons ("Ask the engine") are read-only queries outside canonical simulation
records; D3 needs G4 aboard, so in Scenario A it is refused with that reason;
only an explicitly selected SAMPLE mode previews fixtures. Preview results are
never added to runner.jsonl. The runner records Decisions but never applies actions.

Replay portability limits: the bundle preserves the admission envelope in addition
to the runner header's ship, manifests, priorities, timeline, seed and profiles.
Replay uses embedded inputs, not current scenario/envelope YAML. It still requires
this callback implementation and the same engine/physics code, assumption registry,
and compatible Python/runtime. Callback code and the assumption registry are not
serialized; this is not an independently portable replay artifact. Keep the final
hash separately for tamper detection. UI previews/read-only queries are not replayed.
To re-execute a downloaded bundle in the same checkout/environment:

```powershell
.\.tools\uv.exe run --locked python -c "import json; from pathlib import Path; from shipos.api.adapter import replay_evidence; print(replay_evidence(json.loads(Path('shipos-evidence.json').read_text())))"
```

API: POST /api/sessions; GET /api/sessions/{id}; POST /api/sessions/{id}/commands;
WebSocket /ws/{id} sends initial state and the same command/update protocol.
GET /api/sessions/{id}/evidence and /replay.jsonl export consistent committed state.
Examples:
- {"op":"admit","card":"G4","value":20}
- {"op":"step","ticks":60}
- {"op":"event","event":"battery_report","battery_pct":13}
- {"op":"event","event":"genset_trip"}
- {"op":"priority","priority":"sar_primary"}
- {"op":"crew","crew":6}
- {"op":"scenario","scenario":"B"}
- {"op":"replay"} or {"op":"reset"}

Tests: `.\.tools\uv.exe run --locked pytest tests/api -rs` runs admission, simulator,
replay and actual Uvicorn WebSocket tests with locked dependencies. Browser checks
skip if Playwright/local Windows Chrome is absent. To run them without adding any
project dependency: `.\.tools\uv.exe run --offline --with playwright pytest tests/api -rs`.
The browser test blocks external requests and uses installed Chrome; no browser is
downloaded. Only Playwright test tooling uses an overlay, never the WebSocket backend.
