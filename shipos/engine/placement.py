"""D2 placement. Implemented in plan row A8."""

from shipos.contracts import Decision, Envelope, GuestManifest, Placement, PriorityFile, ShipSpec


def decide(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    present: list[str],
    fixed: dict[str, Placement],
    priorities: PriorityFile,
    envelope: Envelope,
) -> Decision:
    """Slots, mast, power line and manifold for `present` guests; `fixed` placements are kept."""
    raise NotImplementedError("plan row A8")
