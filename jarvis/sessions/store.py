"""SQLite-Store fuer Voice-Sessions (sync, WAL-Mode).

Im Unterschied zu ``jarvis/missions/event_store.py`` (aiosqlite, async)
laeuft dieser Store synchron mit ``sqlite3`` + ``threading.Lock``.
Begruendung: der ``SessionRecorder`` wird aus EventBus-Subscriber-
Callbacks aufgerufen; das passiert je nach Event aus dem asyncio-Loop
ODER aus einem Worker-Thread (Pipeline ist Multi-Threaded). Ein
synchroner Store mit Lock ist der einfachste Pfad ohne Loop-Detection-
Hacks. SQLite-Writes liegen im µs-Bereich, Lock-Contention ist auf
einem Voice-Pfad mit < 100 Events/Sek nicht messbar.

Atomicitaet: WAL + ``synchronous=NORMAL`` flusht Writes auf Disk;
zwischen Process-Crash und Bus-Publish kann ein Event verloren gehen
- bei Voice-Sessions akzeptabel (nicht-kritisches UI-Feature).

Cleanup: ``prune_older_than(days)`` loescht alte Sessions; ON DELETE
CASCADE in schema.sql raeumt Turns + Events automatisch mit.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from .models import SessionListItem, VoiceEventRow, VoiceSessionRow, VoiceTurnRow

log = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class SessionStore:
    """Sync SQLite-Store mit threading.Lock-Synchronisation."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # --- Lifecycle ----------------------------------------------------

    def open(self) -> None:
        """DB oeffnen, PRAGMAs setzen, Schema (idempotent) laden, Migrations."""
        with self._lock:
            if self._conn is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: wir serialisieren selbst via self._lock,
            # mehrere Threads (Pipeline-Worker, FastAPI-Routes) duerfen lesen+schreiben.
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; WAL ist Lock-Manager
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
            conn.executescript(schema_sql)
            conn.row_factory = sqlite3.Row
            self._conn = conn
            self._apply_migrations()
            log.debug("SessionStore opened: %s", self._db_path)

    def _apply_migrations(self) -> None:
        """Idempotente Spalten-Migrations fuer pre-existing DBs.

        SQLite hat kein ``ADD COLUMN IF NOT EXISTS`` — wir lesen
        ``pragma_table_info`` und appendieren nur fehlende Spalten.
        Pattern uebernommen aus ``jarvis/missions/event_store.py``.
        """
        assert self._conn is not None
        cur = self._conn.execute("PRAGMA table_info(voice_turns)")
        existing = {str(row["name"]) for row in cur.fetchall()}
        if "think_ms" not in existing:
            self._conn.execute(
                "ALTER TABLE voice_turns ADD COLUMN think_ms INTEGER NOT NULL DEFAULT 0"
            )
            log.info("SessionStore migration: added voice_turns.think_ms")
        if "speak_ms" not in existing:
            self._conn.execute(
                "ALTER TABLE voice_turns ADD COLUMN speak_ms INTEGER NOT NULL DEFAULT 0"
            )
            log.info("SessionStore migration: added voice_turns.speak_ms")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SessionStore: open() vor Verwendung aufrufen")
        return self._conn

    # --- Session-Header ----------------------------------------------

    def upsert_session(
        self,
        *,
        session_id: str,
        started_ms: int,
        wake_keyword: str = "",
        language: str = "de",
    ) -> None:
        """Anlegen oder (bei Recorder-Re-Init) idempotent re-upserten.

        ON CONFLICT: ``started_ms`` bleibt erhalten (erstes Wake gewinnt),
        nur ``wake_keyword``/``language`` werden ggf. ueberschrieben.
        """
        with self._lock:
            self._c.execute(
                """
                INSERT INTO voice_sessions
                    (id, started_ms, wake_keyword, language)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    wake_keyword = excluded.wake_keyword,
                    language     = excluded.language
                """,
                (session_id, started_ms, wake_keyword, language),
            )

    def finalize_session(
        self,
        *,
        session_id: str,
        ended_ms: int,
        hangup_reason: str,
        turn_count: int,
        total_cost_usd: float,
        total_tokens_in: int,
        total_tokens_out: int,
        providers_used: list[str],
    ) -> None:
        """Hangup-Update — schreibt End-Zeit und Aggregate."""
        with self._lock:
            self._c.execute(
                """
                UPDATE voice_sessions SET
                    ended_ms         = ?,
                    hangup_reason    = ?,
                    turn_count       = ?,
                    total_cost_usd   = ?,
                    total_tokens_in  = ?,
                    total_tokens_out = ?,
                    providers_used   = ?
                WHERE id = ?
                """,
                (
                    ended_ms,
                    hangup_reason,
                    turn_count,
                    total_cost_usd,
                    total_tokens_in,
                    total_tokens_out,
                    json.dumps(sorted(set(providers_used))),
                    session_id,
                ),
            )

    # --- Turns --------------------------------------------------------

    def upsert_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        idx: int,
        started_ms: int,
    ) -> None:
        with self._lock:
            self._c.execute(
                """
                INSERT INTO voice_turns
                    (id, session_id, idx, started_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    idx = excluded.idx,
                    started_ms = MIN(voice_turns.started_ms, excluded.started_ms)
                """,
                (turn_id, session_id, idx, started_ms),
            )

    def finalize_turn(
        self,
        *,
        turn_id: str,
        ended_ms: int,
        user_text: str,
        user_lang: str,
        jarvis_text: str,
        jarvis_lang: str,
        tier: str,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        latency_total_ms: int,
        tool_calls: list[str],
        think_ms: int = 0,
        speak_ms: int = 0,
    ) -> None:
        with self._lock:
            self._c.execute(
                """
                UPDATE voice_turns SET
                    ended_ms         = ?,
                    user_text        = ?,
                    user_lang        = ?,
                    jarvis_text      = ?,
                    jarvis_lang      = ?,
                    tier             = ?,
                    provider         = ?,
                    model            = ?,
                    tokens_in        = ?,
                    tokens_out       = ?,
                    cost_usd         = ?,
                    latency_total_ms = ?,
                    think_ms         = ?,
                    speak_ms         = ?,
                    tool_calls_json  = ?
                WHERE id = ?
                """,
                (
                    ended_ms,
                    user_text,
                    user_lang,
                    jarvis_text,
                    jarvis_lang,
                    tier,
                    provider,
                    model,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    latency_total_ms,
                    think_ms,
                    speak_ms,
                    json.dumps(tool_calls),
                    turn_id,
                ),
            )

    # --- Events -------------------------------------------------------

    def append_event(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        ts_ms: int,
        kind: str,
        payload: dict[str, Any],
    ) -> int:
        """Roh-Event anhaengen, gibt vergebene seq zurueck."""
        with self._lock:
            cur = self._c.execute(
                """
                INSERT INTO voice_events
                    (session_id, turn_id, ts_ms, kind, payload_json)
                VALUES (?, ?, ?, ?, ?)
                RETURNING seq
                """,
                (session_id, turn_id, ts_ms, kind, json.dumps(payload, default=_json_default)),
            )
            row = cur.fetchone()
            return int(row["seq"])

    # --- Read-API -----------------------------------------------------

    def list_sessions(self, *, limit: int = 100) -> list[SessionListItem]:
        """Sessions nach started_ms desc — neueste zuerst.

        ``preview`` ist die erste User-Utterance (erste Turn-Row mit
        nicht-leerem ``user_text``).
        """
        with self._lock:
            cur = self._c.execute(
                """
                SELECT s.*,
                       (SELECT t.user_text
                        FROM voice_turns t
                        WHERE t.session_id = s.id
                          AND t.user_text != ''
                        ORDER BY t.idx ASC
                        LIMIT 1) AS preview
                FROM voice_sessions s
                ORDER BY s.started_ms DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
        # Per-row validation in a try/except: a single invalid row (e.g.
        # a hangup_reason value that drifted ahead of the Pydantic
        # Literal — see BUG-008) MUST NOT empty the whole list. We log
        # a structured warning so the drift is visible in operations,
        # then skip just the bad row. The list-API thus degrades to
        # "missing some rows" instead of "HTTP 500, empty UI".
        result: list[SessionListItem] = []
        for r in rows:
            duration_s = (
                (r["ended_ms"] - r["started_ms"]) / 1000.0
                if r["ended_ms"] is not None
                else None
            )
            try:
                item = SessionListItem(
                    id=r["id"],
                    started_ms=r["started_ms"],
                    ended_ms=r["ended_ms"],
                    hangup_reason=r["hangup_reason"] or "",
                    turn_count=r["turn_count"],
                    total_cost_usd=r["total_cost_usd"],
                    total_tokens_in=r["total_tokens_in"],
                    total_tokens_out=r["total_tokens_out"],
                    providers_used=json.loads(r["providers_used"] or "[]"),
                    language=r["language"],
                    wake_keyword=r["wake_keyword"],
                    duration_s=duration_s,
                    preview=_truncate(r["preview"] or "", 120),
                )
            except ValidationError as exc:
                log.warning(
                    "hangup_reason_drift_skipped: id=%s hangup_reason=%r err=%s",
                    r["id"],
                    r["hangup_reason"],
                    exc.errors(include_url=False, include_input=False),
                )
                continue
            result.append(item)
        return result

    def get_session(self, session_id: str) -> VoiceSessionRow | None:
        with self._lock:
            cur = self._c.execute(
                "SELECT * FROM voice_sessions WHERE id = ?", (session_id,)
            )
            r = cur.fetchone()
        if r is None:
            return None
        return _row_to_session(r)

    def get_turns(self, session_id: str) -> list[VoiceTurnRow]:
        with self._lock:
            cur = self._c.execute(
                """
                SELECT * FROM voice_turns
                WHERE session_id = ?
                ORDER BY idx ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
        return [_row_to_turn(r) for r in rows]

    def get_events(self, session_id: str) -> list[VoiceEventRow]:
        with self._lock:
            cur = self._c.execute(
                """
                SELECT * FROM voice_events
                WHERE session_id = ?
                ORDER BY seq ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
        return [
            VoiceEventRow(
                seq=r["seq"],
                session_id=r["session_id"],
                turn_id=r["turn_id"],
                ts_ms=r["ts_ms"],
                kind=r["kind"],
                payload=json.loads(r["payload_json"] or "{}"),
            )
            for r in rows
        ]

    def list_open_sessions(self) -> list[str]:
        """IDs aller Sessions ohne ``ended_ms`` — fuer Crash-Recovery."""
        with self._lock:
            cur = self._c.execute(
                "SELECT id FROM voice_sessions WHERE ended_ms IS NULL"
            )
            return [r["id"] for r in cur.fetchall()]

    # --- Maintenance --------------------------------------------------

    def prune_older_than(self, days: int) -> int:
        """Loescht Sessions mit ``started_ms`` aelter als N Tage.

        Cascade-Delete in schema.sql raeumt Turns + Events automatisch
        mit. Returns Anzahl geloeschter Session-Rows.
        """
        if days <= 0:
            return 0
        cutoff_ms = _now_ms() - days * 86_400_000
        with self._lock:
            cur = self._c.execute(
                "DELETE FROM voice_sessions WHERE started_ms < ?",
                (cutoff_ms,),
            )
            return cur.rowcount

    def wal_checkpoint(self) -> None:
        with self._lock:
            self._c.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# --- Helpers ----------------------------------------------------------


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _json_default(obj: Any) -> Any:
    """Fallback fuer json.dumps: UUID, set, dataclass-ish."""
    if hasattr(obj, "hex") and hasattr(obj, "int"):  # UUID
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    return repr(obj)


def _row_to_session(r: sqlite3.Row) -> VoiceSessionRow:
    return VoiceSessionRow(
        id=r["id"],
        started_ms=r["started_ms"],
        ended_ms=r["ended_ms"],
        hangup_reason=r["hangup_reason"] or "",
        turn_count=r["turn_count"],
        total_cost_usd=r["total_cost_usd"],
        total_tokens_in=r["total_tokens_in"],
        total_tokens_out=r["total_tokens_out"],
        providers_used=json.loads(r["providers_used"] or "[]"),
        language=r["language"],
        wake_keyword=r["wake_keyword"],
    )


def _row_to_turn(r: sqlite3.Row) -> VoiceTurnRow:
    return VoiceTurnRow(
        id=r["id"],
        session_id=r["session_id"],
        idx=r["idx"],
        started_ms=r["started_ms"],
        ended_ms=r["ended_ms"],
        user_text=r["user_text"],
        user_lang=r["user_lang"],
        jarvis_text=r["jarvis_text"],
        jarvis_lang=r["jarvis_lang"],
        tier=r["tier"],
        provider=r["provider"],
        model=r["model"],
        tokens_in=r["tokens_in"],
        tokens_out=r["tokens_out"],
        cost_usd=r["cost_usd"],
        latency_total_ms=r["latency_total_ms"],
        think_ms=_safe_col(r, "think_ms", 0),
        speak_ms=_safe_col(r, "speak_ms", 0),
        tool_calls=json.loads(r["tool_calls_json"] or "[]"),
    )


def _safe_col(r: sqlite3.Row, name: str, default: int) -> int:
    """Liest eine Spalte, gibt Default zurueck wenn sie nicht existiert.

    Brauchen wir nur fuer das schmale Fenster, in dem ein laufender
    Backend noch ohne Migration startet — schadet im Normalfall nicht.
    """
    try:
        v = r[name]
        return int(v) if v is not None else default
    except (KeyError, IndexError):
        return default


# Suppress unused import warning for typing-only Iterable
_ = Iterable

__all__ = ["SessionStore"]
