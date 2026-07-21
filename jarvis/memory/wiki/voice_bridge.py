"""Voice-fact -> Wiki bridge with two parallel ingest paths.

Closes the gap between the voice pipeline and the WikiCurator:

* :class:`StoryTracker` writes ``awareness_episodes`` triggered by
  window-switch / idle, but never sees the spoken user content.
* :class:`SessionRollupWorker` reads ``awareness_episodes`` at idle and
  rolls them into a session digest, but does not look at ``voice_turns``.

This bridge listens for ``TranscriptFinal`` and ``ResponseGenerated`` on
the bus, correlates them as one voice turn, and feeds the user text to
the WikiCurator via two complementary paths:

The realtime engine never emits ``TranscriptFinal``/``MessageSent`` — its
one signal carrying both final texts of a turn is ``VoiceTurnCompleted``
(tier="realtime"). The bridge consumes that event directly (no pairing
state needed) and routes it through the SAME two paths below, so both
voice engines share one ingest contract.

1. **Ack path (B5, narrow):** when the brain reply contains an
   acknowledgement keyword ("notiert", "vermerkt", ...). Mirrors the
   user-visible contract -- if the brain says it noted the fact, the
   wiki gets the note. False-positive-free, but misses any fact the
   brain replies to conversationally without an ack keyword.

2. **Aggressive path (B8, defence-in-depth):** every user turn with at
   least ``cfg.min_user_chars`` characters is handed to the curator
   anyway, with the curator's prompt acting as the salience filter
   (smalltalk -> empty list, real facts -> pages). An optional
   ``cfg.rate_limit_seconds`` can reduce review calls, but the default is
   zero so consecutive durable facts are never silently skipped.

Both paths are fire-and-forget background tasks. The voice path is
never blocked, and a failure on one path never crashes the other. When
both fire on the same turn the bridge deduplicates so the curator sees
the text only once.

Configuration: :class:`jarvis.core.config.VoiceBridgeConfig`. The
aggressive path can be disabled by setting ``aggressive_mode = false``;
the ack path is always on.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
    from jarvis.core.config import VoiceBridgeConfig
    from jarvis.memory.wiki.curator import WikiCurator
    from jarvis.memory.wiki.extractor import ConversationFactExtractor

log = logging.getLogger(__name__)

# Bounded in-memory dedupe of recently dispatched turns. A voice turn can
# surface twice (TranscriptFinal AND the server's MessageSent mirror); the
# hash gate makes sure only one extraction fires per distinct turn text.
_SEEN_HASHES_MAX = 128

# Per-turn extractions captured DURING a live realtime call are deferred and
# run at session end: each extraction is an LLM round (provider call, 429
# retry storms, Codex CLI subprocess spawns) on the same event loop that
# paces the call's audio, and freshly journaled candidates additionally
# trigger in-call consolidator judge rounds (live 2026-07-21 11:31: a
# 10.9 s judge round mid-call; the loop-stall forensics measured 481 ms).
# AP-9: awareness work stays off the voice hot path. Bounded so a session
# that never ends cannot grow the queue without limit.
_DEFERRED_MAX_PER_SESSION = 200


def _turn_hash(text: str) -> str:
    """Stable hash of a normalised turn text (case/whitespace-insensitive)."""
    normalised = " ".join((text or "").casefold().split())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()  # noqa: S324 — dedupe key, not security


# Keywords (lowercase, simple substring match) that mark the brain's
# reply as "yes, I stored this". Keep narrow -- false positives mean
# noise in the wiki; false negatives are caught by the aggressive path.
_ACK_KEYWORDS = (  # i18n-allow: German acknowledgement-phrase matching vocabulary
    "notiert",
    "vermerkt",
    "gespeichert",  # i18n-allow: German acknowledgement-phrase matching vocabulary
    "merke ich",
    "merke mir",
    "noted",
    "got it",
    "saved",
)

# Minimum length for the ack path to consider the user utterance worth
# curating. Filters out "ja", "ok", "hallo" -- anything shorter is
# almost certainly not a fact. The aggressive path uses its own,
# higher threshold from config.
_MIN_ACK_USER_CHARS = 12


@dataclass
class _PendingTurn:
    """User text from TranscriptFinal/MessageSent, held until ResponseGenerated."""

    user_text: str = ""
    user_language: str = ""
    captured_at_ns: int = 0
    # "voice" (TranscriptFinal) or "chat" (MessageSent role=user) — only
    # labels the journal source; the processing pipeline is identical.
    origin: str = "voice"
    session_id: str = ""
    turn_id: str = ""
    review_key: str = ""
    context_turns: tuple[ConversationContextTurn, ...] = ()


class VoiceFactBridge:
    """Bridge voice-pipeline -> WikiCurator with ack + aggressive paths.

    Construct once at app boot, hand it the bus, a curator and (since
    B8) a :class:`VoiceBridgeConfig`, then call :meth:`start`. The
    bridge subscribes itself and runs until :meth:`stop` is called.

    ``config=None`` falls back to default settings (aggressive_mode=True,
    min_user_chars=12, rate_limit_seconds=0) so legacy callers keep
    working unchanged.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        curator: WikiCurator,
        config: VoiceBridgeConfig | None = None,
        extractor: ConversationFactExtractor | None = None,
    ) -> None:
        # Late import keeps the dataclass-only module import-cheap and
        # avoids a circular-import risk with ``jarvis.core.config`` in
        # very-early bootstrap paths.
        if config is None:
            from jarvis.core.config import VoiceBridgeConfig
            config = VoiceBridgeConfig()
        self._bus = bus
        self._curator = curator
        self._cfg = config
        # Wave-2: when an extractor is attached, both paths feed the
        # candidate journal (Stage 1) instead of the legacy blind
        # per-turn curator ingest. ``None`` keeps the legacy behaviour
        # (WikiIntegrationConfig.fallback_to_direct_ingest posture).
        self._extractor = extractor
        self._pending = _PendingTurn()
        self._unsubs: list[Any] = []
        self._inflight: set[asyncio.Task[Any]] = set()
        self._session_inflight: dict[str, set[asyncio.Task[Any]]] = {}
        # In-call extraction jobs held back until the session ends (AP-9).
        # Keyed by session id; each entry is the full argument tuple of one
        # already-deduped `_extract_safe` run.
        self._deferred_extractions: dict[
            str, list[tuple[_PendingTurn, str, str, str, str]]
        ] = {}
        self._realtime_sessions: dict[str, list[ConversationContextTurn]] = {}
        self._realtime_turn_ids: dict[str, set[str]] = {}
        self._started = False
        # Recently dispatched turn hashes (bounded LRU) — dedupes the
        # TranscriptFinal/MessageSent double delivery of the same turn.
        self._seen_hashes: OrderedDict[str, None] = OrderedDict()
        # Monotonic ns -- never compared across reboots, so wall-clock
        # drift is irrelevant. Initialised to a value far enough in the
        # past that the first aggressive ingest always passes the gate.
        self._last_aggressive_ns: int = -(10**18)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Subscribe to the turn events of both voice engines."""
        if self._started:
            return
        self._unsubs.append(
            self._bus.subscribe(TranscriptFinal, self._on_transcript_final)
        )
        self._unsubs.append(
            self._bus.subscribe(MessageSent, self._on_user_message)
        )
        self._unsubs.append(
            self._bus.subscribe(ResponseGenerated, self._on_response_generated)
        )
        self._unsubs.append(
            self._bus.subscribe(VoiceTurnCompleted, self._on_voice_turn_completed)
        )
        self._unsubs.append(
            self._bus.subscribe(VoiceSessionEnded, self._on_voice_session_ended)
        )
        self._started = True
        log.info(
            "VoiceFactBridge started "
            "(aggressive_mode=%s, min_user_chars=%d, rate_limit_s=%d)",
            self._cfg.aggressive_mode,
            self._cfg.min_user_chars,
            self._cfg.rate_limit_seconds,
        )

    def stop(self) -> None:
        """Cancel in-flight ingests and unsubscribe. Idempotent."""
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
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001, S110
                pass
        self._unsubs.clear()
        tasks = tuple(self._inflight)
        for task in tasks:
            task.cancel()
        self._session_inflight.clear()
        self._realtime_sessions.clear()
        self._realtime_turn_ids.clear()
        self._deferred_extractions.clear()
        self._started = False
        return tasks

    # ------------------------------------------------------------------
    # bus handlers
    # ------------------------------------------------------------------

    async def _on_transcript_final(self, event: TranscriptFinal) -> None:
        """Capture the user text. Don't ingest yet -- wait for the brain reply."""
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

        Mirrors :meth:`_on_transcript_final` for the text path so chat turns
        feed the same journal. Non-user roles (assistant/preamble/...) are
        ignored. A voice turn that the server mirrors as ``MessageSent`` is
        deduped later by the turn-hash gate, never double-extracted.
        """
        if (getattr(event, "role", "") or "") != "user":
            return
        text = (getattr(event, "text", "") or "").strip()
        if not text:
            return
        ts_ns = getattr(event, "timestamp_ns", 0) or 0
        # A voice turn already sits in pending (TranscriptFinal fired first):
        # do not downgrade its origin to "chat" when the server mirrors it.
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
        """Pair the pending user text with the brain reply and ingest."""
        reply_raw = (getattr(event, "text", "") or "").strip()
        if not reply_raw:
            return

        pending = self._pending
        if not pending.user_text:
            return
        if self._decide_and_dispatch(pending, reply_raw):
            self._pending = _PendingTurn()

    async def _on_voice_turn_completed(self, event: VoiceTurnCompleted) -> None:
        """Ingest a REALTIME turn — both final texts arrive on this one event.

        The realtime engine emits ``TranscriptionUpdate`` (never
        ``TranscriptFinal``/``MessageSent``), so the pairing path above stays
        silent for it. Pipeline turns (any other tier) are already ingested
        via that pairing and MUST be ignored here — reacting to both signals
        would double-extract every pipeline turn. The turn-hash gate in
        :meth:`_dispatch` additionally dedupes any residual overlap.

        Unlike the pairing path, an empty reply does not block ingestion:
        a realtime turn can end without an output transcript (barge-in) and
        the fact lives in the user text.
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
        session_turns = self._realtime_sessions.setdefault(session_id, [])
        seen_ids = self._realtime_turn_ids.setdefault(session_id, set())
        context_turns = tuple(session_turns[-5:])
        reply_raw = (getattr(event, "jarvis_text", "") or "").strip()
        if turn_id not in seen_ids:
            session_turns.append(
                ConversationContextTurn(
                    turn_id=turn_id,
                    user_text=user_text,
                    assistant_text=reply_raw,
                )
            )
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
        """Schedule one full-run Realtime completeness sweep off the hot path."""
        session_id = (getattr(event, "session_id", "") or "").strip()
        turns = tuple(self._realtime_sessions.pop(session_id, ()))
        self._realtime_turn_ids.pop(session_id, None)
        deferred = tuple(self._deferred_extractions.pop(session_id, ()))
        if deferred and self._extractor is not None:
            # Registered under the session BEFORE the sweep task is created,
            # so the sweep's inflight snapshot waits for these turn reviews
            # exactly as it waited for their in-call predecessors.
            flush = asyncio.create_task(
                self._run_deferred_extractions(session_id, deferred),
                name=f"wiki-realtime-deferred-extract-{session_id[:12]}",
            )
            self._inflight.add(flush)
            flush.add_done_callback(self._inflight.discard)
            session_tasks = self._session_inflight.setdefault(session_id, set())
            session_tasks.add(flush)

            def _discard_flush(done: asyncio.Task[Any]) -> None:
                active = self._session_inflight.get(session_id)
                if active is None:
                    return
                active.discard(done)
                if not active:
                    self._session_inflight.pop(session_id, None)

            flush.add_done_callback(_discard_flush)
        if self._extractor is None or not session_id or not turns:
            return
        task = asyncio.create_task(
            self._sweep_session_safe(session_id, turns),
            name=f"wiki-realtime-session-sweep-{session_id[:12]}",
        )
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    def _decide_and_dispatch(self, pending: _PendingTurn, reply_raw: str) -> bool:
        """Run ack-path first, then aggressive-path. At most one dispatch fires.

        Returns ``True`` when the turn was consumed (dispatched) so callers
        holding pairing state know to clear it.
        """
        jarvis_text = reply_raw.lower()

        # ---- Path 1: ack-keyword match -----------------------------------
        if jarvis_text and any(kw in jarvis_text for kw in _ACK_KEYWORDS):
            if len(pending.user_text) < _MIN_ACK_USER_CHARS:
                log.debug(
                    "VoiceFactBridge: ack-keyword matched but user text too "
                    "short (len=%d).",
                    len(pending.user_text),
                )
                if self._extractor is not None:
                    self._dispatch(
                        pending,
                        reply_raw,
                        source_kind=f"{pending.origin}-filtered",
                    )
                    return True
                return False
            telemetry.inc("voice_turns_ingested_ack")
            log.info(
                "VoiceFactBridge[ack]: brain acked storage, capturing user text "
                "(%d chars, lang=%s, origin=%s)",
                len(pending.user_text), pending.user_language, pending.origin,
            )
            self._dispatch(pending, reply_raw, source_kind=f"{pending.origin}-fact")
            return True

        # ---- Path 2: aggressive ingest -----------------------------------
        if not self._cfg.aggressive_mode:
            return False
        min_chars = (
            self._extractor.min_user_chars
            if self._extractor is not None
            else int(self._cfg.min_user_chars)
        )
        if len(pending.user_text) < min_chars:
            if self._extractor is not None:
                self._dispatch(
                    pending,
                    reply_raw,
                    source_kind=f"{pending.origin}-filtered",
                )
                return True
            return False
        if not self._aggressive_rate_limit_ok():
            log.debug(
                "VoiceFactBridge[aggressive]: rate-limited (last ingest "
                "%.1fs ago, limit=%ds).",
                (time.monotonic_ns() - self._last_aggressive_ns) / 1e9,
                self._cfg.rate_limit_seconds,
            )
            return False

        self._last_aggressive_ns = time.monotonic_ns()
        telemetry.inc("voice_turns_ingested_aggressive")
        log.info(
            "VoiceFactBridge[aggressive]: brain did not ack but user text "
            "looks fact-shaped, capturing via salience filter "
            "(%d chars, lang=%s, origin=%s)",
            len(pending.user_text), pending.user_language, pending.origin,
        )
        self._dispatch(pending, reply_raw, source_kind=f"{pending.origin}-aggressive")
        return True

    # ------------------------------------------------------------------
    # ingest plumbing
    # ------------------------------------------------------------------

    def _dispatch(
        self, pending: _PendingTurn, reply_raw: str, *, source_kind: str,
    ) -> None:
        """Route one captured turn to Stage-1 extraction (or legacy ingest).

        Extractor mode dedupes by turn hash FIRST (in-memory LRU + the
        journal's durable ``seen_turn``) so the TranscriptFinal/MessageSent
        double delivery of one turn never extracts twice. Always
        fire-and-forget — the voice path is never blocked (AP-9).
        """
        if self._extractor is None:
            self._spawn_ingest(pending, source_kind=source_kind)
            return

        turn_hash = _turn_hash(pending.user_text)
        review_key = pending.review_key or (
            f"live:v2:{pending.origin}:{pending.captured_at_ns}:{turn_hash}"
        )
        if review_key in self._seen_hashes:
            log.debug(
                "VoiceFactBridge: duplicate turn (hash=%s) — skipping extraction",
                review_key[-24:],
            )
            return

        if (
            pending.origin == "realtime"
            and pending.session_id
            and pending.session_id in self._realtime_sessions
        ):
            # The call is still live: hold the LLM round back (AP-9). The
            # turn is already deduped, so it is recorded as seen now — the
            # queue append cannot fail the way create_task can.
            queue = self._deferred_extractions.setdefault(
                pending.session_id, []
            )
            if len(queue) >= _DEFERRED_MAX_PER_SESSION:
                log.warning(
                    "VoiceFactBridge: deferred-extraction queue full for %s "
                    "— dropping turn (hash=%s)",
                    pending.session_id[:12],
                    review_key[-24:],
                )
                return
            queue.append(
                (pending, reply_raw, source_kind, turn_hash, review_key)
            )
            self._seen_hashes[review_key] = None
            while len(self._seen_hashes) > _SEEN_HASHES_MAX:
                self._seen_hashes.popitem(last=False)
            log.debug(
                "VoiceFactBridge[%s]: extraction deferred until session end "
                "(%d queued)",
                source_kind,
                len(queue),
            )
            return

        task = asyncio.create_task(
            self._extract_safe(
                pending,
                reply_raw,
                source_kind=source_kind,
                turn_hash=turn_hash,
                review_key=review_key,
            ),
            name=f"voice-fact-bridge-extract-{source_kind}",
        )
        # Record the hash only AFTER the task was actually created — a
        # create_task failure (loop shutting down) must not permanently
        # block this turn text for the rest of the process lifetime. The
        # dispatch path runs on the event loop, so add-after-create cannot
        # race a concurrent dispatch of the same hash.
        self._seen_hashes[review_key] = None
        while len(self._seen_hashes) > _SEEN_HASHES_MAX:
            self._seen_hashes.popitem(last=False)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        if pending.session_id:
            session_tasks = self._session_inflight.setdefault(pending.session_id, set())
            session_tasks.add(task)

            def _discard_session_task(done: asyncio.Task[Any]) -> None:
                active = self._session_inflight.get(pending.session_id)
                if active is None:
                    return
                active.discard(done)
                if not active:
                    self._session_inflight.pop(pending.session_id, None)

            task.add_done_callback(_discard_session_task)

    def _spawn_ingest(self, pending: _PendingTurn, *, source_kind: str) -> None:
        """Start a fire-and-forget ingest task. Voice path is never blocked."""
        task = asyncio.create_task(
            self._ingest_safe(pending, source_kind=source_kind),
            name=f"voice-fact-bridge-{source_kind}",
        )
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _extract_safe(
        self,
        pending: _PendingTurn,
        reply_raw: str,
        *,
        source_kind: str,
        turn_hash: str,
        review_key: str,
    ) -> None:
        """Stage-1 extraction with broad exception handling (background only).

        The conversation must never notice a memory failure — log and move
        on; the worst case is one lost candidate fact.
        """
        try:
            if self._extractor.capture_seen(review_key):
                log.debug(
                    "VoiceFactBridge: turn already journaled (hash=%s) — skipping",
                    review_key[-24:],
                )
                return
            count = await self._extractor.extract_and_journal(
                pending.user_text,
                reply_raw,
                source_label=f"{source_kind}:{pending.captured_at_ns}",
                turn_hash=turn_hash,
                review_key=review_key,
                session_id=pending.session_id,
                turn_id=pending.turn_id,
                source_kind=source_kind,
                context_turns=pending.context_turns,
            )
            log.info(
                "VoiceFactBridge[%s]: extraction done — %d candidate(s) journaled",
                source_kind, count,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "VoiceFactBridge[%s]: extraction failed, candidates lost.",
                source_kind,
            )

    async def _run_deferred_extractions(
        self,
        session_id: str,
        jobs: tuple[tuple[_PendingTurn, str, str, str, str], ...],
    ) -> None:
        """Run the call's held-back per-turn extractions, one at a time.

        Sequential on purpose: firing the whole backlog concurrently at
        hangup would reproduce the in-call 429 retry storms this deferral
        removes, just compressed into one burst.
        """
        log.info(
            "VoiceFactBridge: running %d deferred extraction(s) for %s",
            len(jobs),
            session_id[:12],
        )
        for pending, reply_raw, source_kind, turn_hash, review_key in jobs:
            await self._extract_safe(
                pending,
                reply_raw,
                source_kind=source_kind,
                turn_hash=turn_hash,
                review_key=review_key,
            )

    async def _sweep_session_safe(
        self,
        session_id: str,
        turns: tuple[ConversationContextTurn, ...],
    ) -> None:
        """Wait for turn reviews, then audit the complete Realtime run once."""
        try:
            active = tuple(self._session_inflight.get(session_id, ()))
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            count = await self._extractor.extract_session_and_journal(
                turns,
                session_id=session_id,
                source_label=f"realtime-session-sweep:{session_id}",
            )
            log.info(
                "VoiceFactBridge[session-sweep]: %d candidate(s) journaled for %s",
                count,
                session_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception(
                "VoiceFactBridge[session-sweep]: extraction failed for %s",
                session_id,
            )

    def _aggressive_rate_limit_ok(self) -> bool:
        """True when at least ``rate_limit_seconds`` have passed since the last fire."""
        limit_ns = int(self._cfg.rate_limit_seconds) * 1_000_000_000
        return (time.monotonic_ns() - self._last_aggressive_ns) >= limit_ns

    async def _ingest_safe(
        self, pending: _PendingTurn, *, source_kind: str,
    ) -> None:
        """Curator ingest with broad exception handling.

        Voice path must never crash because the wiki write failed -- log
        and move on. The user already finished their turn, so the worst
        case is a silent fact-loss, which is logged once.
        """
        try:
            result = await self._curator.ingest(
                pending.user_text,
                f"{source_kind}:{pending.captured_at_ns}",
            )
            log.info(
                "VoiceFactBridge[%s]: ingest done -- %s",
                source_kind, _format_result(result),
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "VoiceFactBridge[%s]: ingest failed, fact lost.", source_kind,
            )


def _format_result(result: Any) -> str:
    """Compact human-readable summary of a WriteResult for log lines."""
    applied = getattr(result, "applied", []) or []
    rolled = getattr(result, "failed_validation", []) or []
    skipped = getattr(result, "skipped_due_to_recent_edit", []) or []
    return (
        f"applied={len(applied)} "
        f"rolled_back={len(rolled)} "
        f"skipped={len(skipped)}"
    )
