"""Snapshot builders for engine tests (the sim builds the real ones from data/scenarios)."""

from shipos.contracts import (
    Course,
    DeckState,
    Emission,
    Env,
    ForecastPoint,
    GuestRuntime,
    Line,
    Noise,
    Placement,
    PlatformStatus,
    ShipState,
    Situation,
    WorldSnapshot,
)


def P(line, **slots):
    return Placement(slots=slots, line=line)


A_INSTALLED = {
    "G1": ("on", P(Line.STBD, body="mast3")),
    "G2": ("profiling", P(Line.STBD, body="bay2")),
    "G5": ("on", P(Line.PORT, body="bay1a", antenna="hangar_roof")),
}


def scenario_a(situation=Situation.PORT, crew=14, forecast=(), installed=None, speed=0.0):
    installed = A_INSTALLED if installed is None else installed
    return WorldSnapshot(
        t_s=0,
        ship=ShipState(
            speed_kn=speed,
            course=Course.FREE if speed == 0 else Course.STEADY,
            noise=Noise.NORMAL,
            deck=DeckState.CLEAR,
            emission=Emission.NORMAL,
        ),
        platform=PlatformStatus(gensets_online=2, situation=situation),
        env=Env(seawater_c=16, sea_state=1, wind_kn=8, forecast=list(forecast)),
        crew=crew,
        guests={g: GuestRuntime(id=g, mode=m, placement=p) for g, (m, p) in installed.items()},
        mission_days_left=14,
    )


def forecast(*points):
    return [ForecastPoint(t_s=t, sea_state=ss, wind_kn=w) for t, ss, w in points]


B_INSTALLED = {
    "G1": ("on", P(Line.STBD, body="mast3")),
    "G3": ("surveying", P(Line.PORT, body="bay1")),
    "G4": ("flying", P(Line.PORT, body="hangar")),
    "G5": ("on", P(Line.STBD, body="bay2", antenna="hangar_roof")),
}


def scenario_b(
    t_s=480,
    crew=14,
    battery=18.0,
    wind=22.0,
    deck=DeckState.OCCUPIED,
    gensets=2,
    modes=None,
    schedules=None,
):
    """Minute 8: G3 on a 40-min leg since t=0 at 3 kn, quiet; G4 airborne reporting battery."""
    modes = modes or {}
    schedules = schedules or {}
    guests = {}
    for g, (m, p) in B_INSTALLED.items():
        guests[g] = GuestRuntime(
            id=g,
            mode=modes.get(g, m),
            placement=p,
            activity="survey_leg" if g == "G3" else None,
            activity_started_s=0 if g == "G3" else None,
            battery_pct=battery if g == "G4" else None,
            schedule=schedules.get(g, []),
        )
    return WorldSnapshot(
        t_s=t_s,
        ship=ShipState(
            speed_kn=3, course=Course.STEADY, noise=Noise.QUIET, deck=deck, emission=Emission.NORMAL
        ),
        platform=PlatformStatus(gensets_online=gensets),
        env=Env(seawater_c=24, sea_state=3, wind_kn=wind),
        crew=crew,
        guests=guests,
        mission_days_left=14,
    )
