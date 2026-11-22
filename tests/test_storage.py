import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from timetable.storage import (
    CACHE_VERSION,
    JsonStore,
    format_timestamp,
    parse_timestamp,
)

WEEK = date(2026, 11, 30)
FETCHED_AT = "2026-11-22T14:00:00+01:00"


_DEFAULT = object()


@pytest.fixture
def store(tmp_path):
    return JsonStore(tmp_path / "timetable")


def make_record(
    week=WEEK,
    fetched_at=FETCHED_AT,
    schedule=_DEFAULT,
    events=_DEFAULT,
    metadata=_DEFAULT,
    **extra,
):
    return {
        "metadata": {
            "version": CACHE_VERSION,
            "week_start": week.isoformat(),
            "fetched_at": fetched_at,
        }
        if metadata is _DEFAULT
        else metadata,
        "schedule": [{"short_name": "1", "from": "08:00"}]
        if schedule is _DEFAULT
        else schedule,
        "events": [{"slug": "urnik$1", "title": "Filozofija"}]
        if events is _DEFAULT
        else events,
        **extra,
    }


def test_save_and_load_roundtrip_preserves_unicode_and_extra_fields(store):
    record = make_record(
        events=[{"slug": "urnik$1", "title": "Filozofija — Žužana Zajtl"}],
        unknown_field={"keep": True},
    )
    store.save_week(WEEK, record)

    assert store.load_week(WEEK) == record
    raw = (store.directory / f"{WEEK.isoformat()}.json").read_text(encoding="utf-8")
    assert "Žužana Zajtl" in raw
    assert json.loads(raw)["unknown_field"] == {"keep": True}


def test_save_uses_monday_filename_and_creates_directory(store):
    assert not store.directory.exists()
    store.save_week(WEEK, make_record())
    assert sorted(p.name for p in store.directory.iterdir()) == ["2026-11-30.json"]


def test_save_replaces_existing_file(store):
    store.save_week(WEEK, make_record(events=[{"slug": "old"}]))
    store.save_week(WEEK, make_record(events=[{"slug": "new"}]))
    assert store.load_week(WEEK)["events"] == [{"slug": "new"}]


def test_failed_replace_leaves_original_file_and_no_temp_files(store, monkeypatch):
    store.save_week(WEEK, make_record(events=[{"slug": "old"}]))
    path = store.directory / f"{WEEK.isoformat()}.json"
    original = path.read_text(encoding="utf-8")

    def fail_replace(self, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        store.save_week(WEEK, make_record(events=[{"slug": "new"}]))

    assert path.read_text(encoding="utf-8") == original
    assert sorted(p.name for p in store.directory.iterdir()) == [path.name]


def test_missing_file_returns_none(store):
    assert store.load_week(WEEK) is None
    assert store.week_exists(WEEK) is False


def test_week_exists_and_delete_week(store):
    store.save_week(WEEK, make_record())
    assert store.week_exists(WEEK) is True
    store.delete_week(WEEK)
    assert store.week_exists(WEEK) is False
    store.delete_week(WEEK)


def test_corrupted_json_is_quarantined(store):
    path = store.directory / f"{WEEK.isoformat()}.json"
    store.directory.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert store.load_week(WEEK) is None
    assert not path.exists()
    preserved = list(store.directory.glob("*.corrupt-invalid-json-*.json"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    "record",
    [
        [],
        {"metadata": None, "schedule": [], "events": []},
        make_record(
            metadata={
                "version": 2,
                "week_start": WEEK.isoformat(),
                "fetched_at": FETCHED_AT,
            }
        ),
        make_record(
            metadata={
                "version": CACHE_VERSION,
                "week_start": "2026-12-07",
                "fetched_at": FETCHED_AT,
            }
        ),
        make_record(fetched_at="2026-11-22T14:00:00"),
        make_record(schedule={}),
        make_record(events=None),
    ],
)
def test_invalid_records_are_quarantined(store, record):
    store.directory.mkdir(parents=True, exist_ok=True)
    (store.directory / f"{WEEK.isoformat()}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    assert store.load_week(WEEK) is None
    preserved = list(store.directory.glob("*.corrupt-invalid-structure-*.json"))
    assert len(preserved) == 1


def test_timestamp_helpers_roundtrip_with_offset():
    original = datetime(2026, 11, 22, 14, tzinfo=UTC)
    assert parse_timestamp(format_timestamp(original)) == original
