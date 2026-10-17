"""schema/guest_manifest.schema.json is generated from GuestManifest and must not drift."""

import json

from shipos.contracts import GuestManifest
from tools import guest_schema


def test_committed_schema_is_current():
    assert guest_schema.SCHEMA_PATH.read_text(encoding="utf-8") == guest_schema.render()
    assert guest_schema.main(["--check"]) == 0


def test_render_is_deterministic():
    assert guest_schema.render() == guest_schema.render()


def test_schema_comes_from_the_contract():
    schema = json.loads(guest_schema.render())
    assert schema["title"] == "GuestManifest"
    assert list(schema["properties"]) == list(GuestManifest.model_fields)
    assert schema["additionalProperties"] is False  # Frozen(extra="forbid")


def test_check_detects_drift_without_writing(tmp_path, monkeypatch):
    stale = tmp_path / "guest_manifest.schema.json"
    stale.write_text(guest_schema.render().replace('"mass_t"', '"mass_kg"', 1), encoding="utf-8")
    before = stale.read_text(encoding="utf-8")
    monkeypatch.setattr(guest_schema, "SCHEMA_PATH", stale)
    assert guest_schema.main(["--check"]) == 1
    assert stale.read_text(encoding="utf-8") == before


def test_check_detects_a_contract_change(tmp_path, monkeypatch):
    path = tmp_path / "guest_manifest.schema.json"
    path.write_text(guest_schema.render(), encoding="utf-8")
    monkeypatch.setattr(guest_schema, "SCHEMA_PATH", path)

    class Changed(GuestManifest):
        new_field: int = 0

    Changed.__name__ = "GuestManifest"
    monkeypatch.setattr(guest_schema, "GuestManifest", Changed)
    assert guest_schema.main(["--check"]) == 1


def test_missing_schema_fails_check(tmp_path, monkeypatch):
    monkeypatch.setattr(guest_schema, "SCHEMA_PATH", tmp_path / "missing.json")
    assert guest_schema.main(["--check"]) == 1
