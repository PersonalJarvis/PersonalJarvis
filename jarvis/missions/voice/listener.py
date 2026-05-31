"""Bus-Listener fuer Mission-Events -> TTS-Pipeline.

Subscribed alle Mission-Events via `MissionBus.subscribe_all`, filtert auf
voice-getriggerte Missionen (source_actor=`hauptjarvis`), mapped Event-Types
auf MissionReadback-Methods und ruft die TTS-Synthesize-Funktion.

ADR-0009 Decision §"Voice-Readback nur fuer voice-getriggerte Missionen":
UI-getriggerte Missionen kriegen Toast (UI hat Live-Updates via WS).

Filterung: lookup MissionDispatched-Event aus Store (das Dispatched-Event
ist persistiert via WAL — kein Race zwischen Mission-Start und Subscriber-
Wiring). Cached pro Mission-ID damit der Lookup nicht pro Event passiert.

Audit F-AUDIT-2 (2026-04-29): Mission-Readback-Texte laufen jetzt durch
``scrub_for_voice`` bevor sie an TTS gehen. Ohne diesen Filter gingen
Tool-Use-Markup, "Sir"-Anrede oder Engineering-Jargon ungefiltert an den
User — siehe ``docs/persona-audit-report.md`` F-AUDIT-2.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from jarvis.brain.output_filter import scrub_for_voice

from ..event_bus import MissionBus
from ..event_store import MissionEventStore
from ..events import (
    EventEnvelope,
    MissionApproved,
    MissionBudgetWarning,
    MissionCancelled,
    MissionFailed,
    MissionTimedOut,
    WorkerCorrectionRequired,
    WorkerKilled,
)
from .readback import Lang, MissionReadback


logger = logging.getLogger(__name__)


# Type fuer die TTS-Synthesize-Funktion. Signatur kompatibel mit
# `SpeechPipeline._tts.synthesize(text, language_code=...)` — wir wrappen
# das im Bootstrap zu einer einfachen `async fn(text, lang)`.
TTSSpeakFn = Callable[[str, Lang], Awaitable[None]]


class MissionVoiceListener:
    """Subscribes auf MissionBus + filtert auf voice-source-Missions."""

    def __init__(
        self,
        *,
        bus: MissionBus,
        store: MissionEventStore,
        readback: MissionReadback,
        tts_speak_fn: TTSSpeakFn,
        announce_critic_loop: bool = False,
        language_default: Lang = "de",
    ) -> None:
        self._bus = bus
        self._store = store
        self._readback = readback
        self._tts = tts_speak_fn
        self._announce_critic_loop = announce_critic_loop
        self._lang_default = language_default
        # Cache: mission_id -> (is_voice, language). Fuellung lazy beim ersten
        # Event pro Mission. None = noch nicht aufgeloest.
        self._mission_voice_cache: dict[str, tuple[bool, Lang]] = {}

    async def start(self) -> None:
        """Registriert den Subscriber. Idempotent: subscribe_all ist append-only."""
        self._bus.subscribe_all(self._on_event)
        logger.info("MissionVoiceListener: bus-subscribe registered")

    async def _on_event(self, env: EventEnvelope) -> None:
        """Bus-Handler. Drop-in-No-Op bei Fehler — TTS darf den Bus nie blocken."""
        try:
            await self._dispatch(env)
        except Exception:  # noqa: BLE001
            logger.warning("MissionVoiceListener crashed", exc_info=True)

    async def _dispatch(self, env: EventEnvelope) -> None:
        # Pre-filter: ist die Mission voice-getriggert?
        is_voice, lang = await self._resolve_voice_meta(env.mission_id)
        if not is_voice:
            return

        text = self._render(env, lang)
        if not text:
            return

        # Audit F-AUDIT-2: Mission-Readback durch Output-Filter schicken,
        # bevor wir an TTS uebergeben. Sonst leakten Mission-Outputs mit
        # Tool-Use-Markup / "Sir"-Anrede / Engineering-Jargon ungefiltert
        # an den User. Sprache der Mission (de/en) wird an scrub_for_voice
        # uebergeben fuer die richtige Fallback-Phrase.
        scrubbed = scrub_for_voice(text, language=lang)
        if scrubbed.actions:
            logger.info(
                "MissionVoiceListener filter [%s]: %s (fallback=%s)",
                lang, scrubbed.actions, scrubbed.fallback_used,
            )
        if not scrubbed.cleaned.strip():
            logger.info("MissionVoiceListener: text leer nach filter — skip")
            return

        await self._tts(scrubbed.cleaned, lang)

    def _render(self, env: EventEnvelope, lang: Lang) -> str:
        """Map Event-Payload -> Voice-Text. Empty string wenn der Event-Type
        keine Voice-Antwort hat.
        """
        payload = env.payload

        if isinstance(payload, MissionApproved):
            return self._readback.render_approved(
                summary=payload.summary_de if lang == "de" else payload.summary_en,
                language=lang,
            )

        if isinstance(payload, MissionFailed):
            return self._readback.render_failed(reason=payload.reason, language=lang)

        if isinstance(payload, MissionTimedOut):
            return self._readback.render_timeout(language=lang)

        if isinstance(payload, MissionCancelled):
            return self._readback.render_cancelled(language=lang)

        if isinstance(payload, MissionBudgetWarning):
            pct = int(payload.pct_used)
            if pct >= 80:
                return self._readback.render_budget_warn(pct=80, language=lang)
            if pct >= 50:
                return self._readback.render_budget_warn(pct=50, language=lang)
            return ""

        if isinstance(payload, WorkerKilled):
            if payload.reason == "injection_detected":
                return self._readback.render_injection_blocked(language=lang)
            if payload.reason == "path_guard":
                return self._readback.render_path_guard_blocked(language=lang)
            if payload.reason == "budget":
                return self._readback.render_budget_exceeded(language=lang)
            return ""

        if isinstance(payload, WorkerCorrectionRequired) and self._announce_critic_loop:
            return self._readback.render_iteration_running(
                n=payload.iteration + 1, language=lang
            )

        return ""

    async def _resolve_voice_meta(self, mission_id: str) -> tuple[bool, Lang]:
        """Liest MissionDispatched aus Store, cached pro Mission.

        Returns (is_voice_source, language). is_voice_source=True wenn
        source_actor=`hauptjarvis`. Default-Lang aus dispatched.language oder
        listener-default.
        """
        if mission_id in self._mission_voice_cache:
            return self._mission_voice_cache[mission_id]

        events = await self._store.events_for_mission(mission_id)
        if not any(
            getattr(e.payload, "event_type", None) == "MissionDispatched" for e in events
        ):
            # MissionDispatched not yet persisted — do not cache, otherwise a
            # later read after the event lands would still return the default
            # and the mission would stay silent permanently.
            return (False, self._lang_default)

        is_voice = False
        lang: Lang = self._lang_default
        for e in events:
            if e.payload.event_type == "MissionDispatched":
                is_voice = e.source_actor == "hauptjarvis"
                lang = e.payload.language  # type: ignore[attr-defined]
                break

        self._mission_voice_cache[mission_id] = (is_voice, lang)
        return (is_voice, lang)


__all__ = ["MissionVoiceListener", "TTSSpeakFn"]
