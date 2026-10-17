"""On-board compute (A.7): 80 % of a fixed rack, accelerators are whole devices."""

import math
from dataclasses import dataclass

from shipos.contracts import ConstraintResult, GuestManifest, ResourceClass, ShipSpec


@dataclass(frozen=True)
class ComputeCapacity:
    cores: int
    mem_gb: float
    accelerators: int


def allocatable(ship: ShipSpec) -> ComputeCapacity:
    c = ship.compute
    f = c.allocatable_fraction
    return ComputeCapacity(
        cores=math.floor(c.nodes * c.cores_per_node * f),
        mem_gb=c.nodes * c.mem_gb_per_node * f,
        accelerators=math.floor(c.nodes * c.accelerators_per_node * f),
    )


def checks(ship: ShipSpec, manifests: list[GuestManifest], corner: str = ""):
    cap = allocatable(ship)
    rows = [
        ("compute.cores", cap.cores, "cores", lambda g: g.compute.cores),
        ("compute.mem", cap.mem_gb, "GB", lambda g: g.compute.mem_gb),
        ("compute.accelerators", cap.accelerators, "devices", lambda g: g.compute.accelerators),
    ]
    return [
        ConstraintResult(
            cls=ResourceClass.COMPUTE,
            resource=res,
            capacity=capacity,
            demand=sum(get(g) for g in manifests),
            unit=unit,
            corner=corner,
            contributors={g.id: get(g) for g in manifests if get(g)},
        )
        for res, capacity, unit, get in rows
    ]
