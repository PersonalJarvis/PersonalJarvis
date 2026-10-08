"""OwnerNotifier — tell the person what matters, once, only when they opted in.

Kinds: the daily briefing, important work (needs you / urgent), cancelled
appointments and moved appointments. Every notification is built from the
deterministic briefing (``jarvis/ops/briefing.py``) — no model call, no cost.

Guarantees:

- **Opt-in.** Off by default; only an explicit settings change turns it on.
  While off, nothing is sent and nothing is recorded.
- **Once.** Every notification has a stable ``dedup_key``; a key that was
  delivered is never sent again. A message split into parts resumes at the
  first undelivered part, so a retry never repeats a part.
- **Short notice first.** High-priority notifications (short-notice changes,
  work that needs the person) go out before the rest; a per-run cap holds the
  rest back for the next run instead of flooding the chat.
- **Safe retries.** Only an error the transport marks retryable is retried,
  a bounded number of times with jittered backoff; a failed notification is
  tried again on later runs up to :data:`MAX_TOTAL_ATTEMPTS`, then given up.
- **No secrets in logs.** Logs carry the kind and a short hash of the key —
  never the text, a chat id or a token. Stored errors are codes, not texts.

Only :class:`SimulatedTelegramTransport` exists: it records what WOULD be sent
and opens no connection, reads no token and needs no chat id. A real
transport is a later, separately approved step.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, Protocol

import aiosqlite

from jarvis.ops.briefing import Briefing, event_line, item_line, normalize_language, phrases

log = logging.getLogger(__name__)

NotificationKind = Literal[
    "daily_briefing", "important_task", "appointment_cancelled", "appointment_moved"
]
NOTIFICATION_KINDS: Final[tuple[str, ...]] = (
    "daily_briefing",
    "important_task",
    "appointment_cancelled",
    "appointment_moved",
)
#: Telegram's limit for one text message.
TELEGRAM_MAX_CHARS: Final = 4096
MAX_PER_RUN: Final = 10
MAX_ATTEMPTS_PER_RUN: Final = 3
MAX_TOTAL_ATTEMPTS: Final = 5
BACKOFF_S: Final[tuple[float, ...]] = (1.0, 4.0)
OUTBOX_KEEP: Final = 500
TEXT_KEEP: Final = TELEGRAM_MAX_CHARS * 4


# ---------------------------------------------------------------- model


@dataclass(frozen=True, slots=True)
class Notification:
    kind: str
    dedup_key: str
    text: str
    priority: Literal["high", "normal"] = "normal"


@dataclass(frozen=True, slots=True)
class NotifySettings:
    enabled: bool = False
    kinds: frozenset[str] = frozenset(NOTIFICATION_KINDS)
    updated_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "kinds": sorted(self.kinds),
            "channel": "telegram",
            "transport": "simulated",
            "updated_ms": self.updated_ms,
        }


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    dedup_key: str
    kind: str
    priority: str
    status: str  # delivered | simulated | duplicate | disabled | kind_off |
    #              rate_limited | failed | gave_up
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dedup_key": self.dedup_key,
            "kind": self.kind,
            "priority": self.priority,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    outcomes: tuple[DeliveryOutcome, ...] = field(default_factory=tuple)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return {"outcomes": [o.to_dict() for o in self.outcomes], "counts": counts}


def key_hash(dedup_key: str) -> str:
    """A short, non-reversible handle for logs."""
    return hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()[:12]


def split_text(text: str, limit: int = TELEGRAM_MAX_CHARS) -> tuple[str, ...]:
    """Split on line breaks into parts of at most *limit* characters."""
    text = text.strip()
    if not text:
        return ()
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # one overlong line: hard cut
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return tuple(p for p in parts if p.strip())


# ---------------------------------------------------------------- building

_TEXTS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "needs_you": "Needs you: {line}",
        "urgent": "Urgent: {line}",
        "cancelled": "Appointment cancelled: {line}",
        "moved": "Appointment moved: {line}",
    },
    "de": {  # i18n-allow: runtime notification output (paired with en/es/zh)
        "needs_you": "Braucht dich: {line}",  # i18n-allow
        "urgent": "Dringend: {line}",  # i18n-allow
        "cancelled": "Termin abgesagt: {line}",  # i18n-allow
        "moved": "Termin verschoben: {line}",  # i18n-allow
    },
    "es": {  # i18n-allow: runtime notification output (paired with en/de/zh)
        "needs_you": "Te necesita: {line}",  # i18n-allow
        "urgent": "Urgente: {line}",  # i18n-allow
        "cancelled": "Cita cancelada: {line}",  # i18n-allow
        "moved": "Cita movida: {line}",  # i18n-allow
    },
    "zh": {  # i18n-allow: runtime notification output (paired with en/de/es)
        "needs_you": "需要你处理：{line}",  # i18n-allow
        "urgent": "紧急：{line}",  # i18n-allow
        "cancelled": "日程已取消：{line}",  # i18n-allow
        "moved": "日程已改期：{line}",  # i18n-allow
    },
}


def notifications_from_briefing(briefing: Briefing) -> list[Notification]:
    """Every notification the briefing's facts call for, in a stable order."""
    language = normalize_language(briefing.language)
    table = phrases(language)
    texts = _TEXTS[language]
    day = briefing.day
    out: list[Notification] = []

    for entry in briefing.section("calendar_cancelled").items:
        line = event_line(entry, table, day=day)[2:]
        out.append(
            Notification(
                "appointment_cancelled",
                f"cancelled:{entry.get('id') or entry.get('title')}:{entry.get('start')}",
                texts["cancelled"].format(line=line),
                "high" if entry.get("short_notice") else "normal",
            )
        )
    for entry in briefing.section("calendar").items:
        if not entry.get("moved_from"):
            continue
        line = event_line(entry, table, day=day)[2:]
        out.append(
            Notification(
                "appointment_moved",
                f"moved:{entry.get('id') or entry.get('title')}:{entry.get('start')}",
                texts["moved"].format(line=line),
                "high" if entry.get("short_notice") else "normal",
            )
        )
    for entry in briefing.section("needs_you").items:
        out.append(
            Notification(
                "important_task",
                f"task:{entry['source']}:{entry['id']}:{entry['status']}",
                texts["needs_you"].format(line=item_line(entry, table)[2:]),
                "high",
            )
        )
    seen = {n.dedup_key for n in out}
    for key in ("focus", "running", "due_today", "priorities"):
        for entry in briefing.section(key).items:
            if entry.get("priority") != "urgent":
                continue
            dedup = f"task:{entry['source']}:{entry['id']}:{entry['status']}"
            if dedup in seen:
                continue
            seen.add(dedup)
            out.append(
                Notification(
                    "important_task",
                    dedup,
                    texts["urgent"].format(line=item_line(entry, table)[2:]),
                    "normal",
                )
            )
    out.append(
        Notification("daily_briefing", f"briefing:{day.isoformat()}", briefing.text, "normal")
    )
    return out


# ---------------------------------------------------------------- transport


class TransportError(Exception):
    """A send that did not go through. ``code`` is a short machine code (never
    a provider text); ``retryable`` says whether trying again can help."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class NotificationTransport(Protocol):
    name: str
    simulated: bool

    async def send(self, text: str) -> None: ...


class SimulatedTelegramTransport:
    """Records what would be sent to Telegram. No network, no token, no chat id.

    ``fail`` scripts errors for tests: each entry is raised by one send call,
    in order (``None`` lets that call succeed).
    """

    name = "telegram"
    simulated = True

    def __init__(self, fail: Sequence[TransportError | None] = ()) -> None:
        self.sent: list[str] = []
        self._fail = list(fail)

    async def send(self, text: str) -> None:
        if not text.strip():
            raise TransportError("empty_text", retryable=False)
        if len(text) > TELEGRAM_MAX_CHARS:
            raise TransportError("text_too_long", retryable=False)
        if self._fail:
            error = self._fail.pop(0)
            if error is not None:
                raise error
        self.sent.append(text)


# ---------------------------------------------------------------- store

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS ops_notify_settings (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        enabled     INTEGER NOT NULL DEFAULT 0,
        kinds       TEXT NOT NULL,
        updated_ms  INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ops_notify_log (
        dedup_key    TEXT PRIMARY KEY,
        kind         TEXT NOT NULL,
        priority     TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('delivered','simulated','failed','gave_up')),
        transport    TEXT NOT NULL,
        attempts     INTEGER NOT NULL DEFAULT 0,
        parts_sent   INTEGER NOT NULL DEFAULT 0,
        parts_total  INTEGER NOT NULL DEFAULT 0,
        error        TEXT NOT NULL DEFAULT '',
        text         TEXT NOT NULL DEFAULT '',
        created_ms   INTEGER NOT NULL,
        updated_ms   INTEGER NOT NULL
    )
    """,
)
FINAL_STATUSES: Final = frozenset({"delivered", "simulated", "gave_up"})


@dataclass(frozen=True, slots=True)
class LogRow:
    dedup_key: str
    kind: str
    priority: str
    status: str
    transport: str
    attempts: int
    parts_sent: int
    parts_total: int
    error: str
    text: str
    created_ms: int
    updated_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dedup_key": self.dedup_key,
            "kind": self.kind,
            "priority": self.priority,
            "status": self.status,
            "transport": self.transport,
            "attempts": self.attempts,
            "parts_sent": self.parts_sent,
            "parts_total": self.parts_total,
            "error": self.error,
            "text": self.text,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
        }


class NotifyStore:
    """Opt-in settings and the delivery log, in the Ops core's own SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def _connect(self) -> aiosqlite.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 5000")
        for statement in _SCHEMA:
            await conn.execute(statement)
        return conn

    async def settings(self) -> NotifySettings:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT enabled, kinds, updated_ms FROM ops_notify_settings WHERE id = 1"
            )
            row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            return NotifySettings()
        kinds = frozenset(k for k in str(row["kinds"]).split(",") if k in NOTIFICATION_KINDS)
        return NotifySettings(bool(row["enabled"]), kinds, int(row["updated_ms"]))

    async def save_settings(self, *, enabled: bool, kinds: Iterable[str]) -> NotifySettings:
        chosen = frozenset(kinds)
        unknown = chosen - set(NOTIFICATION_KINDS)
        if unknown:
            raise ValueError(f"unknown notification kinds: {sorted(unknown)}")
        now = int(time.time() * 1000)
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO ops_notify_settings (id, enabled, kinds, updated_ms)"
                " VALUES (1, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET"
                " enabled = excluded.enabled, kinds = excluded.kinds,"
                " updated_ms = excluded.updated_ms",
                (int(bool(enabled)), ",".join(sorted(chosen)), now),
            )
            await conn.commit()
        finally:
            await conn.close()
        return NotifySettings(bool(enabled), chosen, now)

    async def get(self, dedup_key: str) -> LogRow | None:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT * FROM ops_notify_log WHERE dedup_key = ?", (dedup_key,)
            )
            row = await cur.fetchone()
        finally:
            await conn.close()
        return _log_row(row) if row is not None else None

    async def record(self, row: LogRow) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO ops_notify_log (dedup_key, kind, priority, status, transport,"
                " attempts, parts_sent, parts_total, error, text, created_ms, updated_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (dedup_key) DO UPDATE SET status = excluded.status,"
                " attempts = excluded.attempts, parts_sent = excluded.parts_sent,"
                " parts_total = excluded.parts_total, error = excluded.error,"
                " text = excluded.text, updated_ms = excluded.updated_ms",
                (
                    row.dedup_key,
                    row.kind,
                    row.priority,
                    row.status,
                    row.transport,
                    row.attempts,
                    row.parts_sent,
                    row.parts_total,
                    row.error,
                    row.text[:TEXT_KEEP],
                    row.created_ms,
                    row.updated_ms,
                ),
            )
            # Keep the outbox bounded: the oldest finished rows go first.
            await conn.execute(
                "DELETE FROM ops_notify_log WHERE dedup_key IN ("
                " SELECT dedup_key FROM ops_notify_log WHERE status != 'failed'"
                " ORDER BY updated_ms DESC LIMIT -1 OFFSET ?)",
                (OUTBOX_KEEP,),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def outbox(self, limit: int = 50) -> list[LogRow]:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT * FROM ops_notify_log ORDER BY updated_ms DESC, dedup_key LIMIT ?",
                (max(1, min(int(limit), OUTBOX_KEEP)),),
            )
            rows = await cur.fetchall()
        finally:
            await conn.close()
        return [_log_row(r) for r in rows]


def _log_row(row: Any) -> LogRow:
    return LogRow(
        dedup_key=str(row["dedup_key"]),
        kind=str(row["kind"]),
        priority=str(row["priority"]),
        status=str(row["status"]),
        transport=str(row["transport"]),
        attempts=int(row["attempts"]),
        parts_sent=int(row["parts_sent"]),
        parts_total=int(row["parts_total"]),
        error=str(row["error"] or ""),
        text=str(row["text"] or ""),
        created_ms=int(row["created_ms"]),
        updated_ms=int(row["updated_ms"]),
    )


# ---------------------------------------------------------------- notifier


Sleep = Callable[[float], Awaitable[None]]


class OwnerNotifier:
    def __init__(
        self,
        store: NotifyStore,
        transport: NotificationTransport,
        *,
        max_per_run: int = MAX_PER_RUN,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._transport = transport
        self._max_per_run = max(1, int(max_per_run))
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - backoff jitter, not crypto
        self._clock = clock

    async def deliver(self, notifications: Sequence[Notification]) -> DeliveryReport:
        settings = await self._store.settings()
        if not settings.enabled:
            return DeliveryReport(
                tuple(
                    DeliveryOutcome(n.dedup_key, n.kind, n.priority, "disabled")
                    for n in notifications
                )
            )
        order = sorted(
            enumerate(notifications), key=lambda pair: (pair[1].priority != "high", pair[0])
        )
        outcomes: dict[int, DeliveryOutcome] = {}
        sent = 0
        batch: set[str] = set()
        for index, note in order:
            if note.kind not in settings.kinds:
                outcomes[index] = self._outcome(note, "kind_off")
                continue
            if note.dedup_key in batch:
                outcomes[index] = self._outcome(note, "duplicate")
                continue
            batch.add(note.dedup_key)
            previous = await self._store.get(note.dedup_key)
            if previous is not None and previous.status in FINAL_STATUSES:
                status = "duplicate" if previous.status != "gave_up" else "gave_up"
                outcomes[index] = self._outcome(note, status)
                continue
            if sent >= self._max_per_run:
                outcomes[index] = self._outcome(note, "rate_limited")
                continue
            outcomes[index] = await self._send_one(note, previous)
            sent += 1
        return DeliveryReport(tuple(outcomes[i] for i in range(len(notifications))))

    @staticmethod
    def _outcome(note: Notification, status: str, error: str = "") -> DeliveryOutcome:
        return DeliveryOutcome(note.dedup_key, note.kind, note.priority, status, error)

    async def _send_one(self, note: Notification, previous: LogRow | None) -> DeliveryOutcome:
        parts = split_text(note.text)
        now_ms = int(self._clock() * 1000)
        attempts = previous.attempts if previous is not None else 0
        parts_sent = previous.parts_sent if previous is not None else 0
        created = previous.created_ms if previous is not None else now_ms
        error = ""
        if not parts:
            return await self._finish(note, "gave_up", attempts, 0, 0, "empty_text", created)
        tries_this_run = 0
        while parts_sent < len(parts):
            if attempts >= MAX_TOTAL_ATTEMPTS:
                return await self._finish(
                    note,
                    "gave_up",
                    attempts,
                    parts_sent,
                    len(parts),
                    error or "too_many_attempts",
                    created,
                )
            attempts += 1
            tries_this_run += 1
            try:
                await self._transport.send(parts[parts_sent])
            except TransportError as exc:
                error = exc.code
                log.warning(
                    "ops notify: %s %s not sent (%s, attempt %d)",
                    note.kind,
                    key_hash(note.dedup_key),
                    exc.code,
                    attempts,
                )
                if not exc.retryable:
                    return await self._finish(
                        note, "gave_up", attempts, parts_sent, len(parts), exc.code, created
                    )
                if tries_this_run >= MAX_ATTEMPTS_PER_RUN:
                    return await self._finish(
                        note, "failed", attempts, parts_sent, len(parts), exc.code, created
                    )
                await self._sleep(self._backoff(tries_this_run))
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — an unknown transport error counts as failed
                error = type(exc).__name__
                log.warning(
                    "ops notify: %s %s transport error (%s)",
                    note.kind,
                    key_hash(note.dedup_key),
                    error,
                )
                return await self._finish(
                    note, "failed", attempts, parts_sent, len(parts), error, created
                )
            parts_sent += 1
        status = "simulated" if self._transport.simulated else "delivered"
        log.info("ops notify: %s %s %s", note.kind, key_hash(note.dedup_key), status)
        return await self._finish(note, status, attempts, parts_sent, len(parts), "", created)

    def _transport_label(self) -> str:
        suffix = "-simulated" if self._transport.simulated else ""
        return f"{self._transport.name}{suffix}"

    def _backoff(self, try_number: int) -> float:
        base = BACKOFF_S[min(try_number - 1, len(BACKOFF_S) - 1)]
        return base * (0.5 + self._rng.random())

    async def _finish(
        self,
        note: Notification,
        status: str,
        attempts: int,
        parts_sent: int,
        parts_total: int,
        error: str,
        created_ms: int,
    ) -> DeliveryOutcome:
        await self._store.record(
            LogRow(
                dedup_key=note.dedup_key,
                kind=note.kind,
                priority=note.priority,
                status=status,
                transport=self._transport_label(),
                attempts=attempts,
                parts_sent=parts_sent,
                parts_total=parts_total,
                error=error,
                text=note.text,
                created_ms=created_ms,
                updated_ms=int(self._clock() * 1000),
            )
        )
        return self._outcome(note, status, error)


def outbox_dicts(rows: Iterable[LogRow]) -> list[Mapping[str, Any]]:
    return [row.to_dict() for row in rows]


__all__ = [
    "MAX_PER_RUN",
    "MAX_TOTAL_ATTEMPTS",
    "NOTIFICATION_KINDS",
    "TELEGRAM_MAX_CHARS",
    "DeliveryOutcome",
    "DeliveryReport",
    "LogRow",
    "Notification",
    "NotificationTransport",
    "NotifySettings",
    "NotifyStore",
    "OwnerNotifier",
    "SimulatedTelegramTransport",
    "TransportError",
    "key_hash",
    "notifications_from_briefing",
    "outbox_dicts",
    "split_text",
]
