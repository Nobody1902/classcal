import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import ical.cache as cache_module
from ical.cache import CalendarCache, build_fingerprint, cache_key
from timetable.models import SourceWeek, TimetableResult

START = date(2026, 11, 30)
END = date(2026, 12, 6)
WEEK = date(2026, 11, 30)
FIXED_NOW = datetime(2026, 11, 22, 14, tzinfo=UTC)


class CountingGenerator:
    def __init__(self):
        self.calls = 0
        self.delay = 0.0

    def generate(self, result, start, end):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return f"BEGIN:VCALENDAR\n{start}/{end}\nEND:VCALENDAR\n".encode()


@pytest.fixture
def env(tmp_path):
    directory = tmp_path / "calendars"
    generator = CountingGenerator()
    cache = CalendarCache(
        directory=directory, generator=generator, clock=lambda: FIXED_NOW
    )
    return SimpleNamespace(cache=cache, generator=generator, directory=directory)


def make_result(fetched_at=FIXED_NOW, stale=False):
    return TimetableResult(
        source_weeks={WEEK: SourceWeek(fetched_at=fetched_at, stale=stale)}
    )


def read_metadata(env):
    key = cache_key(START, END)
    return json.loads((env.directory / f"{key}.json").read_text(encoding="utf-8"))


def test_first_request_generates_and_second_reuses_cached_bytes(env):
    result = make_result()

    first = env.cache.get_calendar(START, END, result)
    second = env.cache.get_calendar(START, END, result)

    assert first.content == second.content
    assert first.etag == second.etag
    assert env.generator.calls == 1
    key = cache_key(START, END)
    assert sorted(p.name for p in env.directory.iterdir()) == [
        f"{key}.ics",
        f"{key}.json",
    ]


def test_metadata_matches_design_schema(env):
    env.cache.get_calendar(START, END, make_result(stale=True))
    metadata = read_metadata(env)

    assert metadata["version"] == 1
    assert metadata["start"] == START.isoformat()
    assert metadata["end"] == END.isoformat()
    assert metadata["generated_at"] == FIXED_NOW.isoformat()
    assert metadata["timezone"] == config.TIMEZONE_NAME
    assert metadata["source_weeks"] == {
        WEEK.isoformat(): {"fetched_at": FIXED_NOW.isoformat(), "stale": True}
    }
    assert metadata["fingerprint"] == build_fingerprint(
        START, END, make_result(stale=True)
    )


def test_etag_is_quoted_fingerprint(env):
    result = make_result()
    data = env.cache.get_calendar(START, END, result)
    assert data.etag == f'"{build_fingerprint(START, END, result)}"'


def test_changed_fetched_at_invalidates_calendar(env):
    first = env.cache.get_calendar(START, END, make_result())
    second = env.cache.get_calendar(
        START, END, make_result(fetched_at=FIXED_NOW + timedelta(hours=1))
    )

    assert second.etag != first.etag
    assert env.generator.calls == 2


def test_changed_stale_status_invalidates_calendar(env):
    first = env.cache.get_calendar(START, END, make_result())
    second = env.cache.get_calendar(START, END, make_result(stale=True))

    assert second.etag != first.etag
    assert env.generator.calls == 2


def test_different_ranges_have_separate_entries(env):
    result = make_result()
    env.cache.get_calendar(START, END, result)
    env.cache.get_calendar(START, date(2026, 12, 13), result)

    assert env.generator.calls == 2
    assert len(list(env.directory.glob("*.ics"))) == 2
    assert len(list(env.directory.glob("*.json"))) == 2


def test_missing_ics_causes_regeneration(env):
    env.cache.get_calendar(START, END, make_result())
    (env.directory / f"{cache_key(START, END)}.ics").unlink()

    env.cache.get_calendar(START, END, make_result())

    assert env.generator.calls == 2


def test_corrupted_metadata_causes_regeneration(env):
    env.cache.get_calendar(START, END, make_result())
    (env.directory / f"{cache_key(START, END)}.json").write_text("{broken")

    env.cache.get_calendar(START, END, make_result())

    assert env.generator.calls == 2


def test_mismatched_fingerprint_causes_regeneration(env):
    env.cache.get_calendar(START, END, make_result())
    path = env.directory / f"{cache_key(START, END)}.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["fingerprint"] = "stale-value"
    path.write_text(json.dumps(metadata), encoding="utf-8")

    env.cache.get_calendar(START, END, make_result())

    assert env.generator.calls == 2


def test_generator_version_change_invalidates_entry(env, monkeypatch):
    env.cache.get_calendar(START, END, make_result())
    monkeypatch.setattr(
        cache_module, "GENERATOR_VERSION", cache_module.GENERATOR_VERSION + 1
    )

    env.cache.get_calendar(START, END, make_result())

    assert env.generator.calls == 2


def test_metadata_write_failure_leaves_entry_invalid(env, monkeypatch):
    original_replace = Path.replace

    def fail_on_json(self, target):
        if str(target).endswith(".json"):
            raise OSError("simulated metadata failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_on_json)
    with pytest.raises(OSError, match="simulated"):
        env.cache.get_calendar(START, END, make_result())

    monkeypatch.setattr(Path, "replace", original_replace)
    env.cache.get_calendar(START, END, make_result())

    assert env.generator.calls == 2
    assert read_metadata(env)["fingerprint"] == build_fingerprint(
        START, END, make_result()
    )


def test_fingerprint_is_deterministic():
    assert build_fingerprint(START, END, make_result()) == build_fingerprint(
        START, END, make_result()
    )


def test_concurrent_requests_generate_once(env):
    env.generator.delay = 0.05
    barrier = threading.Barrier(5)

    def worker():
        barrier.wait()
        return env.cache.get_calendar(START, END, make_result())

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: worker(), range(5)))

    assert env.generator.calls == 1
    assert all(data.content == results[0].content for data in results)
