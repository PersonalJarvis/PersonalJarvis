"""Keep local teams operable while an optional distributed backend is absent."""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

from jarvis.core.swarm_types import TeamCreate

from .store import SwarmAccessError, SwarmConflictError, SwarmStoreError, TeamRegistry

log = logging.getLogger(__name__)


class RegistryRouter:
    def __init__(self, local: TeamRegistry) -> None:
        self.local = local
        self.root = local.root
        self.remote: Any = None
        self._retired: list[Any] = []
        self.remote_error = ""
        self._lock = threading.RLock()

    def set_remote(self, remote: Any) -> None:
        with self._lock:
            if remote is not None and remote is not self.remote and len(self._retired) >= 8:
                raise SwarmConflictError(
                    "Wait for prior distributed operations to finish before replacing settings"
                )
            if self.remote is not None and self.remote is not remote:
                previous = self.remote
                retire = getattr(previous, "retire", None)
                if callable(retire):
                    retire()
                self._retired.append(previous)
            self.remote = remote
            self.remote_error = ""

    def retired(self) -> tuple[Any, ...]:
        with self._lock:
            return tuple(self._retired)

    def collect_retired(self) -> None:
        for registry in self.retired():
            try:
                close = getattr(registry, "close_if_unused", None)
                closed = close() if callable(close) else registry.close() is not False
            except Exception:  # noqa: BLE001 - independent retired backends must still be reclaimed
                log.exception("Retired distributed registry cleanup will retry")
                continue
            if closed:
                with self._lock:
                    if registry in self._retired:
                        self._retired.remove(registry)

    def create(self, spec: TeamCreate, owner: str = "local-user") -> dict[str, Any]:
        if spec.mode == "local":
            return self.local.create(spec, owner)
        if self.remote is None:
            raise SwarmStoreError(
                "Configure the optional distributed services before starting this mode"
            )
        return self.remote.create(spec, owner)

    def open(self, team_id: str, owner: str = "local-user") -> Any:
        try:
            return self.local.open(team_id, owner)
        except SwarmAccessError:
            # A local miss never grants remote access: its registry rechecks owner.
            if self.remote is None:
                raise
        return self.remote.open(team_id, owner)

    def _local_count(self, owner: str) -> int:
        path = self.root / "catalog.sqlite3"
        if not path.is_file():
            return 0
        with sqlite3.connect(path, timeout=10) as connection:
            return int(
                connection.execute("SELECT count(*) FROM teams WHERE owner=?", (owner,)).fetchone()[
                    0
                ]
            )

    def list(
        self, owner: str = "local-user", *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        records = self.local.list(owner, limit=limit, offset=offset)
        if len(records) == limit or self.remote is None:
            return records
        remote_offset = max(0, offset - self._local_count(owner))
        try:
            records.extend(
                self.remote.list(owner, limit=limit - len(records), offset=remote_offset)
            )
            self.remote_error = ""
        except Exception:  # noqa: BLE001 - optional infrastructure cannot break local mode
            self.remote_error = "Distributed team storage is unavailable; recovery will retry."
            log.exception("Distributed team listing unavailable; local teams remain available")
        return records

    def close(self) -> None:
        self.set_remote(None)
        self.collect_retired()
