"""`shipos validate`: real inputs pass, each kind of broken card fails with a readable message."""

import shutil

import pytest

from shipos.cli import main
from shipos.data import load_guest, load_priorities
from shipos.paths import DATA_DIR
from shipos.validation import validate_cards, validate_data

GUESTS = DATA_DIR / "guests"


def edited(tmp_path, guest: str, old: str, new: str, name: str = "card.yaml"):
    """A copy of a real card with one text edit applied."""
    text = (GUESTS / f"{guest}.yaml").read_text(encoding="utf-8")
    assert old in text, f"{guest}.yaml no longer contains {old!r}"
    path = tmp_path / name
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


def one_error(path) -> str:
    errors = validate_cards([path])
    assert len(errors) == 1, errors
    return errors[0]


def test_repo_data_validates():
    errors, counts = validate_data()
    assert errors == []
    assert counts["guests"] >= 5 and counts["ship"] == 1 and counts["envelopes"] >= 1


@pytest.mark.parametrize("guest", ["G1", "G2", "G3", "G4", "G5"])
def test_annex_cards_validate_by_path(guest):
    assert validate_cards([GUESTS / f"{guest}.yaml"]) == []


def test_one_number_edit_validates_and_loads(tmp_path):
    path = edited(
        tmp_path, "G2", "{name: profiling, power_kw: 6}", "{name: profiling, power_kw: 7}"
    )
    assert validate_cards([path]) == []
    assert load_guest(path).mode("profiling").power_kw == 7


def test_type_violation(tmp_path):
    path = edited(tmp_path, "G2", "power_kw: 6}", "power_kw: lots}")
    error = one_error(path)
    assert "modes.0.power_kw" in error and "'lots'" in error


def test_unknown_field_is_rejected(tmp_path):
    path = edited(tmp_path, "G2", "initial_mode: profiling", "initial_mode: profiling\ncolour: red")
    assert "colour" in one_error(path)


def test_unknown_assumption_reference(tmp_path):
    path = edited(tmp_path, "G2", "$guests.g2.standby_kw", "$guests.g2.no_such_value")
    assert "unknown assumption $guests.g2.no_such_value" in one_error(path)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "file is empty"),
        ("id: G9\nmodes: [unclosed\n", "malformed YAML"),
        ("- just\n- a list\n", "expected a mapping"),
    ],
)
def test_bad_yaml(tmp_path, text, expected):
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    assert expected in one_error(path)


def test_missing_file(tmp_path):
    assert "file not found" in one_error(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    ("guest", "old", "new", "expected"),
    [
        ("G2", "initial_mode: profiling", "initial_mode: idle", "initial_mode 'idle'"),
        ("G2", "next_mode: profiling", "next_mode: idle", "modes[winch].next_mode 'idle'"),
        ("G3", "    mode: surveying", "    mode: diving", "activities[survey_leg].mode 'diving'"),
        (
            "G3",
            "hold_mode: holding, window_min: 15",
            "hold_mode: hover, window_min: 15",
            "hold_mode 'hover'",
        ),
        ("G4", "followed_by: launch", "followed_by: relaunch", "followed_by 'relaunch'"),
        (
            "G2",
            "{name: profiling, power_kw: 6}",
            "{name: winch, power_kw: 6}",
            "'winch' is declared 2 times",
        ),
    ],
)
def test_dangling_mode_and_activity_names(tmp_path, guest, old, new, expected):
    path = edited(tmp_path, guest, old, new)
    assert expected in "\n".join(validate_cards([path]))


def test_exclusion_targets(tmp_path):
    unknown_guest = edited(tmp_path, "G2", "target: G4,", "target: G9,")
    assert "excludes[2].target 'G9' is not a known guest" in one_error(unknown_guest)
    unknown_mode = edited(tmp_path, "G2", "target_mode: recovery", "target_mode: hovering")
    assert "excludes[2].target_mode 'hovering' is not a mode of G4" in one_error(unknown_mode)


def test_conditional_and_emission_exclusions_stay_valid(tmp_path):
    # G5's absolute "emission:silent" is special target syntax, not a guest id.
    assert validate_cards([GUESTS / "G5.yaml"]) == []
    # A new guest may conditionally exclude an existing guest's mode.
    path = edited(tmp_path, "G2", "id: G2", "id: G7")
    assert validate_cards([path]) == []


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("slot:bay2", None),
        ("G4", None),
        ("slot:ramp", "names no slot kind"),
        ("G9", "not a known guest"),
    ],
)
def test_requirement_targets(tmp_path, target, expected):
    path = edited(
        tmp_path,
        "G1",
        "initial_mode:",
        f'requires: [{{target: "{target}", reason: test}}]\ninitial_mode:',
    )
    errors = validate_cards([path])
    assert errors == [] if expected is None else expected in one_error(path)


def test_duplicate_guest_ids_are_reported(tmp_path):
    shutil.copytree(DATA_DIR, tmp_path / "data")
    guests = (tmp_path / "data" / "guests").resolve()
    shutil.copy(guests / "G1.yaml", guests / "G1_copy.yaml")
    errors, _ = validate_data(tmp_path / "data")
    assert errors == [
        f"{guests / 'G1_copy.yaml'}: duplicate guest id 'G1' (also in {guests / 'G1.yaml'})"
    ]


def test_priority_files(tmp_path):
    shutil.copytree(DATA_DIR, tmp_path / "data")
    pri = (tmp_path / "data" / "priorities" / "patrol.yaml").resolve()
    text = pri.read_text(encoding="utf-8")
    pri.write_text(text.replace("overrides: {}", "overrides: {G9: 1}"), encoding="utf-8")
    errors, _ = validate_data(tmp_path / "data")
    assert errors == [f"{pri}: overrides names unknown guest 'G9'"]


def test_patrol_priorities_weight_the_patrol_capabilities():
    patrol = load_priorities("patrol")
    guests = {g: load_guest(GUESTS / f"{g}.yaml") for g in ("G1", "G3", "G4")}
    assert patrol.mission == "patrol"
    assert patrol.weight(guests["G1"]) > patrol.weight(guests["G4"]) > patrol.weight(guests["G3"])


def test_cli_ok(capsys):
    assert main(["validate"]) == 0
    assert capsys.readouterr().out.startswith("OK:")


def test_cli_reports_errors_and_fails(tmp_path, capsys):
    bad = edited(tmp_path, "G2", "initial_mode: profiling", "initial_mode: idle")
    assert main(["validate", str(GUESTS / "G1.yaml"), str(bad)]) == 1
    out = capsys.readouterr().out
    assert "initial_mode 'idle' is not a mode of G2" in out and "FAILED: 1 problem(s)" in out


def test_cli_version_still_works(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["--version"])
    assert exit_.value.code == 0 and capsys.readouterr().out.startswith("shipos ")
