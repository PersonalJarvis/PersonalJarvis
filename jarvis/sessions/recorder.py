"""Bus-Wildcard-Subscriber, der Voice-Sessions in den SessionStore schreibt.

Architekturentscheidung: read-only zur Pipeline. Der Recorder dockt nur
ueber ``bus.subscribe_all`` an und produziert keine Events zurueck —
damit ist er garantiert latenzneutral fuer den Voice-Hot-Path.

State-Machine pro Session (linear, da Pipeline nur 1 aktive Voice-
Session zulaesst):

  IDLE
   |  VoiceSessionStarted
   v
  ACTIVE  ---VoiceTurnStarted--->  TURN_OPEN
                                       | TranscriptFinal -> user_text
                                       | BrainTurnCompleted -> tier/provider/tokens/cost
                                       | ToolCallCompleted -> tool_calls.append
                                       | ResponseGenerated -> jarvis_text
                                       | AudioOutFirst -> latency_total_ms
                                       v
                                    VoiceTurnCompleted
                                       v
                                    ACTIVE  (naechster Turn moeglich)
   |  VoiceSessionEnded
   v
  IDLE


Whitelist fuer ``voice_events``-Append: nicht jedes Bus-Event landet in
der DB — TerminalOutput-Streams oder MissionEvents sind irrelevant.
``_RAW_EVENT_KINDS`` (siehe unten) listet die fuer Replay relevanten
Klassen.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionExecuted,
    AudioOutFirst,
    BrainTTFT,
    BrainTurnCompleted,
    BrainTurnStarted,
    Event,
    ListeningStarted,
    ResponseGenerated,
    OpenClawTaskCompleted,
    OpenClawTaskStarted,
    SystemStateChanged,
    ToolCallCompleted,
    ToolCallStarted,
    TranscriptFinal,
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceTurnCompleted,
    VoiceTurnStarted,
    WakeWordDetected,
)

from .store import SessionStore

log = logging.getLogger(__name__)


_RAW_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "WakeWordDetected",
        "ListeningStarted",
        "TranscriptFinal",
        "BrainTurnStarted",
        "BrainTurnCompleted",
        "BrainTTFT",
        "ToolCallStarted",
        "ToolCallCompleted",
        "ActionExecuted",
        "ResponseGenerated",
        "AudioOutFirst",
        "OpenClawTaskStarted",
        "OpenClawTaskCompleted",
        "SystemStateChanged",
        "VoiceSessionStarted",
        "VoiceSessionEnded",
        "VoiceTurnStarted",
        "VoiceTurnCompleted",
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
    """In-Memory-State eines laufenden Turns, bevor er finalisiert wird."""

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
    # Stage-Boundaries fuer Think-/Speak-Latenz-Berechnung. Werden aus
    # TranscriptFinal + SystemStateChanged(SPEAKING/LISTENING) abgeleitet,
    # weil diese Pipeline-Variante keine Phase-L.1-Stage-Events emittiert.
    transcript_final_ms: int = 0
    speaking_started_ms: int = 0
    think_ms: int = 0
    speak_ms: int = 0
    finalized: bool = False


@dataclass
class _SessionState:
    """In-Memory-Aggregat einer Session."""

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
    """EventBus-Wildcard-Subscriber, persistiert Voice-Sessions."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._state: _SessionState | None = None
        # Fallback wenn Pipeline VoiceTurnStarted vergisst — wir vergeben
        # selbst eine turn_id beim ersten Turn-relevanten Event.
        self._auto_turn_counter: int = 0

    def attach(self, bus: EventBus) -> None:
        """Wildcard-Subscribe an den Bus."""
        bus.subscribe_all(self._on_event)
        log.info("SessionRecorder attached to bus")

    # -----------------------------------------------------------------
    # Haupt-Dispatch
    # -----------------------------------------------------------------

    async def _on_event(self, event: Event) -> None:
        """Wird vom Bus fuer JEDES Event aufgerufen.

        Defensive: jede Exception schlucken — der Bus catcht zwar selbst,
        aber wir wollen sicher kein Error-Log-Spam pro Voice-Turn.
        """
        try:
            self._dispatch(event)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "SessionRecorder dispatch failed",
                exc_info=exc,
                extra={"event_type": type(event).__name__},
            )

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

        # Alle anderen Events nur wenn eine Session laeuft.
        if self._state is None:
            return

        if isinstance(event, VoiceTurnStarted):
            self._on_turn_started(event)
        elif isinstance(event, VoiceTurnCompleted):
            self._on_turn_completed(event)
        elif isinstance(event, WakeWordDetected):
            # Wake im Multi-Turn-Modus — zaehlt als Turn-Boundary.
            self._ensure_turn_open(event.timestamp_ns // 1_000_000)
        elif isinstance(event, ListeningStarted):
            self._ensure_turn_open(event.timestamp_ns // 1_000_000)
        elif isinstance(event, TranscriptFinal):
            self._on_transcript_final(event)
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
        elif isinstance(event, OpenClawTaskStarted):
            self._on_openclaw_task_started(event)
        elif isinstance(event, OpenClawTaskCompleted):
            if self._state.current_turn:
                self._state.current_turn.cost_usd += event.cost_estimate_usd
        elif isinstance(event, SystemStateChanged):
            self._on_system_state(event)

        self._maybe_append_raw(event, kind)

    # -----------------------------------------------------------------
    # Session-Lifecycle
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
        self._store.upsert_session(
            session_id=event.session_id,
            started_ms=ts_ms,
            wake_keyword=event.wake_keyword,
            language=event.language or "de",
        )
        log.info("SessionRecorder: session started id=%s", event.session_id)

    def _on_session_ended(self, event: VoiceSessionEnded) -> None:
        if self._state is None:
            return
        # Offenen Turn auto-finalisieren falls noch da
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
        self._state = None

    def _force_finalize_session(self, *, reason: str) -> None:
        """Notbremse — wenn ein neues VoiceSessionStarted ohne vorheriges
        Ended kommt (Pipeline-Bug oder Crash-Recovery)."""
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
    # Turn-Lifecycle
    # -----------------------------------------------------------------

    def _on_turn_started(self, event: VoiceTurnStarted) -> None:
        assert self._state is not None
        # Falls ein vorheriger Turn nicht sauber finalisiert wurde — auto-close
        if self._state.current_turn and not self._state.current_turn.finalized:
            self._finalize_current_turn(end_ms=event.timestamp_ns // 1_000_000)
        idx = event.turn_index if event.turn_index >= 0 else self._state.turn_count
        ts_ms = event.timestamp_ns // 1_000_000
        self._state.current_turn = _TurnState(
            turn_id=event.turn_id,
            idx=idx,
            started_ms=ts_ms,
        )
        self._store.upsert_turn(
            turn_id=event.turn_id,
            session_id=self._state.session_id,
            idx=idx,
            started_ms=ts_ms,
        )

    def _on_turn_completed(self, event: VoiceTurnCompleted) -> None:
        assert self._state is not None
        # Werte aus dem Event uebernehmen falls sie konsistent sind
        if self._state.current_turn is None or self._state.current_turn.turn_id != event.turn_id:
            log.debug("VoiceTurnCompleted ohne offenen Turn — ignoriere")
            return
        t = self._state.current_turn
        # Event-Werte gewinnen (Pipeline weiss es genau)
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
        self._finalize_current_turn(end_ms=event.timestamp_ns // 1_000_000)

    def _ensure_turn_open(self, ts_ms: int) -> None:
        """Falls Pipeline keinen VoiceTurnStarted schickt — selber einen erfinden.

        Wird bei WakeWordDetected/ListeningStarted aufgerufen — neuer Turn
        beginnt nur wenn der vorige finalisiert ist.
        """
        assert self._state is not None
        if self._state.current_turn is not None and not self._state.current_turn.finalized:
            return  # Turn laeuft noch
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
        # Latenz default: end - start, falls nicht via AudioOutFirst gesetzt.
        if t.latency_total_ms == 0:
            t.latency_total_ms = max(0, end_ms - t.started_ms)
        # Speak-Latenz: falls Pipeline noch in SPEAKING war beim Turn-End,
        # rechnen wir bis end_ms hoch; sonst war sie schon gesetzt durch
        # SystemStateChanged(LISTENING)-Transition.
        if t.speak_ms == 0 and t.speaking_started_ms > 0:
            t.speak_ms = max(0, end_ms - t.speaking_started_ms)
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
        )
        # Aggregate hochzaehlen
        self._state.turn_count += 1
        self._state.total_cost_usd += t.cost_usd
        self._state.total_tokens_in += t.tokens_in
        self._state.total_tokens_out += t.tokens_out
        if t.provider:
            self._state.providers_used.add(t.provider)
        t.finalized = True

    # -----------------------------------------------------------------
    # Per-Event Handler
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
            self._finalize_current_turn(end_ms=ts_ms)

        self._ensure_turn_open(ts_ms)
        if event.transcript is not None and self._state.current_turn is not None:
            self._state.current_turn.user_text = event.transcript.text
            lang = getattr(event.transcript, "language", None) or self._state.language
            self._state.current_turn.user_lang = lang
            # Stage-Anker fuer think_ms = TranscriptFinal -> SPEAKING.
            self._state.current_turn.transcript_final_ms = ts_ms

    def _on_system_state(self, event: SystemStateChanged) -> None:
        """Tracke SPEAKING/LISTENING-Boundaries fuer think_ms + speak_ms.

        Diese Pipeline-Variante emittiert keine Phase-L.1-Stage-Events
        (AudioOutFirst, TTSFirstByte). Stattdessen markiert sie via
        ``_set_turn_state`` -> ``_transition`` den High-Level-State:
        IDLE | LISTENING | THINKING | SPEAKING. Wir nehmen:
          - SPEAKING-Start  = Anker fuer think_ms (User-Done -> Jarvis-spricht)
          - LISTENING-Start nach SPEAKING = Anker fuer speak_ms-Ende UND
            **Turn-Boundary** — der Turn ist fertig, der naechste TranscriptFinal
            oeffnet via ``_ensure_turn_open`` einen neuen Turn. Ohne diese
            explizite Finalisierung bleibt in Multi-Turn-Sessions Turn 1 offen
            und alle folgenden user_text/jarvis_text-Werte ueberschreiben sich.
        """
        assert self._state is not None
        t = self._state.current_turn
        if t is None:
            return
        ts_ms = event.timestamp_ns // 1_000_000
        new = (event.new_state or "").upper()
        prev = (event.previous or "").upper()
        if new == "SPEAKING" and prev != "SPEAKING":
            # Anker setzen — speak_ms beginnt jetzt.
            t.speaking_started_ms = ts_ms
            # think_ms = transcript_final_ms -> jetzt
            if t.transcript_final_ms > 0 and t.think_ms == 0:
                t.think_ms = max(0, ts_ms - t.transcript_final_ms)
        elif prev == "SPEAKING" and new != "SPEAKING":
            # Sprech-Phase ist vorbei — speak_ms = SPEAKING_start -> jetzt.
            if t.speaking_started_ms > 0 and t.speak_ms == 0:
                t.speak_ms = max(0, ts_ms - t.speaking_started_ms)
            # Turn-Boundary: dieser Turn ist abgeschlossen. Nicht-finalisieren
            # waere falsch — sonst akkumulieren sich Werte ueber alle Turns
            # einer Multi-Turn-Session und nur der letzte Turn bleibt sichtbar.
            self._finalize_current_turn(end_ms=ts_ms)

    def _on_brain_started(self, event: BrainTurnStarted) -> None:
        # Defensive: kein hartes assert — bei Race-Conditions zwischen
        # BrainTurnStarted und VoiceSessionEnded landet das Event sonst
        # nur im Logger statt im Turn (BUG-Diagnose 2026-04-28).
        if self._state is None:
            return
        self._ensure_turn_open(event.timestamp_ns // 1_000_000)
        t = self._state.current_turn
        if t is None:
            return
        # Bug C Fix (2026-04-29): provider/model NICHT mehr aus Started
        # uebernehmen. Stattdessen aus BrainTurnCompleted — dort steht
        # nur der ERFOLGREICHE Provider, kein Fallback-Versuch der spaeter
        # gecrashed ist. Verhindert Halluzinations-Tags in voice_turns
        # (z.B. "openai/gpt-4o" obwohl kein OpenAI-Key existiert).
        # intent_level ist die Router-Decision (z.B. "spawn_worker"/
        # "direct_action"/"trivial"). Das Recorder-Schema erlaubt fuer
        # ``tier`` aber nur die echten Tier-Namen ({"router", "openclaw",
        # "trivial", "fast", "deep", "code"}). Werte ausserhalb mappen wir
        # auf "router" — der Hauptjarvis-Brain hat geantwortet.
        if event.intent_level and not t.tier:
            t.tier = _normalize_intent_level_to_tier(event.intent_level)

    def _on_openclaw_task_started(self, event: OpenClawTaskStarted) -> None:
        """OpenClaw-Task-Spawn markieren — Tier + Provider/Model fuellen.

        Welle-4-Migration: vorher hiess das ``_on_sub_jarvis_started`` mit
        ``SubJarvisStarted``-Event. Sub-Jarvis-Tier wurde durch OpenClaw-
        Bridge ersetzt (siehe docs/openclaw-bridge.md §11). Schema bleibt 1:1.

        Wichtig fuer die Telemetrie: ohne diesen Setter blieb provider/model
        in voice_turns leer, wenn der Worker seinen eigenen
        BrainTurnStarted nicht (mehr) emittierte — z.B. wenn die
        Provider-Chain wegen fehlendem API-Key continue'd. ``OpenClawTaskStarted``
        traegt provider/model bereits, also nutzen wir das als Quelle.

        Tier wird hier IMMER auf ``"openclaw"`` gesetzt (nicht nur wenn
        leer): Der OpenClaw-Spawn ist die ``tier``-bestimmende Aktion fuer
        diesen Turn — auch wenn vorher bereits der Router seinen
        BrainTurnStarted("router") emittiert hatte.
        """
        if self._state is None or self._state.current_turn is None:
            return
        t = self._state.current_turn
        t.tier = "openclaw"
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
        # Bug C Fix (2026-04-29): provider/model aus Completed-Event
        # uebernehmen — das ist die ERFOLGREICHE Quelle. Wenn ein spaeterer
        # Fallback-Versuch nochmal Tokens liefert (selten — Multi-Step-Tool-
        # Loop), gewinnt der spaetere Wert; das ist OK weil beide erfolgreich
        # sind. Im Normalfall einer Voice-Session wird _on_brain_completed
        # nur einmal pro Turn mit echten Daten aufgerufen.
        if event.provider:
            t.provider = event.provider
        if event.model:
            t.model = event.model

    def _on_tool_started(self, event: ToolCallStarted) -> None:
        assert self._state is not None
        t = self._state.current_turn
        if t is None or not event.tool_name:
            return
        if event.tool_name not in t.tool_calls:
            t.tool_calls.append(event.tool_name)

    def _on_tool_completed(self, event: ToolCallCompleted) -> None:
        # Nur Roh-Event-Append — Liste wurde in _on_tool_started gepflegt.
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
    # Raw-Event-Append (selektiv via Whitelist)
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


# --- Helpers ----------------------------------------------------------


# VoiceTurnRow.tier-Literal — synchron mit jarvis/sessions/models.py halten.
# Welle-4-Migration: ``sub_jarvis`` Legacy-Wert weiter akzeptiert fuer
# Backwards-Kompatibilitaet alter Voice-Sessions in der DB; neue Turns
# nutzen ``openclaw``.
_VALID_TIERS: frozenset[str] = frozenset(
    {"router", "openclaw", "sub_jarvis", "trivial", "fast", "deep", "code"}
)


def _normalize_intent_level_to_tier(intent_level: str) -> str:
    """Mappt eine Router-Decision (``decision.level``) auf einen Tier-Namen.

    Der Router emittiert ``BrainTurnStarted.intent_level`` als die getroffene
    Routing-Decision (``trivial`` / ``direct_action`` / ``spawn_worker``).
    Recorder-Schema erlaubt fuer ``tier`` aber nur die Tier-Bezeichner
    ``router``/``openclaw``/usw. Alles ausserhalb wird zu ``"router"`` —
    semantisch korrekt, weil in beiden Faellen (direct_action, spawn) der
    Router-Brain den Turn beantwortet hat. OpenClaw-Tier wird in
    ``_on_openclaw_task_started`` explizit ueberschrieben.
    """
    if intent_level in _VALID_TIERS:
        return intent_level
    return "router"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _payload_for(event: Event) -> dict[str, Any]:
    """Selektiert die fuer Replay relevanten Felder eines Events.

    Vermeidet Riesen-Payloads (z.B. komplette Transcript-Audio-Refs)
    und filtert privacy-sensitive Felder. Whitelist > Blacklist.
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
        # Tuples zu Listen fuer JSON-Serialisierbarkeit
        if isinstance(v, tuple):
            v = list(v)
        # Transcript-Sub-Object: nur text + language
        if k == "transcript" and v is not None:
            payload["text"] = getattr(v, "text", "")
            payload["lang"] = getattr(v, "language", "")
            continue
        payload[k] = v
    return payload


__all__ = ["SessionRecorder"]
