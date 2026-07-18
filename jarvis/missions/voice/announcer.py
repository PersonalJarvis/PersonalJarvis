"""MissionAnnouncer — bridge MissionBus -> AnnouncementRequested bus event.

AD-17 (see ``docs/jarvis-agents-bridge.md``): "Notifications via existing
``_on_announcement`` bus" — the Jarvis-Agent bridge pipes mission summaries
through the existing ``AnnouncementRequested`` event into the
``SpeechPipeline``. Advantages over the direct ``tts_speak_fn``
path in ``MissionVoiceListener``:

- Reuse of the finished path in ``pipeline._on_announcement``
  including the ``scrub_for_voice`` filter and barge-in handling
  (``priority="interrupt"`` stops current playback).
- No additional TTS provider dependency in the mission subsystem
  (the speech pipeline bus is sufficient).
- Consistent with sub-Jarvis/skill/vision announcements, which all
  travel through the same event.

Architecture:

- Subscriber on ``MissionBus`` (via ``subscribe_all``) — filters for
  ``hauptjarvis``-source missions (voice-triggered), reads the language
  from the cached ``MissionDispatched`` event and maps event types to
  voice text (``summary_de``/``summary_en`` or static phrases).
- Emits ``AnnouncementRequested`` on the global speech EventBus.
- Errors are logged but never propagated — the voice path must never be
  blocked by a broken mission event.

ADDITIVE component: ``MissionVoiceListener`` (direct-TTS path) remains
unchanged. The bootstrap decides which path is active —
activating both simultaneously results in a double announcement.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Literal

from jarvis.brain.output_filter import scrub_for_voice
from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.missions.voice.readback import (
    FAILURE_REASON_PHRASES,
    failure_phrase_key,
    render_agent_brand,
)
from jarvis.voice.contextual_readback import render_readback

if TYPE_CHECKING:
    from jarvis.voice.contextual_readback import ReadbackComposer

from ..event_bus import MissionBus
from ..event_store import MissionEventStore
from ..events import (
    EventEnvelope,
    MissionApproved,
    MissionCancelled,
    MissionFailed,
    MissionTimedOut,
)

logger = logging.getLogger(__name__)


_Lang = Literal["de", "en"]
MISSION_ANNOUNCEMENT_SOURCE_LAYER = "missions.voice.announcer"


class MissionAnnouncer:
    """Mission-Bus -> Speech-Bus bridge via ``AnnouncementRequested``.

    Subscribes on ``start()`` to the ``MissionBus`` and emits on the
    ``speech_bus`` (typically the global ``jarvis.core.bus.EventBus``
    that ``SpeechPipeline._on_announcement`` also subscribes to). Filters
    for voice-triggered missions analogously to ``MissionVoiceListener``.

    Args:
        bus: MissionBus from which mission events are received.
        store: MissionEventStore for looking up ``MissionDispatched``
            metadata (source_actor, language).
        speech_bus: Global EventBus on which ``AnnouncementRequested``
            is published.
        scrub: If ``True`` (default), passes the text through
            ``scrub_for_voice`` BEFORE publishing it as an event.
            ``pipeline._on_announcement`` also scrubs itself — double
            scrubbing is idempotent (pattern-match filter) and therefore safe.
            Set ``scrub=False`` to disable the pre-scrub.
        announce_critic_loop: If True, the announcer also announces
            intermediate iteration states (analogous to the same-named
            flag in ``MissionVoiceListener``). Default False (too chatty).
        priority: ``"normal"`` (default) queues behind current speech.
            ``"interrupt"`` stops ongoing speech — mandatory mode for failures.
    """

    def __init__(
        self,
        *,
        bus: MissionBus,
        store: MissionEventStore,
        speech_bus: EventBus,
        scrub: bool = True,
        announce_critic_loop: bool = False,
        language_default: _Lang = "de",
        readback_composer: ReadbackComposer | None = None,
    ) -> None:
        self._bus = bus
        self._store = store
        self._speech_bus = speech_bus
        self._scrub = scrub
        self._announce_critic_loop = announce_critic_loop
        self._lang_default: _Lang = language_default
        # Context-aware readbacks (maintainer mandate: no fixed stock phrases).
        # The signed/canned line from _render is the deterministic ground truth;
        # the composer only rephrases it naturally, honesty-bound for the signed
        # MissionApproved summary (ADR-0009 — rephrase, never invent). None =>
        # the canned line is spoken unchanged (risk-free when unwired).
        self._readback_composer = readback_composer
        # Cache (mission_id -> (is_voice_source, language))
        self._mission_voice_cache: dict[str, tuple[bool, _Lang]] = {}
        self._unsubscribe = None  # set by start()

    async def start(self) -> None:
        """Register the wildcard subscriber on the MissionBus."""
        self._unsubscribe = self._bus.subscribe_all(self._on_event)
        logger.info("MissionAnnouncer: bus-subscribe registered")

    def stop(self) -> None:
        """Cancel the subscription. Idempotent."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def _on_event(self, env: EventEnvelope) -> None:
        """Wildcard handler. Drop-in no-op on error — the mission bus
        must never freeze because of a broken announcer."""
        try:
            await self._dispatch(env)
        except Exception:  # noqa: BLE001
            logger.warning("MissionAnnouncer crashed", exc_info=True)

    async def _dispatch(self, env: EventEnvelope) -> None:
        is_voice, lang = await self._resolve_voice_meta(env.mission_id)
        if not is_voice:
            return

        text, priority = self._render(env, lang)
        if not text:
            return

        # Context-aware rephrasing of the signed/canned line. honesty_bound for
        # the MissionApproved summary so the spoken surface stays a faithful
        # rephrasing of the Kontrollierer-signed observation (ADR-0009); failure/
        # timeout/cancel are status, not observations, so they phrase more freely
        # (digit + vocab guards + strict persona still apply). Falls back to the
        # exact canned line on any miss (AD-OE6, zero silent drops).
        instruction, honesty_bound = self._situation(env.payload)
        canned_line = text
        text = await render_readback(
            self._readback_composer,
            instruction=instruction,
            language=lang,
            canned=lambda: canned_line,
            facts={"result": canned_line},
            honesty_bound=honesty_bound,
            latency_budget_ms=2500,
        )
        if not text:
            return

        if self._scrub:
            scrubbed = scrub_for_voice(text, language=lang)
            if scrubbed.actions:
                logger.info(
                    "MissionAnnouncer pre-scrub [%s]: %s (fallback=%s)",
                    lang, scrubbed.actions, scrubbed.fallback_used,
                )
            text = scrubbed.cleaned
            if not text.strip():
                logger.info("MissionAnnouncer: text leer nach pre-scrub — skip")
                return

        await self._speech_bus.publish(
            AnnouncementRequested(
                source_layer=MISSION_ANNOUNCEMENT_SOURCE_LAYER,
                text=text,
                priority=priority,
                language=lang,
                # Every MissionAnnouncer event is a TERMINAL outcome (approved /
                # failed / cancelled / timed-out) — the answer the user asked
                # for, delivered by a spawned sub-agent/mission. ``subagent``
                # punches through the pipeline's hangup gate (a readback kind,
                # like ``completion``) even when the mission finished after
                # "auflegen" (live bug 2026-06-14: an offloaded research result
                # was silently dropped), and renders on the attributed
                # "Jarvis Sub-Agent / Output" transcript track.
                kind="subagent",
                # Keep the mission identity and artifact location out of speech
                # while making them available to the next model turn.
                detail=self._context_detail(env),
            )
        )

    @staticmethod
    def _context_detail(env: EventEnvelope) -> str:
        """Serialize safe terminal metadata for follow-up context."""
        payload = env.payload
        detail: dict[str, str] = {
            "mission_id": env.mission_id,
            "event_type": payload.event_type,
        }
        if isinstance(payload, MissionApproved):
            detail["result_uri"] = payload.result_uri
        elif isinstance(payload, (MissionFailed, MissionCancelled)):
            detail["reason"] = payload.reason
        return json.dumps(detail, ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _situation(payload: object) -> tuple[str, bool]:
        """English (instruction, honesty_bound) for a terminal mission event.

        ``honesty_bound`` is True ONLY for the signed success summary, so its
        spoken surface stays a faithful rephrasing (ADR-0009). Failure / timeout
        / cancel are status lines, not observations, so they phrase freely.
        """
        if isinstance(payload, MissionApproved):
            return (
                "A background task the user asked for has finished successfully; "
                "tell them naturally, keeping the reported result faithfully.",
                True,
            )
        if isinstance(payload, MissionFailed):
            return (
                "A background task the user asked for did not succeed; tell them "
                "plainly and kindly, keeping any reason given.",
                False,
            )
        if isinstance(payload, MissionTimedOut):
            return ("A background task the user asked for ran out of time.", False)
        if isinstance(payload, MissionCancelled):
            return ("A background task the user asked for was cancelled.", False)
        return ("A background task the user asked for has an update.", False)

    def _render(
        self, env: EventEnvelope, lang: _Lang,
    ) -> tuple[str, Literal["normal", "interrupt"]]:
        """Maps a mission-event type -> (voice text, priority).

        Mandatory: ONLY runtime-signed ``summary_de``/``summary_en`` from
        ``MissionApproved`` are taken verbatim — see the ADR-0009 Action/
        Observation invariant. Worker-LLM output NEVER reaches the voice
        path as free text. Failure/cancel/timeout texts are static in
        this function.
        """
        payload = env.payload

        if isinstance(payload, MissionApproved):
            summary = payload.summary_de if lang == "de" else payload.summary_en
            return (summary, "normal")

        if isinstance(payload, MissionFailed):
            # BUG-LIVE-03 (Recon-Agent 3, 2026-05-16): the announcer used
            # to swallow MissionFailed.reason and emit one nacked German
            # phrase for seven distinct failure modes — user heard the
            # same "fehlgeschlagen" whether the worker timed out, the  # i18n-allow: quoted literal historical TTS output
            # critic ran out of loops, or a stale mission was swept on
            # boot. Map the reason to a short human cue so the user
            # knows what to do next.
            reason = (getattr(payload, "reason", "") or "").strip()
            short_reason = failure_phrase_key(
                reason, getattr(payload, "error_class", None)
            )
            # crash_recovery and interrupted are boot-time housekeeping, NOT
            # live failures: on every startup `startup_recover` sweeps
            # still-in-flight missions to FAILED('crash_recovery') or
            # FAILED('interrupted'). Those missions were dispatched by voice in
            # a PRIOR session, so the is_voice gate (keyed on the ORIGINAL
            # MissionDispatched.source_actor) lets them through — and the
            # announcer would barge in with "Die Mission ist fehlgeschlagen." i18n-allow
            # at interrupt priority. That is exactly the user's "random Mission
            # fehlgeschlagen, although I never started one" complaint (deep-dive i18n-allow
            # 2026-05-29). Suppress both: return empty text so _dispatch skips
            # publishing an AnnouncementRequested. (interrupted added 2026-06-07
            # for commit 13b86605 which emits 'interrupted' instead of
            # 'crash_recovery' for stale missions with partial work.)
            if short_reason in ("crash_recovery", "interrupted"):
                return ("", "normal")
            # Reason -> phrase map shared with MissionReadback.render_failed via
            # FAILURE_REASON_PHRASES (single source) so the announcer and the
            # direct-TTS listener can never drift apart (2026-05-27 finding #7).
            # Among the codes: critic_unavailable is emitted when the Critic
            # subprocess crashed but iter0 produced a real diff — the user hears
            # that the work succeeded and only the reviewer failed (the diff is
            # recoverable from the artifacts dir; live repro mission_019e3288).
            de_map = FAILURE_REASON_PHRASES["de"]
            en_map = FAILURE_REASON_PHRASES["en"]
            if lang == "de":
                tail = de_map.get(short_reason, f"Grund: {reason}" if reason else "")  # i18n-allow
                text = f"Die Mission ist fehlgeschlagen. {render_agent_brand(tail)}".rstrip()  # i18n-allow: real German TTS voice output
            else:
                tail = en_map.get(short_reason, f"Reason: {reason}" if reason else "")
                text = f"The mission failed. {render_agent_brand(tail)}".rstrip()
            # AD-OE5 (2026-05-29): "speak ONLY at the next turn-boundary, never
            # interrupt mid-utterance". A failed background mission must NOT
            # barge in over current speech — combined with the silent spawn-ACK
            # (user mandate 2026-05-12), an interrupt made a failure feel like a
            # "random" intrusion. Queue at "normal" so it is still spoken
            # (AD-OE6 — no silent drops) but at the next natural turn boundary.
            return (text, "normal")

        if isinstance(payload, MissionCancelled):
            text = (
                "Mission abgebrochen."
                if lang == "de"
                else "Mission cancelled."
            )
            return (text, "normal")

        if isinstance(payload, MissionTimedOut):
            text = (
                "Die Mission lief in das Zeitlimit."  # i18n-allow: real German TTS voice output
                if lang == "de"
                else "The mission timed out."
            )
            # AD-OE5: do not barge in mid-utterance — queue for the next
            # turn-boundary (see MissionFailed above).
            return (text, "normal")

        return ("", "normal")

    async def _resolve_voice_meta(self, mission_id: str) -> tuple[bool, _Lang]:
        """Cache lookup: is the mission voice-triggered? Which language?

        Replicated from ``MissionVoiceListener._resolve_voice_meta``.
        Deliberately no shared helper class, because the two listeners
        have completely independent lifecycles — we don't want to record
        a broken cache tier twice.
        """
        if mission_id in self._mission_voice_cache:
            return self._mission_voice_cache[mission_id]

        events = await self._store.events_for_mission(mission_id)
        is_voice = False
        lang: _Lang = self._lang_default
        for e in events:
            if e.payload.event_type == "MissionDispatched":
                is_voice = e.source_actor == "hauptjarvis"
                raw_lang = e.payload.language  # type: ignore[attr-defined]
                if raw_lang in ("de", "en"):
                    lang = raw_lang  # type: ignore[assignment]
                break

        self._mission_voice_cache[mission_id] = (is_voice, lang)
        return (is_voice, lang)


__all__ = ["MISSION_ANNOUNCEMENT_SOURCE_LAYER", "MissionAnnouncer"]
