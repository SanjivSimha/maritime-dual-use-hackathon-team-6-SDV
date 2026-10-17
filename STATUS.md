# ShipOS: Team Coordination and Handoff Board

**Live state:** GitHub `main`, [open pull requests](https://github.com/SanjivSimha/maritine-dual-use-hackathon-team-6-SDV/pulls) and GitHub Actions checks always override this file. This is a lightweight human-maintained checkpoint, **not** an automated status feed. Do not infer a feature is done just because a PR was opened.

## Owners

| Track | Human | Work | Branches |
|---|---|---|---|
| A | Sanjiv | Physics, engine, state machine, integration/CI ownership | `a/*` |
| B | Asa | Data, simulation, API/UI, crew policy, docs | `b/*` |

## Immediate integration priorities

| Priority | Deliverable | Acceptance condition | Current source |
|---|---|---|---|
| 1 | Shared contracts | Contract and fixture definitions are merged | `shipos/contracts.py`, `tests/fixtures/` |
| 2 | Scenario A | A changed guest-card number causes a new valid admission calculation and traceable rationale | Relevant engine + B API/UI tests |
| 3 | Scenario B | Both priority configurations and genset trip run using physically possible modes | Scenario/integration tests |
| 4 | Required writeups | One-page Physical Account, crew memo (≤2 pages), README (≤3 pages) | `docs/` |
| 5 | Offline 90-second demo | Runs without network and can accept live judge edits | Run-through + demo command |

## Agent work allocation (fill in only after tasks actually start)

| Agent label | Assigned B task | Worktree / branch | PR | Blocker |
|---|---|---|---|---|
| `school` | Pending confirmed assignment | Dedicated worktree | — | — |
| `main` | Pending confirmed assignment | Dedicated worktree | — | — |
| `claude` | Pending confirmed assignment | Dedicated worktree | — | — |
| `droid` | Pending confirmed assignment | Dedicated worktree | — | — |

`agy`, `argon`, and `muse` are review/coordination contributors unless explicitly assigned otherwise. The table is deliberately not auto-filled from assumptions; check GitHub before updating.

## Coordinator's checkpoint template

Update at agreed integration checkpoints, not on every agent keystroke. Only one designated coordinator should own edits to this file to avoid conflicting PRs.

```text
Checkpoint time (local):
Merged since last checkpoint (PR links):
Open PRs and failed checks (links):
What now works end-to-end (tested evidence):
Blockers / decisions needed from the other track:
Next integration test and owner:
Feature freeze / next submission deadline:
```

## Agent handoff template

Include this in each agent's final response or PR description, **not** by having every agent modify `STATUS.md`:

```text
Agent label:
Task / plan row:
Worktree and branch:
PR URL / commit SHA:
Files changed:
What actually works:
Tests run and exact results:
Known problems or missing dependencies:
Next recommended action:
```


## Integration captain checkpoint ? 2026-10-17

- Owner: main, sole merger/checkpoint writer for this phase.
- Merged implementation: #35 squash-merged as d309257897c56b5a0501ede6f35aa9b95cb16c19.
  Reviewed head d8bf25d included main 6ace4b6 unchanged. Source branch preserved.
- Baseline proof at #35: locked sync and all repository static/guard checks pass;
  389 tests pass, one optional Playwright skip. Installed-Chrome API/browser suite:
  27 pass with external requests blocked. This proves observation/admission/replay,
  not execution of recommendations.
- Automated review job is green but skipped substantive review because
  ANTHROPIC_API_KEY is absent. Droid reviewed d8bf25d and found no observation-mode merge blocker. Execution
  must preserve winner protection, distinguish request time bases and gate on actual
  confirmation receipt. Those requirements gate follow-up execution integration.
- Runtime work: draft #37 publishes exact execution/confirmation/environment commands.
  School's #36 execution seam is still in progress; Claude's #38 controls are separate.
  No opt-in action execution proof or runtime merge is claimed.
- Consolidation: #16?#28 and #30 heads are ancestors of #35, excluding #29. #29's
  judge-card work was reconciled in #30 (78d6eb4) rather than wholesale merged;
  its cards.py/test_judge_cards.py files are not adopted. #31 has three still-unique
  release-evidence documents (fc00d86); keep it open for current-evidence refresh.
  All older source PRs remain open and branches preserved.
- G6 exact-five tests and Windows default-encoding YAML corruption remain outstanding
  ownership/shared-loader issues, reported to Sanjiv. No Track A or loader fixes here.

- API #37 authored commits were rebased ONLY from recorded #35 d8bf25d onto
  merged main d309257; no consolidated Track B commits are being reapplied.
  Current API proof: 397 repository tests pass, one optional browser skip;
  35 installed-Chrome API/browser tests pass. Eight new runtime tests exercise
  public crew/environment libraries, real protected/confirmation callback inputs
  and Uvicorn WebSocket environment stepping/replay. Execution remains unavailable
  until finished #36 code is imported. No applied-action or landing proof claimed.
- G6/Windows loader reproductions: issue #39, assigned to Sanjiv.
