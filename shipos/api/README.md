Run from the repository root in PowerShell. The integration branch imports PR #17
(API/UI), PR #18 (simulator), and PR #20 (locked WebSocket runtime). No temporary
websockets overlay is needed:

```powershell
.\.tools\uv.exe sync --locked
.\.tools\uv.exe run --locked --offline python -m uvicorn shipos.api.server:app --host 127.0.0.1 --port 8001 --ws websockets-sansio
```

Open http://127.0.0.1:8001/. The browser uses local JS/CSS/SVG, no build step or
external requests. `.tools/uv.exe` is this worktree's verified local installer;
replace it with `uv` where installed on PATH. Sessions are isolated and in memory;
use one server worker. Restarting loses sessions. Canonical exports contain no
session IDs or wall-clock latency. Snapshot response-time gauge remains unavailable.

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
   D3 is explicitly unavailable. First step emits initial.t_s; later steps advance
   one simulated second. Every injected event targets public runner.next_t_s.
5. Click Step (t=1), fast-forward 3 ticks (t=4), then inject genset trip (t=5).
   Platform status becomes one generator. D4 remains unavailable. HTTP and WebSocket
   receive the same updated snapshots. No action-applier or crew policy runs.
6. Click Verify and execution-replay: integrity is checked and the complete run is
   re-executed with the same callback and captured admission envelope. Every actual
   record must match byte-for-byte. A valid recomputed chain containing a false
   engine verdict fails execution replay.
7. Download Export replay evidence (JSON bundle) and Canonical JSONL. The bundle
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

NotImplementedError from D3/D4 is recorded by Runner as engine_not_implemented;
progression is LIVE, with no SAMPLE fallback. Runner discards decisions for that
whole callback batch if any engine call raises NotImplementedError. Unavailability
is retained in the UI log even if later ticks have no events. Other failures reject
the API command without committing its snapshot, context, RNG, history or log.
Direct D3/D4 buttons are read-only queries outside canonical simulation records;
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

Complete judge-card JSON input:

1. Select a catalog/proposed card to populate **Complete card JSON**. Edit any
   supported GuestManifest field, or replace the JSON with an unfamiliar card.
   Give new equipment its own nonempty ID. Click **Validate JSON and run real admission**.
2. The quick **Body mass** / **Run real admission** control remains available. It
   edits the selected stored card; it does not submit unsaved JSON-editor changes.
   Successful JSON imports become selectable proposed cards. Evaluation never
   installs equipment or replaces an installed guest.
3. Reset removes imported proposed cards and their evidence. Priority/crew changes
   rerun real admission for imported proposals just like catalog proposals.

This complete working command can be sent to
`POST /api/sessions/{id}/commands` or the existing WebSocket. In the browser editor,
paste only the inner `manifest` object:

```json
{
  "op": "admit",
  "manifest": {
    "id": "JUDGE7",
    "name": "Civil air-quality sampler",
    "mass_t": 0.2,
    "cooling": "water",
    "preferred": ["hangar"],
    "allowed": ["hangar"],
    "modes": [{"name": "sampling", "power_kw": 3}],
    "initial_mode": "sampling",
    "capability": {
      "name": "Air-quality sampling",
      "value_by_mission": {"survey": "medium"}
    },
    "installation": {"where": "port_only", "people": 2, "hours": 1}
  }
}
```

For example, change `modes[0].power_kw` from 3 to 7 and submit again. The engine
receives that power unchanged; a supported edit need not change the verdict.
Omitted optional contract fields receive existing contract defaults. JSON must be
fully resolved: `$assumption` strings, nonfinite numbers (including numeric-string
coercions), missing required fields, malformed structures, duplicate/unknown mode
references and invalid guest references are rejected. The API uses GuestManifest
and the existing `guest_errors` helper. Installed-ID collisions and mixed
`manifest` plus `card`/`value` submissions are rejected too. Rejected commands do
not alter session state, snapshot, proposed cards, RNG or canonical evidence.

The full validated card is carried in the existing `card_presented` Event and
stored in Runner's separate proposed-card context. Incumbent manifests and
installed runtimes remain unchanged. Export contains the complete card; execution
replay needs no card file. Existing runtime/code/assumption-registry replay limits
still apply. All labels/errors/JSON use text/value rendering, with no HTML injection
or external editor assets. No files are uploaded and no card paths are accepted.

Tests: `.\.tools\uv.exe run --locked pytest tests/api -rs` runs admission, simulator,
replay and actual Uvicorn WebSocket tests with locked dependencies. Browser checks
skip if Playwright/local Windows Chrome is absent. To run them without adding any
project dependency: `.\.tools\uv.exe run --offline --with playwright pytest tests/api -rs`.
The browser test blocks external requests and uses installed Chrome; no browser is
downloaded. Only Playwright test tooling uses an overlay, never the WebSocket backend.
