"""Voice-profile continuity across TTS takeovers (2026-07-17).

Bug: "the voice suddenly changes mid-conversation, masculine to feminine".
Every takeover path — the ``FallbackTTS`` wrapper, the plugins' internal
key-aware cross-family fallback — picked the taking-over family's arbitrary
default/config voice with no regard for the voice that was just speaking.
Live example (maintainer log 2026-07-11..16): primary OpenRouter spoke the
masculine model default (config voice "eve" is a Grok name, silently
replaced), then a 402/keyless failure crossed to Grok which spoke the
feminine "eve" — an audible mid-conversation gender flip.

Fix: curated gender tags in ``curated_catalog`` + ``continuity_voice``; the
factory pins the fallback family's profile-matching voice on ``FallbackTTS``,
and ``resolve_keyed_fallback`` accepts the failing provider's active voice as
``reference_voice``. Unknown profiles keep today's behavior (fail-safe).
"""
from __future__ import annotations

import pytest

import jarvis.plugins.tts as factory
from jarvis.plugins.tts import build_tts_from_config
from jarvis.plugins.tts.curated_catalog import continuity_voice, voice_gender
from jarvis.plugins.tts.fallback_tts import FallbackTTS

# --- Curated gender register --------------------------------------------------

def test_voice_gender_known_voices():
    assert voice_gender("Fenrir") == "m"      # Gemini Live (maintainer realtime)
    assert voice_gender("Charon") == "m"
    assert voice_gender("Kore") == "f"
    assert voice_gender("leo") == "m"
    assert voice_gender("eve") == "f"
    assert voice_gender("Josef") == "m"
    assert voice_gender("onwK4e9ZLuTAKqWW03F9") == "m"  # ElevenLabs Daniel


def test_voice_gender_is_case_insensitive_and_fail_safe():
    assert voice_gender("fenrir") == "m"
    assert voice_gender("EVE") == "f"
    assert voice_gender("some-unknown-voice") is None
    assert voice_gender("") is None
    assert voice_gender(None) is None


def test_continuity_voice_per_family():
    assert continuity_voice("grok-voice", "m") == "leo"
    assert continuity_voice("grok-voice", "f") == "ara"
    assert continuity_voice("gemini-flash-tts", "m") == "Charon"
    assert continuity_voice("gemini-flash-tts", "f") == "Kore"
    # ElevenLabs curates only masculine prebuilt voices — feminine has no match.
    assert continuity_voice("elevenlabs", "m") == "onwK4e9ZLuTAKqWW03F9"
    assert continuity_voice("elevenlabs", "f") is None


def test_continuity_voice_openrouter_resolves_vendor_prefix():
    google = "google/gemini-3.1-flash-tts-preview"
    assert continuity_voice("openrouter", "m", model_id=google) == "Charon"
    assert continuity_voice("openrouter", "f", model_id=google) == "Kore"
    assert continuity_voice("openrouter", "f", model_id="x-ai/grok-voice-tts-1.0") == "ara"
    # No vendor mapping → no guess (caller keeps the model default).
    assert continuity_voice("openrouter", "m", model_id="hexgrad/kokoro-82m") is None
    assert continuity_voice("openrouter", "m", model_id=None) is None


def test_continuity_voice_unknown_family_or_no_curated_voices():
    assert continuity_voice("cartesia", "m") is None   # ids not curated here
    assert continuity_voice("sapi5", "m") is None
    assert continuity_voice("", "m") is None


def test_inworld_language_bound_voices_are_never_pinned():
    # Inworld curates only language-bound voices; a pinned fallback voice must
    # be able to speak ANY turn language, so no match is returned.
    assert continuity_voice("inworld", "m") is None


# --- Factory whitelists follow the curated catalog ----------------------------

def test_factory_whitelists_cover_the_full_curated_roster():
    # Regression: the old hand-maintained whitelists (9 Gemini / 5 Grok names)
    # force-rewrote every OTHER legitimately picked voice to the family default
    # — itself an unwanted voice change.
    assert "Puck" in factory._GEMINI_VOICES
    assert "Sulafat" in factory._GEMINI_VOICES
    assert "luna" in factory._GROK_VOICES
    assert "atlas" in factory._GROK_VOICES


# --- FallbackTTS forwards the pinned continuity voice --------------------------

class _FakeTTS:
    supports_streaming = True

    def __init__(self, name, *, chunks=1, raise_before=False):
        self.name = name
        self._chunks = chunks
        self._raise_before = raise_before
        self.synth_calls: list[tuple] = []

    async def synthesize(self, text, voice=None, language_code=None):
        self.synth_calls.append((text, voice, language_code))
        if self._raise_before:
            raise RuntimeError(f"{self.name} boom")
        for i in range(self._chunks):
            yield f"{self.name}-{i}"


@pytest.mark.asyncio
async def test_wrapper_pins_continuity_voice_on_fallback():
    primary = _FakeTTS("primary", raise_before=True)
    fallback = _FakeTTS("fallback", chunks=1)
    wrapper = FallbackTTS(primary, fallback, fallback_voice="leo")
    out = [c async for c in wrapper.synthesize("Hallo Welt.", language_code="de-DE")]
    assert out == ["fallback-0"]
    assert fallback.synth_calls[0][1] == "leo"


@pytest.mark.asyncio
async def test_wrapper_without_pin_keeps_provider_default():
    primary = _FakeTTS("primary", raise_before=True)
    fallback = _FakeTTS("fallback", chunks=1)
    wrapper = FallbackTTS(primary, fallback)
    [c async for c in wrapper.synthesize("Hallo Welt.", language_code="de-DE")]
    assert fallback.synth_calls[0][1] is None


# --- Factory wiring -------------------------------------------------------------

class _Cfg:
    """Maintainer regression case: OpenRouter primary with a contaminated
    shared [tts] block — foreign model ("sonic-2" = Cartesia) and foreign
    voice ("eve" = Grok). The primary coerces to the Google speech model and
    speaks its masculine default; the Grok fallback would speak feminine
    "eve"."""

    provider = "openrouter-tts"
    fallback = "grok-voice"
    model = "sonic-2"
    voice_de = "eve"
    voice_en = "eve"
    language_code = "auto"
    style_prompt = ""
    allow_sapi5_fallback = False
    chunk_by_sentence = False
    seed = 7
    temperature = 0.7
    use_vertex = False
    vertex_project = None
    vertex_location = "us-central1"
    service_account_path = None
    speed = 1.0
    stability = 0.5
    similarity_boost = 0.75
    style = 0.0


@pytest.fixture()
def _all_keys_present(monkeypatch):
    """Keep the configured provider regardless of this host's real keys."""
    monkeypatch.setattr(factory, "_tts_has_credential", lambda canonical, cfg: True)


def test_factory_pins_masculine_grok_voice_for_masculine_primary(_all_keys_present):
    tts = build_tts_from_config(_Cfg())
    assert isinstance(tts, FallbackTTS)
    # Primary effectively speaks "Charon" (m) → the Grok fallback must not
    # speak "eve" (f); the factory pins the masculine Grok voice instead.
    assert tts._fallback_voice == "leo"


def test_factory_skips_pin_when_fallback_already_matches(_all_keys_present):
    class _Coherent(_Cfg):
        provider = "gemini-flash-tts"
        model = "gemini-3.1-flash-tts-preview"
        voice_de = "Charon"
        voice_en = "Charon"

    tts = build_tts_from_config(_Coherent())
    assert isinstance(tts, FallbackTTS)
    # Grok natively resolves Charon→leo (m), same profile → no override.
    assert tts._fallback_voice is None


def test_factory_pins_feminine_gemini_voice_for_feminine_grok_primary(_all_keys_present):
    class _FemininePrimary(_Cfg):
        provider = "grok-voice"
        fallback = "gemini-flash-tts"
        model = ""
        voice_de = "eve"
        voice_en = "eve"

    tts = build_tts_from_config(_FemininePrimary())
    assert isinstance(tts, FallbackTTS)
    # Grok speaks "eve" (f); Gemini would default to "Charon" (m) → pin "Kore".
    assert tts._fallback_voice == "Kore"


# --- Key-aware internal fallback carries the reference profile -------------------

def test_resolve_keyed_fallback_matches_reference_profile(monkeypatch):
    monkeypatch.setattr(
        factory, "_tts_has_credential",
        lambda canonical, cfg: canonical == "grok-voice",
    )
    fb = factory.resolve_keyed_fallback(
        "gemini-flash-tts", language_code="auto", reference_voice="Kore",
    )
    assert fb is not None and fb.name == "grok-voice"
    # Feminine reference ("Kore") → the crossed-to Grok speaks "ara", not "leo".
    assert fb._default_voice == "ara"


def test_resolve_keyed_fallback_without_reference_keeps_defaults(monkeypatch):
    monkeypatch.setattr(
        factory, "_tts_has_credential",
        lambda canonical, cfg: canonical == "grok-voice",
    )
    fb = factory.resolve_keyed_fallback("gemini-flash-tts", language_code="auto")
    assert fb is not None and fb.name == "grok-voice"
    assert fb._default_voice == "leo"
