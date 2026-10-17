"""Physical Account registry: real use sites, honest gaps, deterministic, drift-checked."""

from pathlib import Path

import pytest

from shipos.data import load_ship
from shipos.paths import REPO_DIR
from tools import physical_account as pa

ASSUMPTIONS = """\
class: Test · class
assumptions:
  - id: t.used_py
    value: 2
    unit: kW
    range: [1, 3]
    why: Read from Python.
    source: invented
  - id: t.used_yaml
    value: 5
    unit: t
    why: Referenced from a card.
    source: A.4
  - id: t.unused
    value: 7
    unit: s
    why: Only mentioned in comments and docs.
    source: invented
"""

CODE = '''\
"""Mentions A("t.unused") in a docstring, which is not a use."""
# A("t.unused") in a comment is not a use either
x = A("t.used_py")
y = A(name)
'''


def make_repo(root: Path, card: str = 'id: G9\nmass_t: "$t.used_yaml"\n') -> Path:
    for d in ("data/assumptions", "data/guests", "shipos", "tools", "docs"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "data/assumptions/test.yaml").write_text(ASSUMPTIONS, encoding="utf-8")
    (root / "data/guests/G9.yaml").write_text(card, encoding="utf-8")
    (root / "shipos/mod.py").write_text(CODE, encoding="utf-8")
    (root / "docs/notes.md").write_text('Uses A("t.unused") and "$t.unused".\n', encoding="utf-8")
    return root


def test_finds_real_python_and_yaml_sites(tmp_path):
    s = pa.scan(make_repo(tmp_path))
    assert s.uses["t.used_py"] == [pa.Site("shipos/mod.py", 3)]
    assert s.uses["t.used_yaml"] == [pa.Site("data/guests/G9.yaml", 2)]
    assert s.uses["t.unused"] == []
    assert s.defined["t.used_yaml"][0] == pa.Site("data/assumptions/test.yaml", 9)
    assert s.dynamic == [(pa.Site("shipos/mod.py", 4), "A(name)")]


def test_render_marks_unused_and_dynamic(tmp_path):
    text = pa.render(make_repo(tmp_path))
    assert "**3 assumptions: 2 used, 1 unused; 1 dynamic lookup(s).**" in text
    assert "- `t.unused` (defined `data/assumptions/test.yaml:14`)" in text
    assert "- `shipos/mod.py:4` `A(name)`" in text
    assert "## Test · class (`data/assumptions/test.yaml`)" in text
    assert (
        "| `t.used_py` | 2 | kW | [1, 3] | Read from Python. | invented "
        "| `data/assumptions/test.yaml:3` | `shipos/mod.py:3` |"
    ) in text
    assert "| `t.used_yaml` | 5 | t | — |" in text
    assert "| **unused** |" in text


def test_render_is_deterministic_with_lf_newlines(tmp_path):
    root = make_repo(tmp_path)
    first = pa.render(root)
    assert first == pa.render(root) and "\r" not in first and first.endswith("\n")


def test_unknown_reference_is_an_actionable_error(tmp_path, capsys):
    root = make_repo(tmp_path, card='id: G9\nmass_t: "$t.missing"\n')
    with pytest.raises(pa.UnknownAssumptionError, match=r"data/guests/G9.yaml:2: unknown"):
        pa.scan(root)
    assert pa.main([], root=root) == 1
    assert "add each id to data/assumptions" in capsys.readouterr().out.lower()
    assert not (root / pa.REL_PATH).exists()


def test_check_detects_drift_without_writing(tmp_path):
    root = make_repo(tmp_path)
    target = root / pa.REL_PATH
    assert pa.main(["--check"], root=root) == 1 and not target.exists()
    assert pa.main([], root=root) == 0
    assert pa.main(["--check"], root=root) == 0
    (root / "data/assumptions/test.yaml").write_text(
        ASSUMPTIONS.replace("value: 7", "value: 8"), encoding="utf-8"
    )
    before = target.read_bytes()
    assert pa.main(["--check"], root=root) == 1
    assert target.read_bytes() == before


def test_repository_registry_is_current():
    assert pa.main(["--check"]) == 0


def test_repository_sites_match_the_source():
    s = pa.scan(REPO_DIR)
    lines = (REPO_DIR / "shipos/physics/cooling.py").read_text(encoding="utf-8").splitlines()
    line = next(i for i, text in enumerate(lines, 1) if 'A("heat.kw_per_kw_electric")' in text)
    assert pa.Site("shipos/physics/cooling.py", line) in s.uses["heat.kw_per_kw_electric"]
    assert all(not site.path.startswith(("tests/", "docs/")) for v in s.uses.values() for site in v)
    assert [site.path for site, _ in s.dynamic] == ["shipos/data.py"]  # the "$id" resolver


def test_account_cites_the_loaded_ship_numbers():
    account = (REPO_DIR / "docs/PHYSICAL_ACCOUNT.md").read_text(encoding="utf-8")
    c = load_ship().cooling
    for figure in (
        f"{c.cap_lo_kw:g} kW at {c.t_lo_c:g} °C",
        f"{c.cap_hi_kw:g} kW at {c.t_hi_c:g} °C",
        f"× {c.degraded_factor:g}",
        f"{c.cap_hi_kw * c.degraded_factor:g} kW at {c.t_hi_c:g} °C",
    ):
        assert figure in account, figure
