from datetime import UTC, date, datetime

import pytest

import config
from timetable.models import (
    InvalidDateRangeError,
    SchoolYearRangeError,
    SourceWeek,
    TimetableError,
    TimetableFetchError,
    TimetableResult,
    TimetableValidationError,
    get_week_start,
    iter_week_starts,
    validate_requested_range,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 11, 30), date(2026, 11, 30)),
        (date(2026, 12, 1), date(2026, 11, 30)),
        (date(2026, 12, 6), date(2026, 11, 30)),
    ],
)
def test_get_week_start(value, expected):
    assert get_week_start(value) == expected


def test_iter_week_starts_for_single_day():
    assert list(iter_week_starts(date(2026, 12, 2), date(2026, 12, 2))) == [
        date(2026, 11, 30)
    ]


def test_iter_week_starts_for_multi_week_range():
    assert list(iter_week_starts(date(2026, 11, 30), date(2026, 12, 20))) == [
        date(2026, 11, 30),
        date(2026, 12, 7),
        date(2026, 12, 14),
    ]


def test_iter_week_starts_overlaps_partial_weeks():
    assert list(iter_week_starts(date(2026, 12, 6), date(2026, 12, 7))) == [
        date(2026, 11, 30),
        date(2026, 12, 7),
    ]


def test_validate_requested_range_accepts_school_year_boundaries(monkeypatch):
    monkeypatch.setattr(config, "SCHOOL_YEAR_START", date(2026, 9, 1))
    monkeypatch.setattr(config, "SCHOOL_YEAR_END", date(2027, 6, 30))
    validate_requested_range(date(2026, 9, 1), date(2027, 6, 30))
    validate_requested_range(date(2026, 11, 30), date(2026, 11, 30))


def test_validate_requested_range_rejects_reversed_range():
    with pytest.raises(InvalidDateRangeError):
        validate_requested_range(date(2026, 12, 8), date(2026, 12, 7))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 8, 31), date(2026, 9, 2)),
        (date(2027, 6, 29), date(2027, 7, 1)),
    ],
)
def test_validate_requested_range_rejects_dates_outside_school_year(start, end):
    with pytest.raises(SchoolYearRangeError):
        validate_requested_range(start, end)


def test_error_hierarchy():
    assert issubclass(TimetableFetchError, TimetableError)
    assert issubclass(TimetableValidationError, TimetableError)
    assert issubclass(SchoolYearRangeError, TimetableError)
    assert issubclass(InvalidDateRangeError, TimetableError)


def test_result_and_source_week_models():
    fetched_at = datetime(2026, 11, 22, 14, tzinfo=UTC)
    source = SourceWeek(fetched_at=fetched_at, stale=True)
    result = TimetableResult(
        events=[{"slug": "urnik$1"}],
        schedule=[{"short_name": "1"}],
        stale_weeks=[date(2026, 11, 30)],
        source_weeks={date(2026, 11, 30): source},
    )
    assert result.source_weeks[date(2026, 11, 30)].stale is True
    assert result.source_weeks[date(2026, 11, 30)].fetched_at == fetched_at
    assert TimetableResult().events == []
