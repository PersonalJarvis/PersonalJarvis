"""The JSON file the computer records live in.

``<user data>/computers/computers.json``, rewritten atomically (temp file +
``os.replace``) under one process-wide lock, so a crash mid-write leaves the
previous file intact and two requests never interleave a read-modify-write.
No secrets are ever written here.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from jarvis.computers.models import Computer
from jarvis.core.paths import user_data_dir

log = logging.getLogger(__name__)

_FILE_VERSION = 1
_LOCK = threading.RLock()


def default_path() -> Path:
    """Where the records live; resolved per call so a test sandbox is honoured."""
    return user_data_dir() / "computers" / "computers.json"


class ComputerStore:
    """Read and write the computer list. Cheap to construct; holds no state."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_path()

    @property
    def path(self) -> Path:
        return self._path

    def all(self) -> list[Computer]:
        with _LOCK:
            return self._read()

    def get(self, computer_id: str) -> Computer | None:
        return next((c for c in self.all() if c.id == computer_id), None)

    def add(self, computer: Computer) -> Computer:
        with _LOCK:
            rows = self._read()
            if any(c.id == computer.id for c in rows):
                raise ValueError(f"A computer with id {computer.id!r} already exists.")
            rows.append(computer)
            self._write(rows)
            return computer

    def update(self, computer_id: str, change: Callable[[Computer], Computer]) -> Computer | None:
        """Apply ``change`` to one record and persist it; ``None`` when unknown."""
        with _LOCK:
            rows = self._read()
            for index, row in enumerate(rows):
                if row.id == computer_id:
                    updated = change(row)
                    rows[index] = updated
                    self._write(rows)
                    return updated
            return None

    def remove(self, computer_id: str) -> bool:
        with _LOCK:
            rows = self._read()
            kept = [c for c in rows if c.id != computer_id]
            if len(kept) == len(rows):
                return False
            self._write(kept)
            return True

    # -- file I/O ----------------------------------------------------------

    def _read(self) -> list[Computer]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:  # no file yet simply means no computers yet
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Never overwrite a file we cannot read: keep a copy for the user and
            # start from an empty list rather than refusing every request.
            backup = self._path.with_suffix(".corrupt.json")
            log.warning("computers: %s is not JSON; moved aside to %s", self._path, backup)
            os.replace(self._path, backup)
            return []
        rows: list[Computer] = []
        for item in data.get("computers", []) if isinstance(data, dict) else []:
            try:
                rows.append(Computer.model_validate(item))
            except ValidationError as exc:
                log.warning("computers: skipping an unreadable record: %s", exc)
        return rows

    def _write(self, rows: list[Computer]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _FILE_VERSION,
            "computers": [row.model_dump(mode="json") for row in rows],
        }
        fd, tmp = tempfile.mkstemp(prefix=".computers.", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
