from datetime import date, datetime, time, timedelta

from icalendar import Calendar, Event, Timezone

import config
from timetable.models import TimetableResult, get_week_start

GENERATOR_VERSION = 4

EXAM_CATEGORY = "EXAM"
EXAM_SUMMARY_PREFIX = "✱ "

STALE_WARNING = (
    "Some timetable data is stale because the source website "
    "could not be reached. The affected weeks may contain outdated information."
)
STALE_EVENT_NOTE = (
    "Stale timetable data: the source could not be reached for this week."
)


class CalendarGenerator:
    def generate(self, result: TimetableResult, start: date, end: date) -> bytes:
        calendar = Calendar()
        calendar.add("prodid", config.ICAL_PRODID)
        calendar.add("version", config.ICAL_VERSION)
        calendar.add("calscale", "GREGORIAN")
        calendar.add("method", "PUBLISH")
        calendar.add_component(
            Timezone.from_tzinfo(
                config.TIMEZONE,
                first_date=config.SCHOOL_YEAR_START,
                last_date=config.SCHOOL_YEAR_END + timedelta(days=1),
            )
        )

        if result.stale_weeks:
            calendar["DESCRIPTION"] = STALE_WARNING
            calendar["X-TIMETABLE-STALE"] = "TRUE"
            calendar["X-TIMETABLE-STALE-WEEKS"] = ",".join(
                week.isoformat() for week in sorted(result.stale_weeks)
            )

        seen_uids: set[str] = set()
        for event in result.events:
            if event.get("type") == "holiday":
                continue

            event_date = date.fromisoformat(event["date"])
            if not start <= event_date <= end:
                continue

            uid = f"{event['slug']}@easistent"
            if uid in seen_uids:
                continue
            seen_uids.add(uid)

            stale = get_week_start(event_date) in result.stale_weeks
            calendar.add_component(self._build_event(event, event_date, uid, stale))

        return calendar.to_ical()

    @staticmethod
    def _build_event(event: dict, event_date: date, uid: str, stale: bool) -> Event:
        component = Event()
        component["UID"] = uid
        component["SUMMARY"] = _build_summary(event)
        component.add(
            "DTSTART",
            datetime.combine(
                event_date, time.fromisoformat(event["from"]), tzinfo=config.TIMEZONE
            ),
        )
        component.add(
            "DTEND",
            datetime.combine(
                event_date, time.fromisoformat(event["to"]), tzinfo=config.TIMEZONE
            ),
        )

        if event.get("classroom"):
            component["LOCATION"] = event["classroom"]

        if _is_exam(event):
            component.add("categories", EXAM_CATEGORY)
        if color := event.get("color"):
            component["COLOR"] = color

        description = _build_description(event, stale)
        if description:
            component["DESCRIPTION"] = description

        return component


def _is_exam(event: dict) -> bool:
    return isinstance(event.get("evaluation"), dict)


def _build_summary(event: dict) -> str:
    title = event["title"]
    return f"{EXAM_SUMMARY_PREFIX}{title}" if _is_exam(event) else title


def _build_description(event: dict, stale: bool) -> str:
    lines: list[str] = []
    evaluation = event.get("evaluation")
    if isinstance(evaluation, dict):
        exam_line = "Ocenjevanje"
        if title := evaluation.get("title"):
            exam_line += f": {title}"
        if grade_type := evaluation.get("grade_type"):
            exam_line += f" ({grade_type})"
        lines.append(exam_line)
        if summary := evaluation.get("summary"):
            lines.append(summary)
    if teachers := event.get("teachers"):
        lines.append(", ".join(teachers))
    subject = event.get("subject")
    if isinstance(subject, dict) and subject.get("title"):
        lines.append(subject["title"])
    if lesson := event.get("lesson"):
        lines.append(str(lesson))
    for label in event.get("type_labels", []):
        if isinstance(label, dict) and label.get("type_label"):
            lines.append(label["type_label"])
    if stale:
        lines.append(STALE_EVENT_NOTE)
    return "\n".join(lines)
