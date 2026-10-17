"""D3 right of way (≤ 2 s). Implemented in plan row A6."""

from shipos.contracts import Decision, GuestManifest, PriorityFile, ShipSpec, WorldSnapshot


def decide(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    snapshot: WorldSnapshot,
    priorities: PriorityFile,
    challenger: str,
    challenger_activity: str,
) -> Decision:
    """Resolve `challenger` starting `challenger_activity` against running guests' conditions."""
    raise NotImplementedError("plan row A6")
