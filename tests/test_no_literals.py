"""Guardrail: engine/ and physics/ must read numbers from the assumption registry."""

from pathlib import Path

from tools.check_no_literals import GUARDED_DIRS, find_literals

REPO = Path(__file__).resolve().parents[1]


def test_flags_numeric_literal(tmp_path):
    src = tmp_path / "bad.py"
    src.write_text("def reserve():\n    return 200.0\n")
    hits = find_literals([src])
    assert [(h.line, h.value) for h in hits] == [(2, 200.0)]


def test_allows_zero_one_and_marked_lines(tmp_path):
    src = tmp_path / "ok.py"
    src.write_text("x = 0\ny = 1\nz = -1\nw = 0.5  # literal-ok: unit conversion\ns = 'text 42'\n")
    assert find_literals([src]) == []


def test_guarded_dirs_are_clean():
    files = [p for d in GUARDED_DIRS for p in (REPO / d).rglob("*.py")]
    hits = find_literals(files)
    assert hits == [], "\n".join(str(h) for h in hits)
