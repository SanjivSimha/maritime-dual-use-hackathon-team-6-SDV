"""D4 degradation (first action ≤ 5 s, full sequence ≤ 60 s). Implemented in plan row A7."""

from shipos.contracts import Decision, GuestManifest, PriorityFile, ShipSpec, WorldSnapshot


def plan(
    ship: ShipSpec,
    guests: dict[str, GuestManifest],
    snapshot: WorldSnapshot,
    priorities: PriorityFile,
    protected: frozenset[str] = frozenset(),
) -> Decision:
    """Ordered throttle → standby → shed sequence restoring the budget over the forecast horizon."""
    raise NotImplementedError("plan row A7")
