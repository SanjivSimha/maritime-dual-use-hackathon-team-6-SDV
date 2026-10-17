# ShipOS — agent conventions

Resource governor for the Fincantieri "Software Defined Vessel" challenge. The full plan,
architecture, and the arithmetic it is built on are in `docs/PLAN.md` — read §1–§2 before
touching `shipos/engine` or `shipos/physics`.

## Commands
- `make install` — `uv sync --locked`
- `make check` — lint + guardrails + tests (exactly what CI runs). Run before every push.
- `make fmt` — auto-format and fix lint.

## Rules (CI or review enforces each)
1. **No numeric literals in `shipos/engine/` or `shipos/physics/`** except 0 and ±1. Read numbers
   with `A("<id>")` and add the entry to `data/assumptions/<class>.yaml` with `value, unit, range,
   why, source`. Annex A/B values go in `data/ship/` and `data/guests/`. A deliberate literal needs
   `# literal-ok: <reason>` on its line. In YAML, an invented value is written `"$<id>"` and
   resolved by `shipos/data.py`; `tests/test_contracts.py` fails on any unknown reference.
2. **Engine is pure**: no `time.time()`, `datetime.now()`, `random`, file or network I/O inside
   `shipos/engine/`. Time arrives via `WorldSnapshot.t`; the sim clock is injected.
3. **Every `Decision` sets `binding`**; rationale text only comes from `rationale.render()`.
   No hand-written explanation strings.
4. **`shipos/contracts.py` is frozen** after PR #2. Changing it is its own PR, reviewed by both people.
5. **Tests first.** Golden scenario tests compute expected values from Annex inputs; never paste
   an engine output as the expected value.
6. **Determinism**: same seed + timeline → identical decision-log hash.

## Contracts (frozen after PR #2)
- `shipos/contracts.py` — ShipSpec, GuestManifest, WorldSnapshot, Decision and friends.
- Loaders: `shipos/data.py` (`load_ship`, `load_guests`, `load_priorities`, `load_envelope`).
- Engine entry points (signatures fixed): `engine.admission.decide`, `engine.placement.decide`,
  `engine.arbitration.decide`, `engine.shedding.plan`, `engine.rationale.render`.
- Sample outputs for UI/sim work before the engine lands: `tests/fixtures/*.json`
  (regenerate with `uv run python -m tools.make_fixtures`).

## Ownership (keeps parallel agents out of each other's files)
- Person A — `shipos/physics/`, `shipos/engine/`, `shipos/statemachine.py`
- Person B — `data/`, `shipos/sim/`, `shipos/api/`, `shipos/crew/`, `docs/`
- Cross-track handoffs go through `tests/fixtures/*.json`; update the fixture in the same PR that
  changes an output shape.
- Shared (either track): `contracts.py`, `data/assumptions/`, `tests/fixtures/`, top-level tests,
  CI, Makefile, `docs/PLAN.md`. The `File ownership` CI check fails a/* PRs that touch B's files
  and vice versa; the full map is `tools/check_ownership.py`.

## Git / PR workflow
- Branch from fresh `origin/main`: `a/<topic>` or `b/<topic>` (CI rejects other names).
- One agent task = one branch = one PR, ideally < 400 lines. Use `git worktree add` to run two
  agents side by side.
- `git fetch && git rebase origin/main && make check`, push, then `gh pr create --fill --base main`.
- CI (lint, guardrails, tests, PR hygiene) must be green; Claude review comments must be
  addressed or answered. Squash-merge, delete the branch.
- Don't add dependencies outside a dedicated PR.

## Constraints from the challenge
Offline, deterministic, explainable. No ML in the decision path. Civil use only: no weapons,
targeting or engagement logic. Only use AI models on the organisers' authorised list (no models
from China-based companies).
