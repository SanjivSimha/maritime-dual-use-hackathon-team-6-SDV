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
