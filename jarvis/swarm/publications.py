"""Selected, owner-authorized publications retained independently of team cleanup."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any


class PublicationArchive:
    def __init__(self, root: Path) -> None:
        self.path = root / "publications.sqlite3"

    def accept(
        self, publication: dict[str, Any], content: bytes, *, owner: str = "local-user"
    ) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        if digest != publication["sha256"]:
            raise ValueError("Publication content does not match the verified artifact")
        if len(content) > 50_000_000:
            raise ValueError("Publication exceeds the local destination limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS publications ("
                "id TEXT PRIMARY KEY, owner TEXT NOT NULL, team_id TEXT NOT NULL, "
                "artifact_id TEXT NOT NULL, destination TEXT NOT NULL, "
                "destination_version INTEGER NOT NULL, sha256 TEXT NOT NULL, "
                "content BLOB NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO publications VALUES (?,?,?,?,?,?,?,?)",
                (
                    publication["id"],
                    owner,
                    publication["team_id"],
                    publication["artifact_id"],
                    publication["destination_id"],
                    publication["destination_version"],
                    digest,
                    content,
                ),
            )
            record = connection.execute(
                "SELECT owner,sha256,destination,destination_version FROM publications WHERE id=?",
                (publication["id"],),
            ).fetchone()
            expected = (
                owner,
                digest,
                publication["destination_id"],
                publication["destination_version"],
            )
            if record != expected:
                raise ValueError("Publication id already identifies different content or authority")
        # The receipt is returned only after SQLite commits bytes and provenance.
        return {
            "destination_key": publication["id"],
            "sha256": digest,
            "destination_id": publication["destination_id"],
            "destination_version": publication["destination_version"],
        }

    def read(self, publication_id: str, *, owner: str = "local-user") -> bytes:
        if not re.fullmatch(r"[a-f0-9]{32}", publication_id) or not self.path.is_file():
            raise PermissionError("Publication unavailable")
        with sqlite3.connect(self.path, timeout=10) as connection:
            row = connection.execute(
                "SELECT content,sha256 FROM publications WHERE id=? AND owner=?",
                (publication_id, owner),
            ).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise PermissionError("Publication unavailable or damaged")
        return bytes(row[0])
