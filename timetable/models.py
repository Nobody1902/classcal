from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import config


class TimetableError(Exception):
    pass


class TimetableFetchError(TimetableError):
    pass


class TimetableValidationError(TimetableError):
    pass


class SchoolYearRangeError(TimetableError):
    pass


class InvalidDateRangeError(TimetableError):
    pass


@dataclass(frozen=True)
class SourceWeek:
    fetched_at: datetime
    stale: bool = False


@dataclass
class TimetableResult:
    events: list[dict] = field(default_factory=list)
    schedule: list[dict] = field(default_factory=list)
    stale_weeks: list[date] = field(default_factory=list)
    source_weeks: dict[date, SourceWeek] = field(default_factory=dict)


def get_week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def iter_week_starts(start: date, end: date) -> Iterator[date]:
    current = get_week_start(start)
    last = get_week_start(end)
    while current <= last:
        yield current
        current += timedelta(days=7)


def validate_requested_range(start: date, end: date) -> None:
    if start > end:
        raise InvalidDateRangeError("start must not be after end")
    if start < config.SCHOOL_YEAR_START or end > config.SCHOOL_YEAR_END:
        raise SchoolYearRangeError(
            "date range must be within "
            f"{config.SCHOOL_YEAR_START} and {config.SCHOOL_YEAR_END}"
        )
