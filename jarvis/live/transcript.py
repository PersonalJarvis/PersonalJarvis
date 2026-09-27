"""Continuous caption projection; display boundaries never control model turns."""

import logging
import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Literal
from uuid import UUID

from jarvis.core.events import VoiceTranscriptUpdated
from jarvis.core.redact import redact_secrets


class LiveTranscript:
    """Retain each speaker's segment independently, including overlapping speech."""

    def __init__(self) -> None:
        self._current: dict[str, VoiceTranscriptUpdated] = {}
        self._recent: dict[str, list[VoiceTranscriptUpdated]] = {"user": [], "assistant": []}
        self._finished: set[str] = set()

    def finish(self, role: str) -> None:
        self._finished.add(role)

    def feed(
        self,
        *,
        session_id: str,
        trace_id: UUID,
        event_id: str,
        role: Literal["user", "assistant"],
        text: str,
        start_ms: int,
        end_ms: int,
        snapshot: bool = False,
    ) -> VoiceTranscriptUpdated:
        old = self._current.get(role)
        late = old is not None and start_ms < old.start_ms
        if late:
            old = next(
                (
                    item
                    for item in reversed(self._recent[role])
                    if item.start_ms <= start_ms <= item.end_ms + 1500
                ),
                None,
            )
        other = self._current.get("assistant" if role == "user" else "user")
        crossed = old is not None and other is not None and old.end_ms <= other.start_ms <= start_ms
        new = (
            old is None
            or (not late and role in self._finished)
            or crossed
            or start_ms - old.end_ms > 1500
            or (not snapshot and len(old.text) + len(text) > 32000)
        )
        if not late:
            self._finished.discard(role)
        if new:
            result = VoiceTranscriptUpdated(
                session_id=session_id,
                segment_id=event_id,
                role=role,
                text=redact_secrets(text),
                start_ms=start_ms,
                end_ms=end_ms,
                trace_id=trace_id,
                source_layer="live.transcript",
            )
        else:
            assert old is not None
            result = replace(
                old,
                text=redact_secrets(text if snapshot else old.text + text),
                end_ms=max(old.end_ms, end_ms),
                revision=old.revision + 1,
                timestamp_ns=time.time_ns(),
            )
        recent = self._recent[role]
        if old is not None and not new:
            recent[recent.index(old)] = result
        else:
            recent.append(result)
            del recent[:-64]
        if not late:
            self._current[role] = result
        return result


def read_legacy_transcript(path: Path, session_id: str) -> list[VoiceTranscriptUpdated]:
    """Recover pre-fix conversations from existing fragments without rewriting data."""
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute(
                "SELECT event_id,role,delta,start_ms,end_ms FROM live_transcripts "
                "WHERE session_id=? ORDER BY start_ms,rowid",
                (session_id,),
            ).fetchall()
    except sqlite3.Error:
        logging.getLogger(__name__).debug("Legacy Live captions unavailable", exc_info=True)
        return []
    projector = LiveTranscript()
    segments = {}
    from uuid import uuid4

    trace = uuid4()
    for event_id, role, text, start, end in rows:
        if role not in {"user", "assistant"}:
            continue
        caption = projector.feed(
            session_id=session_id,
            trace_id=trace,
            event_id=event_id,
            role=role,
            text=text,
            start_ms=start,
            end_ms=end,
        )
        segments[caption.segment_id] = caption
    return sorted(segments.values(), key=lambda item: item.start_ms)
