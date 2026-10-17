"""Optional 1 Hz wall-clock presentation adapter; never part of replay."""

import time
from collections.abc import Iterable, Iterator

from shipos.contracts import WorldSnapshot


def paced(snapshots: Iterable[WorldSnapshot]) -> Iterator[WorldSnapshot]:
    deadline = time.monotonic()
    for snapshot in snapshots:
        time.sleep(max(0, deadline - time.monotonic()))
        yield snapshot
        deadline += 1
