"""What the dictation lane does when the provider says no, and whose STT it uses.

Three fixes meet here, and all three exist because the lane treated every
transcription failure as the same event:

* **The retry ladder had no opinion** (F2). It slept a fixed 0.6 s three times
  and asked again — a dead key exactly as eagerly as a rate limit. The error
  class it needed to tell those apart existed, and ``_transcribe`` threw it away
  by collapsing every exception into ``("", "", False)``. So a 401 cost the user
  1.8 s to be told what the first answer already said, and a 429 was re-fired
  0.6 s into a window the server had just said lasts longer than that.
* **Nothing bounded a single call** (F4's other half). google-genai forces
  ``timeout=None`` onto its own client and runs the request in a thread nobody
  can cancel, so a Gemini user whose call never returned had the lane wedged
  after the microphone had already closed: no text, no error, no end.
* **The lane transcribed with the VOICE provider** (F14/F3), which carries
  ``[stt].bias_prompt`` — the input the config documents as a
  silence-hallucination amplifier and deliberately withholds from the local
  engine — and, before the fallback chain, bound one provider for the whole
  session so a depleted key ended the dictation with three other keyed families
  sitting unused.

The retry behaviour is exercised through a whole ``_dictation_session`` with a
fake microphone and a scripted provider, because the ladder lives inside that
closure and its whole point is what it does BETWEEN calls.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.config import DictationConfig, STTConfig
from jarvis.core.events import DictationCompleted
from jarvis.speech.pipeline import (
    SpeechPipeline,
    _dictation_retry_worthwhile,
    _stt_error_status,
    _stt_retry_delay,
)

# 16 kHz mono int16 — the capture contract every dictation records under.
BYTES_PER_SECOND = 16_000 * 2


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


@dataclass
class _Transcript:
    text: str
    language: str = "en"


class _ScriptedSTT:
    """A provider that answers a fixed script, one entry per call.

    Each entry is either a string (a successful transcript) or an exception
    instance to raise. Running past the end repeats the last entry, so a test
    that wants "always fails" writes one exception rather than counting calls.
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        self.calls += 1
        step = self._script[min(self.calls, len(self._script)) - 1]
        if isinstance(step, BaseException):
            raise step
        return _Transcript(text=step)


class _NeverAnswersSTT:
    """The Gemini shape: a call that simply never comes back."""

    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = 0

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        self.calls += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise AssertionError("unreachable")  # pragma: no cover


class _Chunk:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.timestamp_ns = 0


class _FakeMic:
    """One burst of audio, then it waits to be cancelled like a real stream."""

    def __init__(self, seconds: float) -> None:
        # 16 kHz mono int16: one second is 16 000 samples of two bytes each.
        self._pcm = b"\x11\x22" * int(16_000 * seconds)

    async def stream(self):  # noqa: ANN201 — an async generator of chunks
        yield _Chunk(self._pcm)
        await asyncio.sleep(3600)


class _NullCapture:
    def __init__(self, source: Any) -> None:
        self._source = source

    async def __aenter__(self) -> Any:
        return self._source

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _session_pipeline(stt: Any, *, seconds: float = 1.0):
    """A pipeline wired for exactly one ``_dictation_session`` run."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = DictationConfig(
        history_enabled=False,
        # Unsegmented: the whole recording is one final piece, so the retry
        # ladder under test runs exactly once rather than per segment.
        segment_seconds=0.0,
        partial_interval_s=0.0,
        polish=False,
    )
    pipe._dictation_target = "chat"
    pipe._dictation_completion_published = False
    pipe._dictation_max_s = 30.0
    pipe._dictation_stt_instance = stt
    pipe._stt_final_timeout_s = 8.0
    pipe._hangup_event = asyncio.Event()
    pipe._dictation_stop_event = asyncio.Event()
    events: list[object] = []

    async def _publish(event: object) -> None:
        events.append(event)

    def _publish_soon(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._publish_event_soon = _publish_soon  # type: ignore[assignment]
    pipe._capture_dictation_input = lambda: _NullCapture(_FakeMic(seconds))  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: SimpleNamespace(  # type: ignore[assignment]
        status="inserted", detail="", method="clipboard+ctrl_v"
    )

    async def _stop_live(task, **_kwargs):  # noqa: ANN001, ANN202
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    pipe._stop_ptt_live_transcription = _stop_live  # type: ignore[assignment]
    return pipe, events


async def _run_session(pipe: Any) -> None:
    """Start a session and stop it as soon as the microphone has delivered."""
    task = asyncio.create_task(pipe._dictation_session())
    await asyncio.sleep(0)
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=30)


def _completed(events: list[object]) -> DictationCompleted:
    return next(e for e in events if isinstance(e, DictationCompleted))


def _retry_waits(slept: list[float]) -> list[float]:
    """The ladder's own waits, without the fakes' park-forever sleeps.

    Patching ``asyncio.sleep`` catches every sleeper in the test, including the
    fake microphone waiting to be cancelled. The ladder is capped at two
    seconds, so anything above that belongs to somebody else.
    """
    return [s for s in slept if 0 < s <= 10]


class _Response:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}


class _HTTPish(RuntimeError):
    """The ``httpx.HTTPStatusError`` shape — the only one the lane used to read."""

    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.response = _Response(status, headers)


# --------------------------------------------------------------------------
# Error classification — the fact the retry decision is built on
# --------------------------------------------------------------------------


def test_the_status_is_read_off_the_typed_error_first() -> None:
    """``STTHTTPError.status`` is the intended contract; the legacy shape stays."""
    from jarvis.plugins.stt.errors import STTHTTPError

    assert _stt_error_status(STTHTTPError("nope", status=429)) == 429
    assert _stt_error_status(_HTTPish(503)) == 503
    assert _stt_error_status(RuntimeError("socket reset")) is None


def test_a_google_style_code_attribute_is_understood_too() -> None:
    """The google-genai SDK reports its status on ``.code`` and is not importable
    on a base install, so it can only ever be duck-typed."""

    class _APIError(RuntimeError):
        code = 429

    assert _stt_error_status(_APIError("quota")) == 429


def test_a_rate_limit_is_retried_and_a_dead_key_is_not() -> None:
    from jarvis.plugins.stt.errors import STTHTTPError

    assert _dictation_retry_worthwhile(STTHTTPError("limit", status=429)) is True
    assert _dictation_retry_worthwhile(STTHTTPError("gateway", status=503)) is True
    assert _dictation_retry_worthwhile(STTHTTPError("bad key", status=401)) is False
    assert _dictation_retry_worthwhile(STTHTTPError("no credit", status=402)) is False
    assert _dictation_retry_worthwhile(STTHTTPError("bad audio", status=400)) is False


def test_a_failure_with_no_status_is_still_worth_one_more_try() -> None:
    """A dropped socket is exactly the blip a second attempt survives."""
    assert _dictation_retry_worthwhile(RuntimeError("connection reset")) is True
    assert _dictation_retry_worthwhile(None) is False


def test_our_own_ceiling_is_not_retried() -> None:
    """Reaching it means the piece already had twice its own length in wall
    clock and produced nothing. Asking again doubles a wait the user is already
    sitting through with the microphone closed."""
    assert _dictation_retry_worthwhile(TimeoutError()) is False


def test_the_servers_own_retry_after_beats_our_backoff() -> None:
    """Both RFC 9110 forms, because a gateway in front of a provider sends the
    date form and only the delta form was ever understood here."""
    from jarvis.plugins.stt.errors import STTHTTPError

    slow = STTHTTPError("limit", status=429, retry_after=1.5)
    # Attempt 0's own backoff would be 0.4 s; the server asked for more.
    assert _stt_retry_delay(slow, 0) == pytest.approx(1.5)
    # Still clamped: the user has stopped speaking and is waiting for text.
    huge = STTHTTPError("limit", status=429, retry_after=300)
    assert _stt_retry_delay(huge, 0) <= 2.0
    # The legacy header shape keeps working for the plugin that raises httpx.
    assert _stt_retry_delay(_HTTPish(429, {"retry-after": "1.0"}), 0) == pytest.approx(
        1.0
    )


# --------------------------------------------------------------------------
# The ladder itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dead_key_is_asked_exactly_once() -> None:
    """The whole point of F2: a 401 answered three times is 1.8 s of the user
    waiting to learn what the first answer already said."""
    from jarvis.plugins.stt.errors import STTHTTPError

    stt = _ScriptedSTT([STTHTTPError("invalid key", status=401)])
    pipe, events = _session_pipeline(stt)

    await _run_session(pipe)

    assert stt.calls == 1
    completed = _completed(events)
    assert completed.outcome == "failed"
    assert completed.error, "a refused dictation must name a reason"


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried_until_it_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 is the case the ladder exists for, and the second attempt wins."""
    from jarvis.plugins.stt.errors import STTHTTPError

    slept: list[float] = []

    async def _no_wait(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _no_wait)
    stt = _ScriptedSTT([STTHTTPError("limit", status=429), "the words"])
    pipe, events = _session_pipeline(stt)

    await _run_session(pipe)

    assert stt.calls == 2
    assert _completed(events).text == "the words"
    # It waited, and it waited at least as long as the ladder's own floor.
    waits = _retry_waits(slept)
    assert waits and max(waits) >= 0.6


@pytest.mark.asyncio
async def test_the_wait_between_attempts_honours_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-firing 0.6 s into a window the server said is longer only extends it."""
    from jarvis.plugins.stt.errors import STTHTTPError

    slept: list[float] = []

    async def _no_wait(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _no_wait)
    stt = _ScriptedSTT(
        [STTHTTPError("limit", status=429, retry_after=1.9), "the words"]
    )
    pipe, _events = _session_pipeline(stt)

    await _run_session(pipe)

    assert _retry_waits(slept) == [pytest.approx(1.9)]


@pytest.mark.asyncio
async def test_a_provider_that_never_answers_ends_the_dictation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unbounded is the one failure mode a user cannot escape: the microphone is
    already closed and nothing is coming.

    The ceiling is derived from the audio, so it is shortened here by shrinking
    the multiplier rather than by pretending a wall clock passed — the number
    under test is the one production computes.
    """
    import jarvis.speech.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "_DICTATION_TRANSCRIBE_TIMEOUT_PER_AUDIO_S", 0.1
    )
    stt = _NeverAnswersSTT()
    pipe, events = _session_pipeline(stt, seconds=0.5)
    pipe._stt_final_timeout_s = 0.05

    await _run_session(pipe)

    assert stt.calls == 1, "our own ceiling means a wedge, not a blip worth retrying"
    assert stt.cancelled == 1, "the lane must stop waiting, not merely give up"
    assert _completed(events).outcome in ("failed", "empty")


# --------------------------------------------------------------------------
# F14 / F3 — whose provider, and what it was built with
# --------------------------------------------------------------------------


def _pipeline_with_config(**stt_kwargs: Any) -> Any:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_stt_instance = None
    pipe._utterance_stt = object()
    pipe._dictation_cfg = DictationConfig()
    pipe._config = SimpleNamespace(stt=STTConfig(**stt_kwargs))
    return pipe


def test_the_dictation_provider_is_built_without_the_voice_bias_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``[stt].bias_prompt`` is documented as a silence-hallucination amplifier
    and withheld from the local engine — and handed to dictation anyway."""
    import jarvis.plugins.stt as stt_plugins

    seen: list[Any] = []

    def _build(cfg: Any) -> Any:
        seen.append(cfg)
        return SimpleNamespace(name="built")

    monkeypatch.setattr(stt_plugins, "build_stt_from_config", _build)
    monkeypatch.setattr(stt_plugins, "resolve_keyed_stt_fallback", lambda *_a, **_k: ())
    pipe = _pipeline_with_config(
        provider="groq-api", bias_prompt="Jarvis, Adex, Ruben, Vokando"
    )

    instance = pipe._dictation_stt()

    assert instance is not None
    assert seen and seen[0].bias_prompt == ""
    # The VOICE config is untouched — this is a copy, not a mutation.
    assert pipe._config.stt.bias_prompt == "Jarvis, Adex, Ruben, Vokando"


def test_the_dictation_provider_is_built_once_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.plugins.stt as stt_plugins

    builds: list[Any] = []
    monkeypatch.setattr(
        stt_plugins,
        "build_stt_from_config",
        lambda cfg: builds.append(cfg) or SimpleNamespace(),
    )
    monkeypatch.setattr(stt_plugins, "resolve_keyed_stt_fallback", lambda *_a, **_k: ())
    pipe = _pipeline_with_config(provider="groq-api")

    first = pipe._dictation_stt()
    second = pipe._dictation_stt()

    assert first is second
    assert len(builds) == 1
    # ...and a settings or language change drops it, or the setting would
    # silently apply to conversations and not to dictation.
    pipe._reset_dictation_stt()
    pipe._dictation_stt()
    assert len(builds) == 2


def test_the_dictation_provider_arms_the_cross_family_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AP-22: a depleted key must not end the dictation while another keyed
    family sits unused — and the alternates come from the resolver that
    guarantees one entry per CREDENTIAL family."""
    import jarvis.plugins.stt as stt_plugins
    import jarvis.speech.pipeline as pipeline_mod
    import jarvis.speech.stt_dictionary as dictionary
    from jarvis.speech.stt_fallback import FallbackSTT

    monkeypatch.setattr(
        stt_plugins, "build_stt_from_config", lambda cfg: SimpleNamespace()
    )
    monkeypatch.setattr(
        pipeline_mod,
        "_resolve_stt_fallback_chain",
        lambda *_a, **_k: ("openai-api", "gemini-api"),
    )
    # The dictionary wrapper sits ON TOP of the chain and its presence depends
    # on whether THIS host has dictionary entries; stand it down so the test
    # asserts about the chain and not about the developer's vocabulary.
    monkeypatch.setattr(dictionary, "wrap_stt_with_dictionary", lambda provider: provider)
    pipe = _pipeline_with_config(provider="groq-api")

    instance = pipe._dictation_stt()

    assert isinstance(instance, FallbackSTT)
    assert instance._alternate_names == ["openai-api", "gemini-api"]


def test_an_unbuildable_dictation_provider_falls_back_to_the_voice_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dictation with the wrong prompt beats a dictation with no provider."""
    import jarvis.plugins.stt as stt_plugins

    def _boom(cfg: Any) -> Any:
        raise RuntimeError("no entry-point")

    monkeypatch.setattr(stt_plugins, "build_stt_from_config", _boom)
    pipe = _pipeline_with_config(provider="groq-api")

    assert pipe._dictation_stt() is pipe._utterance_stt


def test_protected_terms_carry_the_words_the_user_chose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The polish guard's input: the STT dictionary plus the wake word, and no
    duplicates — a repeated term is a wasted slot in a bounded prompt."""
    import jarvis.speech.stt_dictionary as dictionary

    monkeypatch.setattr(dictionary, "dictionary_bias_words", lambda: ["Vokando", "Adex"])
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._wake_phrase_label = "Hey Adex"

    terms = pipe._dictation_protected_terms()

    assert "Vokando" in terms
    assert "Adex" in terms
    assert len(terms) == len(set(t.casefold() for t in terms))


def test_protected_terms_survive_an_unreadable_dictionary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard input, never a gate: this may not be a way to lose a dictation."""
    import jarvis.speech.stt_dictionary as dictionary

    def _boom() -> list[str]:
        raise RuntimeError("store is locked")

    monkeypatch.setattr(dictionary, "dictionary_bias_words", _boom)
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._wake_phrase_label = "Nova"

    assert pipe._dictation_protected_terms() == ("Nova",)
