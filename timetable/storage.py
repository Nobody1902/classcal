import json
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import config

logger = logging.getLogger(__name__)

CACHE_VERSION = 1


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def format_timestamp(value: datetime) -> str:
    return value.isoformat()


class JsonStore:
    def __init__(self, directory: Path | str | None = None):
        self.directory = (
            Path(directory) if directory is not None else config.TIMETABLE_DIR
        )

    def _path_for(self, week_start: date) -> Path:
        return self.directory / f"{week_start.isoformat()}.json"

    def load_week(self, week_start: date) -> dict | None:
        path = self._path_for(week_start)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            logger.exception("Cannot read timetable cache for week %s", week_start)
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in timetable cache %s", path)
            self._quarantine(path, "invalid-json")
            return None

        if not self._is_valid_record(data, week_start):
            logger.error("Invalid structure in timetable cache %s", path)
            self._quarantine(path, "invalid-structure")
            return None

        return data

    def save_week(self, week_start: date, data: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for(week_start)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            temp_path.replace(path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        logger.info("Saved timetable cache for week %s", week_start)

    def delete_week(self, week_start: date) -> None:
        self._path_for(week_start).unlink(missing_ok=True)

    def week_exists(self, week_start: date) -> bool:
        return self._path_for(week_start).is_file()

    @staticmethod
    def _is_valid_record(data: object, week_start: date) -> bool:
        if not isinstance(data, dict):
            return False
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            return False
        if metadata.get("version") != CACHE_VERSION:
            return False
        if metadata.get("week_start") != week_start.isoformat():
            return False
        try:
            fetched_at = parse_timestamp(metadata["fetched_at"])
        except (KeyError, TypeError, ValueError):
            return False
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            return False
        return isinstance(data.get("schedule"), list) and isinstance(
            data.get("events"), list
        )

    @staticmethod
    def _quarantine(path: Path, reason: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        target = path.with_name(f"{path.stem}.corrupt-{reason}-{stamp}{path.suffix}")
        try:
            path.replace(target)
        except OSError:
            logger.exception("Could not preserve invalid cache file %s", path)
        else:
            logger.error("Preserved invalid cache file as %s", target.name)
