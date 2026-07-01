"""Voice-fact -> Wiki bridge with two parallel ingest paths.

Closes the gap between the voice pipeline and the WikiCurator:

* :class:`StoryTracker` writes ``awareness_episodes`` triggered by
  window-switch / idle, but never sees the spoken user content.
* :class:`SessionRollupWorker` reads ``awareness_episodes`` at idle and
  rolls them into a session digest, but does not look at ``voice_turns``.

This bridge listens for ``TranscriptFinal`` and ``ResponseGenerated`` on
the bus, correlates them as one voice turn, and feeds the user text to
the WikiCurator via two complementary paths:

1. **Ack path (B5, narrow):** when the brain reply contains an
   acknowledgement keyword ("notiert", "vermerkt", ...). Mirrors the
   user-visible contract -- if the brain says it noted the fact, the
   wiki gets the note. False-positive-free, but misses any fact the
   brain replies to conversationally without an ack keyword.

2. **Aggressive path (B8, defence-in-depth):** every user turn with at
   least ``cfg.min_user_chars`` characters is handed to the curator
   anyway, with the curator's prompt acting as the salience filter
   (smalltalk -> empty list, real facts -> pages). Rate-limited to at
   most one ingest per ``cfg.rate_limit_seconds`` so chitchat does not
   burn LLM calls.

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
from jarvis.core.events import MessageSent, ResponseGenerated, TranscriptFinal
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
    "gespeichert",
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


class VoiceFactBridge:
    """Bridge voice-pipeline -> WikiCurator with ack + aggressive paths.

    Construct once at app boot, hand it the bus, a curator and (since
    B8) a :class:`VoiceBridgeConfig`, then call :meth:`start`. The
    bridge subscribes itself and runs until :meth:`stop` is called.

    ``config=None`` falls back to default settings (aggressive_mode=True,
    min_user_chars=30, rate_limit_seconds=60) so legacy callers keep
    working unchanged.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        curator: "WikiCurator",
        config: "VoiceBridgeConfig | None" = None,
        extractor: "ConversationFactExtractor | None" = None,
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
        """Subscribe to TranscriptFinal, MessageSent and ResponseGenerated."""
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
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()
        for task in list(self._inflight):
            task.cancel()
        self._inflight.clear()
        self._started = False

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
        )

    async def _on_response_generated(self, event: ResponseGenerated) -> None:
        """Run ack-path first, then aggressive-path. At most one dispatch fires."""
        reply_raw = (getattr(event, "text", "") or "").strip()
        jarvis_text = reply_raw.lower()
        if not jarvis_text:
            return

        pending = self._pending
        if not pending.user_text:
            return

        # ---- Path 1: ack-keyword match -----------------------------------
        if any(kw in jarvis_text for kw in _ACK_KEYWORDS):
            if len(pending.user_text) < _MIN_ACK_USER_CHARS:
                log.debug(
                    "VoiceFactBridge: ack-keyword matched but user text too "
                    "short (len=%d).",
                    len(pending.user_text),
                )
                return
            self._pending = _PendingTurn()
            telemetry.inc("voice_turns_ingested_ack")
            log.info(
                "VoiceFactBridge[ack]: brain acked storage, capturing user text "
                "(%d chars, lang=%s, origin=%s)",
                len(pending.user_text), pending.user_language, pending.origin,
            )
            self._dispatch(pending, reply_raw, source_kind=f"{pending.origin}-fact")
            return

        # ---- Path 2: aggressive ingest -----------------------------------
        if not self._cfg.aggressive_mode:
            return
        if len(pending.user_text) < self._cfg.min_user_chars:
            return
        if not self._aggressive_rate_limit_ok():
            log.debug(
                "VoiceFactBridge[aggressive]: rate-limited (last ingest "
                "%.1fs ago, limit=%ds).",
                (time.monotonic_ns() - self._last_aggressive_ns) / 1e9,
                self._cfg.rate_limit_seconds,
            )
            return

        self._pending = _PendingTurn()
        self._last_aggressive_ns = time.monotonic_ns()
        telemetry.inc("voice_turns_ingested_aggressive")
        log.info(
            "VoiceFactBridge[aggressive]: brain did not ack but user text "
            "looks fact-shaped, capturing via salience filter "
            "(%d chars, lang=%s, origin=%s)",
            len(pending.user_text), pending.user_language, pending.origin,
        )
        self._dispatch(pending, reply_raw, source_kind=f"{pending.origin}-aggressive")

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
        if turn_hash in self._seen_hashes:
            log.debug(
                "VoiceFactBridge: duplicate turn (hash=%s) — skipping extraction",
                turn_hash[:12],
            )
            return

        task = asyncio.create_task(
            self._extract_safe(
                pending, reply_raw, source_kind=source_kind, turn_hash=turn_hash,
            ),
            name=f"voice-fact-bridge-extract-{source_kind}",
        )
        # Record the hash only AFTER the task was actually created — a
        # create_task failure (loop shutting down) must not permanently
        # block this turn text for the rest of the process lifetime. The
        # dispatch path runs on the event loop, so add-after-create cannot
        # race a concurrent dispatch of the same hash.
        self._seen_hashes[turn_hash] = None
        while len(self._seen_hashes) > _SEEN_HASHES_MAX:
            self._seen_hashes.popitem(last=False)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

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
    ) -> None:
        """Stage-1 extraction with broad exception handling (background only).

        The conversation must never notice a memory failure — log and move
        on; the worst case is one lost candidate fact.
        """
        try:
            if self._extractor.seen_turn(turn_hash):
                log.debug(
                    "VoiceFactBridge: turn already journaled (hash=%s) — skipping",
                    turn_hash[:12],
                )
                return
            count = await self._extractor.extract_and_journal(
                pending.user_text,
                reply_raw,
                source_label=f"{source_kind}:{pending.captured_at_ns}",
                turn_hash=turn_hash,
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
