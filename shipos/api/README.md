Run from the repository root in PowerShell after installing uv and running `uv sync --locked`.

The current lockfile includes plain `uvicorn`, but neither `websockets` nor `wsproto`.
A dedicated dependency PR must add `websockets>=15` to project dependencies and regenerate
`uv.lock` (`uv add "websockets>=15"`). This B4 PR intentionally does not modify either file.
Until then, provision an explicit overlay once with network access:

```powershell
.\.tools\uv.exe run --with "websockets>=15" python -m uvicorn shipos.api.server:app --host 127.0.0.1 --port 8000 --ws websockets
```

Once cached, the tested offline launch command is:

```powershell
.\.tools\uv.exe run --offline --with "websockets>=15" python -m uvicorn shipos.api.server:app --host 127.0.0.1 --port 8000 --ws websockets
```

Open http://127.0.0.1:8000/. `.tools/uv.exe` is the local installer location for this
worktree; installations with uv on PATH can replace `.\.tools\uv.exe` with `uv`.
The UI has no build step, CDN, font download, or network data source.
Sessions are isolated, in memory, and served by one worker; restarting loses sessions.
Reset restores baseline A, LIVE, survey priority, crew, cards and decision log.

LIVE admission uses loaded manifests/priorities/envelope and the existing Scenario A
baseline snapshot from `tests/fixtures/`. Decisions come from `admission.decide`,
including D2 placement; evaluations do not install guests or apply decision actions.
Gauges use `physics.budget.gauges` for current installed equipment, separately from
admission envelope checks. Time is unavailable without a snapshot timing measurement.
Decision latency is measured wall-clock engine time, not a simulated response guarantee.

SAMPLE B is a fixture snapshot. SAMPLE D3/D4 previews are fixture decisions and do not
react to edits or priority. LIVE calls stubs honestly and reports unavailable. No
fixture is substituted for a failed LIVE call. Event buttons currently report unavailable.
School must publish its runner's initialization, Event submission, snapshot retrieval,
and decision-update interface before `adapter.py` can connect B/live events or a clock.
No simulator branch/PR was available at initial implementation; recheck before integration.

HTTP: `POST /api/sessions`, `GET /api/sessions/{id}`, and
`POST /api/sessions/{id}/commands`. WebSocket `/ws/{id}` sends initial state,
accepts the same commands, and broadcasts updates from either transport.
Example: `{"op":"admit","card":"G4","value":20}` edits body mass in tonnes.
Other commands: `reset`, `crew` (0/6/14), `priority`, `mode`, `scenario`,
`arbitration`, `shedding`, `event`. Invalid commands never commit session changes.

Tests: `uv run pytest tests/api`. Optional real Uvicorn/browser verification:
`uv run --with "websockets>=15" --with playwright pytest tests/api`.
The browser check uses installed Windows Chrome; it never downloads a browser.
