import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

import config
from timetable.manager import TimetableManager, get_ttl, validate_response
from timetable.models import (
    SchoolYearRangeError,
    TimetableFetchError,
    TimetableValidationError,
)
from timetable.storage import CACHE_VERSION, JsonStore, format_timestamp

NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)
PAST = date(2026, 11, 9)
CURRENT = date(2026, 11, 16)
NEXT = date(2026, 11, 23)
NEAR = date(2026, 12, 7)
FAR = date(2027, 1, 4)


class FakeClient:
    def __init__(self, factory):
        self.factory = factory

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def timetable(self, timetable_date, week_start_date=None):
        if self.factory.delay:
            time.sleep(self.factory.delay)
        self.factory.calls.append(timetable_date)
        value = self.factory.responses[timetable_date].pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeClientFactory:
    def __init__(self):
        self.responses = defaultdict(list)
        self.calls = []
        self.delay = 0.0

    def queue(self, week, response):
        self.responses[week.isoformat()].append(response)

    def __call__(self):
        return FakeClient(self)


def response(week, slug_suffix="1", **extra):
    return {
        "schedule": [{"short_name": "1", "from": "08:00", "to": "08:45"}],
        "events": [
            {
                "slug": f"urnik${slug_suffix}",
                "type": "school_hour",
                "title": "FIL",
                "date": week.isoformat(),
                "from": "08:00",
                "to": "08:45",
                "teachers": ["Marko Levačić"],
                "classroom": "213",
                "subject": {"title": "Filozofija"},
                **extra,
            }
        ],
    }


@pytest.fixture
def env(tmp_path):
    store = JsonStore(tmp_path / "timetable")
    factory = FakeClientFactory()
    manager = TimetableManager(store=store, client_factory=factory, clock=lambda: NOW)
    return SimpleNamespace(manager=manager, store=store, factory=factory)


def save_record(store, week, data, fetched_at=NOW):
    record = dict(data)
    record["metadata"] = {
        "version": CACHE_VERSION,
        "week_start": week.isoformat(),
        "fetched_at": format_timestamp(fetched_at),
    }
    store.save_week(week, record)
    return record


@pytest.mark.parametrize(
    ("week", "expected"),
    [
        (PAST, None),
        (CURRENT, 3600),
        (NEXT, 10800),
        (date(2026, 11, 30), 43200),
        (date(2026, 12, 14), 43200),
        (date(2026, 12, 21), 86400),
    ],
)
def test_get_ttl(week, expected):
    assert get_ttl(week, CURRENT) == expected


def test_fresh_current_week_is_not_fetched(env):
    data = response(CURRENT)
    save_record(env.store, CURRENT, data, NOW - timedelta(minutes=30))

    result = env.manager.get_timetable(CURRENT, CURRENT)

    assert env.factory.calls == []
    assert result.events == data["events"]
    assert result.schedule == data["schedule"]
    assert result.source_weeks[CURRENT].stale is False
    assert result.stale_weeks == []


@pytest.mark.parametrize(
    ("week", "age_hours"),
    [(CURRENT, 2), (NEXT, 4), (NEAR, 13), (FAR, 25)],
)
def test_expired_week_is_refreshed(env, week, age_hours):
    save_record(
        env.store, week, response(week, "old"), NOW - timedelta(hours=age_hours)
    )
    env.factory.queue(week, response(week, "new"))

    result = env.manager.get_timetable(week, week)

    assert env.factory.calls == [week.isoformat()]
    assert result.events[0]["slug"] == "urnik$new"
    assert result.source_weeks[week].fetched_at == NOW
    assert result.source_weeks[week].stale is False


def test_past_week_with_cache_is_never_refreshed(env):
    data = response(PAST, "old")
    save_record(env.store, PAST, data, NOW - timedelta(days=365))

    result = env.manager.get_timetable(PAST, PAST)

    assert env.factory.calls == []
    assert result.events == data["events"]
    assert result.stale_weeks == []


def test_missing_past_week_is_fetched_once(env):
    env.factory.queue(PAST, response(PAST))

    first = env.manager.get_timetable(PAST, PAST)
    second = env.manager.get_timetable(PAST, PAST)

    assert env.factory.calls == [PAST.isoformat()]
    assert first.events == second.events


def test_failed_fetch_with_cache_returns_stale(env):
    data = response(CURRENT, "old")
    save_record(env.store, CURRENT, data, NOW - timedelta(hours=2))
    env.factory.queue(CURRENT, RuntimeError("simulated rate limit"))

    result = env.manager.get_timetable(CURRENT, CURRENT)

    assert env.factory.calls == [CURRENT.isoformat()]
    assert result.events == data["events"]
    assert result.stale_weeks == [CURRENT]
    assert result.source_weeks[CURRENT].stale is True
    assert env.store.load_week(CURRENT)["events"] == data["events"]


def test_failed_fetch_without_cache_raises(env):
    env.factory.queue(CURRENT, RuntimeError("simulated failure"))

    with pytest.raises(TimetableFetchError):
        env.manager.get_timetable(CURRENT, CURRENT)
    assert not env.store.week_exists(CURRENT)

    env.factory.queue(CURRENT, response(CURRENT))
    assert env.manager.get_timetable(CURRENT, CURRENT).events


def test_invalid_fetched_response_does_not_overwrite_cache(env):
    data = response(CURRENT, "old")
    stored = save_record(env.store, CURRENT, data, NOW - timedelta(hours=2))
    env.factory.queue(CURRENT, {"schedule": [], "events": "invalid"})

    result = env.manager.get_timetable(CURRENT, CURRENT)

    assert result.events == data["events"]
    assert result.source_weeks[CURRENT].stale is True
    assert env.store.load_week(CURRENT) == stored


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"events": []},
        {"schedule": [], "events": "invalid"},
        {"schedule": [], "events": [[]]},
        {"schedule": [], "events": [{"title": "FIL"}]},
        {
            "schedule": [],
            "events": [dict(response(CURRENT)["events"][0], date="2026-13-01")],
        },
        {
            "schedule": [],
            "events": [dict(response(CURRENT)["events"][0], **{"from": "25:00"})],
        },
    ],
)
def test_validate_response_rejects_invalid_payloads(payload):
    with pytest.raises(TimetableValidationError):
        validate_response(payload)


def test_validate_response_allows_extra_fields():
    payload = dict(response(CURRENT), future_field={"keep": True})
    assert validate_response(payload) is payload


def test_multiweek_aggregation(env):
    env.factory.queue(CURRENT, response(CURRENT, "current"))
    env.factory.queue(NEXT, response(NEXT, "next"))

    result = env.manager.get_timetable(date(2026, 11, 18), date(2026, 11, 25))

    assert env.factory.calls == [CURRENT.isoformat(), NEXT.isoformat()]
    assert [event["slug"] for event in result.events] == ["urnik$current", "urnik$next"]
    assert set(result.source_weeks) == {CURRENT, NEXT}
    assert result.stale_weeks == []


def test_stale_status_is_per_week(env):
    save_record(env.store, CURRENT, response(CURRENT), NOW - timedelta(hours=2))
    save_record(env.store, NEXT, response(NEXT), NOW - timedelta(hours=4))
    env.factory.queue(CURRENT, response(CURRENT, "refreshed"))
    env.factory.queue(NEXT, RuntimeError("simulated failure"))

    result = env.manager.get_timetable(CURRENT, NEXT)

    assert result.stale_weeks == [NEXT]
    assert result.source_weeks[CURRENT].stale is False
    assert result.source_weeks[NEXT].stale is True


def test_request_date_is_normalized_to_monday(env):
    env.factory.queue(CURRENT, response(CURRENT))
    env.manager.get_timetable(date(2026, 11, 18), date(2026, 11, 18))
    assert env.factory.calls == [CURRENT.isoformat()]


def test_school_year_validation_runs_before_fetching(env, monkeypatch):
    monkeypatch.setattr(config, "SCHOOL_YEAR_START", date(2026, 9, 1))
    monkeypatch.setattr(config, "SCHOOL_YEAR_END", date(2027, 6, 30))
    with pytest.raises(SchoolYearRangeError):
        env.manager.get_timetable(date(2026, 8, 30), date(2026, 9, 2))
    assert env.factory.calls == []


def test_unknown_response_fields_are_preserved(env):
    env.factory.queue(CURRENT, dict(response(CURRENT), custom={"keep": True}))
    env.manager.get_timetable(CURRENT, CURRENT)
    assert env.store.load_week(CURRENT)["custom"] == {"keep": True}


def test_concurrent_requests_for_same_week_fetch_once(env):
    env.factory.delay = 0.05
    env.factory.queue(CURRENT, response(CURRENT))
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        return env.manager.get_timetable(CURRENT, CURRENT)

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: worker(), range(6)))

    assert env.factory.calls == [CURRENT.isoformat()]
    assert all(result.events == results[0].events for result in results)


def test_different_weeks_can_be_fetched_concurrently(env):
    env.factory.delay = 0.05
    env.factory.queue(CURRENT, response(CURRENT))
    env.factory.queue(NEXT, response(NEXT))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(env.manager.get_timetable, week, week)
            for week in (CURRENT, NEXT)
        ]
        results = [future.result() for future in futures]

    assert set(env.factory.calls) == {CURRENT.isoformat(), NEXT.isoformat()}
    assert [result.events[0]["date"] for result in results] == [
        CURRENT.isoformat(),
        NEXT.isoformat(),
    ]
