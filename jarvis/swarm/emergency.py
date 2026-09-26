"""Persist emergency suspension across unavailable backends and process recovery."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path


class EmergencyFence:
    """A stop generation requires explicit user start/resume for each team."""

    def __init__(self, root: Path) -> None:
        self.path = root / "emergency-stop.sqlite3"
        self._lock = threading.Lock()

    def trip(self, generation: str) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS stop (id INTEGER PRIMARY KEY, generation TEXT)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS allowances "
                    "(team_id TEXT PRIMARY KEY, generation TEXT)"
                )
                current = connection.execute("SELECT generation FROM stop WHERE id=1").fetchone()
                if current is not None and current[0] == generation:
                    return
                connection.execute("DELETE FROM allowances")
                connection.execute("INSERT OR REPLACE INTO stop VALUES (1,?)", (generation,))

    def permits(self, team_id: str) -> bool:
        with self._lock:
            if not self.path.is_file():
                return True
            with closing(sqlite3.connect(self.path, timeout=5)) as connection:
                return (
                    connection.execute(
                        "SELECT 1 FROM allowances a JOIN stop s ON s.generation=a.generation "
                        "WHERE s.id=1 AND a.team_id=?",
                        (team_id,),
                    ).fetchone()
                    is not None
                )

    def allow(self, team_id: str) -> None:
        with self._lock:
            if not self.path.is_file():
                return
            with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO allowances SELECT ?,generation FROM stop WHERE id=1",
                    (team_id,),
                )
