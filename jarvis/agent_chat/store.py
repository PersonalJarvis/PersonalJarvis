"""SQLite persistence for agent-chat sessions.

Mirrors ``jarvis/state/chat_store.py``: one ``sqlite3`` connection in WAL
mode behind a ``threading.Lock`` (route handlers and the runner share the
asyncio loop; the lock keeps a future worker-thread caller safe). Two tables:

``agent_chat_sessions``
    One row per session — title, the provider / model / effort the composer
    last used in it, the working directory, the permission mode and, for the
    CLI runners, the vendor's own session id so a later turn can resume it.

``agent_chat_events``
    The append-only event log (see :mod:`jarvis.agent_chat.events`), ordered
    by ``seq`` per session. Transient kinds are never written.

Ordering by ``seq`` (our own counter), not by wall clock: Windows ``time()``
resolution can tie two fast appends.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jarvis.agent_chat.events import is_transient, now_ms

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_chat_sessions (
    session_id       TEXT PRIMARY KEY,
    title            TEXT NOT NULL DEFAULT '',
    provider         TEXT NOT NULL DEFAULT '',
    model            TEXT NOT NULL DEFAULT '',
    effort           TEXT NOT NULL DEFAULT '',
    cwd              TEXT NOT NULL DEFAULT '',
    permission_mode  TEXT NOT NULL DEFAULT 'ask',
    vendor_session   TEXT,
    created_ms       INTEGER NOT NULL,
    updated_ms       INTEGER NOT NULL,
    message_count    INTEGER NOT NULL DEFAULT 0,
    preview          TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS agent_chat_events (
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    ts_ms       INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_agent_chat_sessions_updated
    ON agent_chat_sessions(updated_ms DESC);
"""

_TITLE_MAX_CHARS = 80
_PREVIEW_MAX_CHARS = 120
# The permission ladders live in jarvis/agent_chat/permissions.py (per
# runner); the store keeps whatever id the route validated.


@dataclass(slots=True)
class AgentChatSession:
    session_id: str
    title: str
    provider: str
    model: str
    effort: str
    cwd: str
    permission_mode: str
    vendor_session: str | None
    created_ms: int
    updated_ms: int
    message_count: int
    preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _title_from(text: str) -> str:
    line = " ".join((text or "").split())
    if len(line) > _TITLE_MAX_CHARS:
        return line[: _TITLE_MAX_CHARS - 1].rstrip() + "…"
    return line


class AgentChatStore:
    """Sessions + event log. ``db_path=":memory:"`` for tests."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if self._path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ sessions

    def create_session(
        self,
        *,
        provider: str,
        model: str,
        effort: str,
        cwd: str,
        permission_mode: str = "",
        title: str = "",
        session_id: str | None = None,
    ) -> AgentChatSession:
        sid = session_id or uuid.uuid4().hex
        now = now_ms()
        # The route validated the mode against the runner's ladder; the store
        # keeps the id as given.
        mode = (permission_mode or "").strip()
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_chat_sessions (session_id, title, provider, model, effort, "
                "cwd, permission_mode, vendor_session, created_ms, updated_ms, message_count, "
                "preview) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, '')",
                (sid, title, provider, model, effort, cwd, mode, now, now),
            )
            self._conn.commit()
        session = self.get_session(sid)
        assert session is not None
        return session

    def get_session(self, session_id: str) -> AgentChatSession | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_chat_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, limit: int = 200) -> list[AgentChatSession]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_chat_sessions ORDER BY updated_ms DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def update_session(self, session_id: str, **fields: Any) -> AgentChatSession | None:
        """Set any of title / provider / model / effort / cwd / permission_mode /
        vendor_session. Unknown keys are ignored so a route can pass its body
        through after validation."""
        allowed = {
            "title",
            "provider",
            "model",
            "effort",
            "cwd",
            "permission_mode",
            "vendor_session",
        }
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_session(session_id)
        updates["updated_ms"] = now_ms()
        cols = ", ".join(f"{k} = ?" for k in updates)
        with self._lock:
            self._conn.execute(
                f"UPDATE agent_chat_sessions SET {cols} WHERE session_id = ?",  # noqa: S608 — column names are from the allow-list above
                (*updates.values(), session_id),
            )
            self._conn.commit()
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM agent_chat_sessions WHERE session_id = ?", (session_id,)
            )
            self._conn.execute("DELETE FROM agent_chat_events WHERE session_id = ?", (session_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # -------------------------------------------------------------- events

    def append_event(self, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Persist ``event`` (assigning ``seq``) and return it with the seq set.

        Transient kinds are returned unchanged and never written. A
        ``user_message`` bumps the session's counters and, on the first one,
        becomes its title; an ``assistant_text`` refreshes the preview.
        """
        if is_transient(event):
            return event
        kind = str(event.get("kind") or "")
        payload = event.get("payload") or {}
        ts_ms = int(event.get("ts_ms") or now_ms())
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM agent_chat_events "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = int(row["next"]) if row else 1
            self._conn.execute(
                "INSERT INTO agent_chat_events (session_id, seq, ts_ms, kind, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, seq, ts_ms, kind, json.dumps(payload, ensure_ascii=False)),
            )
            if kind == "user_message":
                text = str(payload.get("text") or "")
                self._conn.execute(
                    "UPDATE agent_chat_sessions SET message_count = message_count + 1, "
                    "updated_ms = ?, preview = ?, "
                    "title = CASE WHEN title = '' THEN ? ELSE title END "
                    "WHERE session_id = ?",
                    (ts_ms, text[:_PREVIEW_MAX_CHARS], _title_from(text), session_id),
                )
            elif kind == "assistant_text":
                text = " ".join(str(payload.get("text") or "").split())
                if text:
                    self._conn.execute(
                        "UPDATE agent_chat_sessions SET updated_ms = ?, preview = ? "
                        "WHERE session_id = ?",
                        (ts_ms, text[:_PREVIEW_MAX_CHARS], session_id),
                    )
            else:
                self._conn.execute(
                    "UPDATE agent_chat_sessions SET updated_ms = ? WHERE session_id = ?",
                    (ts_ms, session_id),
                )
            self._conn.commit()
        out = dict(event)
        out["seq"] = seq
        out["ts_ms"] = ts_ms
        return out

    def list_events(self, session_id: str, *, after_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, ts_ms, kind, payload FROM agent_chat_events "
                "WHERE session_id = ? AND seq > ? ORDER BY seq ASC",
                (session_id, int(after_seq)),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except (TypeError, ValueError):
                payload = {}
            out.append(
                {
                    "seq": int(r["seq"]),
                    "ts_ms": int(r["ts_ms"]),
                    "kind": r["kind"],
                    "payload": payload,
                }
            )
        return out

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> AgentChatSession:
        return AgentChatSession(
            session_id=row["session_id"],
            title=row["title"],
            provider=row["provider"],
            model=row["model"],
            effort=row["effort"],
            cwd=row["cwd"],
            permission_mode=row["permission_mode"],
            vendor_session=row["vendor_session"],
            created_ms=int(row["created_ms"]),
            updated_ms=int(row["updated_ms"]),
            message_count=int(row["message_count"]),
            preview=row["preview"],
        )
