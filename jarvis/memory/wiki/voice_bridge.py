"""Explicit-save bridge: acknowledged voice/chat turns -> Stage-1 extraction.

When the brain confirms that it stored something ("notiert", "noted", ...),
the user's turn is handed to the Stage-1 fact extractor, which journals
candidate facts for the Stage-2 consolidator. The acknowledgement is the
user-visible contract: if Jarvis says it noted the fact, the wiki gets it.

Removed 2026-09-30 by maintainer decision: the automatic review of every user
turn (the former "aggressive" path), the end-of-call sweep of a realtime
session, and the per-turn extractions held back until hangup. They reviewed
turns nobody asked to keep and spent paid API keys around the clock. What
the user triggers explicitly stays: this acknowledgement path, the
``wiki-ingest`` tool, the memory-save skill and the manual backfill route.

Both voice engines share this one contract. The pipeline pairs
``TranscriptFinal``/``MessageSent`` with ``ResponseGenerated``; the realtime
engine delivers both final texts on ``VoiceTurnCompleted`` (tier
``realtime``). ``VoiceSessionEnded`` only drops the call's reference-
resolution context — nothing runs at hangup.

Extraction is a fire-and-forget background task, so the voice path never
waits on it (AP-9). It bills only what background work may bill
(:mod:`jarvis.brain.background_policy`). When the wiki has to wait — the
subscription is not usable, the runaway guard is backing off, or the daily
call cap is reached — the turn is kept in a small in-memory queue and
:meth:`VoiceFactBridge.retry_waiting` retries it once a model call is allowed
again. It is never moved onto an API key.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jarvis.brain.background_policy import BackgroundDeferred
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    MessageSent,
    ResponseGenerated,
    TranscriptFinal,
    VoiceSessionEnded,
    VoiceTurnCompleted,
)
from jarvis.memory.wiki.extractor import ConversationContextTurn
from jarvis.memory.wiki.telemetry import telemetry

if TYPE_CHECKING:
    from jarvis.memory.wiki.extractor import ConversationFactExtractor

log = logging.getLogger(__name__)

# Bounded in-memory dedupe of recently dispatched turns. A voice turn can
# surface twice (TranscriptFinal AND the server's MessageSent mirror); the
# hash gate makes sure only one extraction fires per distinct turn text.
_SEEN_HASHES_MAX = 128

# Explicit saves held while the wiki must wait for a usable subscription or
# the next day's call budget. Small on purpose: these are turns the user
# asked to keep, not a transcript archive. The oldest is dropped (and logged)
# when a long outage overflows it.
_MAX_WAITING_SAVES = 20

# Reference-resolution context kept per live realtime call, and how many
# calls are tracked at once (a session whose end event never arrives must
# not grow the map without limit).
_CONTEXT_TURNS = 5
_MAX_TRACKED_SESSIONS = 8


def _turn_hash(text: str) -> str:
    """Stable hash of a normalised turn text (case/whitespace-insensitive)."""
    normalised = " ".join((text or "").casefold().split())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()  # noqa: S324 — dedupe key, not security


# Keywords (lowercase, simple substring match) that mark the brain's
# reply as "yes, I stored this". Keep narrow -- false positives mean
# noise in the wiki.
_ACK_KEYWORDS = (  # i18n-allow: multilingual acknowledgement-phrase matching vocabulary
    "notiert",
    "vermerkt",
    "gespeichert",  # i18n-allow: German acknowledgement-phrase matching vocabulary
    "merke ich",
    "merke mir",
    "noted",
    "got it",
    "saved",
    "anotado",  # i18n-allow: Spanish acknowledgement-phrase matching vocabulary
    "guardado",  # i18n-allow: Spanish acknowledgement-phrase matching vocabulary
    "apuntado",  # i18n-allow: Spanish acknowledgement-phrase matching vocabulary
)

# Minimum length for an acknowledged user utterance to be worth a review.
# Filters out "ja", "ok", "hallo" -- anything shorter is almost certainly
# not a fact.
_MIN_ACK_USER_CHARS = 12


@dataclass
class _PendingTurn:
    """User text from TranscriptFinal/MessageSent, held until ResponseGenerated."""

    user_text: str = ""
    user_language: str = ""
    captured_at_ns: int = 0
    # "voice" (TranscriptFinal), "chat" (MessageSent role=user) or
    # "realtime" — only labels the journal source.
    origin: str = "voice"
    session_id: str = ""
    turn_id: str = ""
    review_key: str = ""
    context_turns: tuple[ConversationContextTurn, ...] = ()


@dataclass(frozen=True, slots=True)
class _ExtractionJob:
    """One acknowledged turn, deduped and ready for Stage-1 extraction."""

    pending: _PendingTurn
    reply_raw: str
    source_kind: str
    turn_hash: str
    review_key: str


class VoiceFactBridge:
    """Bridge acknowledged voice/chat turns into the Stage-1 extractor.

    Construct once at app boot with the bus and the extractor, then call
    :meth:`start`. The bridge subscribes itself and runs until :meth:`stop`
    (or :meth:`stop_and_wait`) is called.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        extractor: ConversationFactExtractor,
    ) -> None:
        self._bus = bus
        self._extractor = extractor
        self._pending = _PendingTurn()
        # (event type, handler) pairs, because EventBus.subscribe returns None:
        # detaching needs both halves handed back to unsubscribe().
        self._subscriptions: list[tuple[type[Any], Any]] = []
        self._inflight: set[asyncio.Task[Any]] = set()
        self._realtime_sessions: OrderedDict[str, list[ConversationContextTurn]] = (
            OrderedDict()
        )
        self._realtime_turn_ids: dict[str, set[str]] = {}
        # Explicit saves waiting for the wiki to be allowed a model call.
        self._waiting: deque[_ExtractionJob] = deque()
        self._retry_lock = asyncio.Lock()
        self._started = False
        # Recently dispatched review keys (bounded LRU) — dedupes the
        # TranscriptFinal/MessageSent double delivery of the same turn.
        self._seen_hashes: OrderedDict[str, None] = OrderedDict()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Subscribe to the turn events of both voice engines."""
        if self._started:
            return
        # Annotated: the handlers take different event types, and an
        # unannotated literal makes the checker join them into a union that
        # no longer matches subscribe()'s Callable[[Event], ...].
        wiring: tuple[tuple[type[Any], Any], ...] = (
            (TranscriptFinal, self._on_transcript_final),
            (MessageSent, self._on_user_message),
            (ResponseGenerated, self._on_response_generated),
            (VoiceTurnCompleted, self._on_voice_turn_completed),
            (VoiceSessionEnded, self._on_voice_session_ended),
        )
        for event_type, handler in wiring:
            self._bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))
        self._started = True
        log.info("VoiceFactBridge started (explicit saves only)")

    def stop(self) -> None:
        """Cancel in-flight reviews and unsubscribe. Idempotent."""
        self._cancel_and_unsubscribe()

    async def stop_and_wait(self, *, timeout_s: float = 5.0) -> None:
        """Cancel and drain background reviews before their journal closes."""
        tasks = self._cancel_and_unsubscribe()
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=max(0.1, float(timeout_s)),
            )
        except TimeoutError:
            log.warning(
                "VoiceFactBridge: %d review task(s) did not cancel within %.1fs",
                len(tasks),
                timeout_s,
            )
        finally:
            self._inflight.difference_update(tasks)

    def _cancel_and_unsubscribe(self) -> tuple[asyncio.Task[Any], ...]:
        """Stop accepting work and return tasks whose cancellation must drain."""
        for event_type, handler in self._subscriptions:
            try:
                self._bus.unsubscribe(event_type, handler)
            except Exception:  # noqa: BLE001 — teardown reports, never raises
                log.debug(
                    "VoiceFactBridge: could not detach %s", event_type.__name__, exc_info=True
                )
        self._subscriptions.clear()
        tasks = tuple(self._inflight)
        for task in tasks:
            task.cancel()
        self._realtime_sessions.clear()
        self._realtime_turn_ids.clear()
        if self._waiting:
            log.info(
                "VoiceFactBridge: %d explicit save(s) still waiting for the "
                "subscription are dropped at shutdown",
                len(self._waiting),
            )
        self._waiting.clear()
        self._started = False
        return tasks

    # ------------------------------------------------------------------
    # bus handlers
    # ------------------------------------------------------------------

    async def _on_transcript_final(self, event: TranscriptFinal) -> None:
        """Capture the user text. Don't review yet -- wait for the brain reply."""
        transcript = getattr(event, "transcript", None)
        if transcript is None:
            return
        text = (getattr(transcript, "text", "") or "").strip()
        if not text:
            return
        lang = getattr(transcript, "language", "") or ""
        ts_ns = getattr(event, "timestamp_ns", 0) or 0
        telemetry.inc("voice_turns_seen")
        self._pending = _PendingTurn(
            user_text=text,
            user_language=lang,
            captured_at_ns=ts_ns,
            review_key=f"live:v2:voice:{ts_ns}:{_turn_hash(text)}",
        )

    async def _on_user_message(self, event: MessageSent) -> None:
        """Capture a CHAT user turn (desktop chat, Discord/Telegram channels).

        Mirrors :meth:`_on_transcript_final` for the text path. Non-user roles
        (assistant/preamble/...) are ignored. A voice turn that the server
        mirrors as ``MessageSent`` keeps its voice origin and is deduped later.
        """
        if (getattr(event, "role", "") or "") != "user":
            return
        text = (getattr(event, "text", "") or "").strip()
        if not text:
            return
        ts_ns = getattr(event, "timestamp_ns", 0) or 0
        if self._pending.user_text and _turn_hash(self._pending.user_text) == _turn_hash(text):
            return
        self._pending = _PendingTurn(
            user_text=text,
            user_language="",
            captured_at_ns=ts_ns,
            origin="chat",
            review_key=f"live:v2:chat:{ts_ns}:{_turn_hash(text)}",
        )

    async def _on_response_generated(self, event: ResponseGenerated) -> None:
        """Pair the pending user text with the brain reply."""
        reply_raw = (getattr(event, "text", "") or "").strip()
        if not reply_raw:
            return
        pending = self._pending
        if not pending.user_text:
            return
        if self._decide_and_dispatch(pending, reply_raw):
            self._pending = _PendingTurn()

    async def _on_voice_turn_completed(self, event: VoiceTurnCompleted) -> None:
        """Handle a REALTIME turn — both final texts arrive on this one event.

        Pipeline turns (any other tier) are already handled by the pairing
        path above and MUST be ignored here; the review-key gate in
        :meth:`_dispatch` additionally dedupes any residual overlap.
        """
        if (getattr(event, "tier", "") or "") != "realtime":
            return
        user_text = (getattr(event, "user_text", "") or "").strip()
        if not user_text:
            return
        telemetry.inc("voice_turns_seen")
        session_id = (getattr(event, "session_id", "") or "").strip()
        raw_turn_id = (getattr(event, "turn_id", "") or "").strip()
        captured_at_ns = getattr(event, "timestamp_ns", 0) or 0
        turn_id = raw_turn_id or f"turn-{captured_at_ns}"
        review_key = f"live:v2:{session_id or 'unknown-session'}:{turn_id}"
        session_turns = self._session_context(session_id)
        seen_ids = self._realtime_turn_ids.setdefault(session_id, set())
        context_turns = tuple(session_turns[-_CONTEXT_TURNS:])
        reply_raw = (getattr(event, "jarvis_text", "") or "").strip()
        if turn_id not in seen_ids:
            session_turns.append(
                ConversationContextTurn(
                    turn_id=turn_id,
                    user_text=user_text,
                    assistant_text=reply_raw,
                )
            )
            del session_turns[:-_CONTEXT_TURNS]
            seen_ids.add(turn_id)
        pending = _PendingTurn(
            user_text=user_text,
            user_language=getattr(event, "user_lang", "") or "",
            captured_at_ns=captured_at_ns,
            origin="realtime",
            session_id=session_id,
            turn_id=turn_id,
            review_key=review_key,
            context_turns=context_turns,
        )
        self._decide_and_dispatch(pending, reply_raw)

    async def _on_voice_session_ended(self, event: VoiceSessionEnded) -> None:
        """Forget the call's reference-resolution context. Nothing else runs."""
        session_id = (getattr(event, "session_id", "") or "").strip()
        self._realtime_sessions.pop(session_id, None)
        self._realtime_turn_ids.pop(session_id, None)

    def _session_context(self, session_id: str) -> list[ConversationContextTurn]:
        turns = self._realtime_sessions.get(session_id)
        if turns is None:
            turns = []
            self._realtime_sessions[session_id] = turns
            while len(self._realtime_sessions) > _MAX_TRACKED_SESSIONS:
                stale, _turns = self._realtime_sessions.popitem(last=False)
                self._realtime_turn_ids.pop(stale, None)
        else:
            self._realtime_sessions.move_to_end(session_id)
        return turns

    def _decide_and_dispatch(self, pending: _PendingTurn, reply_raw: str) -> bool:
        """Dispatch the turn when the brain acknowledged storing it.

        Returns ``True`` when the turn was consumed so callers holding
        pairing state know to clear it. A reply without an acknowledgement
        leaves the pending turn in place: a later part of the same reply may
        still carry it.
        """
        reply = reply_raw.lower()
        if not reply or not any(kw in reply for kw in _ACK_KEYWORDS):
            return False
        if len(pending.user_text) < _MIN_ACK_USER_CHARS:
            log.debug(
                "VoiceFactBridge: acknowledgement matched but the user text is "
                "too short (len=%d).",
                len(pending.user_text),
            )
            return False
        telemetry.inc("voice_turns_ingested_ack")
        log.debug(
            "VoiceFactBridge: brain acknowledged a save, reviewing the user text "
            "(%d chars, lang=%s, origin=%s)",
            len(pending.user_text), pending.user_language, pending.origin,
        )
        self._dispatch(pending, reply_raw, source_kind=f"{pending.origin}-fact")
        return True

    # ------------------------------------------------------------------
    # extraction plumbing
    # ------------------------------------------------------------------

    def _dispatch(
        self, pending: _PendingTurn, reply_raw: str, *, source_kind: str,
    ) -> None:
        """Start one deduped, fire-and-forget Stage-1 review (AP-9)."""
        turn_hash = _turn_hash(pending.user_text)
        review_key = pending.review_key or (
            f"live:v2:{pending.origin}:{pending.captured_at_ns}:{turn_hash}"
        )
        if review_key in self._seen_hashes:
            log.debug(
                "VoiceFactBridge: duplicate turn (key=%s) — skipping",
                review_key[-24:],
            )
            return
        job = _ExtractionJob(
            pending=pending,
            reply_raw=reply_raw,
            source_kind=source_kind,
            turn_hash=turn_hash,
            review_key=review_key,
        )
        task = asyncio.create_task(
            self._extract_safe(job),
            name=f"voice-fact-bridge-extract-{source_kind}",
        )
        # Record the key only AFTER the task was actually created — a
        # create_task failure (loop shutting down) must not permanently
        # block this turn text for the rest of the process lifetime.
        self._seen_hashes[review_key] = None
        while len(self._seen_hashes) > _SEEN_HASHES_MAX:
            self._seen_hashes.popitem(last=False)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _extract_safe(self, job: _ExtractionJob) -> bool:
        """Stage-1 extraction with broad exception handling (background only).

        Returns ``False`` when the wiki has to wait and the job was queued for
        :meth:`retry_waiting`, ``True`` otherwise. The conversation must never
        notice a memory failure — log and move on.
        """
        try:
            # ``capture_seen`` performs a locked SQLite lookup. Keep it off the
            # latency-critical voice event loop (AP-9).
            if await asyncio.to_thread(self._extractor.capture_seen, job.review_key):
                log.debug(
                    "VoiceFactBridge: turn already journaled (key=%s) — skipping",
                    job.review_key[-24:],
                )
                return True
            count = await self._extractor.extract_and_journal(
                job.pending.user_text,
                job.reply_raw,
                source_label=f"{job.source_kind}:{job.pending.captured_at_ns}",
                turn_hash=job.turn_hash,
                review_key=job.review_key,
                session_id=job.pending.session_id,
                turn_id=job.pending.turn_id,
                source_kind=job.source_kind,
                context_turns=job.pending.context_turns,
            )
            log.info(
                "VoiceFactBridge[%s]: review done — %d candidate(s) journaled",
                job.source_kind, count,
            )
            return True
        except BackgroundDeferred as exc:  # held for retry; _hold logs the wait
            self._hold(job, reason=str(exc))
            return False
        except Exception:  # noqa: BLE001
            log.exception(
                "VoiceFactBridge[%s]: review failed, candidates lost.",
                job.source_kind,
            )
            return True

    def _hold(self, job: _ExtractionJob, *, reason: str) -> None:
        if len(self._waiting) >= _MAX_WAITING_SAVES:
            dropped = self._waiting.popleft()
            log.warning(
                "VoiceFactBridge: waiting queue full — dropping the oldest held "
                "save (key=%s)",
                dropped.review_key[-24:],
            )
        self._waiting.append(job)
        log.info(
            "VoiceFactBridge[%s]: explicit save held until the wiki may call a "
            "model again (%d waiting) — %s",
            job.source_kind,
            len(self._waiting),
            reason,
        )

    @property
    def waiting_count(self) -> int:
        """Explicit saves currently held for a later retry."""
        return len(self._waiting)

    async def retry_waiting(self) -> int:
        """Retry held explicit saves, oldest first; stop at the first new wait.

        Called by the wiki integration's background loop once the runaway
        guard allows model calls again. Sequential on purpose: a burst of
        held saves must not turn into a burst of parallel provider calls.
        Returns how many held saves completed.
        """
        if not self._waiting:
            return 0
        done = 0
        async with self._retry_lock:
            while self._waiting:
                job = self._waiting.popleft()
                if not await self._extract_safe(job):
                    # ``_extract_safe`` re-queued it at the back; restore the
                    # original order so the oldest save stays first.
                    self._waiting.rotate(1)
                    break
                done += 1
        if done:
            log.info("VoiceFactBridge: %d held explicit save(s) reviewed", done)
        return done
