"""Evidence-safe Realtime session backfill for the Wiki capture pipeline.

The backfill never replays legacy candidate rows whose user evidence is
unknown. It re-reads persisted voice turns, keeps only Realtime user turns,
and runs the same evidence-bound session sweep as live capture. Durable review
keys make it idempotent across retries and restarts.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

from jarvis.memory.wiki.extractor import ConversationContextTurn


class SessionTurnReader(Protocol):
    """Narrow read protocol implemented by :class:`SessionStore`."""

    def list_sessions(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]: ...

    def get_turns(self, session_id: str) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class BackfillResult:
    dry_run: bool
    days: int
    sessions_scanned: int
    sessions_eligible: int
    sessions_already_reviewed: int
    sessions_in_progress: int
    sessions_reviewed: int
    sessions_failed: int
    turns_considered: int
    candidates_journaled: int
    review_keys: tuple[str, ...] = ()
    attempted_review_keys: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "dry_run": self.dry_run,
            "days": self.days,
            "sessions_scanned": self.sessions_scanned,
            "sessions_eligible": self.sessions_eligible,
            "sessions_already_reviewed": self.sessions_already_reviewed,
            "sessions_in_progress": self.sessions_in_progress,
            "sessions_reviewed": self.sessions_reviewed,
            "sessions_failed": self.sessions_failed,
            "turns_considered": self.turns_considered,
            "candidates_journaled": self.candidates_journaled,
        }


async def backfill_realtime_sessions(
    *,
    store: SessionTurnReader,
    extractor: Any,
    days: int = 2,
    max_sessions: int = 20,
    dry_run: bool = True,
    now_ms: int | None = None,
) -> BackfillResult:
    """Review recent persisted Realtime sessions without inventing evidence."""
    bounded_days = min(30, max(1, int(days)))
    bounded_sessions = min(100, max(1, int(max_sessions)))
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    cutoff_ms = current_ms - bounded_days * 24 * 60 * 60 * 1000

    scanned = 0
    eligible: list[
        tuple[
            str,
            tuple[ConversationContextTurn, ...],
            tuple[str, ...],
            bool,
        ]
    ] = []
    already_reviewed = 0
    in_progress = 0
    turns_considered = 0
    review_keys: list[str] = []

    # Page until the requested number of genuinely eligible Realtime
    # sessions is found or the time window is exhausted. A fixed prefix can
    # silently miss later sessions when the newest rows are classic, open, or
    # already reviewed.
    page_size = max(100, bounded_sessions * 4)
    offset = 0
    reached_cutoff = False
    while len(eligible) < bounded_sessions and not reached_cutoff:
        sessions = await asyncio.to_thread(
            store.list_sessions,
            limit=page_size,
            offset=offset,
        )
        if not sessions:
            break
        for session in sessions:
            if len(eligible) >= bounded_sessions:
                break
            started_ms = int(getattr(session, "started_ms", 0) or 0)
            if started_ms < cutoff_ms:
                reached_cutoff = True
                break
            if getattr(session, "ended_ms", None) is None:
                continue
            scanned += 1
            session_id = str(getattr(session, "id", "") or "")
            if not session_id:
                continue
            raw_turns = await asyncio.to_thread(store.get_turns, session_id)
            realtime_turns = tuple(
                ConversationContextTurn(
                    turn_id=str(getattr(turn, "id", "") or ""),
                    user_text=str(getattr(turn, "user_text", "") or ""),
                    assistant_text=str(getattr(turn, "jarvis_text", "") or ""),
                )
                for turn in raw_turns
                if str(getattr(turn, "tier", "") or "") == "realtime"
                and str(getattr(turn, "id", "") or "")
                and str(getattr(turn, "user_text", "") or "").strip()
            )
            if not realtime_turns:
                continue
            turns_considered += len(realtime_turns)
            key_builder = getattr(extractor, "session_review_keys", None)
            uses_chunk_keys = callable(key_builder)
            keys = (
                tuple(key_builder(realtime_turns, session_id=session_id))
                if uses_chunk_keys
                else (f"session:v2:{session_id}",)
            )
            review_keys.extend(keys)
            status_reader = getattr(extractor, "capture_status", None)
            statuses = (
                tuple(status_reader(key) for key in keys)
                if callable(status_reader)
                else tuple(
                    "candidates" if extractor.capture_seen(key) else None
                    for key in keys
                )
            )
            if any(status == "started" for status in statuses):
                in_progress += 1
                continue
            if statuses and all(
                status in {"filtered", "empty", "candidates"}
                for status in statuses
            ):
                already_reviewed += 1
                continue
            attempted_keys = tuple(
                key
                for key, status in zip(keys, statuses, strict=True)
                if status not in {"filtered", "empty", "candidates"}
            )
            eligible.append(
                (session_id, realtime_turns, attempted_keys, uses_chunk_keys)
            )
        if len(sessions) < page_size:
            break
        offset += len(sessions)

    if dry_run:
        return BackfillResult(
            dry_run=True,
            days=bounded_days,
            sessions_scanned=scanned,
            sessions_eligible=len(eligible),
            sessions_already_reviewed=already_reviewed,
            sessions_in_progress=in_progress,
            sessions_reviewed=0,
            sessions_failed=0,
            turns_considered=turns_considered,
            candidates_journaled=0,
            review_keys=tuple(review_keys),
            attempted_review_keys=tuple(
                key
                for _session_id, _turns, keys, _uses_chunk_keys in eligible
                for key in keys
            ),
        )

    reviewed = 0
    failed = 0
    candidates = 0
    for session_id, turns, keys, uses_chunk_keys in eligible:
        kwargs = {
            "session_id": session_id,
            "source_label": f"realtime-session-backfill:{session_id}",
        }
        if not uses_chunk_keys:
            kwargs["review_key"] = keys[0]
        candidates += await extractor.extract_session_and_journal(turns, **kwargs)
        status_reader = getattr(extractor, "capture_status", None)
        if callable(status_reader):
            final_statuses = tuple(status_reader(key) for key in keys)
            if final_statuses and all(
                status in {"filtered", "empty", "candidates"}
                for status in final_statuses
            ):
                reviewed += 1
            elif any(status == "started" for status in final_statuses):
                in_progress += 1
            else:
                failed += 1
        else:
            reviewed += 1

    return BackfillResult(
        dry_run=False,
        days=bounded_days,
        sessions_scanned=scanned,
        sessions_eligible=len(eligible),
        sessions_already_reviewed=already_reviewed,
        sessions_in_progress=in_progress,
        sessions_reviewed=reviewed,
        sessions_failed=failed,
        turns_considered=turns_considered,
        candidates_journaled=candidates,
        review_keys=tuple(review_keys),
        attempted_review_keys=tuple(
            key
            for _session_id, _turns, keys, _uses_chunk_keys in eligible
            for key in keys
        ),
    )


__all__ = ["BackfillResult", "SessionTurnReader", "backfill_realtime_sessions"]
