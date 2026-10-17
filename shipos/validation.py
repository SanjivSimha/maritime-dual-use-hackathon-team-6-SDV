"""`shipos validate`: check input files before anything loads them.

Structure and types come from the Pydantic contracts and "$<id>" values resolve exactly as in
`shipos.data`. This module only adds what model validation cannot see on its own: unknown
assumption references (all of them, not just the first), malformed or empty YAML, duplicate guest
ids that `load_guests` would silently collapse, and names that point at modes, activities or guests
that do not exist.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from shipos.assumptions import registry
from shipos.contracts import Envelope, GuestManifest, PriorityFile, ShipSpec, SlotKind
from shipos.data import REF, resolve
from shipos.paths import DATA_DIR, REPO_DIR

EMISSION_TARGET = "emission:silent"  # Exclusion.target besides a guest id
SLOT_PREFIX = "slot:"  # Requirement.target "slot:<kind>" besides a guest id
LEVELS = ("high", "medium", "low")  # Capability levels PriorityFile.weight looks up

M = TypeVar("M", bound=BaseModel)


def _show(path: Path) -> str:
    path = path.resolve()
    return path.relative_to(REPO_DIR).as_posix() if path.is_relative_to(REPO_DIR) else str(path)


def _refs(node) -> Iterable[str]:
    if isinstance(node, str) and node.startswith(REF):
        yield node[len(REF) :]
    elif isinstance(node, dict):
        for v in node.values():
            yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def _location(loc: tuple) -> str:
    return ".".join(str(part) for part in loc) or "(top level)"


def load_checked(path: Path, model: type[M]) -> tuple[M | None, list[str]]:
    """Read, resolve and validate one file; return the model or readable errors."""
    where = _show(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"{where}: file not found"]
    except yaml.YAMLError as exc:
        return None, [f"{where}: malformed YAML: {exc}".replace("\n", " ")]
    if raw is None:
        return None, [f"{where}: file is empty"]
    if not isinstance(raw, dict):
        return None, [f"{where}: expected a mapping at the top level, got {type(raw).__name__}"]

    known = registry()
    unknown = sorted({ref for ref in _refs(raw) if ref not in known})
    if unknown:
        return None, [
            f"{where}: unknown assumption {REF}{ref}; add it to data/assumptions/"
            for ref in unknown
        ]
    try:
        return model.model_validate(resolve(raw)), []
    except ValidationError as exc:
        return None, [
            f"{where}: {_location(e['loc'])}: {e['msg']} (got {e['input']!r})" for e in exc.errors()
        ]


def guest_errors(card: GuestManifest, guests: dict[str, GuestManifest]) -> list[str]:
    """Names on `card` that point nowhere. `guests` is the fleet the card is checked against."""
    errors = []
    modes = {m.name for m in card.modes}
    activities = {a.name for a in card.activities}

    def need_mode(name: str | None, what: str) -> None:
        if name is not None and name not in modes:
            errors.append(f"{what} {name!r} is not a mode of {card.id}")

    errors += [
        f"mode {name!r} is declared {n} times"
        for name, n in Counter(m.name for m in card.modes).items()
        if n > 1
    ]
    need_mode(card.initial_mode, "initial_mode")
    for m in card.modes:
        need_mode(m.next_mode, f"modes[{m.name}].next_mode")
    for a in card.activities:
        need_mode(a.mode, f"activities[{a.name}].mode")
        for p in a.preempt:
            need_mode(p.hold_mode, f"activities[{a.name}].preempt[{p.name}].hold_mode")
        if a.followed_by is not None and a.followed_by not in activities:
            errors.append(f"activities[{a.name}].followed_by {a.followed_by!r} is not an activity")

    for i, ex in enumerate(card.excludes):
        need_mode(ex.mode, f"excludes[{i}].mode")
        if ex.target == EMISSION_TARGET:
            continue
        other = guests.get(ex.target)
        if other is None:
            errors.append(f"excludes[{i}].target {ex.target!r} is not a known guest")
        elif ex.target_mode is not None and ex.target_mode not in {m.name for m in other.modes}:
            errors.append(
                f"excludes[{i}].target_mode {ex.target_mode!r} is not a mode of {other.id}"
            )

    for i, req in enumerate(card.requires):
        if req.target.startswith(SLOT_PREFIX):
            kind = req.target[len(SLOT_PREFIX) :]
            if kind not in {k.value for k in SlotKind}:
                errors.append(f"requires[{i}].target {req.target!r} names no slot kind")
        elif req.target not in guests:
            errors.append(f"requires[{i}].target {req.target!r} is not a known guest")
    return errors


def priority_errors(priorities: PriorityFile, guests: dict[str, GuestManifest]) -> list[str]:
    errors = [f"levels has no weight for {lvl!r}" for lvl in LEVELS if lvl not in priorities.levels]
    errors += [
        f"overrides names unknown guest {gid!r}"
        for gid in priorities.overrides
        if gid not in guests
    ]
    return errors


def _load_guest_dir(directory: Path) -> tuple[dict[str, tuple[Path, GuestManifest]], list[str]]:
    guests: dict[str, tuple[Path, GuestManifest]] = {}
    errors: list[str] = []
    for path in sorted(directory.glob("*.yaml")):
        card, errs = load_checked(path, GuestManifest)
        errors += errs
        if card is None:
            continue
        if card.id in guests:
            first = _show(guests[card.id][0])
            errors.append(f"{_show(path)}: duplicate guest id {card.id!r} (also in {first})")
            continue
        guests[card.id] = (path, card)
    return guests, errors


def validate_cards(cards: Iterable[Path], data_dir: Path = DATA_DIR) -> list[str]:
    """Check guest cards given by path against the fleet in data_dir/guests.

    A card with the id of an existing guest stands in for it (an edited copy).
    """
    fleet, _ = _load_guest_dir(data_dir / "guests")
    guests = {gid: card for gid, (_, card) in fleet.items()}
    errors = []
    for path in cards:
        card, errs = load_checked(path, GuestManifest)
        errors += errs
        if card is not None:
            where = _show(path)
            errors += [f"{where}: {e}" for e in guest_errors(card, guests | {card.id: card})]
    return errors


def validate_data(data_dir: Path = DATA_DIR) -> tuple[list[str], Counter]:
    """Check every input collection under data_dir. Returns (errors, files checked per kind).

    "$<id>" references always resolve against the registry in data/assumptions/.
    """
    fleet, errors = _load_guest_dir(data_dir / "guests")
    guests = {gid: card for gid, (_, card) in fleet.items()}
    counts = Counter(guests=len(list((data_dir / "guests").glob("*.yaml"))))
    for path, card in fleet.values():
        errors += [f"{_show(path)}: {e}" for e in guest_errors(card, guests)]

    for kind, model in (("ship", ShipSpec), ("priorities", PriorityFile), ("envelopes", Envelope)):
        for path in sorted((data_dir / kind).glob("*.yaml")):
            counts[kind] += 1
            loaded, errs = load_checked(path, model)
            errors += errs
            if isinstance(loaded, PriorityFile):
                errors += [f"{_show(path)}: {e}" for e in priority_errors(loaded, guests)]
    for kind in ("ship", "guests"):
        if not counts[kind]:
            errors.append(f"{_show(data_dir / kind)}: no *.yaml files")
    return errors, counts
