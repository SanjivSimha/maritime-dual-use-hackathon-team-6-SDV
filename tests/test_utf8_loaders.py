"""Repository YAML is UTF-8, independently of the host default encoding."""

import yaml

from shipos import data
from shipos.assumptions import load_registry


def test_yaml_preserves_non_ascii_values_and_reference_resolution(tmp_path, monkeypatch):
    path = tmp_path / "card.yaml"
    expected = {"name": "caf\u00e9", "nested": ["\u6e29\u5ea6", {"unit": "\u00b0C"}]}
    document = {**expected, "reference": "$test.temperature"}
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(data, "A", lambda aid: {"test.temperature": 24}[aid])

    assert data.load_yaml(path) == {**expected, "reference": 24}


def test_assumption_registry_preserves_non_ascii_metadata(tmp_path):
    entry = {
        "id": "test.label",
        "value": "caf\u00e9",
        "unit": "\u00b0C",
        "range": [0, 30],
        "why": "Temp\u00e9rature \u2014 essai",
        "source": "R\u00e9f\u00e9rence",
    }
    resource_class = "\u6e29\u5ea6"
    path = tmp_path / "metadata.yaml"
    path.write_text(
        yaml.safe_dump({"class": resource_class, "assumptions": [entry]}, allow_unicode=True),
        encoding="utf-8",
    )

    assumption = load_registry(tmp_path)[entry["id"]]

    for field in ("id", "value", "unit", "why", "source"):
        assert getattr(assumption, field) == entry[field]
    assert assumption.range == tuple(entry["range"])
    assert assumption.resource_class == resource_class
    assert assumption.file == path.name
