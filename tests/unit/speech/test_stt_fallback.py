"""Runtime STT fallback — a failing provider must not cost the user their words.

The live bug these lock (2026-07-29): a 137 s dictation came back with 367
characters because Groq answered ``429 Too Many Requests`` partway through and
nothing crossed to any of the three other providers the user had keys for. That
is the single-provider brick AP-22 exists to forbid.
"""

from __future__ import annotations

import pytest

from jarvis.core.protocols import Transcript
from jarvis.plugins.stt import _STT_CROSS_FAMILY_ORDER
from jarvis.speech.stt_fallback import (
    RATE_LIMIT_COOLDOWN_S,
    FallbackSTT,
    alternate_provider_names,
    configured_fallback_names,
    wrap_stt_with_fallback,
)


class FakeSTT:
    """Minimal STTProvider double: answers, or raises what it was told to."""

    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0

    async def transcribe_pcm(self, pcm, language=None, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return Transcript(text=self.text, language="de", confidence=0.9)


def _chain(primary: FakeSTT, **alternates: FakeSTT) -> FallbackSTT:
    return FallbackSTT(
        primary,
        list(alternates),
        lambda name: alternates[name],
        primary_name="groq-api",
    )


# ----------------------------------------------------------------------
# Crossing over
# ----------------------------------------------------------------------


async def test_rate_limit_crosses_to_the_next_provider():
    """REGRESSION: a 429 mid-dictation must not lose the audio."""
    primary = FakeSTT(error=RuntimeError("Client error '429 Too Many Requests'"))
    backup = FakeSTT(text="die echten Worte")  # i18n-allow: German test fixture
    chain = _chain(primary, **{"openai-api": backup})

    result = await chain.transcribe_pcm(b"\x00" * 32000, language="de")

    assert result.text == "die echten Worte"
    assert backup.calls == 1


@pytest.mark.parametrize(
    "selected",
    (
        "openrouter-stt",
        "openai-api",
        "gemini-api",
        "groq-api",
        "faster-whisper",
        "nemotron-local",
    ),
)
async def test_every_healthy_user_selected_provider_stays_primary(selected: str):
    primary = FakeSTT(text="selected")
    backup = FakeSTT(text="fallback")
    chain = FallbackSTT(
        primary,
        ["emergency-stt"],
        lambda _name: backup,
        primary_name=selected,
    )

    result = await chain.transcribe_pcm(b"x")

    assert result.text == "selected"
    assert chain.last_used_provider == selected
    assert primary.calls == 1
    assert backup.calls == 0


async def test_fallback_exposes_the_model_that_actually_answered():
    primary = FakeSTT(error=RuntimeError("429 Too Many Requests"))
    primary._model = "whisper-large-v3-turbo"
    backup = FakeSTT(text="recovered")
    backup._model = "gpt-4o-transcribe"
    chain = _chain(primary, **{"openai-api": backup})

    await chain.transcribe_pcm(b"x")

    assert chain.last_used_provider == "openai-api"
    assert chain.last_used_model == "gpt-4o-transcribe"


@pytest.mark.parametrize(
    "message",
    [
        "Client error '429 Too Many Requests'",
        "insufficient_quota: You exceeded your current quota",
        "Error code: 402 - payment required",
        "HTTP 503 Service Unavailable",
        "ReadTimeout: The read operation timed out",
        "ConnectError: [Errno 111] Connection refused",
        "Error code: 401 - invalid api key",
    ],
)
async def test_every_failure_another_provider_could_survive_crosses(message):
    primary = FakeSTT(error=RuntimeError(message))
    backup = FakeSTT(text="recovered")
    chain = _chain(primary, **{"openai-api": backup})

    assert (await chain.transcribe_pcm(b"x")).text == "recovered"


async def test_a_rejection_the_audio_caused_is_not_retried_elsewhere():
    """A 400 means the request itself was refused — another key answers the same.

    Crossing over here would burn a second provider's quota to be told the same
    thing, and hide the real reason from the user.
    """
    primary = FakeSTT(error=ValueError("400 Bad Request: audio file is too short"))
    backup = FakeSTT(text="never reached")
    chain = _chain(primary, **{"openai-api": backup})

    with pytest.raises(ValueError):
        await chain.transcribe_pcm(b"x")
    assert backup.calls == 0


async def test_the_error_surfaces_when_every_provider_is_exhausted():
    """No silent empty transcript: the user has to learn their keys are spent."""
    primary = FakeSTT(error=RuntimeError("429 Too Many Requests"))
    backup = FakeSTT(error=RuntimeError("429 Too Many Requests"))
    chain = _chain(primary, **{"openai-api": backup})

    with pytest.raises(RuntimeError, match="429"):
        await chain.transcribe_pcm(b"x")


# ----------------------------------------------------------------------
# Cooldown — hammering a rate limit is what keeps it locked
# ----------------------------------------------------------------------


async def test_a_rate_limited_provider_is_skipped_on_the_next_call():
    """The probe fires again 1.2s later; retrying the 429 only extends it."""
    primary = FakeSTT(error=RuntimeError("429 Too Many Requests"))
    backup = FakeSTT(text="ok")
    chain = _chain(primary, **{"openai-api": backup})

    await chain.transcribe_pcm(b"x")
    calls_after_first = primary.calls
    await chain.transcribe_pcm(b"x")

    assert primary.calls == calls_after_first  # not retried while cooling down
    assert backup.calls == 2


async def test_selected_provider_returns_after_its_emergency_cooldown(monkeypatch):
    import jarvis.speech.stt_fallback as fallback_mod

    now = 0.0
    monkeypatch.setattr(fallback_mod.time, "monotonic", lambda: now)
    primary = FakeSTT(error=RuntimeError("429 Too Many Requests"))
    backup = FakeSTT(text="temporary fallback")
    chain = _chain(primary, **{"openai-api": backup})

    assert (await chain.transcribe_pcm(b"x")).text == "temporary fallback"
    primary.error = None
    primary.text = "selected provider restored"
    now = RATE_LIMIT_COOLDOWN_S + 1.0

    assert (await chain.transcribe_pcm(b"x")).text == "selected provider restored"
    assert primary.calls == 2
    assert backup.calls == 1


async def test_a_cooling_provider_is_still_the_last_resort():
    """Skipped is not dropped: with every alternate dead it gets the last word."""
    primary = FakeSTT(text="from the configured provider")
    chain = _chain(primary, **{"openai-api": FakeSTT(error=RuntimeError("429"))})
    # Put the primary into cooldown by hand, then starve every alternate.
    chain._penalize("groq-api", "quota")

    result = await chain.transcribe_pcm(b"x")

    assert result.text == "from the configured provider"


async def test_the_working_alternate_is_preferred_on_later_segments():
    """A long dictation must not re-walk the whole chain on every segment."""
    primary = FakeSTT(error=RuntimeError("429 Too Many Requests"))
    first = FakeSTT(error=RuntimeError("503 Service Unavailable"))
    second = FakeSTT(text="ok")
    chain = FallbackSTT(
        primary,
        ["openai-api", "gemini-api"],
        lambda name: {"openai-api": first, "gemini-api": second}[name],
        primary_name="groq-api",
    )

    await chain.transcribe_pcm(b"x")
    calls_after_first = first.calls
    await chain.transcribe_pcm(b"x")

    assert first.calls == calls_after_first  # the dead one is not re-tried
    assert second.calls == 2


# ----------------------------------------------------------------------
# Chain construction
# ----------------------------------------------------------------------


def test_local_whisper_is_the_floor_not_the_first_choice():
    """A cloud key is fast and present; local is the option no quota can revoke."""
    order = alternate_provider_names(
        "groq-api", ["groq-api", "openai-api", "faster-whisper", "gemini-api"]
    )
    assert order == ["openai-api", "gemini-api", "faster-whisper"]
    assert "groq-api" not in order  # never falls back to the one that just failed


def test_groq_is_the_last_cloud_fallback():
    """Poor dictation quality keeps Groq available, but never preferred."""
    assert _STT_CROSS_FAMILY_ORDER == (
        "openrouter-stt",
        "openai-api",
        "gemini-api",
        "groq-api",
    )

    order = alternate_provider_names(
        "openrouter-stt", [*_STT_CROSS_FAMILY_ORDER, "faster-whisper"]
    )
    assert order == ["openai-api", "gemini-api", "groq-api", "faster-whisper"]


def test_a_single_key_install_is_not_wrapped():
    """Nothing to fall back to — no wrapper, no cost."""

    class Cfg:
        provider = "groq-api"

    provider = FakeSTT(text="x")
    import jarvis.plugins.stt as stt_mod

    original = stt_mod.available_stt_provider_names
    stt_mod.available_stt_provider_names = lambda: ["groq-api"]
    try:
        assert wrap_stt_with_fallback(provider, Cfg()) is provider
    finally:
        stt_mod.available_stt_provider_names = original


def test_empty_fallback_setting_disables_the_runtime_wrapper():
    class Cfg:
        provider = "openrouter-stt"
        fallback = ""

    provider = FakeSTT(text="x")
    assert wrap_stt_with_fallback(provider, Cfg()) is provider


def test_concrete_fallback_setting_pins_exactly_one_provider():
    class Cfg:
        fallback = "gemini-api"

    assert configured_fallback_names(
        Cfg(),
        "openrouter-stt",
        ("openai-api", "groq-api"),
    ) == ("gemini-api",)


def test_attributes_delegate_to_the_provider_in_front():
    """Duck-typed callers (recover(), is_warm, model fields) keep working."""
    primary = FakeSTT(text="x")
    primary.is_warm = True
    chain = _chain(primary, **{"openai-api": FakeSTT()})
    assert chain.is_warm is True
