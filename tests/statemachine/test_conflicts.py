from shipos.statemachine import conflicts


def test_survey_vs_drone_recovery_conflict(guests):
    survey = guests["G3"].mode("surveying").conditions
    recovery = guests["G4"].mode("recovery")
    found = conflicts(survey, recovery.conditions, b_deck_machinery=recovery.deck_machinery)
    assert set(found) == {"speed_kn", "course", "noise", "deck_machinery"}  # hangar doors


def test_winch_vs_listening_vehicle(guests):
    assert "deck_machinery" in conflicts(
        guests["G3"].mode("surveying").conditions,
        None,
        b_deck_machinery=guests["G2"].mode("winch").deck_machinery,
    )


def test_flight_deck_and_ramp_exclusive(guests):
    assert "flight_deck_vs_ramp" in conflicts(
        guests["G4"].mode("recovery").conditions, guests["G3"].mode("recovering").conditions
    )


def test_compatible_conditions(guests):
    assert (
        conflicts(guests["G5"].mode("on").conditions, guests["G4"].mode("recovery").conditions)
        == []
    )
