"""Baseline inputs; boundary for the future school-owned simulator.

No simulator interface is published on main yet. Event integration needs a
runner accepting contract Events and returning WorldSnapshots. No clock here.
"""

from shipos.contracts import Decision, WorldSnapshot
from shipos.paths import REPO_DIR

FIXTURES = REPO_DIR / "tests" / "fixtures"


def snapshot(scenario: str, mode: str) -> WorldSnapshot:
    if scenario == "B" and mode != "SAMPLE":
        raise ValueError("Scenario B LIVE unavailable: simulator interface has not landed")
    name = "snapshot_scenario_a_port" if scenario == "A" else "snapshot_scenario_b_min8"
    return WorldSnapshot.model_validate_json((FIXTURES / f"{name}.json").read_text())


def preview(engine: str) -> Decision:
    name = {"arbitration": "decision_arbitration", "shedding": "decision_shedding"}[engine]
    return Decision.model_validate_json((FIXTURES / f"{name}.json").read_text())


def apply_event(event: str) -> WorldSnapshot:
    raise NotImplementedError(f"{event}: simulator event integration unavailable")
