# Runtime acceptance: frozen local assembly

A local assembly for verification, **not** a merge of any GitHub PR. Branch
`b/runtime-acceptance-v1`, built on `main` `d309257` with `git merge --no-edit`, in this order:

| Component | Branch | SHA |
|---|---|---|
| #36 simulator execution (school) | `b/sim-execution-v1` | `fb45a9cc81b85932b645e33deca006be95b9109b` |
| #37 runtime API (main) | `b/api-runtime-v2` | `1a7bb6aec9a9ccabcd35dc7dd340793a28765c4d` |
| #38 runtime UI (claude) | `b/ui-runtime-v1` | `747785736b7ac9db08ad14dd0773f3b1646f4f53`, re-merged at `af7a2e3` (test-only follow-up) |
| #42 UTF-8 loaders | `b/utf8-loaders-v1` | `cdf1fdab5904f2dec10db34e4bc9d95630877d98` |

All the merges were clean. The assumption registry was already current (school's regeneration
`40df301` is included), so the assembly has no registry delta.

Compatibility commits authored here (also in the separate API compatibility PR):

- `966cf6b`: observation runs keep the 720 s horizon. Execution runs get `EXECUTION_HORIZON_S = 1800`.
  The reason: in Scenario B the deadline is anchored at t=1200, confirmation comes at 841 and recovery
  ends at 1021 (`docs/SIM_EXECUTION.md`). The per-command bound of 1–120 ticks is unchanged.
  `horizon_s` in the view reports the actual horizon. The garbled tick-range message is now ASCII.
- `c4c3313` plus the skip guard: `tests/api/test_execution_acceptance.py`.

## Reproduce

```powershell
git fetch origin
git worktree add "C:\path\shipos-acceptance" origin/b/runtime-acceptance-v1
& "C:\path\shipos-acceptance\tools\demo.ps1" -Prepare   # once, online
& "C:\path\shipos-acceptance\tools\demo.ps1" -Port 8004
```

In the browser:
1. Choose scenario B. In **Run mission**, tick **Apply chosen engine actions in the simulation**.
2. Run 120 s seven times, then step 1 s (next tick is 841).
3. Click **Record deck-clear confirmation**.
4. Run to 1022, then **Verify by replaying**.

## Results (real backend: browser, HTTP and WebSocket)

These describe the simulated ship's state only. No real equipment is commanded.

| Check | Result |
|---|---|
| Enable execution | fresh run, previous evidence archived, horizon 1800 s |
| Survey, 14 crew | at t=480 the battery report gives real D3: G3 pause, G4 proceed. Preparation completes at 540/780/840 |
| Confirmation before the deck is clear (t=482) | rejected ("deck was occupied"); state and evidence unchanged |
| t=841, before confirmation | deck clear, G4 still `flying`; crew policy `pending`, not permitted |
| Operator confirmation at t=841 | G4 → `recovery`, G3 → `holding`; applied audit includes the action at t=841 |
| t=1022 | G4 → `launching` (declared follow-up; no charging invented) |
| Replay / export | byte-identical over 1023 records; export is v3 with `operator_confirmation`; final hash `64593e79eede6dd90831519c3e88c76db4a55ffebf866f6bca8dd632d5f1301f` |
| WebSocket | a second client's state equals the page state |
| SAR, 14 crew | G3 suspend, G4 proceed and a charging *reservation* (no consumption); same recovery path |
| 6 crew / 0 crew, deck occupied | real D3: G3 proceeds, G4 `deny` ("ditch: risk accepted"), recorded as `denied_by_engine`; nothing pending; confirmation rejected; G4 stays `flying` |
| Captured weather + execution | v3 header embeds the cache; replays byte-identically without the cache file |
| Reset | evidence archived; `?run=N` returns it unchanged; execution off, horizon back to 720 s |
| Observation horizon | step past 720 rejected with an ASCII message; a 121-tick command is rejected |

Tests in this assembly:
- `pytest -rs`: 435 passed, 7 skipped.
- `--offline --with playwright pytest tests/api -rs`: 47 passed, 1 skipped. The skip is the old-backend check, now superseded.
- `ruff check`, `ruff format --check`, `check_no_literals`, `guest_schema --check`, `shipos validate` and `physical_account --check` all pass.

`test_runtime_controls.py` also contains *controlled-response* UI tests. They prove the payloads and the rendering only. The runtime proof is the real-backend results in the table above and in `test_execution_acceptance.py`.

## Not verified, or unsupported (school's documented boundaries)

- No charging after landing: G4's card declares recovery → launch.
- Zero-crew success needs a clear deck. The baseline deck is occupied, so 0 crew yields a denial.
- Ambiguous D3 deferrals (`keep` + `defer`), and repeated-mode D4 deferrals, fail closed. Exact DEFER metadata is pending (agy / Track A).
- No protection release is invented.
- Applied audit entries carry only task, kind and time. The UI shows them as such; the action detail is in the pending entries before they apply.
