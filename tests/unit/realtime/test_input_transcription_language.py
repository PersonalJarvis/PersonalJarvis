"""The session language reaches the local recognizer, and the engine is reused.

Two defects live here, and they compound. The transport that needs local
transcription hands over the session's input language; until the recognizer
accepted one, that language was dropped and the engine ran on ``auto`` — the
configuration that answers near-silence with caption-style boilerplate, which
this transport then treats as something the user said. And every call built its
own engine, so the model load was re-paid inside each handshake, in front of the
user's first word.

The tests below therefore pin the language on the CONFIG the recognizer is built
from (not on a keyword some backend may not have), and pin that identical
construction parameters yield the identical engine while different ones do not.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from jarvis.core.config import STTConfig
from jarvis.realtime import input_transcription as module
from jarvis.realtime.input_transcription import LocalInputTranscriber

RATE = 24_000


class _FakeStt:
    """A recognizer stand-in that remembers what it was configured with."""

    native_sample_rate = 16_000

    def __init__(self, language: str) -> None:
        self.language = language

    async def transcribe(self, chunks: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("these tests never recognize audio")


class _FakeSttFactory:
    """Records every build and hands out a distinct engine each time."""

    def __init__(self) -> None:
        self.configs: list[Any] = []

    def __call__(self, stt_cfg: Any) -> _FakeStt:
        self.configs.append(stt_cfg)
        return _FakeStt(stt_cfg.language)


@pytest.fixture(autouse=True)
def _clean_recognizer_cache():
    module._reset_for_tests()
    yield
    module._reset_for_tests()


@pytest.fixture
def stt_builds(monkeypatch: pytest.MonkeyPatch) -> _FakeSttFactory:
    """Replace config loading and STT construction with recording fakes."""
    builder = _FakeSttFactory()
    config = type("_Config", (), {"stt": STTConfig(provider="groq-api", language="en")})

    monkeypatch.setattr(
        "jarvis.core.config.load_config", lambda *a, **k: config, raising=True
    )
    monkeypatch.setattr(
        "jarvis.plugins.stt.build_stt_from_config", builder, raising=True
    )
    return builder


# -- the language kwarg ------------------------------------------------


def test_the_language_keyword_is_accepted_and_keyword_only() -> None:
    """The transport passes ``language=``; a TypeError here drops it silently."""
    signature = inspect.signature(LocalInputTranscriber.__init__)
    parameter = signature.parameters["language"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None
    # The exact call the Codex subscription transport makes.
    LocalInputTranscriber(sample_rate=RATE, language="de")


async def test_the_session_language_reaches_the_built_recognizer(
    stt_builds: _FakeSttFactory,
) -> None:
    transcriber = LocalInputTranscriber(sample_rate=RATE, language="de")

    assert await transcriber.warm() is True
    assert [cfg.language for cfg in stt_builds.configs] == ["de"]


async def test_no_language_keeps_the_configured_one(
    stt_builds: _FakeSttFactory,
) -> None:
    """Existing callers pass nothing and must keep the STT config's own value."""
    transcriber = LocalInputTranscriber(sample_rate=RATE)

    assert await transcriber.warm() is True
    assert [cfg.language for cfg in stt_builds.configs] == ["en"]


async def test_a_blank_language_is_the_same_as_none(
    stt_builds: _FakeSttFactory,
) -> None:
    transcriber = LocalInputTranscriber(sample_rate=RATE, language="   ")

    assert await transcriber.warm() is True
    assert [cfg.language for cfg in stt_builds.configs] == ["en"]


def test_a_config_without_a_language_field_is_left_alone(caplog) -> None:
    """An honest no-op beats losing the recognizer over a missing attribute."""
    plain = object()

    with caplog.at_level("DEBUG", logger=module.__name__):
        assert module._language_pinned_stt_config(plain, "de") is plain

    assert "carries no language field" in caplog.text


async def test_an_injected_factory_configures_itself(caplog) -> None:
    """An injected factory owns its construction; the drop is said out loud."""
    built: list[_FakeStt] = []

    def factory() -> _FakeStt:
        engine = _FakeStt("its-own")
        built.append(engine)
        return engine

    transcriber = LocalInputTranscriber(
        sample_rate=RATE, stt_factory=factory, language="de"
    )

    with caplog.at_level("DEBUG", logger=module.__name__):
        assert await transcriber.warm() is True

    assert len(built) == 1
    assert "session language 'de' is not applied" in caplog.text
    # An injected engine is the caller's own object and never enters the cache.
    assert not module._stt_cache


# -- the process-wide recognizer cache ---------------------------------


async def test_two_sessions_with_the_same_language_share_one_engine(
    stt_builds: _FakeSttFactory,
) -> None:
    first = LocalInputTranscriber(sample_rate=RATE, language="de")
    second = LocalInputTranscriber(sample_rate=RATE, language="de")

    assert await first.warm() is True
    assert await second.warm() is True

    assert len(stt_builds.configs) == 1
    assert len(module._stt_cache) == 1


async def test_a_different_language_builds_its_own_engine(
    stt_builds: _FakeSttFactory,
) -> None:
    german = LocalInputTranscriber(sample_rate=RATE, language="de")
    spanish = LocalInputTranscriber(sample_rate=RATE, language="es")

    assert await german.warm() is True
    assert await spanish.warm() is True

    assert [cfg.language for cfg in stt_builds.configs] == ["de", "es"]
    assert len(module._stt_cache) == 2


def test_identical_keys_return_the_identical_entry() -> None:
    builds: list[str] = []

    def build() -> object:
        builds.append("built")
        return object()

    first = module._shared_recognizer("key-a", build)
    again = module._shared_recognizer("key-a", build)
    other = module._shared_recognizer("key-b", build)

    assert again is first
    assert other is not first
    assert len(builds) == 2


def test_the_cache_is_bounded() -> None:
    for index in range(module._STT_CACHE_MAX + 2):
        module._shared_recognizer(f"key-{index}", object)

    assert len(module._stt_cache) == module._STT_CACHE_MAX
    # The oldest entries are the ones that went.
    assert "key-0" not in module._stt_cache
    assert f"key-{module._STT_CACHE_MAX + 1}" in module._stt_cache


def test_a_retired_engine_is_not_handed_out_again() -> None:
    builds: list[object] = []

    def build() -> object:
        engine = object()
        builds.append(engine)
        return engine

    first = module._shared_recognizer("key-a", build)
    module._forget_shared_recognizer(first)
    second = module._shared_recognizer("key-a", build)

    assert second is not first
    assert len(builds) == 2


async def test_recovering_a_wedged_engine_evicts_it_from_the_cache(
    stt_builds: _FakeSttFactory,
) -> None:
    """The wedge must not be inherited by the next call (AP-24)."""
    transcriber = LocalInputTranscriber(sample_rate=RATE, language="de")
    assert await transcriber.warm() is True
    assert len(module._stt_cache) == 1

    await transcriber._recover_recognizer("a test asked for it")

    assert not module._stt_cache
    assert await transcriber.warm() is True
    assert len(stt_builds.configs) == 2


async def test_a_shared_engine_carries_its_own_in_flight_guard(
    stt_builds: _FakeSttFactory,
) -> None:
    """Two sessions on one engine must not enter it concurrently (AP-24)."""
    first = LocalInputTranscriber(sample_rate=RATE, language="de")
    second = LocalInputTranscriber(sample_rate=RATE, language="de")
    assert await first.warm() is True
    assert await second.warm() is True

    first._recognition_busy = True

    assert second._recognition_busy is True
    first._recognition_busy = False
    assert second._recognition_busy is False
