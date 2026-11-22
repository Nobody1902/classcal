from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from api.service import CalendarService
from ical.cache import CalendarCache, CalendarData
from main import app
from timetable.models import (
    SchoolYearRangeError,
    SourceWeek,
    TimetableFetchError,
    TimetableResult,
)

START = date(2026, 11, 23)
END = date(2026, 12, 20)
ICS = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"


class FakeCalendarService:
    def __init__(self, data=None, error=None):
        self.data = data or CalendarData(content=ICS, etag='"abc123"')
        self.error = error
        self.calls = []

    def get_calendar(self, start, end):
        self.calls.append((start, end))
        if self.error:
            raise self.error
        return self.data


class FakeManager:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def get_timetable(self, start, end):
        self.calls.append((start, end))
        return self.result


class CountingGenerator:
    def __init__(self):
        self.calls = 0

    def generate(self, result, start, end):
        self.calls += 1
        return ICS


@pytest.fixture
def client():
    service = FakeCalendarService()
    original = app.state.calendar_service
    app.state.calendar_service = service
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client, service
    finally:
        app.state.calendar_service = original


def request_calendar(client):
    return client.get(
        "/calendar.ics", params={"start": START.isoformat(), "end": END.isoformat()}
    )


def test_successful_calendar_response(client):
    test_client, service = client
    response = request_calendar(test_client)

    assert response.status_code == 200
    assert response.content == ICS
    assert response.headers["content-type"].startswith("text/calendar")
    assert "charset=utf-8" in response.headers["content-type"]
    assert response.headers["etag"] == '"abc123"'
    assert response.headers["cache-control"] == "no-cache"
    assert service.calls == [(START, END)]


def test_invalid_dates_return_422(client):
    test_client, service = client
    response = test_client.get(
        "/calendar.ics", params={"start": "not-a-date", "end": END.isoformat()}
    )
    assert response.status_code == 422
    assert service.calls == []


def test_missing_parameters_return_422(client):
    test_client, service = client
    assert (
        test_client.get(
            "/calendar.ics", params={"start": START.isoformat()}
        ).status_code
        == 422
    )
    assert service.calls == []


def test_reversed_range_returns_422(client):
    test_client, service = client
    response = test_client.get(
        "/calendar.ics", params={"start": END.isoformat(), "end": START.isoformat()}
    )
    assert response.status_code == 422
    assert service.calls == []


def test_outside_school_year_returns_400(client):
    test_client, service = client
    service.error = SchoolYearRangeError("date range must be within the school year")
    assert request_calendar(test_client).status_code == 400


def test_unavailable_timetable_returns_503(client):
    test_client, service = client
    service.error = TimetableFetchError("no data")
    response = request_calendar(test_client)
    assert response.status_code == 503
    assert response.json()["detail"] == "Timetable data is unavailable"


def test_matching_etag_returns_304(client):
    test_client, service = client
    response = test_client.get(
        "/calendar.ics",
        params={"start": START.isoformat(), "end": END.isoformat()},
        headers={"If-None-Match": '"abc123"'},
    )
    assert response.status_code == 304
    assert response.headers["etag"] == '"abc123"'
    assert response.content == b""
    assert len(service.calls) == 1


def test_nonmatching_etag_returns_200(client):
    test_client, _ = client
    response = test_client.get(
        "/calendar.ics",
        params={"start": START.isoformat(), "end": END.isoformat()},
        headers={"If-None-Match": '"different"'},
    )
    assert response.status_code == 200


def test_unexpected_error_returns_generic_500(client):
    test_client, service = client
    service.error = RuntimeError("secret internal detail")
    response = request_calendar(test_client)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


def test_service_always_consults_manager_before_cache(tmp_path):
    result = TimetableResult(
        source_weeks={
            START: SourceWeek(fetched_at=datetime(2026, 11, 22, 14, tzinfo=UTC))
        }
    )
    manager = FakeManager(result)
    generator = CountingGenerator()
    service = CalendarService(
        manager=manager, cache=CalendarCache(directory=tmp_path, generator=generator)
    )
    original = app.state.calendar_service
    app.state.calendar_service = service
    try:
        with TestClient(app) as test_client:
            first = request_calendar(test_client)
            second = test_client.get(
                "/calendar.ics",
                params={"start": START.isoformat(), "end": END.isoformat()},
                headers={"If-None-Match": first.headers["etag"]},
            )
    finally:
        app.state.calendar_service = original

    assert first.status_code == 200
    assert second.status_code == 304
    assert generator.calls == 1
    assert len(manager.calls) == 2
