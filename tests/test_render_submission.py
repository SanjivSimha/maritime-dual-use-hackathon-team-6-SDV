"""Pure-function checks for tools/render_submission.py.

Chrome and the PDF libraries are not in the project environment; the full render is the
acceptance procedure in docs/SUBMISSION_BUILD.md.
"""

from pathlib import Path

import pytest

from tools import render_submission as rs

ROOT = Path(__file__).resolve().parents[1]
URL = "https://example.test/repo"


def href(link: str, md_path: str = "docs/x.md", root: Path = ROOT) -> str:
    return rs.portable_href(link, md_path, root, URL, "abc123")


def test_relative_links_become_repo_urls_at_the_ref():
    assert href("PLAN.md") == f"{URL}/blob/abc123/docs/PLAN.md"
    assert href("../README.md#run") == f"{URL}/blob/abc123/README.md#run"
    assert href("/shipos/") == f"{URL}/tree/abc123/shipos"
    assert href("docs/PLAN.md", md_path="README.md") == f"{URL}/blob/abc123/docs/PLAN.md"


def test_web_and_anchor_links_are_kept():
    for link in ("https://example.org/a", "mailto:team@example.org", "#limits"):
        assert href(link) == link


@pytest.mark.parametrize(
    ("link", "reason"),
    [
        ("file:///C:/Users/me/README.md", "non-portable scheme"),
        ("C:/Users/me/README.md", "non-portable scheme"),
        ("../../outside.md", "leaves the repository"),
        ("NO_SUCH_FILE.md", "missing"),
    ],
)
def test_unportable_links_are_rejected(link, reason):
    with pytest.raises(rs.BuildError, match=reason):
        href(link)


def test_missing_ids_detects_a_single_absent_id_and_ignores_wrapping():
    ids = ["power.reserve_kw", "heat.loop_kw"]
    assert rs.missing_ids(ids, "power.reserve_\nkw | heat.loop_kw") == []
    assert rs.missing_ids(ids, "power.reserve_kw | heat.loop") == ["heat.loop_kw"]


def test_registry_ids_match_the_assumption_files(tmp_path):
    folder = tmp_path / "data" / "assumptions"
    folder.mkdir(parents=True)
    (folder / "b.yaml").write_text("assumptions:\n  - id: b.one\n", encoding="utf-8")
    (folder / "a.yaml").write_text(
        "assumptions:\n  - id: a.one\n    why: 12 °C\n  - id: a.two\n", encoding="utf-8"
    )
    assert rs.registry_ids(tmp_path) == ["a.one", "a.two", "b.one"]
    assert len(rs.registry_ids(ROOT)) == len(set(rs.registry_ids(ROOT)))


def test_registry_ids_fail_when_registry_is_empty(tmp_path):
    with pytest.raises(rs.BuildError, match="no assumption ids"):
        rs.registry_ids(tmp_path)


def test_missing_sources_fail_before_any_output_is_touched(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "README.pdf").write_bytes(b"old")
    assert rs.main(["--source", str(tmp_path / "empty"), "--out", str(out)]) == 1
    assert "missing sources" in capsys.readouterr().err
    assert [p.name for p in out.iterdir()] == ["README.pdf"]
    assert (out / "README.pdf").read_bytes() == b"old"


def test_explicit_missing_chrome_is_reported():
    with pytest.raises(rs.BuildError, match="does not exist"):
        rs.find_chrome(str(ROOT / "no-such-chrome.exe"))


def test_failed_publish_names_what_was_already_replaced(tmp_path):
    stage, out = tmp_path / "stage", tmp_path / "out"
    stage.mkdir()
    out.mkdir()
    (stage / "a.pdf").write_bytes(b"new")
    with pytest.raises(rs.BuildError, match="already replaced: a.pdf"):
        rs.publish(stage, out, ["a.pdf", "b.pdf"])
    assert (out / "a.pdf").read_bytes() == b"new"
