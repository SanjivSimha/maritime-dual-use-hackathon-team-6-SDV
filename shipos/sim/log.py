"""Canonical UTF-8 JSONL and SHA-256 chain. No wall clock fields."""

import hashlib
import json

ZERO_HASH = "0" * 64


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def chain_line(record: dict, index: int, previous_hash: str) -> tuple[bytes, str]:
    digest = hashlib.sha256(bytes.fromhex(previous_hash) + canonical(record)).hexdigest()
    line = {"index": index, "previous_hash": previous_hash, "record": record, "hash": digest}
    return canonical(line) + b"\n", digest


def verify_jsonl(data: bytes, *, expected_hash: str | None = None) -> str:
    """Check canonical encoding, continuity, and optionally an independently retained final hash."""
    previous = ZERO_HASH
    if not data:
        raise ValueError("empty replay log")
    for index, line in enumerate(data.splitlines(keepends=True)):
        try:
            item = json.loads(line)
            expected, digest = chain_line(item["record"], index, previous)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"invalid replay record {index}") from exc
        if line != expected:
            raise ValueError(f"replay integrity failure at record {index}")
        previous = digest
    if expected_hash is not None and previous != expected_hash:
        raise ValueError("final replay hash differs from expected_hash")
    return previous
