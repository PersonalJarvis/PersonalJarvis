"""Stable ChatGPT-subscription composition for classic voice sessions.

This module deliberately does not implement audio transport.  It selects the
stable Codex App Server text protocol for conversational turns while the
existing SpeechPipeline continues to own capture, STT, TTS, playback receipts,
follow-up listening, and interruption on Windows, macOS, and Linux.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

from jarvis.brain.turn_planner import TurnReason, plan_turn
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.core.turn_language import is_substantive_turn, resolve_output_language
from jarvis.plugins.brain.codex import CodexBrain

CODEX_SUBSCRIPTION_VOICE_PROFILE = "codex-subscription-voice"
LEGACY_CODEX_REALTIME_PROVIDER = "codex-subscription-realtime"

_LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish"}
_MAX_HISTORY_MESSAGES = 6
_SUPPORTED_DESKTOP_PLATFORMS = frozenset({"win32", "darwin", "linux"})

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubscriptionVoiceCapability:
    """One honest readiness snapshot for the composed subscription voice."""

    available: bool
    platform_supported: bool
    interactive_audio: bool
    account_ready: bool
    text_transport_ready: bool
    stt_configured: bool
    tts_configured: bool
    runtime_attached: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def subscription_voice_capability(
    config: object,
    *,
    account_ready: bool,
    runtime_attached: bool,
    display_present: bool,
    platform: str | None = None,
) -> SubscriptionVoiceCapability:
    """Compose account, text, speech, device, and OS readiness without IO."""
    platform_supported = str(platform or sys.platform) in _SUPPORTED_DESKTOP_PLATFORMS
    stt_configured = bool(str(getattr(getattr(config, "stt", None), "provider", "") or "").strip())
    tts_configured = bool(str(getattr(getattr(config, "tts", None), "provider", "") or "").strip())
    interactive_audio = bool(display_present)
    available = all(
        (
            platform_supported,
            interactive_audio,
            account_ready,
            stt_configured,
            tts_configured,
            runtime_attached,
        )
    )
    if not platform_supported:
        reason = "unsupported_platform"
    elif not interactive_audio:
        reason = "headless_audio_unavailable"
    elif not account_ready:
        reason = "subscription_login_required"
    elif not stt_configured:
        reason = "stt_not_configured"
    elif not tts_configured:
        reason = "tts_not_configured"
    elif not runtime_attached:
        reason = "desktop_runtime_not_attached"
    else:
        reason = "ready"
    return SubscriptionVoiceCapability(
        available=available,
        platform_supported=platform_supported,
        interactive_audio=interactive_audio,
        account_ready=bool(account_ready),
        text_transport_ready=bool(account_ready),
        stt_configured=stt_configured,
        tts_configured=tts_configured,
        runtime_attached=bool(runtime_attached),
        reason=reason,
    )


def configured_voice_profile(config: object) -> str:
    """Return only an explicitly selected classic-pipeline voice profile.

    A realtime provider pin is not a profile alias. Treating the Codex
    subscription provider as one silently changed ``voice.mode`` to pipeline
    after a user selected it in the Realtime section.
    """
    voice = getattr(config, "voice", None)
    profile = str(getattr(voice, "profile", "") or "").strip().lower()
    if profile == CODEX_SUBSCRIPTION_VOICE_PROFILE:
        return CODEX_SUBSCRIPTION_VOICE_PROFILE
    return ""


def subscription_voice_selected(config: object) -> bool:
    """Whether the stable ChatGPT-subscription voice composition is selected."""
    return configured_voice_profile(config) == CODEX_SUBSCRIPTION_VOICE_PROFILE


def subscription_language_directive(language: str) -> str:
    name = _LANGUAGE_NAMES.get(language, "English")
    return (
        f"REPLY LANGUAGE — MANDATORY: Always reply in {name}, no matter which "
        "language the user writes or speaks in. This overrides every other "
        f"language cue. Keep the reply natural and fluent in {name}."
    )


class CodexSubscriptionVoiceBrain:
    """Route conversational voice turns to the subscription text transport.

    Tool, private-data, current-data, and local-state turns remain on the
    existing BrainManager.  That preserves the complete Jarvis tool contract;
    the read-only subscription transport never guesses that an action happened.
    """

    def __init__(self, delegate: Any, config: object) -> None:
        self._delegate = delegate
        self._config = config
        self._subscription = CodexBrain(
            prefer_subscription=True,
            persistent_subscription_transport=True,
        )
        self._history: list[BrainMessage] = []
        self._conversation_language = ""
        self._last_turn_all_failed = False
        self._last_turn_suppressed = False
        self._last_turn_executed_action_tool = False

    def __getattr__(self, name: str) -> Any:
        # SpeechPipeline reads per-turn delivery flags and optional hooks from
        # BrainManager.  Proxying keeps those contracts intact for delegated
        # action/evidence turns.
        return getattr(self._delegate, name)

    async def __call__(self, text: str) -> str:
        return "".join([chunk async for chunk in self.generate_stream(text)])

    async def generate(self, text: str, **kwargs: Any) -> str:
        return "".join([chunk async for chunk in self.generate_stream(text, **kwargs)])

    async def _stream_delegate_turn(
        self,
        text: str,
        *,
        on_progress: Any | None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        delegated_parts: list[str] = []
        try:
            async for chunk in self._delegate.generate_stream(
                text,
                on_progress=on_progress,
                **kwargs,
            ):
                delegated_parts.append(chunk)
                yield chunk
        finally:
            self._last_turn_all_failed = bool(
                getattr(self._delegate, "_last_turn_all_failed", False)
            )
            self._last_turn_suppressed = bool(
                getattr(self._delegate, "_last_turn_suppressed", False)
            )
            self._last_turn_executed_action_tool = bool(
                getattr(
                    self._delegate,
                    "_last_turn_executed_action_tool",
                    False,
                )
            )
            delegated_answer = "".join(delegated_parts).strip()
            if delegated_answer:
                self._history.extend(
                    [
                        BrainMessage("user", text),
                        BrainMessage("assistant", delegated_answer),
                    ]
                )
                self._history = self._history[-_MAX_HISTORY_MESSAGES:]

    async def generate_stream(
        self,
        text: str,
        *,
        on_progress: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        context = tuple(
            str(message.content)
            for message in self._history[-_MAX_HISTORY_MESSAGES:]
            if isinstance(message.content, str)
        )
        turn_plan = plan_turn(text, context=context)
        action_detector = getattr(self._delegate, "_turn_has_action_intent", None)
        detected_action = bool(action_detector(text)) if callable(action_detector) else False
        delegate_turn = turn_plan.requires_orchestrator
        if turn_plan.reasons == frozenset({TurnReason.ACTION}):
            if callable(action_detector):
                # The planner's broad verb fallback intentionally overmatches
                # phrases such as "another one please". The mature BrainManager
                # detector is the authority for this action-only boundary.
                delegate_turn = detected_action
        elif detected_action:
            # Capability-registry actions must never fall through to the
            # read-only subscription talker, even if their phrasing was not in
            # the planner's generic action vocabulary.
            delegate_turn = True
        if delegate_turn:
            async for chunk in self._stream_delegate_turn(
                text,
                on_progress=on_progress,
                **kwargs,
            ):
                yield chunk
            return

        self._last_turn_all_failed = False
        self._last_turn_suppressed = False
        self._last_turn_executed_action_tool = False

        reply_pin = getattr(getattr(self._config, "brain", None), "reply_language", "auto")
        language = resolve_output_language(
            reply_pin,
            "",
            text,
            conversation_language=self._conversation_language,
        )
        if is_substantive_turn(text):
            self._conversation_language = language

        messages = tuple([*self._history[-_MAX_HISTORY_MESSAGES:], BrainMessage("user", text)])
        request = BrainRequest(
            messages=messages,
            system=subscription_language_directive(language),
            stream=True,
        )
        answer_parts: list[str] = []
        try:
            async for delta in self._subscription.complete(request):
                if on_progress is not None:
                    on_progress()
                if delta.content:
                    answer_parts.append(delta.content)
                    yield delta.content
        except Exception as exc:  # noqa: BLE001 - cross-family fallback boundary
            if answer_parts:
                raise
            log.warning(
                "Subscription voice transport failed before emitting an answer; "
                "delegating the turn to the configured provider chain: %s",
                exc,
                exc_info=True,
            )
            async for chunk in self._stream_delegate_turn(
                text,
                on_progress=on_progress,
                **kwargs,
            ):
                yield chunk
            return

        answer = "".join(answer_parts).strip()
        if answer:
            self._history.extend([BrainMessage("user", text), BrainMessage("assistant", answer)])
            if len(self._history) > _MAX_HISTORY_MESSAGES:
                self._history = self._history[-_MAX_HISTORY_MESSAGES:]


__all__ = [
    "CODEX_SUBSCRIPTION_VOICE_PROFILE",
    "LEGACY_CODEX_REALTIME_PROVIDER",
    "CodexSubscriptionVoiceBrain",
    "SubscriptionVoiceCapability",
    "configured_voice_profile",
    "subscription_language_directive",
    "subscription_voice_capability",
    "subscription_voice_selected",
]
