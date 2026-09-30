"""Bus listener for mission events -> TTS pipeline.

Subscribes to all mission events via `MissionBus.subscribe_all`, filters
for voice-triggered missions (source_actor=`hauptjarvis`), maps event
types onto MissionReadback methods, and calls the TTS synthesize function.

ADR-0009 Decision §"Voice readback only for voice-triggered missions":
UI-triggered missions get a toast (the UI has live updates via WS).

Filtering: looks up the MissionDispatched event from the store (the
Dispatched event is persisted via WAL — no race between mission start and
subscriber wiring). Cached per mission ID so the lookup does not happen
per event.

Audit F-AUDIT-2 (2026-04-29): mission-readback texts now go through
``scrub_for_voice`` before reaching TTS. Without this filter, tool-use
markup, "Sir" address, or engineering jargon reached the user unfiltered
— see ``docs/persona-audit-report.md`` F-AUDIT-2.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from jarvis.brain.output_filter import scrub_for_voice
from jarvis.brain.scrub_verdict import is_harmless_scrub_residue

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


# Type for the TTS synthesize function. Signature compatible with
# `SpeechPipeline._tts.synthesize(text, language_code=...)` — we wrap it
# in the bootstrap into a simple `async fn(text, lang)`.
TTSSpeakFn = Callable[[str, Lang], Awaitable[None]]


class MissionVoiceListener:
    """Subscribes to MissionBus + filters for voice-sourced missions."""

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
        # Cache: mission_id -> (is_voice, language). Filled lazily on the
        # first event per mission. None = not yet resolved.
        self._mission_voice_cache: dict[str, tuple[bool, Lang]] = {}

    async def start(self) -> None:
        """Registers the subscriber. Idempotent: subscribe_all is append-only."""
        self._bus.subscribe_all(self._on_event)
        logger.info("MissionVoiceListener: bus-subscribe registered")

    async def _on_event(self, env: EventEnvelope) -> None:
        """Bus handler. Drop-in no-op on error — TTS must never block the bus."""
        try:
            await self._dispatch(env)
        except Exception:  # noqa: BLE001
            logger.warning("MissionVoiceListener crashed", exc_info=True)

    async def _dispatch(self, env: EventEnvelope) -> None:
        # Pre-filter: is the mission voice-triggered?
        is_voice, lang = await self._resolve_voice_meta(env.mission_id)
        if not is_voice:
            return

        # The signed summary or a fixed status phrase, spoken as it is: no
        # model call rephrases it (2026-09-30).
        text = self._render(env, lang)
        if not text:
            return

        # Audit F-AUDIT-2: send the mission readback through the output
        # filter before handing it to TTS. Otherwise mission outputs with
        # tool-use markup / "Sir" address / engineering jargon reached the
        # user unfiltered. The mission's language (de/en) is passed to
        # scrub_for_voice so it picks the right fallback phrase.
        scrubbed = scrub_for_voice(text, language=lang)
        if scrubbed.actions:
            logger.info(
                "MissionVoiceListener filter [%s]: %s (fallback=%s)",
                lang, scrubbed.actions, scrubbed.fallback_used,
            )
        if is_harmless_scrub_residue(scrubbed):
            # Filler-only readback. The residue guard replaced it with the
            # generic error phrase; speaking that would report a mission
            # failure that never happened.
            logger.info(
                "MissionVoiceListener: readback carried no substance (%s) — "
                "staying silent instead of speaking the error phrase: %r",
                scrubbed.actions,
                text[:80],
            )
            return
        if not scrubbed.cleaned.strip():
            logger.info("MissionVoiceListener: text empty after filter — skip")
            return

        await self._tts(scrubbed.cleaned, lang)

    def _render(self, env: EventEnvelope, lang: Lang) -> str:
        """Map event payload -> voice text. Empty string if the event type
        has no voice response.
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
        """Reads MissionDispatched from the store, cached per mission.

        Returns (is_voice_source, language). is_voice_source=True if
        source_actor=`hauptjarvis`. Default language from dispatched.language
        or the listener default.
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
