from datetime import date
from typing import Protocol

from ical.cache import CalendarCache, CalendarData
from timetable.manager import TimetableManager
from timetable.models import TimetableResult


class TimetableManagerLike(Protocol):
    def get_timetable(self, start: date, end: date) -> TimetableResult: ...


class CalendarCacheLike(Protocol):
    def get_calendar(
        self, start: date, end: date, result: TimetableResult
    ) -> CalendarData: ...


class CalendarService:
    def __init__(
        self,
        manager: TimetableManagerLike | None = None,
        cache: CalendarCacheLike | None = None,
    ):
        self.manager = manager or TimetableManager()
        self.cache = cache or CalendarCache()

    def get_calendar(self, start: date, end: date) -> CalendarData:
        result = self.manager.get_timetable(start, end)
        return self.cache.get_calendar(start, end, result)
