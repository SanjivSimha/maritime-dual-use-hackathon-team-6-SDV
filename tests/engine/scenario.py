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
