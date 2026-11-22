import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import config
from ical.generator import GENERATOR_VERSION, CalendarGenerator
from timetable.models import TimetableResult
from timetable.storage import format_timestamp, parse_timestamp

logger = logging.getLogger(__name__)

METADATA_VERSION = 1


class CalendarGeneratorLike(Protocol):
    def generate(self, result: TimetableResult, start: date, end: date) -> bytes: ...


@dataclass(frozen=True)
class CalendarData:
    content: bytes
    etag: str


def cache_key(start: date, end: date) -> str:
    value = f"{start.isoformat()}:{end.isoformat()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_fingerprint(start: date, end: date, result: TimetableResult) -> str:
    payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generator_version": GENERATOR_VERSION,
        "timezone": config.TIMEZONE_NAME,
        "source_weeks": {
            week.isoformat(): {
                "fetched_at": format_timestamp(source.fetched_at),
                "stale": source.stale,
            }
            for week, source in sorted(result.source_weeks.items())
        },
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CalendarCache:
    def __init__(
        self,
        directory: Path | str | None = None,
        generator: CalendarGeneratorLike | None = None,
        clock=None,
    ):
        self.directory = (
            Path(directory) if directory is not None else config.CALENDAR_DIR
        )
        self._generator = generator or CalendarGenerator()
        self._clock = clock or _default_clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def get_calendar(
        self, start: date, end: date, result: TimetableResult
    ) -> CalendarData:
        key = cache_key(start, end)
        fingerprint = build_fingerprint(start, end, result)
        etag = f'"{fingerprint}"'

        with self._lock_for(key):
            content = self._load_cached(key, start, end, fingerprint)
            if content is not None:
                logger.info("Using cached calendar for %s to %s", start, end)
                return CalendarData(content=content, etag=etag)

            logger.info("Generating calendar for %s to %s", start, end)
            content = self._generator.generate(result, start, end)
            self._save(key, start, end, fingerprint, content, result)
            return CalendarData(content=content, etag=etag)

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _load_cached(
        self, key: str, start: date, end: date, fingerprint: str
    ) -> bytes | None:
        ics_path = self.directory / f"{key}.ics"
        metadata_path = self.directory / f"{key}.json"
        if not ics_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not self._metadata_valid(metadata, start, end, fingerprint):
            return None
        try:
            return ics_path.read_bytes()
        except OSError:
            return None

    @staticmethod
    def _metadata_valid(
        metadata: object, start: date, end: date, fingerprint: str
    ) -> bool:
        if not isinstance(metadata, dict):
            return False
        if metadata.get("version") != METADATA_VERSION:
            return False
        if metadata.get("start") != start.isoformat():
            return False
        if metadata.get("end") != end.isoformat():
            return False
        if metadata.get("generator_version") != GENERATOR_VERSION:
            return False
        if metadata.get("timezone") != config.TIMEZONE_NAME:
            return False
        if metadata.get("fingerprint") != fingerprint:
            return False
        try:
            parse_timestamp(metadata["generated_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return isinstance(metadata.get("source_weeks"), dict)

    def _save(
        self,
        key: str,
        start: date,
        end: date,
        fingerprint: str,
        content: bytes,
        result: TimetableResult,
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        generated_at = self._clock()
        metadata = {
            "version": METADATA_VERSION,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "generated_at": format_timestamp(generated_at),
            "generator_version": GENERATOR_VERSION,
            "timezone": config.TIMEZONE_NAME,
            "source_weeks": {
                week.isoformat(): {
                    "fetched_at": format_timestamp(source.fetched_at),
                    "stale": source.stale,
                }
                for week, source in sorted(result.source_weeks.items())
            },
            "fingerprint": fingerprint,
        }
        _atomic_write_bytes(self.directory / f"{key}.ics", content)
        _atomic_write_text(
            self.directory / f"{key}.json",
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )


def _default_clock() -> datetime:
    return datetime.now(tz=config.TIMEZONE)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    _atomic_write(path, "wb", lambda file: file.write(content))


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write(path, "w", lambda file: file.write(content))


def _atomic_write(path: Path, mode: str, write) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    encoding = None if "b" in mode else "utf-8"
    try:
        with temp_path.open(mode, encoding=encoding) as file:
            write(file)
            file.flush()
            os.fsync(file.fileno())
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
