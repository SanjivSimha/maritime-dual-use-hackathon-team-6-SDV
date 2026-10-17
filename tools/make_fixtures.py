"""Write sample snapshots/decisions to tests/fixtures/ so UI and sim can build before the engine.

Shapes are real (validated against shipos.contracts); numbers are illustrative samples, and golden
tests never compare engine output against these files.
Regenerate: uv run python -m tools.make_fixtures
"""

from pathlib import Path

from shipos.contracts import (
    Action,
    ActionKind,
    ConstraintResult,
    Cost,
    Course,
    Decision,
    DecisionKind,
    DeckState,
    Emission,
    Env,
    GuestRuntime,
    Line,
    Noise,
    Option,
    Placement,
    PlatformStatus,
    ResourceClass,
    ShipState,
    Situation,
    StateRequest,
    Verdict,
    WorldSnapshot,
)

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _p(line: Line, **slots: str) -> Placement:
    return Placement(slots=slots, line=line)


def snapshot_scenario_a_port() -> WorldSnapshot:
    return WorldSnapshot(
        t_s=0,
        ship=ShipState(
            speed_kn=0,
            course=Course.FREE,
            noise=Noise.NORMAL,
            deck=DeckState.OCCUPIED,
            emission=Emission.NORMAL,
        ),
        platform=PlatformStatus(gensets_online=2, situation=Situation.PORT),
        env=Env(seawater_c=16, sea_state=1, wind_kn=8),
        crew=14,
        guests={
            "G1": GuestRuntime(id="G1", mode="on", placement=_p(Line.STBD, body="mast3")),
            "G2": GuestRuntime(id="G2", mode="profiling", placement=_p(Line.STBD, body="bay2")),
            "G5": GuestRuntime(
                id="G5", mode="on", placement=_p(Line.PORT, body="bay1a", antenna="hangar_roof")
            ),
        },
        mission_days_left=14,
    )


def snapshot_scenario_b_min8() -> WorldSnapshot:
    return WorldSnapshot(
        t_s=480,
        ship=ShipState(
            speed_kn=3,
            course=Course.STEADY,
            noise=Noise.QUIET,
            deck=DeckState.OCCUPIED,
            emission=Emission.NORMAL,
        ),
        platform=PlatformStatus(gensets_online=2),
        env=Env(seawater_c=24, sea_state=3, wind_kn=22),
        crew=14,
        guests={
            "G1": GuestRuntime(id="G1", mode="on", placement=_p(Line.STBD, body="mast3")),
            "G3": GuestRuntime(
                id="G3",
                mode="surveying",
                placement=_p(Line.PORT, body="bay1"),
                activity="survey_leg",
                activity_started_s=0,
            ),
            "G4": GuestRuntime(
                id="G4", mode="flying", placement=_p(Line.PORT, body="hangar"), battery_pct=18
            ),
            "G5": GuestRuntime(
                id="G5", mode="on", placement=_p(Line.STBD, body="bay2", antenna="hangar_roof")
            ),
        },
        mission_days_left=13.9,
    )


def decision_admission_yes_if() -> Decision:
    binding = ConstraintResult(
        cls=ResourceClass.POWER,
        resource="power.total",
        capacity=100,
        demand=101.5,
        unit="kW",
        corner="slow, 30 °C, one genset down",
        contributors={"G1": 1.5, "G2": 20, "G4": 35, "G5": 45},
    )
    interlock = Action(
        guest="G2",
        kind=ActionKind.INTERLOCK,
        to_mode="winch",
        other="G4.charging",
        when="degraded",
        capability_lost=0.05,
    )
    return Decision(
        kind=DecisionKind.ADMISSION,
        subject="G4",
        verdict=Verdict.YES_IF,
        binding=binding,
        chosen=Option(name="interlock G2 winch / G4 charging", actions=[interlock], feasible=True),
        alternatives=[
            Option(
                name="G5 low-power when one genset is down",
                actions=[
                    Action(
                        guest="G5",
                        kind=ActionKind.THROTTLE,
                        to_mode="low_power",
                        when="degraded",
                        capability_lost=0.14,
                        kw_recovered=37,
                    )
                ],
                feasible=True,
                cost_contingent=0.14,
            )
        ],
        placement={"G4": Placement(slots={"body": "hangar", "drone": "hangar"}, line=Line.PORT)},
        holds_one_genset_down=False,
        rationale="SAMPLE — real rationale is rendered by engine/rationale.py",
    )


def decision_arbitration() -> Decision:
    return Decision(
        kind=DecisionKind.ARBITRATION,
        subject="G4 vs G3",
        verdict=Verdict.DECIDED,
        winner="G4",
        loser="G3",
        binding=ConstraintResult(
            cls=ResourceClass.TIME,
            resource="time.landing_deadline",
            capacity=12,
            demand=32,
            unit="min",
            facts={"waiting_for": "end of survey leg"},
        ),
        chosen=Option(
            name="G4 recovers; G3 pauses",
            actions=[
                Action(guest="G3", kind=ActionKind.PAUSE, to_mode="holding"),
                Action(guest="G4", kind=ActionKind.PROCEED, to_mode="recovery"),
            ],
            feasible=True,
            cost=27.6,
        ),
        state_requests=[
            StateRequest(variable="speed_kn", to="5", start_s=60, duration_s=120),
            StateRequest(variable="deck", to="clear", start_s=0, duration_s=360),
        ],
        costs=[Cost(guest="G3", amount=23, unit="survey-min", what="15-min pause + leg re-run")],
        rationale="SAMPLE — real rationale is rendered by engine/rationale.py",
        t_s=480,
    )


def decision_shedding() -> Decision:
    return Decision(
        kind=DecisionKind.SHEDDING,
        subject="genset_trip",
        verdict=Verdict.SHED,
        binding=ConstraintResult(
            cls=ResourceClass.POWER,
            resource="power.total",
            capacity=100,
            demand=106.5,
            unit="kW",
            corner="one genset down",
        ),
        chosen=Option(
            name="defer G3 charge",
            actions=[
                Action(
                    guest="G3",
                    kind=ActionKind.DEFER,
                    to_mode="charging",
                    delay_min=5,
                    t_offset_s=1,
                    capability_lost=1.0,
                )
            ],
            feasible=True,
        ),
        costs=[Cost(guest="G3", amount=5, unit="survey-min", what="charge deferred")],
        rationale="SAMPLE — real rationale is rendered by engine/rationale.py",
        t_s=720,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for fn in (
        snapshot_scenario_a_port,
        snapshot_scenario_b_min8,
        decision_admission_yes_if,
        decision_arbitration,
        decision_shedding,
    ):
        (OUT / f"{fn.__name__}.json").write_text(fn().model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    main()
