"""Bus wildcard subscriber that writes voice sessions into the SessionStore.

Architecture decision: read-only with respect to the pipeline. The recorder
only docks on via ``bus.subscribe_all`` and produces no events back —
which guarantees it is latency-neutral for the voice hot path.

State machine per session (linear, since the pipeline allows only 1 active
voice session):

  IDLE
   |  VoiceSessionStarted
   v
  ACTIVE  ---VoiceTurnStarted--->  TURN_OPEN
                                       | TranscriptFinal / final
                                       | TranscriptionUpdate -> user_text
                                       | BrainTurnCompleted -> tier/provider/tokens/cost
                                       | ToolCallCompleted -> tool_calls.append
                                       | ResponseGenerated -> jarvis_text
                                       | AudioOutFirst -> latency_total_ms
                                       v
                                    VoiceTurnCompleted
                                       v
                                    ACTIVE  (next turn possible)
   |  VoiceSessionEnded
   v
  IDLE


Whitelist for the ``voice_events`` append: not every bus event lands in
the DB — TerminalOutput streams or MissionEvents are irrelevant.
``_RAW_EVENT_KINDS`` (see below) lists the classes relevant for
replay.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionExecuted,
    AudioOutFirst,
    BrainTurnCompleted,
    BrainTurnStarted,
    Event,
    JarvisAgentTaskCompleted,
    JarvisAgentTaskStarted,
    ListeningStarted,
    RealtimeSessionReady,
    ResponseGenerated,
    SpeechSpoken,
    SystemStateChanged,
    ToolCallCompleted,
    ToolCallStarted,
    TranscriptFinal,
    TranscriptionUpdate,
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceTurnCompleted,
    VoiceTurnStarted,
    WakeWordDetected,
)

from .constants import (
    SPOKEN_KIND_COMPLETION,
    SPOKEN_KIND_REPLY,
    SPOKEN_KIND_SUBAGENT,
    VOICE_MODE_PIPELINE,
    VOICE_MODE_REALTIME,
)
from .store import SessionStore

log = logging.getLogger(__name__)


_RAW_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "WakeWordDetected",
        "ListeningStarted",
        "TranscriptFinal",
        "TranscriptionUpdate",
        "BrainTurnStarted",
        "BrainTurnCompleted",
        "BrainTTFT",
        "ToolCallStarted",
        "ToolCallCompleted",
        "ActionExecuted",
        "ResponseGenerated",
        "AudioOutFirst",
        "JarvisAgentTaskStarted",
        "JarvisAgentTaskCompleted",
        "SystemStateChanged",
        "VoiceSessionStarted",
        "VoiceSessionEnded",
        "VoiceTurnStarted",
        "VoiceTurnCompleted",
        "RealtimeSessionReady",
        # Every phrase Jarvis VOICES that is not the brain's normal reply —
        # timeout/unavailable apologies, clarifying questions, skill/mission
        # announcements, the "still working" progress nudge. Without this the
        # Transcription view only shows the conversational reply and silently
        # drops everything else the user actually heard (2026-06-15).
        "SpeechSpoken",
        # Run Inspector forensic events (2026-06-17). The recorder is a read-only
        # wildcard subscriber, so persisting these adds no hot-path cost; they
        # power the latency waterfall, decision path, and error panel. _payload_for
        # already pulls fields by hasattr, so no new imports are needed here.
        "IntentClassified",
        "ActionProposed",
        "ActionApproved",
        "ActionDenied",
        "ErrorOccurred",
        "LatencySpan",
    }
)


@dataclass
class _TurnState:
    """In-memory state of a running turn, before it is finalized."""

    turn_id: str
    idx: int
    started_ms: int
    user_text: str = ""
    user_lang: str = "de"
    jarvis_text: str = ""
    jarvis_lang: str = "de"
    tier: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_total_ms: int = 0
    tool_calls: list[str] = field(default_factory=list)
    # Which voice actually spoke the audible reply ("Fenrir", "Charon", …) and
    # the speaking family ("gemini-live", "openrouter"). Adopted from the
    # authoritative SpeechSpoken track; VoiceTurnCompleted only fills a blank.
    voice_name: str = ""
    voice_provider: str = ""
    # Stage boundaries for think/speak latency calculation. The first thinking
    # segment begins at TranscriptFinal (classic) or final TranscriptionUpdate
    # (Realtime); later THINKING transitions reuse transcript_final_ms as the
    # open-segment anchor because the stored schema needs only aggregate totals.
    transcript_final_ms: int = 0
    speaking_started_ms: int = 0
    think_ms: int = 0
    speak_ms: int = 0
    # Set when the turn ended on a two-turn voice/chat confirmation
    # (BrainTurnCompleted.finish_reason == "voice_confirm_pending"): the reply
    # is a pending yes/no question, not a normal answer.
    awaiting_confirmation: bool = False
    # Realtime emits an authoritative VoiceTurnStarted/VoiceTurnCompleted pair.
    # Supervisor state changes may happen between those two events and must not
    # close the row early under a recorder-generated boundary.
    uses_explicit_lifecycle: bool = False
    finalized: bool = False


@dataclass
class _SessionState:
    """In-memory aggregate of a session."""

    session_id: str
    started_ms: int
    language: str
    turn_count: int = 0
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    providers_used: set[str] = field(default_factory=set)
    current_turn: _TurnState | None = None


class SessionRecorder:
    """EventBus wildcard subscriber, persists voice sessions."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._state: _SessionState | None = None
        # SQLite is synchronous and can briefly wait on another reader/writer.
        # Keep those waits off the asyncio loop so microphone handoff, live
        # level metering, and the native overlay remain responsive while a
        # session event is persisted. The lock preserves event order and keeps
        # this recorder's in-memory state machine single-threaded.
        self._dispatch_lock = asyncio.Lock()
        # ``to_thread`` work cannot be force-cancelled. If the EventBus timeout
        # cancels an awaiting coroutine, this second lock keeps the still-live
        # worker serialized with the next dispatch.
        self._dispatch_thread_lock = threading.Lock()
        # Fallback when the pipeline forgets VoiceTurnStarted — we assign
        # our own turn_id at the first turn-relevant event.
        self._auto_turn_counter: int = 0
        # (session_id, last_turn_id) of the most recently FINALIZED session.
        # A background mission's readback can be voiced after the user hung up
        # (the pipeline lets a readback kind — "completion" or "subagent" —
        # punch through the hangup gate, AD-OE6). The readback carries no
        # session id, so we
        # attach it to this just-ended session. Cleared when a new session
        # starts, so a late readback can never glue onto the wrong session.
        self._afterglow: tuple[str, str | None] | None = None

    def attach(self, bus: EventBus) -> None:
        """Wildcard-subscribe to the bus."""
        bus.subscribe_all(self._on_event)
        log.info("SessionRecorder attached to bus")

    # -----------------------------------------------------------------
    # Main dispatch
    # -----------------------------------------------------------------

    async def _on_event(self, event: Event) -> None:
        """Called by the bus for EVERY event.

        Defensive: swallow every exception — the bus does catch itself,
        but we definitely don't want error-log spam per voice turn.
        """
        try:
            async with self._dispatch_lock:
                await asyncio.to_thread(self._dispatch_serialized, event)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "SessionRecorder dispatch failed",
                exc_info=exc,
                extra={"event_type": type(event).__name__},
            )

    def _dispatch_serialized(self, event: Event) -> None:
        with self._dispatch_thread_lock:
            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        kind = type(event).__name__

        if isinstance(event, VoiceSessionStarted):
            self._on_session_started(event)
            self._maybe_append_raw(event, kind)
            return

        if isinstance(event, VoiceSessionEnded):
            self._maybe_append_raw(event, kind)
            self._on_session_ended(event)
            return

        # Every other event is recorded only while a session is live — with
        # ONE exception: a mission readback that arrives after the user hung up
        # (a "completion"/"subagent" kind, which the pipeline deliberately lets
        # through the hangup gate, AD-OE6) must still be attributed to the
        # just-ended session; otherwise the user hears the answer but the
        # transcript stays empty (forensic 2026-06-19, session 514cddc0).
        if self._state is None:
            if isinstance(event, SpeechSpoken):
                self._record_posthangup_readback(event)
            return

        if isinstance(event, VoiceTurnStarted):
            self._on_turn_started(event)
        elif isinstance(event, VoiceTurnCompleted):
            self._on_turn_completed(event)
        elif isinstance(event, WakeWordDetected):
            # Wake in multi-turn mode — counts as a turn boundary.
            self._ensure_turn_open(event.timestamp_ns // 1_000_000)
        elif isinstance(event, RealtimeSessionReady):
            if event.session_id != self._state.session_id:
                return
            self._on_realtime_ready(event)
        elif isinstance(event, ListeningStarted):
            # This event is emitted only after the classic STT -> Brain -> TTS
            # path has actually begun listening. It therefore also captures a
            # fallback that follows a successful realtime handshake.
            self._store.update_session_voice_mode(
                session_id=self._state.session_id,
                voice_mode=VOICE_MODE_PIPELINE,
            )
            self._ensure_turn_open(event.timestamp_ns // 1_000_000)
        elif isinstance(event, TranscriptFinal):
            self._on_transcript_final(event)
        elif isinstance(event, TranscriptionUpdate):
            self._on_transcription_update(event)
        elif isinstance(event, BrainTurnStarted):
            self._on_brain_started(event)
        elif isinstance(event, BrainTurnCompleted):
            self._on_brain_completed(event)
        elif isinstance(event, ToolCallStarted):
            self._on_tool_started(event)
        elif isinstance(event, ToolCallCompleted):
            self._on_tool_completed(event)
        elif isinstance(event, ActionExecuted):
            self._on_action_executed(event)
        elif isinstance(event, ResponseGenerated):
            self._on_response_generated(event)
        elif isinstance(event, AudioOutFirst):
            self._on_audio_out_first(event)
        elif isinstance(event, JarvisAgentTaskStarted):
            self._on_worker_task_started(event)
        elif isinstance(event, JarvisAgentTaskCompleted):
            if self._state.current_turn:
                self._state.current_turn.cost_usd += event.cost_estimate_usd
        elif isinstance(event, SystemStateChanged):
            self._on_system_state(event)
        elif isinstance(event, SpeechSpoken):
            self._on_speech_spoken(event)

        self._maybe_append_raw(event, kind)

    def _on_speech_spoken(self, event: SpeechSpoken) -> None:
        """Adopt the speaking voice onto the open turn.

        ``SpeechSpoken`` is the authoritative audible track, so a voice it
        names beats the session-level claim in ``VoiceTurnCompleted`` — that is
        what makes a surface-TTS readback inside a realtime session honest
        (the surface voice spoke, not the session voice). The reply phrase
        wins over supplemental phrases; a supplemental phrase only fills a
        blank.
        """
        assert self._state is not None
        t = self._state.current_turn
        voice = getattr(event, "voice", None)
        if t is None or not voice:
            return
        if event.spoken_kind == SPOKEN_KIND_REPLY or not t.voice_name:
            t.voice_name = str(voice)
            t.voice_provider = str(getattr(event, "voice_provider", None) or "")

    # -----------------------------------------------------------------
    # Session lifecycle
    # -----------------------------------------------------------------

    def _on_session_started(self, event: VoiceSessionStarted) -> None:
        if self._state is not None:
            log.warning(
                "VoiceSessionStarted while another session is active — "
                "auto-finalizing previous (id=%s)",
                self._state.session_id,
            )
            self._force_finalize_session(reason="error")

        ts_ms = event.timestamp_ns // 1_000_000
        self._state = _SessionState(
            session_id=event.session_id,
            started_ms=ts_ms,
            language=event.language or "de",
        )
        self._auto_turn_counter = 0
        # A fresh session supersedes the previous one as the attach target for
        # any late completion readback.
        self._afterglow = None
        self._store.upsert_session(
            session_id=event.session_id,
            started_ms=ts_ms,
            wake_keyword=event.wake_keyword,
            language=event.language or "de",
        )
        log.info("SessionRecorder: session started id=%s", event.session_id)

    def _on_realtime_ready(self, event: RealtimeSessionReady) -> None:
        """Record only an accepted provider handshake as Realtime evidence."""
        if self._state is None:
            return
        self._store.update_session_voice_mode(
            session_id=self._state.session_id,
            voice_mode=VOICE_MODE_REALTIME,
        )

    def _on_session_ended(self, event: VoiceSessionEnded) -> None:
        if self._state is None:
            return
        # Auto-finalize an open turn if one still exists
        if self._state.current_turn is not None and not self._state.current_turn.finalized:
            self._finalize_current_turn(end_ms=event.timestamp_ns // 1_000_000)
        self._store.finalize_session(
            session_id=self._state.session_id,
            ended_ms=event.timestamp_ns // 1_000_000,
            hangup_reason=event.hangup_reason or "",
            turn_count=self._state.turn_count,
            total_cost_usd=self._state.total_cost_usd,
            total_tokens_in=self._state.total_tokens_in,
            total_tokens_out=self._state.total_tokens_out,
            providers_used=sorted(self._state.providers_used),
        )
        log.info(
            "SessionRecorder: session ended id=%s reason=%s turns=%d",
            self._state.session_id,
            event.hangup_reason,
            self._state.turn_count,
        )
        last_turn_id = (
            self._state.current_turn.turn_id
            if self._state.current_turn is not None
            else None
        )
        self._afterglow = (self._state.session_id, last_turn_id)
        self._state = None

    def _force_finalize_session(self, *, reason: str) -> None:
        """Emergency brake — when a new VoiceSessionStarted arrives without a
        preceding Ended (pipeline bug or crash recovery)."""
        if self._state is None:
            return
        ts_ms = _now_ms()
        if self._state.current_turn is not None and not self._state.current_turn.finalized:
            self._finalize_current_turn(end_ms=ts_ms)
        self._store.finalize_session(
            session_id=self._state.session_id,
            ended_ms=ts_ms,
            hangup_reason=reason,
            turn_count=self._state.turn_count,
            total_cost_usd=self._state.total_cost_usd,
            total_tokens_in=self._state.total_tokens_in,
            total_tokens_out=self._state.total_tokens_out,
            providers_used=sorted(self._state.providers_used),
        )
        self._state = None

    # -----------------------------------------------------------------
    # Turn lifecycle
    # -----------------------------------------------------------------

    def _on_turn_started(self, event: VoiceTurnStarted) -> None:
        assert self._state is not None
        # If a previous turn wasn't cleanly finalized — auto-close
        if self._state.current_turn and not self._state.current_turn.finalized:
            self._finalize_current_turn(end_ms=event.timestamp_ns // 1_000_000)
        idx = event.turn_index if event.turn_index >= 0 else self._state.turn_count
        ts_ms = event.timestamp_ns // 1_000_000
        self._state.current_turn = _TurnState(
            turn_id=event.turn_id,
            idx=idx,
            started_ms=ts_ms,
            uses_explicit_lifecycle=True,
        )
        self._store.upsert_turn(
            turn_id=event.turn_id,
            session_id=self._state.session_id,
            idx=idx,
            started_ms=ts_ms,
        )

    def _on_turn_completed(self, event: VoiceTurnCompleted) -> None:
        assert self._state is not None
        # Adopt values from the event if they are consistent
        if self._state.current_turn is None or self._state.current_turn.turn_id != event.turn_id:
            log.debug("VoiceTurnCompleted without an open turn — ignoring")
            return
        t = self._state.current_turn
        # Event values win (the pipeline knows exactly)
        if event.user_text:
            t.user_text = event.user_text
        if event.user_lang:
            t.user_lang = event.user_lang
        if event.jarvis_text:
            t.jarvis_text = event.jarvis_text
        if event.jarvis_lang:
            t.jarvis_lang = event.jarvis_lang
        if event.tier:
            t.tier = event.tier
        if event.provider:
            t.provider = event.provider
        if event.model:
            t.model = event.model
        t.tokens_in = max(t.tokens_in, event.tokens_in)
        t.tokens_out = max(t.tokens_out, event.tokens_out)
        t.cost_usd = max(t.cost_usd, event.cost_usd)
        if event.latency_total_ms:
            t.latency_total_ms = event.latency_total_ms
        if event.tool_calls:
            for tc in event.tool_calls:
                if tc not in t.tool_calls:
                    t.tool_calls.append(tc)
        # The audible SpeechSpoken track wins; the session-level claim only
        # fills a blank (see _on_speech_spoken).
        if getattr(event, "voice", None) and not t.voice_name:
            t.voice_name = str(event.voice)
            t.voice_provider = str(getattr(event, "voice_provider", None) or "")
        self._finalize_current_turn(end_ms=event.timestamp_ns // 1_000_000)

    def _ensure_turn_open(self, ts_ms: int) -> None:
        """If the pipeline doesn't send a VoiceTurnStarted — invent one ourselves.

        Called on WakeWordDetected/ListeningStarted — a new turn only
        starts once the previous one is finalized.
        """
        assert self._state is not None
        if self._state.current_turn is not None and not self._state.current_turn.finalized:
            return  # turn still running
        self._auto_turn_counter += 1
        auto_id = f"{self._state.session_id}-auto-{self._auto_turn_counter}"
        self._state.current_turn = _TurnState(
            turn_id=auto_id,
            idx=self._state.turn_count,
            started_ms=ts_ms,
        )
        self._store.upsert_turn(
            turn_id=auto_id,
            session_id=self._state.session_id,
            idx=self._state.turn_count,
            started_ms=ts_ms,
        )

    def _finalize_current_turn(self, *, end_ms: int) -> None:
        assert self._state is not None
        t = self._state.current_turn
        if t is None or t.finalized:
            return
        # Latency default: end - start, if not set via AudioOutFirst.
        if t.latency_total_ms == 0:
            t.latency_total_ms = max(0, end_ms - t.started_ms)
        # Close any open phase. Realtime can alternate THINKING/SPEAKING more
        # than once (short bridge, more work, final answer), so both values are
        # accumulated instead of treated as one start/end pair.
        if t.speaking_started_ms > 0:
            t.speak_ms += max(0, end_ms - t.speaking_started_ms)
            t.speaking_started_ms = 0
        if t.transcript_final_ms > 0:
            t.think_ms += max(0, end_ms - t.transcript_final_ms)
            t.transcript_final_ms = 0
        self._store.finalize_turn(
            turn_id=t.turn_id,
            ended_ms=end_ms,
            user_text=t.user_text,
            user_lang=t.user_lang,
            jarvis_text=t.jarvis_text,
            jarvis_lang=t.jarvis_lang,
            tier=t.tier,
            provider=t.provider,
            model=t.model,
            tokens_in=t.tokens_in,
            tokens_out=t.tokens_out,
            cost_usd=t.cost_usd,
            latency_total_ms=t.latency_total_ms,
            tool_calls=t.tool_calls,
            think_ms=t.think_ms,
            speak_ms=t.speak_ms,
            awaiting_confirmation=t.awaiting_confirmation,
            voice_name=t.voice_name,
            voice_provider=t.voice_provider,
        )
        # Bump aggregates
        self._state.turn_count += 1
        self._state.total_cost_usd += t.cost_usd
        self._state.total_tokens_in += t.tokens_in
        self._state.total_tokens_out += t.tokens_out
        if t.provider:
            self._state.providers_used.add(t.provider)
        t.finalized = True

    # -----------------------------------------------------------------
    # Per-event handlers
    # -----------------------------------------------------------------

    def _on_transcript_final(self, event: TranscriptFinal) -> None:
        assert self._state is not None
        ts_ms = event.timestamp_ns // 1_000_000

        # If the current turn already carries a user_text, this TranscriptFinal
        # belongs to a NEW utterance — close the previous turn and open a new
        # one. Without this, multi-utterance sessions where the brain returns
        # ``suppress_response`` (no SPEAKING transition triggers the boundary
        # via _on_system_state) collapse into a single auto-turn whose
        # user_text is overwritten on every final. The visible symptom is the
        # transcript view showing only the last word ("Auflegen.") for every
        # session — see BUG-008 history and the BUG_TRANSCRIPT_OVERWRITE entry.
        cur = self._state.current_turn
        if cur is not None and cur.user_text and not cur.finalized:
            # Continuation-recombine: the user kept talking while the brain was
            # still thinking, so the pipeline re-thinks the COMBINED sentence as
            # ONE turn (TranscriptFinal.continues_previous). Mirror that here —
            # APPEND the new fragment to the open turn instead of finalizing it
            # and opening a new one — so the Transcription view shows the single
            # prompt the brain processes, not 2-3 split user turns.
            #
            # When it is NOT a continuation we keep splitting (below): that guard
            # exists so multi-utterance sessions with no SPEAKING boundary
            # (brain returned suppress_response) don't overwrite each other into
            # the last word — see BUG-008 history / the BUG_TRANSCRIPT_OVERWRITE
            # entry.
            if getattr(event, "continues_previous", False) and event.transcript is not None:
                frag = event.transcript.text
                if frag:
                    cur.user_text = f"{cur.user_text} {frag}".strip()
                lang = getattr(event.transcript, "language", None) or self._state.language
                cur.user_lang = lang
                cur.transcript_final_ms = ts_ms
                return
            self._finalize_current_turn(end_ms=ts_ms)

        self._ensure_turn_open(ts_ms)
        if event.transcript is not None and self._state.current_turn is not None:
            self._state.current_turn.user_text = event.transcript.text
            lang = getattr(event.transcript, "language", None) or self._state.language
            self._state.current_turn.user_lang = lang
            # Stage anchor for think_ms = TranscriptFinal -> SPEAKING.
            self._state.current_turn.transcript_final_ms = ts_ms

    def _on_transcription_update(self, event: TranscriptionUpdate) -> None:
        """Use Realtime's final transcript as its thinking-phase anchor."""
        if not event.is_final or self._state is None:
            return
        t = self._state.current_turn
        if t is None or t.finalized or not t.uses_explicit_lifecycle:
            return
        t.user_text = event.text
        t.transcript_final_ms = event.timestamp_ns // 1_000_000

    def _on_system_state(self, event: SystemStateChanged) -> None:
        """Track THINKING/SPEAKING boundaries for think_ms + speak_ms.

        This pipeline variant does not emit Phase-L.1 stage events
        (AudioOutFirst, TTSFirstByte). Instead it marks the high-level
        state via ``_set_turn_state`` -> ``_transition``:
        IDLE | LISTENING | THINKING | SPEAKING. We take:
          - SPEAKING start = close and accumulate the current thinking segment
          - THINKING start after SPEAKING = close speech and open more thinking
          - LISTENING start after SPEAKING = close speech AND
            **turn boundary** — the turn is done, the next TranscriptFinal
            opens a new turn via ``_ensure_turn_open``. Without this
            explicit finalization, turn 1 stays open in multi-turn sessions
            and every following user_text/jarvis_text value overwrites the last.
        """
        assert self._state is not None
        t = self._state.current_turn
        if t is None:
            return
        ts_ms = event.timestamp_ns // 1_000_000
        new = (event.new_state or "").upper()
        prev = (event.previous or "").upper()
        if new == "SPEAKING" and prev != "SPEAKING":
            # Close the current thinking segment and open a speaking segment.
            if t.transcript_final_ms > 0:
                t.think_ms += max(0, ts_ms - t.transcript_final_ms)
                t.transcript_final_ms = 0
            t.speaking_started_ms = ts_ms
        elif prev == "SPEAKING" and new != "SPEAKING":
            # Close the current speaking segment. A Realtime bridge can return
            # to THINKING before a later final-answer SPEAKING segment.
            if t.speaking_started_ms > 0:
                t.speak_ms += max(0, ts_ms - t.speaking_started_ms)
                t.speaking_started_ms = 0
            if new == "THINKING":
                t.transcript_final_ms = ts_ms
            # Turn boundary: this turn is complete. Not finalizing here
            # would be wrong — otherwise values would accumulate across all
            # turns of a multi-turn session and only the last turn stays visible.
            # Classic turns infer their boundary from SPEAKING -> LISTENING.
            # Realtime publishes its explicit completion just after the desktop
            # callback makes this transition. Closing it here would make that
            # completion miss its turn_id and create a response-only auto row.
            if not t.uses_explicit_lifecycle:
                self._finalize_current_turn(end_ms=ts_ms)

    def _on_brain_started(self, event: BrainTurnStarted) -> None:
        # Defensive: no hard assert — on race conditions between
        # BrainTurnStarted and VoiceSessionEnded the event would otherwise
        # land only in the logger instead of the turn (bug diagnosis 2026-04-28).
        if self._state is None:
            return
        self._ensure_turn_open(event.timestamp_ns // 1_000_000)
        t = self._state.current_turn
        if t is None:
            return
        # Bug C fix (2026-04-29): no longer adopt provider/model from Started.
        # Instead take it from BrainTurnCompleted — that holds
        # only the SUCCESSFUL provider, not a fallback attempt that later
        # crashed. Prevents hallucination tags in voice_turns
        # (e.g. "openai/gpt-4o" even though no OpenAI key exists).
        # intent_level is the router decision (e.g. "spawn_worker"/
        # "direct_action"/"trivial"). The recorder schema, however, only
        # allows the real tier names for ``tier`` ({"router", "openclaw",
        # "trivial", "fast", "deep", "code"}). Values outside that set are
        # mapped to "router" — the main Jarvis brain answered.
        if event.intent_level and not t.tier:
            t.tier = _normalize_intent_level_to_tier(event.intent_level)

    def _on_worker_task_started(self, event: JarvisAgentTaskStarted) -> None:
        """Jarvis-Agent task spawn — set tier + provider/model for telemetry.

        Wave 4 migration: formerly ``_on_sub_jarvis_started`` with
        ``SubJarvisStarted``-Event. Sub-Jarvis tier was replaced by the worker
        harness (see docs/openclaw-bridge.md §11). Schema preserved 1:1.

        Important for telemetry: without this setter, provider/model stayed
        empty in voice_turns when the worker no longer emitted its own
        BrainTurnStarted — e.g. when the provider chain continued due to a
        missing API key. ``JarvisAgentTaskStarted`` already carries provider/
        model, so we use that as the source.

        Tier is ALWAYS set to ``"jarvis_agent"`` here (not only when empty):
        the worker spawn is the tier-determining action for this turn — even
        when the router had already emitted BrainTurnStarted("router").
        """
        if self._state is None or self._state.current_turn is None:
            return
        t = self._state.current_turn
        t.tier = "jarvis_agent"
        if event.provider:
            t.provider = event.provider
        if event.model:
            t.model = event.model

    def _on_brain_completed(self, event: BrainTurnCompleted) -> None:
        assert self._state is not None
        t = self._state.current_turn
        if t is None:
            return
        t.tokens_in += event.tokens_in
        t.tokens_out += event.tokens_out
        t.cost_usd += event.cost_usd
        # Bug C fix (2026-04-29): adopt provider/model from the Completed
        # event — that is the SUCCESSFUL source. If a later fallback
        # attempt delivers tokens again (rare — multi-step tool
        # loop), the later value wins; that's OK because both were
        # successful. In the normal case of a voice session, _on_brain_completed
        # is called only once per turn with real data.
        if event.provider:
            t.provider = event.provider
        if event.model:
            t.model = event.model
        # A consequential ask-tier tool deferred into a two-turn confirmation
        # ends the turn here; flag it so the transcript labels the reply as a
        # pending yes/no question rather than a normal answer (forensic
        # 2026-06-19). Latch True — a later successful round must not clear it.
        if getattr(event, "finish_reason", "") == "voice_confirm_pending":
            t.awaiting_confirmation = True

    def _on_tool_started(self, event: ToolCallStarted) -> None:
        assert self._state is not None
        t = self._state.current_turn
        if t is None or not event.tool_name:
            return
        if event.tool_name not in t.tool_calls:
            t.tool_calls.append(event.tool_name)

    def _on_tool_completed(self, event: ToolCallCompleted) -> None:
        # Raw-event append only — the list was maintained in _on_tool_started.
        return

    def _on_action_executed(self, event: ActionExecuted) -> None:
        assert self._state is not None
        t = self._state.current_turn
        if t is None or not event.tool_name:
            return
        if event.tool_name not in t.tool_calls:
            t.tool_calls.append(event.tool_name)

    def _on_response_generated(self, event: ResponseGenerated) -> None:
        assert self._state is not None
        self._ensure_turn_open(event.timestamp_ns // 1_000_000)
        t = self._state.current_turn
        if t is None:
            return
        if event.text:
            t.jarvis_text = event.text
        if event.language:
            t.jarvis_lang = event.language

    def _on_audio_out_first(self, event: AudioOutFirst) -> None:
        assert self._state is not None
        t = self._state.current_turn
        if t is None:
            return
        ts_ms = event.timestamp_ns // 1_000_000
        t.latency_total_ms = max(0, ts_ms - t.started_ms)

    # -----------------------------------------------------------------
    # Raw-event append (selective via whitelist)
    # -----------------------------------------------------------------

    def _maybe_append_raw(self, event: Event, kind: str) -> None:
        if self._state is None:
            return
        if kind not in _RAW_EVENT_KINDS:
            return
        turn_id = (
            self._state.current_turn.turn_id
            if self._state.current_turn is not None
            else None
        )
        ts_ms = event.timestamp_ns // 1_000_000
        payload = _payload_for(event)
        self._store.append_event(
            session_id=self._state.session_id,
            turn_id=turn_id,
            ts_ms=ts_ms,
            kind=kind,
            payload=payload,
        )

    def _record_posthangup_readback(self, event: SpeechSpoken) -> None:
        """Persist a readback voiced AFTER the session was closed.

        Both readback kinds qualify — a generic background ``completion`` and the
        attributed ``subagent`` result — the terminal answer of an offloaded
        mission/sub-agent. A progress nudge ("still working") arriving after
        hangup is suppressed by the pipeline and must not be attached here
        either. The row is appended to the just-ended session (the one that
        spawned the mission) and to its last turn, so ``formatter.py`` groups
        it into the transcript exactly like an in-session announcement.
        """
        if self._afterglow is None:
            return
        # Both readback kinds (generic background ``completion`` and the
        # attributed ``subagent`` result) earn a late transcript row; a progress
        # nudge does not.
        if getattr(event, "spoken_kind", "") not in (
            SPOKEN_KIND_COMPLETION,
            SPOKEN_KIND_SUBAGENT,
        ):
            return
        session_id, turn_id = self._afterglow
        self._store.append_event(
            session_id=session_id,
            turn_id=turn_id,
            ts_ms=event.timestamp_ns // 1_000_000,
            kind="SpeechSpoken",
            payload=_payload_for(event),
        )


# --- Helpers ----------------------------------------------------------


# VoiceTurnRow.tier literal — keep in sync with jarvis/sessions/models.py.
# Wave 4 migration: the ``sub_jarvis`` legacy value is still accepted for
# backwards compatibility with old voice sessions in the DB; new turns
# use ``openclaw``.
_VALID_TIERS: frozenset[str] = frozenset(
    {
        "router",
        "openclaw",
        "sub_jarvis",
        "trivial",
        "fast",
        "deep",
        "code",
        "realtime",
    }
)


def _normalize_intent_level_to_tier(intent_level: str) -> str:
    """Maps a router decision (``decision.level``) to a tier name.

    The router emits ``BrainTurnStarted.intent_level`` as the routing
    decision it made (``trivial`` / ``direct_action`` / ``spawn_worker``).
    The recorder schema, however, only allows the tier identifiers
    ``router``/``openclaw``/etc. for ``tier``. Anything outside that set
    becomes ``"router"`` — semantically correct, because in both cases
    (direct_action, spawn) the router brain answered the turn. The
    OpenClaw tier is explicitly overridden in
    ``_on_openclaw_task_started``.
    """
    if intent_level in _VALID_TIERS:
        return intent_level
    return "router"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _payload_for(event: Event) -> dict[str, Any]:
    """Selects the fields of an event relevant for replay.

    Avoids giant payloads (e.g. complete transcript audio refs)
    and filters privacy-sensitive fields. Whitelist > blacklist.
    """
    fields_whitelist = {
        "keyword",
        "confidence",
        "language",
        "text",
        "is_final",
        "lang",
        # The Transcript sub-object on TranscriptFinal/Partial is unwrapped
        # below into payload["text"] + payload["lang"]. Without this entry
        # the unwrap branch is unreachable and TranscriptFinal events are
        # persisted with an empty payload — which is what BUG_TRANSCRIPT_LOST
        # was: the Transcription view had no text to show even when the
        # turn aggregate did, because the raw event replay was blank.
        "transcript",
        "tool_name",
        "args_preview",
        "output_preview",
        # Session-Decision-Log: the brain's rationale on ActionProposed (the
        # "why"). Already redacted + capped at publish time by the ToolExecutor,
        # so persisting it raw here is safe — no unredacted secret can arrive.
        "rationale",
        "success",
        "duration_ms",
        "duration_s",
        "error",
        "provider",
        "model",
        "intent_level",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "text_len",
        "finish_reason",
        # SpeechSpoken: the phrase-kind tag (timeout / announcement / clarify /
        # …). ``text`` + ``language`` are already whitelisted above, so a
        # persisted SpeechSpoken row carries {text, language, spoken_kind}.
        "spoken_kind",
        # SpeechSpoken / VoiceTurnCompleted: which voice actually spoke
        # ("Fenrir", "Charon") and the speaking family ("gemini-live",
        # "openrouter") — user request 2026-07-17.
        "voice",
        "voice_provider",
        # SpeechSpoken: optional technical diagnostic NOT spoken aloud (e.g. a
        # failed Computer-Use exit code + harness reason). LatencySpan also
        # carries a ``detail`` attribute and IS a recorded kind since the Run
        # Inspector change (2026-06-17), so its detail rides along too — that is
        # harmless and occasionally useful context for the latency waterfall.
        "detail",
        "new_state",
        "previous",
        "session_id",
        "turn_id",
        "turn_index",
        "surface",
        "input_sample_rate",
        "output_sample_rate",
        "wake_keyword",
        "hangup_reason",
        "turn_count",
        "user_text",
        "user_lang",
        "jarvis_text",
        "jarvis_lang",
        "tier",
        "latency_total_ms",
        "tool_calls",
        "cache_hit",
        # Run Inspector: decision-path + latency + error fields (2026-06-17).
        "intent",        # IntentClassified.intent
        "risk_tier",     # IntentClassified / ActionProposed
        "approved_by",   # ActionApproved
        "reason",        # ActionDenied
        "phase",         # LatencySpan
        "layer",         # ErrorOccurred
        "error_type",    # ErrorOccurred
        "message",       # ErrorOccurred
        "recoverable",   # ErrorOccurred (bool — _payload_for skips None, keeps False)
    }
    payload: dict[str, Any] = {}
    for k in fields_whitelist:
        if not hasattr(event, k):
            continue
        v = getattr(event, k)
        if v is None:
            continue
        # Tuples to lists for JSON serializability
        if isinstance(v, tuple):
            v = list(v)
        # Transcript sub-object: only text + language
        if k == "transcript" and v is not None:
            payload["text"] = getattr(v, "text", "")
            payload["lang"] = getattr(v, "language", "")
            continue
        payload[k] = v
    # ActionProposed.args may hold PII (an email body / recipient, a search
    # query) — it is deliberately NOT in the whitelist, so the raw dict is never
    # persisted. Pull only the short, enum-like ``action`` selector so forensics
    # can tell which operation a mixed read/write tool ran: before this a gmail
    # read was indistinguishable from a send in the persisted event (forensic
    # 2026-06-19, session dc533e39). Bounded length keeps a tool from smuggling
    # a payload through the key.
    raw_args = getattr(event, "args", None)
    if isinstance(raw_args, dict):
        action = raw_args.get("action")
        if isinstance(action, str) and 0 < len(action) <= 64:
            payload["action"] = action
    return payload


__all__ = ["SessionRecorder"]
