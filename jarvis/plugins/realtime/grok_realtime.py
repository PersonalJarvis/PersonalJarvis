"""xAI Grok Voice Agent realtime provider plugin.

xAI speaks the OpenAI-Realtime wire protocol, so this adapter reuses the
hardened transport lifecycle from the OpenAI plugin and only injects what is
genuinely different: the endpoint, the credential family, the model, the
transcription model, and — the one behavioural difference that matters — the
fact that **the server answers spoken turns by itself**.

That difference is why the first ``grok-realtime`` adapter was removed on
2026-07-16 (BUG-064). It was written as a plain OpenAI clone, i.e. as a client
that owns response creation, and every automatic response therefore looked
like a broken session contract. Re-measured against
``grok-voice-think-fast-2.0`` on 2026-08-13 (the model ``grok-voice-latest``
switched to on 2026-08-05, after the removal):

* the session payload Jarvis builds is accepted whole — tools, ``tool_choice``,
  ``grok-transcribe`` with the language hint, and the VAD silence window;
* function calling completes end to end, and ``response.cancel`` mid-answer is
  clean: no stray responses, no errors, the next turn answers normally;
* input transcription survives a cancel — the deafness that made the provider
  unshippable does not reproduce, and utterances are no longer truncated
  mid-sentence;
* ``turn_detection.create_response: false`` is echoed back and then ignored:
  at the end of a SPOKEN turn the server always creates exactly one response,
  completes it cleanly, and stays quiet afterwards. Disabling turn detection
  entirely does not change this. It is how the product reaches its low time to
  first audio, not a defect.

Text turns and tool continuations, by contrast, DO wait for our own
``response.create`` (measured, same session). So Jarvis keeps explicit control
everywhere it needs it, and only the spoken turn arrives pre-answered — which
is exactly the Gemini Live shape this session class declares.

The module imports no ``jarvis.*`` modules and keeps the OpenAI SDK lazy so
importing the plugin never slows startup (AP-26).
"""

from __future__ import annotations

import logging
from typing import Any

from .openai_realtime import _open_realtime_session, _OpenAIRealtimeSession

log = logging.getLogger(__name__)

_MODEL = "grok-voice-latest"
_BASE_URL = "https://api.x.ai/v1"
_TRANSCRIPTION_MODEL = "grok-transcribe"
_INPUT_RATE = 24_000
_OUTPUT_RATE = 24_000


class _GrokVoiceSession(_OpenAIRealtimeSession):
    """The shared transport, told the truth about who creates responses."""

    # The server answers every spoken turn on its own; the orchestrator must
    # not wait for a response it already has (and must not request a second).
    creates_responses_automatically = True
    server_answers_speech_turns = True


def _grok_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt the shared GA payload to what xAI actually honours.

    The manual-response flags are dropped rather than sent-and-ignored: a
    session payload that states a contract the server never keeps is a
    standing invitation to re-diagnose BUG-064 on every future reading of a
    live log. Everything else in the payload is accepted verbatim.
    """
    audio_input = payload.get("audio", {}).get("input", {})
    transcription = audio_input.get("transcription")
    if isinstance(transcription, dict):
        transcription["model"] = _TRANSCRIPTION_MODEL
    turn_detection = audio_input.get("turn_detection")
    if isinstance(turn_detection, dict):
        turn_detection.pop("create_response", None)
        turn_detection.pop("interrupt_response", None)
    return payload


class GrokRealtimeProvider:
    """Structural provider entry point for xAI's Grok Voice Agent API."""

    name = "grok-realtime"
    # Account/quota metadata for the AP-22 delegate-chain diagnostics. It must
    # be the family the BRAIN side resolves for ``grok`` too: voice and brain
    # spend ONE xAI account here, so a single 429 silences both tiers at once
    # — precisely the chain the warning exists to surface.
    credential_family = "grok"
    supports_realtime = True
    implicit_usage_fallback_allowed = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates = (
        ("realtime_grok_api_key", "JARVIS_REALTIME_GROK_API_KEY"),
        ("grok_api_key", "GROK_API_KEY"),
        ("xai_api_key", "XAI_API_KEY"),
    )

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = (api_key or "").strip()

    async def can_open_duplex_session(self) -> bool:
        return bool(self._api_key)

    async def open_session(self, cfg: Any) -> _OpenAIRealtimeSession:
        if not self._api_key:
            raise RuntimeError("xAI Grok Realtime API key is not configured")

        from openai import AsyncOpenAI  # lazy (AP-26)

        client = AsyncOpenAI(api_key=self._api_key, base_url=_BASE_URL)
        connect_model = str(getattr(cfg, "model", "") or _MODEL)
        return await _open_realtime_session(
            client,
            cfg,
            model=connect_model,
            transcription_model=_TRANSCRIPTION_MODEL,
            session_cls=_GrokVoiceSession,
            payload_transform=_grok_session_payload,
        )


__all__ = ["GrokRealtimeProvider"]
