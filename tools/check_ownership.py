"""Fail a PR whose branch track (a/* or b/*) touches files owned by the other track.

Ownership follows CLAUDE.md and docs/PLAN.md §4–§5. Anything not listed is shared and may
be changed from either track; review rules (CODEOWNERS, contracts.py needs both people)
still apply. The governance files themselves are Person A's so a b/* agent can't loosen
the check by accident.

Usage: python -m tools.check_ownership <branch> < changed_paths.txt
"""

import sys

A_OWNED = (
    "shipos/physics/",
    "shipos/engine/",
    "shipos/statemachine.py",
    "tests/physics/",
    "tests/engine/",
    "tests/statemachine/",
    "tests/test_statemachine.py",
    # Governance: the ownership check and review routing.
    ".github/CODEOWNERS",
    ".github/workflows/ownership.yml",
    "tools/check_ownership.py",
)

B_OWNED = (
    "data/",
    "shipos/sim/",
    "shipos/api/",
    "shipos/crew/",
    "docs/",
    "schema/",
    "tests/sim/",
    "tests/api/",
    "tests/crew/",
)

# Carved out of the trees above: both tracks legitimately edit these.
SHARED = (
    "data/assumptions/",  # CLAUDE.md rule 1: whoever adds A("<id>") adds the YAML entry
    "docs/PLAN.md",
)


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in prefixes)


def owner(path: str) -> str | None:
    """'a', 'b', or None for shared."""
    if _matches(path, SHARED):
        return None
    if _matches(path, A_OWNED):
        return "a"
    if _matches(path, B_OWNED):
        return "b"
    return None


def violations(branch: str, paths: list[str]) -> list[str]:
    track = branch.split("/", 1)[0]
    if track not in ("a", "b"):
        return list(paths)
    return [p for p in paths if owner(p) not in (None, track)]


def main() -> int:
    branch = sys.argv[1]
    paths = [line.strip() for line in sys.stdin if line.strip()]
    bad = violations(branch, paths)
    for path in bad:
        other = owner(path)
        who = f"Person {other.upper()}" if other else "an a/* or b/* branch"
        print(f"::error file={path}::{path} is owned by {who}; '{branch}' may not change it.")
    if "shipos/contracts.py" in paths:
        print("::warning::shipos/contracts.py changed: needs its own PR approved by both people.")
    print(f"Checked {len(paths)} changed file(s) on '{branch}': {len(bad)} ownership violation(s).")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
