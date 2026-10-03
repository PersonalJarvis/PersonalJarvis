"""Board insights — one read-only picture of how someone works with Jarvis.

The Board's numbers come from the stores the app already keeps. Nothing new is
recorded for the Board and no text leaves this module: only counts,
timestamps and totals are read.

Sources, each optional (a missing or unreadable store reads as "nothing yet"):

* ``dictation_stats.json``  — lifetime dictation counters (words, seconds,
  dictations per local day). Never pruned, holds no text.
* ``personal.db``           — the Board ledger: per-day voice words, voice
  sessions and talk time, aggregated from ``sessions.db``.
* ``sessions.db``           — voice turn start times, for the hour-of-day map.
* ``agent_chat.db``         — chats with Jarvis and the agents: sessions per
  provider and the user's own messages (timestamps only).
* ``cli_usage_index.db``    — coding-agent turns (Claude Code, Codex, ...)
  indexed from the CLIs' own transcripts on this computer.

Days are LOCAL calendar days, like every other streak in the app.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Average typing speed the "time saved" figure compares dictation against.
#: Sent to the UI with the numbers so the comparison is stated, never implied.
TYPING_WPM_REFERENCE = 40

#: Length of an average novel, for the "that is N books" line.
NOVEL_WORDS_REFERENCE = 90_000

#: How many days the daily series covers (53 weeks fill the calendar grid).
SERIES_DAYS = 371

#: Seconds a computed picture is reused before the stores are read again.
CACHE_TTL_S = 30.0


@dataclass(frozen=True, slots=True)
class InsightSources:
    """Where each store lives. ``None`` switches a source off."""

    dictation_stats: Path | None = None
    board_db: Path | None = None
    sessions_db: Path | None = None
    agent_chat_db: Path | None = None
    cli_index_db: Path | None = None


@contextlib.contextmanager
def _read_only(path: Path | None) -> Iterator[sqlite3.Connection | None]:
    """A read-only connection, or ``None`` when the store is absent/unreadable."""
    if path is None or not Path(path).is_file():
        yield None
        return
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        log.warning("Board insights: %s is not readable", path)
        yield None
        return
    try:
        yield conn
    finally:
        with contextlib.suppress(sqlite3.Error):
            conn.close()


def _local_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0).astimezone().date().isoformat()


def _local_slot(ms: int) -> tuple[int, int]:
    """(weekday Mon=0, hour) of an epoch-ms instant in local time."""
    moment = datetime.fromtimestamp(ms / 1000.0).astimezone()
    return moment.weekday(), moment.hour


def _today() -> _date:
    return datetime.now().astimezone().date()


def current_streak(active_days: set[str], today: _date | None = None) -> int:
    """Consecutive active days up to today; a quiet today does not break it."""
    cursor = today or _today()
    if cursor.isoformat() not in active_days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def longest_streak(active_days: set[str]) -> int:
    parsed = sorted(_date.fromisoformat(d) for d in active_days)
    best = run = 0
    previous: _date | None = None
    for day in parsed:
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        best = max(best, run)
        previous = day
    return best


class _Day:
    __slots__ = (
        "agent_sessions",
        "agent_turns",
        "chat_messages",
        "dictation_words",
        "dictations",
        "voice_words",
    )

    def __init__(self) -> None:
        self.dictation_words = 0
        self.dictations = 0
        self.voice_words = 0
        self.chat_messages = 0
        self.agent_sessions = 0
        self.agent_turns = 0

    def active(self) -> bool:
        return bool(
            self.dictations or self.voice_words or self.chat_messages
            or self.agent_sessions or self.agent_turns
        )


class BoardInsights:
    """Builds the Board's picture from the stores in ``sources``. Thread-safe."""

    def __init__(self, sources: InsightSources) -> None:
        self._sources = sources
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def get(self, *, max_age_s: float = CACHE_TTL_S) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < max_age_s:
                return self._cached
            picture = self.build()
            self._cached, self._cached_at = picture, now
            return picture

    # ------------------------------------------------------------------

    def build(self, today: _date | None = None) -> dict[str, Any]:
        today = today or _today()
        days: dict[str, _Day] = defaultdict(_Day)
        punch = [[0] * 24 for _ in range(7)]

        dictation = self._read_dictation(days)
        voice = self._read_voice(days, punch)
        chats = self._read_chats(days, punch)
        agents = self._read_agents(days, punch, today)

        active = {d for d, v in days.items() if v.active()}
        series_start = today - timedelta(days=SERIES_DAYS - 1)
        series = []
        for offset in range(SERIES_DAYS):
            key = (series_start + timedelta(days=offset)).isoformat()
            day = days.get(key)
            series.append({
                "date": key,
                "dictation_words": day.dictation_words if day else 0,
                "voice_words": day.voice_words if day else 0,
                "chat_messages": day.chat_messages if day else 0,
                "agent_sessions": day.agent_sessions if day else 0,
                "agent_turns": day.agent_turns if day else 0,
            })

        def words_between(start: _date, end: _date) -> int:
            total = 0
            for key, day in days.items():
                if start.isoformat() <= key <= end.isoformat():
                    total += day.dictation_words + day.voice_words
            return total

        best_words = max(
            days.items(),
            key=lambda kv: (kv[1].dictation_words + kv[1].voice_words, kv[0]),
            default=None,
        )
        best_agents = max(
            days.items(), key=lambda kv: (kv[1].agent_sessions, kv[0]), default=None,
        )

        return {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reference": {
                "typing_wpm": TYPING_WPM_REFERENCE,
                "novel_words": NOVEL_WORDS_REFERENCE,
            },
            "dictation": dictation,
            "voice": voice,
            "chats": chats,
            "agents": agents,
            "days": series,
            "punch_card": punch,
            "trend": {
                "words_30d": words_between(today - timedelta(days=29), today),
                "words_prev_30d": words_between(
                    today - timedelta(days=59), today - timedelta(days=30)
                ),
            },
            "streak": {
                "current_days": current_streak(active, today),
                "longest_days": longest_streak(active),
                "active_days": len(active),
                "first_day": min(active) if active else None,
            },
            "records": {
                "best_words_day": (
                    {"date": best_words[0],
                     "value": best_words[1].dictation_words + best_words[1].voice_words}
                    if best_words and (best_words[1].dictation_words + best_words[1].voice_words)
                    else None
                ),
                "best_agent_day": (
                    {"date": best_agents[0], "value": best_agents[1].agent_sessions}
                    if best_agents and best_agents[1].agent_sessions else None
                ),
            },
        }

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def _read_dictation(self, days: dict[str, _Day]) -> dict[str, Any]:
        out: dict[str, Any] = {"available": False, "words": 0, "dictations": 0, "seconds": 0.0}
        path = self._sources.dictation_stats
        if path is None or not Path(path).is_file():
            return out
        from jarvis.dictation.stats import DictationStats

        totals, by_day = DictationStats(path).load()
        for key, stat in by_day.items():
            day = days[key]
            day.dictation_words += stat.words
            day.dictations += stat.dictations
        out.update(
            available=True,
            words=int(totals.get("words", 0)),
            dictations=int(totals.get("dictations", 0)),
            seconds=round(float(totals.get("seconds", 0.0)), 1),
        )
        return out

    def _read_voice(self, days: dict[str, _Day], punch: list[list[int]]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "available": False, "sessions": 0, "turns": 0,
            "user_words": 0, "jarvis_words": 0, "seconds": 0.0,
        }
        with _read_only(self._sources.board_db) as conn:
            if conn is not None:
                try:
                    for date, user_words, jarvis_words, sessions, seconds in conn.execute(
                        "SELECT date, user_words_count, jarvis_words_count, "
                        "session_count, conversation_seconds_estimate FROM daily_stats"
                    ):
                        days[date].voice_words += int(user_words or 0)
                        out["user_words"] += int(user_words or 0)
                        out["jarvis_words"] += int(jarvis_words or 0)
                        out["sessions"] += int(sessions or 0)
                        out["seconds"] += float(seconds or 0.0)
                    out["available"] = True
                except sqlite3.Error:
                    log.warning("Board insights: board ledger unreadable", exc_info=True)
        with _read_only(self._sources.sessions_db) as conn:
            if conn is not None:
                try:
                    for (started_ms,) in conn.execute("SELECT started_ms FROM voice_turns"):
                        if started_ms:
                            out["turns"] += 1
                            weekday, hour = _local_slot(int(started_ms))
                            punch[weekday][hour] += 1
                    out["available"] = True
                except sqlite3.Error:
                    log.warning("Board insights: sessions.db unreadable", exc_info=True)
        out["seconds"] = round(out["seconds"], 1)
        return out

    def _read_chats(self, days: dict[str, _Day], punch: list[list[int]]) -> dict[str, Any]:
        out: dict[str, Any] = {"available": False, "sessions": 0, "messages": 0, "providers": []}
        with _read_only(self._sources.agent_chat_db) as conn:
            if conn is None:
                return out
            try:
                providers: dict[str, dict[str, int]] = {}
                for provider, surface, sessions in conn.execute(
                    "SELECT provider, surface, COUNT(*) FROM agent_chat_sessions "
                    "GROUP BY provider, surface"
                ):
                    row = providers.setdefault(
                        provider or "", {"sessions": 0, "messages": 0, "jarvis_sessions": 0}
                    )
                    row["sessions"] += int(sessions)
                    if surface == "jarvis":
                        row["jarvis_sessions"] += int(sessions)
                    out["sessions"] += int(sessions)
                for provider, ts_ms in conn.execute(
                    "SELECT s.provider, e.ts_ms FROM agent_chat_events e "
                    "JOIN agent_chat_sessions s ON s.session_id = e.session_id "
                    "WHERE e.kind = 'user_message'"
                ):
                    if not ts_ms:
                        continue
                    providers.setdefault(
                        provider or "", {"sessions": 0, "messages": 0, "jarvis_sessions": 0}
                    )["messages"] += 1
                    out["messages"] += 1
                    days[_local_day(int(ts_ms))].chat_messages += 1
                    weekday, hour = _local_slot(int(ts_ms))
                    punch[weekday][hour] += 1
                out["providers"] = sorted(
                    ({"provider": k, **v} for k, v in providers.items() if k),
                    key=lambda r: (-r["messages"], -r["sessions"], r["provider"]),
                )
                out["available"] = True
            except sqlite3.Error:
                log.warning("Board insights: agent_chat.db unreadable", exc_info=True)
        return out

    def _read_agents(
        self, days: dict[str, _Day], punch: list[list[int]], today: _date
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "available": False, "sessions": 0, "turns": 0, "tokens": 0, "items": [],
        }
        with _read_only(self._sources.cli_index_db) as conn:
            if conn is None:
                return out
            cutoff_ms = int(
                datetime.combine(today - timedelta(days=29), datetime.min.time())
                .astimezone().timestamp() * 1000
            )
            try:
                items: dict[str, dict[str, Any]] = {}
                for agent, session_id, first_ms, last_ms, turns, tokens in conn.execute(
                    "SELECT agent, session_id, MIN(ts_ms), MAX(ts_ms), COUNT(*), "
                    "SUM(tokens_in + tokens_out) FROM cli_turns WHERE ts_ms > 0 "
                    "GROUP BY agent, session_id"
                ):
                    item = items.setdefault(agent, {
                        "agent": agent, "sessions": 0, "sessions_30d": 0, "turns": 0,
                        "turns_30d": 0, "tokens": 0, "last_ms": 0,
                    })
                    item["turns"] += int(turns)
                    item["tokens"] += int(tokens or 0)
                    item["last_ms"] = max(item["last_ms"], int(last_ms))
                    if session_id:
                        item["sessions"] += 1
                        days[_local_day(int(first_ms))].agent_sessions += 1
                        if int(first_ms) >= cutoff_ms:
                            item["sessions_30d"] += 1
                for agent, day, turns in conn.execute(
                    "SELECT agent, strftime('%Y-%m-%d', ts_ms / 1000, 'unixepoch', 'localtime'), "
                    "COUNT(*) FROM cli_turns WHERE ts_ms > 0 GROUP BY 1, 2"
                ):
                    days[day].agent_turns += int(turns)
                    if day >= (today - timedelta(days=29)).isoformat() and agent in items:
                        items[agent]["turns_30d"] += int(turns)
                for weekday, hour, turns in conn.execute(
                    "SELECT "
                    "CAST(strftime('%w', ts_ms / 1000, 'unixepoch', 'localtime') AS INTEGER), "
                    "CAST(strftime('%H', ts_ms / 1000, 'unixepoch', 'localtime') AS INTEGER), "
                    "COUNT(*) FROM cli_turns WHERE ts_ms > 0 GROUP BY 1, 2"
                ):
                    # SQLite counts Sunday as 0; the punch card is Monday-first.
                    punch[(int(weekday) + 6) % 7][int(hour)] += int(turns)
                out["items"] = sorted(
                    items.values(), key=lambda r: (-r["sessions"], -r["turns"], r["agent"])
                )
                out["sessions"] = sum(r["sessions"] for r in items.values())
                out["turns"] = sum(r["turns"] for r in items.values())
                out["tokens"] = sum(r["tokens"] for r in items.values())
                out["available"] = True
            except sqlite3.Error:
                log.warning("Board insights: cli_usage_index.db unreadable", exc_info=True)
        return out


def default_sources(
    *, data_dir: Path, sessions_db: Path | None, board_db: Path | None
) -> InsightSources:
    """The live app's store locations, resolved the way each store's owner does."""
    from jarvis.costs.cli_usage_index import index_db_path
    from jarvis.dictation.stats import default_stats_path

    return InsightSources(
        dictation_stats=default_stats_path(),
        board_db=board_db,
        sessions_db=sessions_db,
        agent_chat_db=Path(data_dir) / "agent_chat.db",
        cli_index_db=index_db_path(),
    )

