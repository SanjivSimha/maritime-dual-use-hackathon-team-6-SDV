"""Strict local payload validation around the frozen shared Event contract."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from shipos.contracts import Event, GuestManifest, PriorityFile
from shipos.data import load_yaml


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class GensetTrip(Payload):
    gensets_online: int = Field(ge=1)


class CoolingLoss(Payload):
    pass


class BatteryReport(Payload):
    guest_id: str
    battery_pct: float = Field(ge=0, le=100)


class CardPresented(Payload):
    card: GuestManifest


class PrioritySwap(Payload):
    priorities: PriorityFile


class CrewChange(Payload):
    crew: int = Field(ge=0)


PAYLOADS = {
    "genset_trip": GensetTrip,
    "cooling_loss": CoolingLoss,
    "battery_report": BatteryReport,
    "card_presented": CardPresented,
    "priority_swap": PrioritySwap,
    "crew_change": CrewChange,
}


def tick_time(value: float) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("simulation times must be nonnegative integer seconds")
    if not float(value).is_integer() or value < 0:
        raise ValueError("simulation times must be nonnegative integer seconds")
    return int(value)


def validate_event(event: Event) -> Event:
    tick_time(event.t_s)
    if event.kind not in PAYLOADS:
        raise ValueError(f"unknown event kind: {event.kind!r}")
    try:
        payload = PAYLOADS[event.kind].model_validate(event.payload)
    except ValueError as exc:
        raise ValueError(f"invalid {event.kind} payload at {event.t_s:g}s: {exc}") from exc
    return Event(t_s=event.t_s, kind=event.kind, payload=payload.model_dump(mode="json"))


def load_timeline(path: Path) -> list[Event]:
    """YAML list of {t, event, payload}; equal times retain file order."""
    rows = load_yaml(path)
    if not isinstance(rows, list):
        raise ValueError("timeline must be a YAML list")
    events = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"t", "event", "payload"}:
            raise ValueError("timeline rows require exactly t, event, payload")
        tick_time(row["t"])
        events.append(
            validate_event(Event(t_s=row["t"], kind=row["event"], payload=row["payload"]))
        )
    return sorted(events, key=lambda event: event.t_s)
