"""Shared types for every track.

Frozen after PR #2: change only in a dedicated PR both people review.

Four groups:
  * ShipSpec        — the platform (Annex A), loaded from data/ship/*.yaml
  * GuestManifest   — a guest card (Annex B, B.0 ontology), loaded from data/guests/*.yaml
  * WorldSnapshot   — what the governor sees at one instant (sim → engine)
  * Decision        — what the engine answers (engine → sim / UI / log)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- base


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Range(Frozen):
    """Closed interval; a missing bound is open."""

    lo: float | None = None
    hi: float | None = None

    def contains(self, x: float) -> bool:
        return (self.lo is None or x >= self.lo) and (self.hi is None or x <= self.hi)

    def intersect(self, other: Range) -> Range | None:
        lo = max((v for v in (self.lo, other.lo) if v is not None), default=None)
        hi = min((v for v in (self.hi, other.hi) if v is not None), default=None)
        if lo is not None and hi is not None and lo > hi:
            return None
        return Range(lo=lo, hi=hi)


# --------------------------------------------------------------------------- enums


class ResourceClass(StrEnum):
    POWER = "power"
    HEAT = "heat"
    DECK = "deck"
    MAST = "mast"
    MASS = "mass"
    DATA = "data"
    COMPUTE = "compute"
    TIME = "time"


class SlotKind(StrEnum):
    BAY1_WHOLE = "bay1_whole"
    BAY1_HALF = "bay1_half"
    BAY2 = "bay2"
    MAST = "mast"
    HANGAR = "hangar"
    HANGAR_ROOF = "hangar_roof"


class SpeedBand(StrEnum):
    STOPPED = "stopped"
    SLOW = "slow"
    CRUISE = "cruise"
    FAST = "fast"


class Course(StrEnum):
    FREE = "free"
    STEADY = "steady"


class Noise(StrEnum):
    QUIET = "quiet"
    NORMAL = "normal"
    MANOEUVRING = "manoeuvring"


class DeckState(StrEnum):
    CLEAR = "clear"
    OCCUPIED = "occupied"


class Emission(StrEnum):
    NORMAL = "normal"
    SILENT = "silent"


class Situation(StrEnum):
    PORT = "port"
    AT_SEA = "at_sea"


class CoolingKind(StrEnum):
    WATER = "water"
    AIR = "air"


class Line(StrEnum):
    PORT = "port"
    STBD = "stbd"


class Link(StrEnum):
    BACKBONE = "backbone"
    SATCOM = "satcom"
    RADIO = "radio"
    ACOUSTIC = "acoustic"


class ModeTier(StrEnum):
    """Shedding ladder, cheapest first: throttle (low_power) before standby before off."""

    NORMAL = "normal"
    LOW_POWER = "low_power"
    STANDBY = "standby"
    OFF = "off"


# --------------------------------------------------------------------------- ship (Annex A)


class Slot(Frozen):
    id: str
    kind: SlotKind
    group: str  # slots sharing physical space share a group (bay1 whole + both halves)
    units: int = 1  # space units used in the group; group capacity = its largest slot's units
    max_mass_t: float
    max_area_m2: float | None = None
    stability_multiplier: float
    cooling_manifold: bool
    occupied_by: str | None = None  # ship's own equipment (nav radar, satcom terminal)
    near_radar: bool = False


class PowerSpec(Frozen):
    reserve_kw: dict[SpeedBand, dict[int, float]]  # speed band → gensets online → kW for guests
    peak_extra_kw: dict[int, float]  # gensets online → extra kW allowed for ≤ peak_window_s
    peak_window_s: float
    lines: dict[Line, float]  # kW per mission line
    line_lost_on_trip: Line
    degraded_spinning_reserve_kw: float  # kept unallocated when one genset is down


class CoolingSpec(Frozen):
    t_lo_c: float
    cap_lo_kw: float
    t_hi_c: float
    cap_hi_kw: float
    degraded_factor: float  # pump/exchanger down: capacity × this
    inertia_kw: float
    inertia_s: float
    inertia_period_s: float
    mast_air_max_kw: float


class MassSpec(Frozen):
    margin_t: float
    stability_budget_t: float


class LinkSpec(Frozen):
    backbone_mbps: float
    satcom_mbps: Range
    satcom_availability: Range
    satcom_latency_ms: Range
    radio_mbps: Range
    radio_range_km: Range
    acoustic_kbps: Range
    acoustic_latency_s: Range
    ship_satcom_reserved_mbps: float


class ComputeSpec(Frozen):
    nodes: int
    cores_per_node: int
    mem_gb_per_node: float
    accelerators_per_node: int
    storage_tb: float
    allocatable_fraction: float


class TimingSpec(Frozen):
    admission_s: float
    arbitration_s: float
    shed_first_s: float
    shed_full_s: float
    safety_command_s: float


class TransitionSpec(Frozen):
    """A.6 transition times in seconds; engine plans on the upper bound (hi)."""

    course_settle_s: Range
    speed_band_s: Range
    to_quiet_s: Range
    deck_clear_s: dict[int, Range | None]  # crew level → time; None = only if already clear
    confirm_s: dict[int, Range | None]  # crew level → human confirmation; None = impossible
    genset_restart_s: Range


class InstallWindowSpec(Frozen):
    sea_state_max: float
    wind_max_kn: float
    speed_kn: Range
    people_flight_deck: int
    people_ramp: int
    drone_swap_min: float
    vehicle_swap_min: float
    portable_max_kg: float


class ShipSpec(Frozen):
    name: str
    crew_levels: list[int]
    power: PowerSpec
    cooling: CoolingSpec
    slots: list[Slot]
    mass: MassSpec
    links: LinkSpec
    compute: ComputeSpec
    timing: TimingSpec
    transitions: TransitionSpec
    install_window: InstallWindowSpec
    speed_bands_kn: dict[SpeedBand, Range]


# --------------------------------------------------------------------------- guest manifest (B.0)


class Conditions(Frozen):
    """Ship state a guest activity needs. None = no requirement on that variable."""

    speed_kn: Range | None = None
    course: Course | None = None
    noise: Noise | None = None  # QUIET = must be quiet
    deck: DeckState | None = None
    emission: Emission | None = None
    deck_machinery_allowed: bool | None = None  # False = no winches / hangar doors
    wind_max_kn: float | None = None
    sea_state_max: float | None = None
    uses_flight_deck: bool = False  # flight deck and ramp are mutually exclusive (A.3)
    uses_ramp: bool = False
    needs_manoeuvre_freedom: bool = False  # free to turn into wind


class Mode(Frozen):
    name: str
    tier: ModeTier = ModeTier.NORMAL
    power_kw: float
    duration_min: float | None = None  # None = continuous
    next_mode: str | None = None  # entered when duration elapses
    interruptible: bool = True
    deferrable: bool = False  # may be postponed before it starts
    capability_factor: float = 1.0  # fraction of the guest's capability delivered in this mode
    peak_kw: float = 0.0  # extra above power_kw for ≤ peak_s
    peak_s: float = 0.0
    deck_machinery: bool = False  # this mode runs deck machinery (winch, hangar doors)
    conditions: Conditions | None = None


class Part(Frozen):
    """part-of: admitted and shed with its parent; placed separately only if `allowed` is set."""

    id: str
    name: str
    mass_t: float
    area_m2: float | None = None
    height_m: float | None = None
    allowed: list[SlotKind] = Field(default_factory=list)  # empty = co-located with the body
    preferred: list[SlotKind] = Field(default_factory=list)


class LinkDemand(Frozen):
    link: Link
    mbps: float
    elastic: bool = True  # shortfall degrades capability instead of refusing


class ComputeDemand(Frozen):
    cores: float = 0
    mem_gb: float = 0
    accelerators: int = 0


class Exclusion(Frozen):
    """excludes: absolute → refusal at admission; conditional → modes cannot coincide."""

    kind: Literal["absolute", "conditional"]
    mode: str | None = None  # own mode (None = any)
    target: str  # guest id, or "emission:silent"
    target_mode: str | None = None
    reason: str


class Requirement(Frozen):
    target: str  # guest id, or "slot:<kind>"
    reason: str


class Capability(Frozen):
    name: str
    value_by_mission: dict[str, Literal["high", "medium", "low"]]
    default: Literal["high", "medium", "low"] = "low"
    tags: list[str] = Field(default_factory=list)


class Installation(Frozen):
    where: Literal["port_only", "at_sea_window", "logical"]
    people: int
    hours: float


class AtSeaSwap(Frozen):
    what: str
    via: Literal["flight_deck", "ramp"]
    people: int
    minutes: float


class Battery(Frozen):
    endurance_min: float
    critical_pct: float
    land_within_min: float  # below critical_pct
    on_deny: Literal["ditch"] = "ditch"


class PreemptOption(Frozen):
    """How an activity can yield to another guest (D3)."""

    name: str  # "pause", "suspend"
    hold_mode: str  # mode entered while yielding
    window_min: float  # ship is released for this long
    rerun: bool  # elapsed activity time is lost
    resume_delay_min: float = 0.0  # extra time before the activity can restart


class Activity(Frozen):
    name: str
    mode: str
    duration_min: float | None = None
    preempt: list[PreemptOption] = Field(default_factory=list)
    followed_by: str | None = None  # activity that needs the same ship state afterwards


class SafetyCommands(Frozen):
    link: Link
    autonomous_fallback: str | None = None  # behaviour on link loss, e.g. "hold_depth"


class GuestManifest(Frozen):
    id: str
    name: str
    mass_t: float  # body mass, parts excluded
    area_m2: float | None = None
    cooling: CoolingKind
    preferred: list[SlotKind]
    allowed: list[SlotKind]
    parts: list[Part] = Field(default_factory=list)
    modes: list[Mode]
    initial_mode: str
    activities: list[Activity] = Field(default_factory=list)
    data: list[LinkDemand] = Field(default_factory=list)
    compute: ComputeDemand = ComputeDemand()
    excludes: list[Exclusion] = Field(default_factory=list)
    requires: list[Requirement] = Field(default_factory=list)
    safety_critical: bool = False
    capability: Capability
    installation: Installation
    at_sea_swap: AtSeaSwap | None = None
    logical_reconfig: str | None = None
    battery: Battery | None = None
    safety_commands: SafetyCommands | None = None
    radar_sensitivity_loss: float = 0.0  # capability lost when mounted near the ship's own radar

    def mode(self, name: str) -> Mode:
        for m in self.modes:
            if m.name == name:
                return m
        raise KeyError(f"{self.id} has no mode {name!r}")

    def activity(self, name: str) -> Activity:
        for a in self.activities:
            if a.name == name:
                return a
        raise KeyError(f"{self.id} has no activity {name!r}")

    @property
    def total_mass_t(self) -> float:
        return self.mass_t + sum(p.mass_t for p in self.parts)


# --------------------------------------------------------------------------- mission context


class PriorityFile(Frozen):
    """Swappable input, never a constant. Weights derive from the manifest's capability levels."""

    id: str
    mission: str
    levels: dict[str, float]  # high / medium / low → weight
    overrides: dict[str, float] = Field(default_factory=dict)  # guest id → weight
    mission_critical_threshold: float
    protect_arbitration_winner: bool = True

    def weight(self, manifest: GuestManifest) -> float:
        if manifest.id in self.overrides:
            return self.overrides[manifest.id]
        level = manifest.capability.value_by_mission.get(self.mission, manifest.capability.default)
        return self.levels[level]

    def mission_critical(self, manifest: GuestManifest) -> bool:
        return self.weight(manifest) >= self.mission_critical_threshold


class Envelope(Frozen):
    """A.10 mission envelope: admission must hold in every corner."""

    id: str
    speed_bands: list[SpeedBand]
    seawater_c_max: float
    gensets_online: list[int]
    include_cooling_degraded: bool = False
    sea_state_max: float
    wind_max_kn: float
    days_without_port: float
    silence_expected: bool = False


class Corner(Frozen):
    speed_band: SpeedBand
    seawater_c: float
    gensets_online: int
    cooling_degraded: bool = False

    @property
    def degraded(self) -> bool:
        return self.gensets_online < 2 or self.cooling_degraded

    @property
    def label(self) -> str:
        parts = [self.speed_band.value, f"{self.seawater_c:g} °C"]
        parts.append("one genset down" if self.gensets_online < 2 else "both gensets")
        if self.cooling_degraded:
            parts.append("loop degraded")
        return ", ".join(parts)


# --------------------------------------------------------------------------- world snapshot


class ShipState(Frozen):
    speed_kn: float
    course: Course
    noise: Noise
    deck: DeckState
    emission: Emission
    deck_machinery_active: bool = False
    flight_deck_in_use: bool = False
    ramp_in_use: bool = False


class PlatformStatus(Frozen):
    gensets_online: int = 2
    cooling_degraded: bool = False
    situation: Situation = Situation.AT_SEA


class ForecastPoint(Frozen):
    t_s: float
    sea_state: float
    wind_kn: float
    seawater_c: float | None = None


class Env(Frozen):
    seawater_c: float
    sea_state: float
    wind_kn: float
    layered_water: bool = False
    forecast: list[ForecastPoint] = Field(default_factory=list)


class Placement(Frozen):
    slots: dict[str, str]  # "body" or part id → slot id
    line: Line


class ScheduledMode(Frozen):
    t_s: float
    mode: str


class GuestRuntime(Frozen):
    id: str
    mode: str
    placement: Placement
    schedule: list[ScheduledMode] = Field(default_factory=list)  # future mode changes
    activity: str | None = None
    activity_started_s: float | None = None
    battery_pct: float | None = None
    measured_kw: float | None = None


class WorldSnapshot(Frozen):
    t_s: float
    ship: ShipState
    platform: PlatformStatus
    env: Env
    crew: int
    guests: dict[str, GuestRuntime]
    mission_days_left: float


class Event(Frozen):
    t_s: float
    kind: str  # genset_trip, cooling_loss, battery_report, card_presented, priority_swap, ...
    payload: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- decisions


class ConstraintResult(Frozen):
    """One resource (or relation / deadline) checked in one envelope corner."""

    cls: ResourceClass
    resource: str  # e.g. "power.total", "heat.loop", "deck.bay1", "time.landing_deadline"
    capacity: float
    demand: float
    unit: str
    corner: str = ""
    hard: bool = True
    contributors: dict[str, float] = Field(default_factory=dict)
    facts: dict[str, str | float] = Field(default_factory=dict)  # values the rationale may cite

    @property
    def margin(self) -> float:
        return self.capacity - self.demand

    @property
    def ok(self) -> bool:
        return self.margin >= -1e-9

    @property
    def norm_margin(self) -> float:
        return self.margin / self.capacity if self.capacity else (0.0 if self.ok else -1.0)


class ActionKind(StrEnum):
    KEEP = "keep"
    THROTTLE = "throttle"
    STANDBY = "standby"
    SHED = "shed"
    REMOVE = "remove"
    MOVE = "move"
    INTERLOCK = "interlock"
    DEFER = "defer"
    PAUSE = "pause"
    SUSPEND = "suspend"
    PROCEED = "proceed"
    DENY = "deny"
    RESERVE = "reserve"
    RETASK = "retask"


class Action(Frozen):
    guest: str
    kind: ActionKind
    to_mode: str | None = None
    other: str | None = None  # interlock partner "G4.charging"
    when: Literal["always", "degraded", "now"] = "always"
    capability_lost: float = 0.0  # priority-weighted
    kw_recovered: float = 0.0
    delay_min: float = 0.0
    t_offset_s: float = 0.0
    facts: dict[str, str | float] = Field(default_factory=dict)


class Cost(Frozen):
    guest: str
    amount: float
    unit: str  # "survey-min", "capability", "kW", "drone-risk"
    what: str


class Option(Frozen):
    name: str
    actions: list[Action] = Field(default_factory=list)
    feasible: bool
    cost: float = 0.0  # permanent / main cost (lower is better)
    cost_contingent: float = 0.0  # only paid in a degraded corner
    binding: ConstraintResult | None = None
    rejected_because: str | None = None
    costs: list[Cost] = Field(default_factory=list)


class StateRequest(Frozen):
    variable: str
    to: str
    start_s: float
    duration_s: float


class DecisionKind(StrEnum):
    ADMISSION = "admission"
    PLACEMENT = "placement"
    ARBITRATION = "arbitration"
    SHEDDING = "shedding"


class Verdict(StrEnum):
    YES = "yes"
    NO = "no"
    YES_IF = "yes_if"
    NOT_NOW = "not_now"
    DECIDED = "decided"  # arbitration
    NO_ACTION = "no_action"  # shedding: budget still holds
    SHED = "shed"


class Decision(Frozen):
    kind: DecisionKind
    subject: str  # guest id (admission/placement), "G4 vs G3" (arbitration), event (shedding)
    verdict: Verdict
    binding: ConstraintResult | None = None
    chosen: Option | None = None
    alternatives: list[Option] = Field(default_factory=list)
    placement: dict[str, Placement] = Field(default_factory=dict)
    state_requests: list[StateRequest] = Field(default_factory=list)
    winner: str | None = None
    loser: str | None = None
    costs: list[Cost] = Field(default_factory=list)
    holds_one_genset_down: bool | None = None
    contingency: list[Action] = Field(default_factory=list)
    next_window: str | None = None
    checks: list[ConstraintResult] = Field(default_factory=list)
    rationale: str = ""
    latency_ms: float | None = None
    t_s: float = 0.0
# ownership-check test
