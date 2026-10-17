"""Assumption registry: every number not stated in Annex A/B, each with a reason.

Entries live in data/assumptions/<class>.yaml. Code reads them with A("<id>"); data files reference
them as the string "$<id>". tools/physical_account.py turns the registry into Annex C.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from shipos.paths import DATA_DIR


@dataclass(frozen=True)
class Assumption:
    id: str
    value: float | int | str | bool
    unit: str
    range: tuple | None
    why: str
    source: str
    resource_class: str
    file: str


def load_registry(directory: Path) -> dict[str, Assumption]:
    registry: dict[str, Assumption] = {}
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in doc.get("assumptions", []):
            aid = entry["id"]
            if aid in registry:
                raise ValueError(f"duplicate assumption id {aid!r} in {path.name}")
            rng = entry.get("range")
            registry[aid] = Assumption(
                id=aid,
                value=entry["value"],
                unit=entry.get("unit", ""),
                range=tuple(rng) if rng is not None else None,
                why=entry["why"],
                source=entry.get("source", "invented"),
                resource_class=doc.get("class", path.stem),
                file=path.name,
            )
    return registry


@cache
def registry() -> dict[str, Assumption]:
    return load_registry(DATA_DIR / "assumptions")


def A(aid: str):  # noqa: N802 — short accessor by design, reads like a citation
    try:
        return registry()[aid].value
    except KeyError:
        raise KeyError(f"unknown assumption {aid!r}; add it to data/assumptions/") from None
