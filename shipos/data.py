"""Load YAML inputs into contract types. Strings "$<id>" resolve through the assumption registry."""

from __future__ import annotations

from pathlib import Path

import yaml

from shipos.assumptions import A
from shipos.contracts import Envelope, GuestManifest, PriorityFile, ShipSpec
from shipos.paths import DATA_DIR

REF = "$"


def resolve(node):
    if isinstance(node, str) and node.startswith(REF):
        return A(node[len(REF) :])
    if isinstance(node, dict):
        return {k: resolve(v) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(v) for v in node]
    return node


def load_yaml(path: Path) -> dict:
    return resolve(yaml.safe_load(path.read_text()))


def load_ship(path: Path = DATA_DIR / "ship" / "reference_vessel.yaml") -> ShipSpec:
    return ShipSpec.model_validate(load_yaml(path))


def load_guest(path: Path) -> GuestManifest:
    return GuestManifest.model_validate(load_yaml(path))


def load_guests(directory: Path = DATA_DIR / "guests") -> dict[str, GuestManifest]:
    guests = [load_guest(p) for p in sorted(directory.glob("*.yaml"))]
    return {g.id: g for g in guests}


def load_priorities(name: str, directory: Path = DATA_DIR / "priorities") -> PriorityFile:
    return PriorityFile.model_validate(load_yaml(directory / f"{name}.yaml"))


def load_envelope(name: str = "default", directory: Path = DATA_DIR / "envelopes") -> Envelope:
    return Envelope.model_validate(load_yaml(directory / f"{name}.yaml"))
