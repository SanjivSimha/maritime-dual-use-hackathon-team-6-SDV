"""D1 admission (≤ 30 s). Implemented in plan row A3."""

from shipos.contracts import (
    Decision,
    Envelope,
    GuestManifest,
    PriorityFile,
    ShipSpec,
    WorldSnapshot,
)


def decide(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    snapshot: WorldSnapshot,
    card: GuestManifest,
    priorities: PriorityFile,
    envelope: Envelope,
) -> Decision:
    """yes / no / yes-if / not-now-and-when for `card`, judged on every envelope corner."""
    raise NotImplementedError("plan row A3")
