"""Pydantic config models for the Pre-Thinking Ack Flash-Brain.

Maps the [ack_brain] section of jarvis.toml. Default `enabled = False`
so the feature is opt-in until the user explicitly turns it on.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Providers accepted in the [ack_brain].provider field. Adding a new
# provider means: add an entry here, add an entry_point in pyproject.toml,
# add a config sub-model below, add an adapter under providers/.
#
# "follow_brain" is a meta-value: build_ack_brain() resolves it at
# startup against cfg.brain.primary. If brain.primary maps to one of
# the four concrete adapters, use that; otherwise fall back to
# "gemini" with a warning. Letting users pin a separate flash provider
# stays possible by setting one of the four concrete names.
SUPPORTED_PROVIDERS: tuple[str, ...] = (
    "follow_brain", "gemini", "grok", "openai", "ollama",
)


class _ProviderBase(BaseModel):
    """Common fields shared by all provider configs."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1, description="Provider-specific model name")
    temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=40, ge=8, le=200)


class GeminiAckProviderConfig(_ProviderBase):
    """Google Gemini Flash provider config."""

    api_key_secret: str = Field(default="gemini_api_key")


class GrokAckProviderConfig(_ProviderBase):
    """xAI Grok Flash provider config."""

    api_key_secret: str = Field(default="grok_api_key")


class OpenAIAckProviderConfig(_ProviderBase):
    """OpenAI mini-model provider config."""

    api_key_secret: str = Field(default="openai_api_key")


class OllamaAckProviderConfig(_ProviderBase):
    """Local Ollama provider config - no API key, just an HTTP endpoint."""

    endpoint: str = Field(default="http://localhost:11434", min_length=1)


class _ProvidersBundle(BaseModel):
    """Container for all provider-specific sub-configs."""

    model_config = ConfigDict(extra="forbid")

    gemini: GeminiAckProviderConfig = Field(
        default_factory=lambda: GeminiAckProviderConfig(model="gemini-3.1-flash")
    )
    grok: GrokAckProviderConfig = Field(
        # 2026-05-13: "grok-4-flash" returned 400 "Model not found" from
        # xAI live. Valid fast variant is "grok-4-fast-non-reasoning".
        default_factory=lambda: GrokAckProviderConfig(model="grok-4-fast-non-reasoning")
    )
    openai: OpenAIAckProviderConfig = Field(
        default_factory=lambda: OpenAIAckProviderConfig(model="gpt-5-mini")
    )
    ollama: OllamaAckProviderConfig = Field(
        default_factory=lambda: OllamaAckProviderConfig(model="llama3.1:8b")
    )


class AckBrainConfig(BaseModel):
    """Root config for the Pre-Thinking Ack Flash-Brain.

    Mapped from the [ack_brain] section of jarvis.toml. The feature is
    opt-in: until enabled=True is set explicitly, the AckGenerator is
    not instantiated and the existing silent-fallback path is used.
    """

    model_config = ConfigDict(extra="forbid")

    # The Flash-Brain is on-by-default. The user opted into the
    # feature by enabling it in the spec; disabling it again is a
    # deliberate jarvis.toml edit, not the silent ground state.
    enabled: bool = Field(default=True)
    # "follow_brain" mirrors cfg.brain.primary so the Flash-Brain
    # naturally tracks whatever main provider the user is on. Pin to
    # a concrete name (gemini/grok/openai/ollama) to override.
    provider: str = Field(default="follow_brain")
    timeout_ms: int = Field(default=1500, ge=100, le=10000)
    on_failure: Literal["silent"] = Field(default="silent")
    circuit_breaker_threshold: int = Field(default=3, ge=1, le=20)
    circuit_breaker_cooldown_s: int = Field(default=60, ge=5, le=600)
    # 2026-05-13: empirical observation — for fast brain replies (~1-2 s)
    # the Flash-Brain ack feels redundant. The ack should ONLY surface
    # when the main brain is still thinking past this threshold. After
    # the ack is generated, the speech pipeline waits this long (polling
    # the turn-state every 100 ms); if the turn-state has already
    # transitioned to JARVIS_SPEAKING / LISTENING, the ack is dropped
    # silently. If the brain is still in PROCESSING when the timer
    # fires, the ack is published.
    suppress_if_brain_faster_than_ms: int = Field(default=2000, ge=0, le=15000)
    # 2026-05-26: cross-surface voice incoherence defence (diagnosis in
    # docs/plans/voice-phrase-mismatch-2026-05-26/README.md). After an
    # AnnouncementRequested with priority="interrupt" lands (typically a
    # MissionFailed / MissionTimedOut readback), preamble-class
    # announcements that arrive within this window are suppressed.  The
    # interrupt has just claimed the conversational slot; a follow-up
    # "Lass mich kurz nachschauen." from any preamble emitter (Flash-Brain
    # or skill announcement) would be the second half of
    # the incoherent voice block the user reported on 2026-05-26.  Default
    # 5000 ms = ~one failure readback + a short breath.  Set to 0 to
    # disable the gate (and reopen the incoherence path).
    suppress_preamble_after_interrupt_ms: int = Field(default=5000, ge=0, le=60000)
    # Wave 3 (omni-latency): stream the ack via the provider's run_stream so the
    # first sentence reaches TTS as soon as it is ready instead of awaiting the
    # full (max_output_tokens) response. Falls back to the non-streaming run()
    # when the provider has no run_stream / the stream errors. The suppress gate
    # is re-evaluated at first-sentence-ready instead of polling after the text.
    streaming: bool = Field(default=True)
    # 2026-06-17: continuation grace (AD-OE5). Live incident 2026-06-17 12:42:
    # the user paused mid-thought after a grammatically complete question; the
    # VAD endpointed, the brain entered PROCESSING, and the streaming ack spoke
    # ~795 ms BEFORE the VAD detected the user's continuation — it talked over
    # the user. The streaming ack speaks its first sentence the instant it is
    # ready (no settle window), so a pure turn-state gate cannot catch a
    # continuation that has not yet crossed the VAD threshold. Before the FIRST
    # audible ack sentence, the pipeline polls the turn-state for this long; if
    # the turn leaves PROCESSING during the grace (user resumed → continuation
    # interrupt, or the brain already answered), the ack is dropped. Reconstructed
    # gap from the incident logs was ~795 ms, so the default leaves margin; the
    # ack is only ever heard on slow brain turns, where a sub-1.5 s delay is
    # invisible. Set to 0 to restore the speak-immediately behaviour.
    ack_continuation_grace_ms: int = Field(default=1200, ge=0, le=5000)
    # 2026-06-10: LLM-composed spawn announcements. When True, the
    # spawn_worker tool phrases its spoken dispatch confirmation via the
    # flash provider (dedicated delegation persona) instead of a canned
    # phrase pool. False keeps the deterministic bilingual fallback pool
    # only — the kill switch for latency-sensitive setups. See
    # jarvis/brain/ack_brain/spawn_announcement.py.
    spawn_announcements: bool = Field(default=True)
    providers: _ProvidersBundle = Field(default_factory=_ProvidersBundle)

    @field_validator("provider")
    @classmethod
    def _provider_must_be_supported(cls, v: str) -> str:
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"unknown provider {v!r}; supported: {SUPPORTED_PROVIDERS}"
            )
        return v
