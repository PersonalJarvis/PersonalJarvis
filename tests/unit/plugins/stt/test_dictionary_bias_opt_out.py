"""BUG-211: the STT dictionary stays out of the decoder prompt on request.

A prompt-capable Whisper recites its primed word list over a pause, and a
lone recited item passes the echo guard. The dictation lane therefore builds
its provider with ``dictionary_bias=False``; this pins that the flag really
keeps the words out of the ``prompt`` kwarg, and that the default still
merges them for the voice lane.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.plugins.stt as stt_plugins
import jarvis.speech.stt_dictionary as dictionary


class _RecordingProvider:
    """Stands in for a cloud plugin; keeps the kwargs it was built with."""

    built: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        type(self).built.append(kwargs)


@pytest.fixture
def _cloud_provider(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingProvider]:
    _RecordingProvider.built = []
    monkeypatch.setattr(stt_plugins, "_resolve_effective_stt", lambda name: (name, ""))
    monkeypatch.setattr(stt_plugins, "_load_provider_class", lambda name: _RecordingProvider)
    monkeypatch.setattr(
        dictionary, "dictionary_bias_words", lambda *_a, **_k: ["GitHub", "Agentic IDE"]
    )
    return _RecordingProvider


def _cfg(**overrides: Any) -> Any:
    base = {"provider": "groq-api", "language": "auto", "bias_prompt": "", "model": ""}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_the_dictionary_is_merged_into_the_prompt_by_default(
    _cloud_provider: type[_RecordingProvider],
) -> None:
    stt_plugins.build_stt_from_config(_cfg(bias_prompt="Jarvis"))

    assert _cloud_provider.built[0]["prompt"] == "Jarvis, GitHub, Agentic IDE"


def test_the_opt_out_sends_no_prompt_when_the_dictionary_is_the_only_bias(
    _cloud_provider: type[_RecordingProvider],
) -> None:
    stt_plugins.build_stt_from_config(_cfg(), dictionary_bias=False)

    assert "prompt" not in _cloud_provider.built[0]


def test_the_opt_out_keeps_an_explicit_bias_prompt_but_not_the_dictionary(
    _cloud_provider: type[_RecordingProvider],
) -> None:
    stt_plugins.build_stt_from_config(_cfg(bias_prompt="Jarvis"), dictionary_bias=False)

    assert _cloud_provider.built[0]["prompt"] == "Jarvis"


def test_a_named_fallback_honours_the_opt_out_too(
    _cloud_provider: type[_RecordingProvider],
) -> None:
    """A fallback that quietly re-added the words would recite them the
    moment it took over — the chain must match the primary."""
    stt_plugins.build_named_stt_provider("openai-api", _cfg(), dictionary_bias=False)

    assert "prompt" not in _cloud_provider.built[0]
