from datetime import date

from icalendar import Calendar

import config
from ical.generator import STALE_EVENT_NOTE, STALE_WARNING, CalendarGenerator
from timetable.models import TimetableResult

START = date(2026, 11, 30)
END = date(2026, 12, 6)


def make_event(slug="urnik$1", event_date="2026-11-30", **extra):
    return {
        "slug": slug,
        "title": "Filozofija",
        "date": event_date,
        "from": "08:00",
        "to": "08:45",
        "classroom": "213",
        "teachers": ["Marko Levačić"],
        "subject": {"title": "Filozofija"},
        "color": "#b03136",
        **extra,
    }


def generate(events, start=START, end=END, stale_weeks=()):
    result = TimetableResult(events=events, stale_weeks=list(stale_weeks))
    return CalendarGenerator().generate(result, start, end)


def parse(events, **kwargs):
    return Calendar.from_ical(generate(events, **kwargs))


def test_calendar_properties_and_vtimezone():
    calendar = parse([make_event()])

    assert str(calendar["PRODID"]) == config.ICAL_PRODID
    assert str(calendar["VERSION"]) == config.ICAL_VERSION
    assert str(calendar["CALSCALE"]) == "GREGORIAN"
    assert str(calendar["METHOD"]) == "PUBLISH"
    timezones = calendar.walk("VTIMEZONE")
    assert len(timezones) == 1
    assert str(timezones[0]["TZID"]) == "Europe/Ljubljana"


def test_event_mapping_with_stable_uid():
    event = parse([make_event()]).walk("VEVENT")[0]

    assert str(event["UID"]) == "urnik$1@easistent"
    assert str(event["SUMMARY"]) == "Filozofija"
    assert str(event["LOCATION"]) == "213"
    assert str(event["COLOR"]) == "#b03136"
    description = str(event["DESCRIPTION"])
    assert "Marko Levačić" in description
    assert "Filozofija" in description

    dtstart = event.decoded("DTSTART")
    dtend = event.decoded("DTEND")
    assert (dtstart.hour, dtstart.minute) == (8, 0)
    assert (dtend.hour, dtend.minute) == (8, 45)
    assert str(dtstart.tzinfo) == "Europe/Ljubljana"
    assert str(dtend.tzinfo) == "Europe/Ljubljana"


def test_optional_description_fields():
    event = parse(
        [
            make_event(
                lesson="Odprti pouk", type_labels=[{"type_label": "NI BILO POUKA"}]
            )
        ]
    ).walk("VEVENT")[0]
    description = str(event["DESCRIPTION"])
    assert "Odprti pouk" in description
    assert "NI BILO POUKA" in description


def test_missing_optional_fields_are_omitted():
    event = parse(
        [
            {
                "slug": "urnik$2",
                "title": "Dogodek",
                "date": "2026-11-30",
                "from": "09:00",
                "to": "10:00",
            }
        ]
    ).walk("VEVENT")[0]
    assert "LOCATION" not in event
    assert "COLOR" not in event
    assert "DESCRIPTION" not in event


def test_events_outside_requested_range_are_excluded():
    calendar = parse(
        [
            make_event("urnik$before", "2026-11-29"),
            make_event("urnik$first", "2026-11-30"),
            make_event("urnik$last", "2026-12-06"),
            make_event("urnik$after", "2026-12-07"),
        ]
    )
    uids = [str(event["UID"]) for event in calendar.walk("VEVENT")]
    assert uids == ["urnik$first@easistent", "urnik$last@easistent"]


def test_duplicates_are_removed_by_uid_only():
    calendar = parse(
        [
            make_event("urnik$1"),
            make_event("urnik$1"),
            make_event("urnik$2"),
        ]
    )
    uids = [str(event["UID"]) for event in calendar.walk("VEVENT")]
    assert uids == ["urnik$1@easistent", "urnik$2@easistent"]


def test_no_stale_warning_when_all_weeks_fresh():
    calendar = parse([make_event()])
    assert "DESCRIPTION" not in calendar
    assert "X-TIMETABLE-STALE" not in calendar
    assert "X-TIMETABLE-STALE-WEEKS" not in calendar


def test_stale_warning_disappears_after_refresh():
    events = [
        make_event("urnik$first", "2026-11-30"),
        make_event("urnik$second", "2026-12-07"),
    ]
    stale = parse(events, end=date(2026, 12, 13), stale_weeks=[date(2026, 11, 30)])

    assert STALE_WARNING in str(stale["DESCRIPTION"])
    assert str(stale["X-TIMETABLE-STALE"]) == "TRUE"
    assert "2026-11-30" in str(stale["X-TIMETABLE-STALE-WEEKS"])
    affected, unaffected = stale.walk("VEVENT")
    assert STALE_EVENT_NOTE in str(affected["DESCRIPTION"])
    assert STALE_EVENT_NOTE not in str(unaffected["DESCRIPTION"])

    fresh = parse(events, end=date(2026, 12, 13))
    assert "X-TIMETABLE-STALE" not in fresh
    assert "DESCRIPTION" not in fresh
    assert generate(events, end=date(2026, 12, 13)) != generate(
        events, end=date(2026, 12, 13), stale_weeks=[date(2026, 11, 30)]
    )


def test_output_is_deterministic_and_unicode_is_preserved():
    events = [make_event()]
    first = generate(events)
    assert first == generate(events)
    assert "Marko Levačić".encode() in first


def test_holiday_events_are_omitted():
    holiday = make_event("solski_koledar$1", title="praznik dela", type="holiday")
    calendar = parse([make_event("urnik$1"), holiday])

    uids = [str(event["UID"]) for event in calendar.walk("VEVENT")]
    assert uids == ["urnik$1@easistent"]


def test_exam_events_get_prefix_category_and_details():
    exam = make_event(
        evaluation={
            "title": "1.test",
            "summary": "Use of English",
            "grade_type": "Pisna ocena",
            "grade_color": "#ff0000",
        }
    )
    event = parse([exam]).walk("VEVENT")[0]
    description = str(event["DESCRIPTION"])

    assert str(event["SUMMARY"]) == "✱ Filozofija"
    assert event.decoded("CATEGORIES") == ["EXAM"]
    assert str(event["COLOR"]) == "#b03136"
    assert "Ocenjevanje: 1.test (Pisna ocena)" in description
    assert "Use of English" in description


def test_non_exam_events_have_no_exam_category():
    event = parse([make_event()]).walk("VEVENT")[0]
    assert "CATEGORIES" not in event
    assert str(event["COLOR"]) == "#b03136"


def test_exam_without_summary_gets_prefix_and_evaluation_line():
    exam = make_event(
        evaluation={"title": "Ustno spraševanje", "grade_type": "Ustna ocena"}
    )
    event = parse([exam]).walk("VEVENT")[0]

    assert str(event["SUMMARY"]) == "✱ Filozofija"
    assert "Ocenjevanje: Ustno spraševanje (Ustna ocena)" in str(event["DESCRIPTION"])
