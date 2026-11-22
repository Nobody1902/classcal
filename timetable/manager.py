import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Protocol, Self

import config
from scraper import EasistentClient
from timetable.models import (
    SourceWeek,
    TimetableFetchError,
    TimetableResult,
    TimetableValidationError,
    get_week_start,
    iter_week_starts,
    validate_requested_range,
)
from timetable.storage import (
    CACHE_VERSION,
    JsonStore,
    format_timestamp,
    parse_timestamp,
)

logger = logging.getLogger(__name__)

REQUIRED_EVENT_FIELDS = ("slug", "title", "date", "from", "to")


class EasistentClientLike(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args) -> None: ...

    def timetable(
        self, timetable_date: str, week_start_date: str | None = None
    ) -> dict: ...


def _default_clock() -> datetime:
    return datetime.now(tz=config.TIMEZONE)


def _default_client_factory() -> EasistentClientLike:
    username, password = config.get_credentials()
    return EasistentClient(username, password)


def get_ttl(week_start: date, current_week: date) -> int | None:
    delta = (week_start - current_week).days // 7
    if delta < 0:
        return None
    if delta == 0:
        return config.CACHE_TTL_CURRENT
    if delta == 1:
        return config.CACHE_TTL_NEXT
    if delta <= 4:
        return config.CACHE_TTL_NEAR_FUTURE
    return config.CACHE_TTL_FUTURE


def validate_response(data: object) -> dict:
    if not isinstance(data, dict):
        raise TimetableValidationError("timetable response is not an object")
    if not isinstance(data.get("schedule"), list):
        raise TimetableValidationError("timetable response has no schedule list")
    if not isinstance(data.get("events"), list):
        raise TimetableValidationError("timetable response has no events list")

    for event in data["events"]:
        if not isinstance(event, dict):
            raise TimetableValidationError("timetable event is not an object")
        for field in REQUIRED_EVENT_FIELDS:
            if not isinstance(event.get(field), str) or not event[field]:
                raise TimetableValidationError(f"timetable event has no valid {field}")
        try:
            date.fromisoformat(event["date"])
            time.fromisoformat(event["from"])
            time.fromisoformat(event["to"])
        except ValueError as exc:
            raise TimetableValidationError(
                "timetable event has invalid date or time"
            ) from exc

    return data


class TimetableManager:
    def __init__(
        self,
        store: JsonStore | None = None,
        client_factory: Callable[[], EasistentClientLike] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store or JsonStore()
        self._client_factory = client_factory or _default_client_factory
        self._clock = clock or _default_clock
        self._locks: dict[date, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def get_timetable(self, start: date, end: date) -> TimetableResult:
        validate_requested_range(start, end)
        now = self._clock()
        events: list[dict] = []
        schedule: list[dict] = []
        stale_weeks: list[date] = []
        source_weeks: dict[date, SourceWeek] = {}

        for week_start in iter_week_starts(start, end):
            record, stale = self._resolve_week(week_start, now)
            events.extend(record["events"])
            if not schedule:
                schedule = record["schedule"]
            source_weeks[week_start] = SourceWeek(
                fetched_at=parse_timestamp(record["metadata"]["fetched_at"]),
                stale=stale,
            )
            if stale:
                stale_weeks.append(week_start)

        return TimetableResult(
            events=events,
            schedule=schedule,
            stale_weeks=stale_weeks,
            source_weeks=source_weeks,
        )

    def _lock_for(self, week_start: date) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(week_start, threading.Lock())

    def _is_expired(self, record: dict, week_start: date, now: datetime) -> bool:
        ttl = get_ttl(week_start, get_week_start(now.date()))
        if ttl is None:
            return False
        fetched_at = parse_timestamp(record["metadata"]["fetched_at"])
        return now >= fetched_at + timedelta(seconds=ttl)

    def _fetch_week(self, week_start: date) -> dict:
        with self._client_factory() as client:
            data = client.timetable(week_start.isoformat())
        return validate_response(data)

    def _resolve_week(self, week_start: date, now: datetime) -> tuple[dict, bool]:
        with self._lock_for(week_start):
            record = self.store.load_week(week_start)
            if record is not None and not self._is_expired(record, week_start, now):
                logger.info("Using cached timetable for week %s", week_start)
                return record, False

            if record is None:
                logger.info("Fetching missing timetable for week %s", week_start)
            else:
                logger.info("Refreshing expired timetable for week %s", week_start)

            try:
                data = self._fetch_week(week_start)
                fetched_at = self._clock()
                new_record = dict(data)
                new_record["metadata"] = {
                    "version": CACHE_VERSION,
                    "week_start": week_start.isoformat(),
                    "fetched_at": format_timestamp(fetched_at),
                }
                self.store.save_week(week_start, new_record)
            except Exception as exc:
                logger.warning(
                    "Timetable fetch failed for week %s (%s)",
                    week_start,
                    type(exc).__name__,
                )
                if record is None:
                    raise TimetableFetchError(
                        f"No timetable data available for week {week_start}"
                    ) from exc
                logger.warning("Serving stale timetable for week %s", week_start)
                return record, True

            logger.info("Saved fresh timetable for week %s", week_start)
            return new_record, False
