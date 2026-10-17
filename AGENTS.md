# ShipOS: Shared AI Agent Instructions

These instructions apply to **all coding agents**, including Codex, Claude Code, and Factory Droid. Before starting, read this file, `CLAUDE.md`, the relevant sections of `docs/PLAN.md`, and `CONTRACT.md`. The current code and `shipos/contracts.py` are authoritative if a summary document is stale. If instructions conflict, stop and ask the two developers rather than guessing.

## Ownership is non-negotiable

**Person A: Sanjiv (`a/<topic>` branches)** owns `shipos/physics/`, `shipos/engine/`, `shipos/statemachine.py`, and their dedicated tests. **Person B: Asa (`b/<topic>` branches)** owns Track B in `docs/PLAN.md` Section 4: `data/` (except shared assumptions), `shipos/sim/`, `shipos/api/`, `shipos/crew/`, `schema/`, `docs/` (except shared `docs/PLAN.md`), and dedicated tests. See `tools/check_ownership.py` for the **actual enforceable path rules**. Files such as `shipos/contracts.py`, `data/assumptions/`, `tests/fixtures/`, root files, and CI may be shared, but **shared does not mean safe to change unilaterally**.

- Person B agents: **only implement B1-B6 tasks explicitly assigned**. Do not fix Section A code, even if it appears broken; report the issue to Sanjiv with a reproduction.
- Person A agents: do not edit Person B-owned code. Coordinate interface changes first.
- Never silently change `shipos/contracts.py`, `docs/PLAN.md`, the established engine signatures, or a shared fixture. A contract change requires its own PR and review by both developers.
- Separate agents need separate **worktrees and branches**. `CODEX_HOME` separates logins, not files. Never work concurrently in the same directory.

## Required implementation standards

1. **Keep it simple.** Prefer the smallest readable solution that satisfies the accepted task and its tests. Avoid premature abstractions, unnecessary dependencies, rewrites, extra frameworks, and speculative features.
2. **Preserve the physical model.** No hard-coded judge outcomes. All eight ship resource classes matter. Physical assumptions go through the documented registry. `CLAUDE.md` defines the engine/physics numeric-literal rule, deterministic decisions, and generated rationales.
3. **Respect the frozen contract.** Use `shipos/contracts.py`, the loaders in `shipos/data.py`, and the fixture examples in `tests/fixtures/`. Do not invent competing API structures. `CONTRACT.md` is a quick reference, not a replacement for source definitions.
4. **Tests first when practical.** Add meaningful tests for new behavior; make sure tests detect one-number input changes, instead of asserting canned outputs. Do not claim a test passed unless it ran.
5. **Simplification pass before PR.** Review changed code for duplication, unnecessary complexity, confusing names, and dead code; simplify without changing behavior. Claude Code's optional `code-simplifier` plugin is not automatically available to Codex or Droid, so perform this review regardless of model.
6. **Run repo checks.** On systems with Make: `make check`. On Windows without Make: `uv run ruff check .`, `uv run ruff format --check .`, `uv run python -m tools.check_no_literals`, and `uv run pytest`. If a command cannot run, state exactly why in the PR.
7. **Keep the demo offline, replayable, explainable and civil-use only.** Do not put live LLM decisions into safety/resource arbitration. Use only hackathon-authorized AI models and disclose AI contributions as required.

## Git and collaboration workflow

1. `git fetch origin` before starting. Work in a dedicated `a/<topic>` or `b/<topic>` branch based on current `origin/main`.
2. One well-scoped issue/task per branch and PR. Stay in your assigned paths; aim for a reviewable diff.
3. When needing another track's change, report the precise missing input/function and use approved existing fixtures if possible. **Do not modify the other track as a workaround.**
4. Run tests and the simplification pass. Commit, push, and open a PR against `main` with a brief summary, tests run, limitations, and dependency/contract changes.
5. Do not merge your own PR without explicit human approval. `File ownership` and CI passing are necessary but not proof that the ship logic is correct.
6. For a handoff or model switch, report: **branch/worktree, task and files changed, commits/PR URL, tests and failures, blockers, and exact next action**. Do not assume the next model has your conversation context.

## Status and coordination

- Read `STATUS.md` for human-maintained project checkpoints; **open GitHub PRs and the latest `main` commit are the live source of truth** for what is merged.
- Only the designated coordinator updates `STATUS.md` as a focused PR or during agreed integration checkpoints. Multiple coding agents should not simultaneously edit it.
- Noncoding agents (Agy, Gemini/Argon, Muse) may review, summarize, or propose tasks, but must not make code changes unless the developers explicitly authorize a different role.
