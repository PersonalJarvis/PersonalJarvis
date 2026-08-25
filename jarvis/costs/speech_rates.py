"""Published rates for the two services that do not bill by token.

:mod:`jarvis.costs.model` prices everything that bills per token. Speech does
not: a transcription is billed per minute of audio, a synthesis per character
of text, and one family (Google) bills speech per token but converts audio and
text into tokens with published ratios of its own. Three units, one answer, so
this module mirrors ``price_entry`` exactly — same four-way honesty, same
"``unknown`` is a GAP, never a quiet zero".

**Every number below carries the page it came from and the date it was read.**
A rate nobody can trace is worse than no rate: it looks like accounting and is
actually a guess, and the person it misleads is the one paying the bill. Where
a vendor publishes no per-character or per-minute price at all — Cartesia sells
credits, xAI publishes nothing for voice, an OpenRouter speech model is priced
by whichever upstream serves it — the answer is ``unknown``. That row shows up
in the report as a gap somebody can go close, which is the truth, instead of a
zero that quietly says the audio was free.

Rates are pay-as-you-go / on-demand list prices, the only tier a downloader
with their own key can be assumed to be on. A cheaper committed tier makes the
number an over-estimate, which is the safe direction for a spend report.

The providers covered are the ones that exist in the code — every ``name`` in
``jarvis/plugins/stt/`` and ``jarvis/plugins/tts/`` — not a remembered list.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from jarvis.costs.model import LOCAL_PROVIDERS, PriceSource

STAGE_STT = "stt"
STAGE_TTS = "tts"

#: Speech engines that run on the user's own hardware. 0.00 is the truth here,
#: not a missing rate. ``LOCAL_PROVIDERS`` in :mod:`jarvis.costs.model` covers
#: the brain-side ids; these are the speech plugin ids it does not know, taken
#: from the ``name`` of each local plugin class.
LOCAL_SPEECH_PROVIDERS: frozenset[str] = frozenset(
    {
        "faster-whisper",   # jarvis/plugins/stt/fwhisper.py
        "nemotron-local",   # jarvis/plugins/stt/nemotron_local.py
        "piper-local",      # jarvis/plugins/tts/piper_local.py
        "sapi5",            # jarvis/plugins/tts/inworld_tts.py — Windows emergency voice
    }
)

# ---------------------------------------------------------------------------
# Rate shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MinuteRate:
    """Billed per minute of audio submitted — the normal STT contract."""

    usd_per_minute: float
    source: str

    def usd(self, chars: int, audio_ms: int) -> float:
        return audio_ms / 60_000.0 * self.usd_per_minute


@dataclass(frozen=True, slots=True)
class CharRate:
    """Billed per character of input text — the normal TTS contract."""

    usd_per_million_chars: float
    source: str

    def usd(self, chars: int, audio_ms: int) -> float:
        return chars / 1_000_000.0 * self.usd_per_million_chars


@dataclass(frozen=True, slots=True)
class GoogleTokenRate:
    """Billed per token, with Google's own published unit conversions.

    Google prices Gemini speech in tokens and documents both ratios needed to
    get there, so this is a derivation from published numbers rather than an
    estimate: "Audio: 32 tokens per second" and "For Gemini models, a token is
    equivalent to about 4 characters" — https://ai.google.dev/gemini-api/docs/tokens
    (read 2026-08-24).

    ``usd_per_million_text_tokens`` is 0.0 for a transcription: the audio is
    the input, and the transcript's output tokens are NOT counted here because
    the meter deliberately never reads the transcript. That makes a Gemini STT
    row a documented under-estimate of the audio term rather than a fabricated
    total — the audio input dominates, and the missing piece is named instead
    of invented.
    """

    usd_per_million_audio_tokens: float
    usd_per_million_text_tokens: float
    source: str

    #: https://ai.google.dev/gemini-api/docs/tokens — "Audio: 32 tokens per second".
    AUDIO_TOKENS_PER_SECOND: ClassVar[float] = 32.0
    #: Same page — "a token is equivalent to about 4 characters".
    CHARS_PER_TEXT_TOKEN: ClassVar[float] = 4.0

    def usd(self, chars: int, audio_ms: int) -> float:
        audio_tokens = audio_ms / 1000.0 * self.AUDIO_TOKENS_PER_SECOND
        text_tokens = chars / self.CHARS_PER_TEXT_TOKEN
        return (
            audio_tokens / 1_000_000.0 * self.usd_per_million_audio_tokens
            + text_tokens / 1_000_000.0 * self.usd_per_million_text_tokens
        )


Rate = MinuteRate | CharRate | GoogleTokenRate

#: Key used inside a provider's table when its price does not depend on the
#: model or voice at all.
ANY_MODEL = "*"

# ---------------------------------------------------------------------------
# Speech-to-text — billed per minute of audio submitted
# ---------------------------------------------------------------------------

_DEEPGRAM_PRICING = "https://deepgram.com/pricing (read 2026-08-24)"
_OPENAI_PRICING = "https://developers.openai.com/api/docs/pricing (read 2026-08-24)"
_GROQ_V3 = "https://console.groq.com/docs/model/whisper-large-v3 (read 2026-08-24)"
_GROQ_V3_TURBO = (
    "https://console.groq.com/docs/model/whisper-large-v3-turbo (read 2026-08-24)"
)
_GEMINI_PRICING = "https://ai.google.dev/gemini-api/docs/pricing (read 2026-08-24)"

_STT_RATES: dict[str, dict[str, Rate]] = {
    # jarvis/plugins/stt/deepgram_api.py — the plugin POSTs a complete WAV and
    # its module docstring says it deliberately does not open the streaming
    # WebSocket, so the pre-recorded rate is the one that applies, not the
    # (higher) streaming one. Monolingual; multilingual is $0.0052/min on the
    # same page, and the plugin does not distinguish the two.
    "deepgram-api": {
        "nova-3": MinuteRate(0.0043, _DEEPGRAM_PRICING),
    },
    # jarvis/plugins/stt/openai_api.py — DEFAULT_MODEL is whisper-1.
    "openai-api": {
        "whisper-1": MinuteRate(0.006, _OPENAI_PRICING),          # "$0.006 / minute"
        "gpt-4o-transcribe": MinuteRate(0.006, _OPENAI_PRICING),  # "$0.006 / minute"
        "gpt-4o-mini-transcribe": MinuteRate(0.003, _OPENAI_PRICING),  # "$0.003 / minute"
    },
    # jarvis/plugins/stt/groq_api.py — DEFAULT_MODEL is whisper-large-v3.
    # Groq quotes per hour of audio; /60 for the per-minute rate.
    "groq-api": {
        "whisper-large-v3": MinuteRate(0.111 / 60.0, _GROQ_V3),            # "$0.111 Per Hour"
        "whisper-large-v3-turbo": MinuteRate(0.04 / 60.0, _GROQ_V3_TURBO),  # "$0.04 Per Hour"
    },
    # jarvis/plugins/stt/gemini_api.py — DEFAULT_MODEL is gemini-3-flash-preview,
    # and the Vertex sibling in the same file (name "vertex-stt") runs the same
    # models. Audio input is priced separately from text on Google's table.
    "gemini-api": {
        "gemini-3-flash-preview": GoogleTokenRate(1.00, 0.0, _GEMINI_PRICING),  # audio "$1.00"
        "gemini-2.5-flash": GoogleTokenRate(1.00, 0.0, _GEMINI_PRICING),        # audio "$1.00"
    },
    # jarvis/plugins/stt/openrouter_stt.py — OpenRouter resells whichever
    # upstream serves the model and publishes no flat per-minute audio rate,
    # so every row here is a gap rather than a guess.
}
_STT_RATES["vertex-stt"] = _STT_RATES["gemini-api"]

# ---------------------------------------------------------------------------
# Text-to-speech — billed per character of input text
# ---------------------------------------------------------------------------

_ELEVENLABS_PRICING = "https://elevenlabs.io/pricing/api (read 2026-08-24)"
_INWORLD_PRICING = "https://inworld.ai/pricing (read 2026-08-24)"

_TTS_RATES: dict[str, dict[str, Rate]] = {
    # jarvis/plugins/tts/elevenlabs_tts.py — DEFAULT_MODEL is eleven_flash_v2_5.
    # ElevenLabs quotes per 1K characters: Flash/Turbo "$0.05", v2 Multilingual
    # "$0.10". Models outside those two families (v3, sts, …) are left unknown
    # rather than assumed to share a tier — the two published tiers differ 2x.
    "elevenlabs": {
        "eleven_flash_v2": CharRate(50.0, _ELEVENLABS_PRICING),
        "eleven_flash_v2_5": CharRate(50.0, _ELEVENLABS_PRICING),
        "eleven_turbo_v2": CharRate(50.0, _ELEVENLABS_PRICING),
        "eleven_turbo_v2_5": CharRate(50.0, _ELEVENLABS_PRICING),
        "eleven_multilingual_v2": CharRate(100.0, _ELEVENLABS_PRICING),
    },
    # jarvis/plugins/tts/inworld_tts.py — DEFAULT_MODEL is inworld-tts-2.
    # On-demand tier: "Realtime TTS-2 $25/1M chars", "Realtime TTS-2 Flash
    # $15/1M chars". inworld-tts-1 is no longer on the pricing page, so it is
    # left unknown.
    "inworld": {
        "inworld-tts-2": CharRate(25.0, _INWORLD_PRICING),
        "inworld-tts-2-flash": CharRate(15.0, _INWORLD_PRICING),
    },
    # jarvis/plugins/tts/gemini_flash_tts.py — default model
    # gemini-3.1-flash-tts-preview, sibling bridge gemini-2.5-flash-preview-tts.
    # Google's table lists these as input (text) / output (audio) per 1M tokens.
    "gemini-flash-tts": {
        # "Input: $1.00 (text)" / "Output: $20.00 (audio)"
        "gemini-3.1-flash-tts-preview": GoogleTokenRate(20.0, 1.00, _GEMINI_PRICING),
        # "Input: $0.50 (text)" / "Output: $10.00 (audio)"
        "gemini-2.5-flash-preview-tts": GoogleTokenRate(10.0, 0.50, _GEMINI_PRICING),
        # "Input: $1.00 (text)" / "Output: $20.00 (audio)"
        "gemini-2.5-pro-preview-tts": GoogleTokenRate(20.0, 1.00, _GEMINI_PRICING),
    },
    # jarvis/plugins/tts/cartesia_tts.py — Cartesia sells monthly credits and
    # publishes no credit-to-character conversion, so no per-character rate
    # can be derived from public pricing. Gap, not free.
    #
    # jarvis/plugins/tts/grok_voice_tts.py — xAI publishes no public rate for
    # voice synthesis at all. Gap.
    #
    # jarvis/plugins/tts/openrouter_tts.py — priced per speech model by the
    # upstream that serves it, with no flat published character rate. Gap.
}
_TTS_RATES["vertex-tts"] = _TTS_RATES["gemini-flash-tts"]

_TABLES: dict[str, dict[str, dict[str, Rate]]] = {
    STAGE_STT: _STT_RATES,
    STAGE_TTS: _TTS_RATES,
}


def find_rate(stage: str, provider: str, model_or_voice: str) -> Rate | None:
    """The published rate for one (stage, provider, model) triple, or ``None``.

    Matching is exact on a casefolded id — no prefix or family guessing. Two
    ElevenLabs models one character apart are priced 2x apart, so a near miss
    has to become a visible gap rather than a plausible wrong number.
    """
    table = _TABLES.get((stage or "").strip().casefold(), {})
    rates = table.get((provider or "").strip().casefold())
    if not rates:
        return None
    return rates.get((model_or_voice or "").strip().casefold()) or rates.get(ANY_MODEL)


def price_speech(
    stage: str,
    provider: str,
    model_or_voice: str = "",
    chars: int = 0,
    audio_ms: int = 0,
) -> tuple[float, PriceSource]:
    """Settle what one metered speech call cost, and how sure that number is.

    The four answers are the ones :func:`jarvis.costs.model.price_entry` uses,
    with the same meanings:

    ``free``      a local engine — 0.00 is the truth, not a missing rate.
    ``derived``   re-priced from the published table above.
    ``recorded``  nothing was consumed, so 0.00 is simply correct.
    ``unknown``   audio or characters were spent at a rate nobody publishes.
                  0.00 here is a GAP and the report must show it as one.

    There is deliberately no ``recorded`` path for a real call: unlike a brain
    response, neither STT nor TTS returns a price with the answer, so a real
    speech charge is always re-derived or a gap.
    """
    used = max(0, chars) + max(0, audio_ms)
    key = (provider or "").strip().casefold()

    if key in LOCAL_SPEECH_PROVIDERS or key in LOCAL_PROVIDERS:
        return 0.0, "free"

    if used <= 0:
        # No audio and no text — a 0.00 with nothing consumed is not a gap.
        return 0.0, "recorded"

    rate = find_rate(stage, provider, model_or_voice)
    if rate is None:
        return 0.0, "unknown"
    return rate.usd(max(0, chars), max(0, audio_ms)), "derived"


__all__ = [
    "ANY_MODEL",
    "LOCAL_SPEECH_PROVIDERS",
    "STAGE_STT",
    "STAGE_TTS",
    "CharRate",
    "GoogleTokenRate",
    "MinuteRate",
    "Rate",
    "find_rate",
    "price_speech",
]
