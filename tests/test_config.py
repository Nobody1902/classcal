import importlib

import pytest

import config

SETTING_NAMES = (
    "CLASSCAL_TIMEZONE",
    "CLASSCAL_SCHOOL_YEAR_START",
    "CLASSCAL_SCHOOL_YEAR_END",
    "CLASSCAL_DATA_DIR",
    "CLASSCAL_TIMETABLE_DIR",
    "CLASSCAL_CALENDAR_DIR",
    "CLASSCAL_CACHE_TTL_CURRENT",
    "CLASSCAL_CACHE_TTL_NEXT",
    "CLASSCAL_CACHE_TTL_NEAR_FUTURE",
    "CLASSCAL_CACHE_TTL_FUTURE",
    "CLASSCAL_ICAL_PRODID",
    "CLASSCAL_USERNAME",
    "CLASSCAL_PASSWORD",
)


@pytest.fixture
def isolated_config(monkeypatch):
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("dotenv.dotenv_values", lambda *args, **kwargs: {})
    module = importlib.reload(config)
    yield module
    monkeypatch.undo()
    importlib.reload(config)


def test_design_defaults(isolated_config):
    assert isolated_config.TIMEZONE_NAME == "Europe/Ljubljana"
    assert str(isolated_config.TIMEZONE) == "Europe/Ljubljana"
    assert isolated_config.SCHOOL_YEAR_START.isoformat() == "2026-09-01"
    assert isolated_config.SCHOOL_YEAR_END.isoformat() == "2027-06-30"
    assert (
        isolated_config.TIMETABLE_DIR == isolated_config.PROJECT_ROOT / "data/timetable"
    )
    assert (
        isolated_config.CALENDAR_DIR == isolated_config.PROJECT_ROOT / "data/calendars"
    )
    assert isolated_config.CACHE_TTL_CURRENT == 3600
    assert isolated_config.CACHE_TTL_NEXT == 10800
    assert isolated_config.CACHE_TTL_NEAR_FUTURE == 43200
    assert isolated_config.CACHE_TTL_FUTURE == 86400
    assert isolated_config.CACHE_TTL_PAST is None
    assert isolated_config.ICAL_PRODID == "-//Easistent Timetable//EN"
    assert isolated_config.ICAL_VERSION == "2.0"


def test_env_overrides(isolated_config, monkeypatch):
    monkeypatch.setenv("CLASSCAL_SCHOOL_YEAR_START", "2025-09-01")
    monkeypatch.setenv("CLASSCAL_CACHE_TTL_CURRENT", "120")
    monkeypatch.setenv("CLASSCAL_DATA_DIR", "custom-data")
    monkeypatch.setenv("CLASSCAL_ICAL_PRODID", "-//Custom//EN")

    module = importlib.reload(isolated_config)
    assert module.SCHOOL_YEAR_START.isoformat() == "2025-09-01"
    assert module.CACHE_TTL_CURRENT == 120
    assert module.DATA_DIR == module.PROJECT_ROOT / "custom-data"
    assert module.ICAL_PRODID == "-//Custom//EN"


def test_absolute_path_override_is_preserved(isolated_config, monkeypatch, tmp_path):
    monkeypatch.setenv("CLASSCAL_TIMETABLE_DIR", str(tmp_path / "weeks"))
    module = importlib.reload(isolated_config)
    assert module.TIMETABLE_DIR == tmp_path / "weeks"


def test_invalid_ttl_rejected(isolated_config, monkeypatch):
    monkeypatch.setenv("CLASSCAL_CACHE_TTL_NEXT", "0")
    with pytest.raises(ValueError, match="positive integer"):
        importlib.reload(isolated_config)


def test_credentials_from_env_file(isolated_config):
    isolated_config._ENV_FILE = {"USERNAME": "file-user", "PASSWORD": "file-pass"}
    assert isolated_config.get_credentials() == ("file-user", "file-pass")


def test_credentials_override_env_file(isolated_config, monkeypatch):
    isolated_config._ENV_FILE = {"USERNAME": "file-user", "PASSWORD": "file-pass"}
    monkeypatch.setenv("CLASSCAL_USERNAME", "env-user")
    monkeypatch.setenv("CLASSCAL_PASSWORD", "env-pass")
    assert isolated_config.get_credentials() == ("env-user", "env-pass")


def test_missing_credentials_raise(isolated_config):
    with pytest.raises(RuntimeError, match="credentials"):
        isolated_config.get_credentials()
