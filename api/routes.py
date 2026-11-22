from datetime import date

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from api.service import CalendarService
from timetable.models import (
    InvalidDateRangeError,
    SchoolYearRangeError,
    TimetableFetchError,
)

router = APIRouter()


def get_calendar_service(request: Request) -> CalendarService:
    return request.app.state.calendar_service


@router.get("/calendar.ics")
def get_calendar(
    start: date,
    end: date,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
):
    if start > end:
        raise HTTPException(status_code=422, detail="Invalid date range")

    service = get_calendar_service(request)
    try:
        data = service.get_calendar(start, end)
    except (InvalidDateRangeError, SchoolYearRangeError) as exc:
        raise HTTPException(
            status_code=422 if isinstance(exc, InvalidDateRangeError) else 400,
            detail=str(exc),
        ) from exc
    except TimetableFetchError as exc:
        raise HTTPException(
            status_code=503, detail="Timetable data is unavailable"
        ) from exc

    if if_none_match == data.etag:
        return Response(status_code=304, headers={"ETag": data.etag})

    return Response(
        content=data.content,
        media_type="text/calendar",
        headers={"ETag": data.etag, "Cache-Control": "no-cache"},
    )
