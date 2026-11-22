import os
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent
_ENV_FILE = dotenv_values(PROJECT_ROOT / ".env")


def _env(name: str) -> str | None:
    return os.getenv(name) or _ENV_FILE.get(name)


def _date_setting(name: str, default: date) -> date:
    value = _env(name)
    return date.fromisoformat(value) if value else default


def _positive_int_setting(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _path_setting(name: str, default: str) -> Path:
    path = Path(_env(name) or default)
    return path if path.is_absolute() else PROJECT_ROOT / path


TIMEZONE_NAME = _env("CLASSCAL_TIMEZONE") or "Europe/Ljubljana"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

SCHOOL_YEAR_START = _date_setting("CLASSCAL_SCHOOL_YEAR_START", date(2026, 9, 1))
SCHOOL_YEAR_END = _date_setting("CLASSCAL_SCHOOL_YEAR_END", date(2027, 6, 30))

DATA_DIR = _path_setting("CLASSCAL_DATA_DIR", "data")
TIMETABLE_DIR = _path_setting("CLASSCAL_TIMETABLE_DIR", "data/timetable")
CALENDAR_DIR = _path_setting("CLASSCAL_CALENDAR_DIR", "data/calendars")

CACHE_TTL_CURRENT = _positive_int_setting("CLASSCAL_CACHE_TTL_CURRENT", 60 * 60)
CACHE_TTL_NEXT = _positive_int_setting("CLASSCAL_CACHE_TTL_NEXT", 3 * 60 * 60)
CACHE_TTL_NEAR_FUTURE = _positive_int_setting(
    "CLASSCAL_CACHE_TTL_NEAR_FUTURE", 12 * 60 * 60
)
CACHE_TTL_FUTURE = _positive_int_setting("CLASSCAL_CACHE_TTL_FUTURE", 24 * 60 * 60)
CACHE_TTL_PAST = None

ICAL_PRODID = _env("CLASSCAL_ICAL_PRODID") or "-//Easistent Timetable//EN"
ICAL_VERSION = "2.0"


def get_credentials() -> tuple[str, str]:
    username = _env("CLASSCAL_USERNAME") or _env("USERNAME")
    password = _env("CLASSCAL_PASSWORD") or _env("PASSWORD")
    if not username or not password:
        raise RuntimeError("Easistent credentials are missing from the environment")
    return username, password
