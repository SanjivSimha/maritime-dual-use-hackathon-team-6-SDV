"""Validate fully resolved judge cards without reading arbitrary card files."""

import math

from shipos.contracts import GuestManifest
from shipos.validation import guest_errors


def _resolved_finite(node):
    if isinstance(node, float) and not math.isfinite(node):
        raise ValueError("Card numbers must be finite")
    if isinstance(node, str) and node.startswith("$"):
        raise ValueError("Use resolved card values; assumption references are not accepted")
    if isinstance(node, dict):
        for value in node.values():
            _resolved_finite(value)
    elif isinstance(node, list):
        for value in node:
            _resolved_finite(value)


def validate_manifest(raw: dict, catalogue: dict[str, GuestManifest]) -> GuestManifest:
    _resolved_finite(raw)
    card = GuestManifest.model_validate(raw)
    # The contract can coerce numeric strings, including nonfinite spellings.
    _resolved_finite(card.model_dump())
    if not card.id.strip():
        raise ValueError("A nonempty guest ID is required")
    errors = guest_errors(card, catalogue | {card.id: card})
    if errors:
        raise ValueError("; ".join(errors))
    return card
