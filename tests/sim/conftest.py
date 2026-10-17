"""Test-local historical replay inputs; production loaders keep the current catalog.

inputs/scenario_b_g1_g5_v1.jsonl is the canonical v1 construction record captured
from fcd8c586069ddbfdc9a4b6b2af8f3de61de619e2 Git inputs: reference_vessel,
G1-G5, survey_primary, Scenario B initial/seed/profiles and B.timeline. All $refs
were resolved using that revision's assumption YAML. No G6, environment override,
execution or injected event is present. At 721 ticks its published hash is ca3abd...
The fixture embeds inputs, not decisions, and needs compatible simulator code.
"""

from pathlib import Path

import pytest

from shipos.sim.runner import replay_jsonl


@pytest.fixture
def historical_scenario_b():
    """Return fresh runners from captured five-card inputs without catalog reads."""
    # Normalize checkout line endings; escaped newlines inside JSON values are unchanged.
    header = (
        (Path(__file__).parent / "inputs/scenario_b_g1_g5_v1.jsonl")
        .read_text(encoding="utf-8")
        .encode("utf-8")
    )

    def make():
        return replay_jsonl(header)

    return make
