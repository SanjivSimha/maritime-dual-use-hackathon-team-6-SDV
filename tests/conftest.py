import pytest

from shipos.data import load_envelope, load_guests, load_priorities, load_ship


@pytest.fixture(scope="session")
def ship():
    return load_ship()


@pytest.fixture(scope="session")
def guests():
    return load_guests()


@pytest.fixture(scope="session")
def survey():
    return load_priorities("survey_primary")


@pytest.fixture(scope="session")
def sar():
    return load_priorities("sar_primary")


@pytest.fixture(scope="session")
def envelope():
    return load_envelope()
