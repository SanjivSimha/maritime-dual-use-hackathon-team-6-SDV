"""Guardrail: a/* branches stay out of Person B's files and b/* branches out of Person A's."""

import pytest

from tools.check_ownership import owner, violations


@pytest.mark.parametrize(
    "path, expected",
    [
        ("shipos/engine/admission.py", "a"),
        ("shipos/physics/power.py", "a"),
        ("shipos/statemachine.py", "a"),
        ("tests/engine/test_kernel.py", "a"),
        ("tests/physics/test_power.py", "a"),
        ("tools/check_ownership.py", "a"),
        (".github/CODEOWNERS", "a"),
        (".github/workflows/ownership.yml", "a"),
        ("data/guests/G6.yaml", "b"),
        ("data/ship/reference_vessel.yaml", "b"),
        ("data/priorities/patrol.yaml", "b"),
        ("shipos/sim/runner.py", "b"),
        ("shipos/api/static/app.js", "b"),
        ("shipos/crew/policy.py", "b"),
        ("docs/PHYSICAL_ACCOUNT.md", "b"),
        ("schema/guest_manifest.schema.json", "b"),
        ("tests/sim/test_runner.py", "b"),
        # Shared: either track may touch these (review rules still apply).
        ("shipos/contracts.py", None),
        ("data/assumptions/power.yaml", None),
        ("tests/fixtures/decision_shedding.json", None),
        ("tests/scenarios/test_scenario_a.py", None),
        ("tests/test_contracts.py", None),
        ("docs/PLAN.md", None),
        (".github/workflows/ci.yml", None),
        ("Makefile", None),
        ("pyproject.toml", None),
        ("shipos/cli.py", None),
        ("CLAUDE.md", None),
    ],
)
def test_owner(path, expected):
    assert owner(path) == expected


def test_prefix_does_not_match_sibling_names():
    assert owner("shipos/engine_notes.md") is None
    assert owner("shipos/statemachine.pyc") is None


def test_a_branch_rejected_on_b_files():
    assert violations("a/kernel", ["shipos/engine/kernel.py", "data/guests/G1.yaml"]) == [
        "data/guests/G1.yaml"
    ]


def test_b_branch_rejected_on_a_files():
    assert violations("b/sim", ["shipos/sim/runner.py", "shipos/physics/power.py"]) == [
        "shipos/physics/power.py"
    ]


def test_shared_files_allowed_from_both_tracks():
    shared = ["shipos/contracts.py", "tests/fixtures/x.json", "data/assumptions/heat.yaml"]
    assert violations("a/contracts-v2", shared) == []
    assert violations("b/contracts-v2", shared) == []


def test_unknown_branch_prefix_rejected_outright():
    assert violations("feature/x", ["README.md"]) == ["README.md"]
