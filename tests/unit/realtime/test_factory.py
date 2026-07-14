"""Provider-neutral realtime factory tests (AP-21/AP-22)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.config import VoiceConfig
from jarvis.realtime.factory import (
    _provider_candidates,
    _resolve_realtime_provider,
    build_realtime_session,
    realtime_available_provider,
)


class _BaseProvider:
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, *, api_key=None):
        self.api_key = api_key

    async def can_open_duplex_session(self):
        return bool(self.api_key)

    async def open_session(self, cfg):  # pragma: no cover - factory does not open
        raise NotImplementedError


class _OpenAIProvider(_BaseProvider):
    name = "openai-realtime"
    credential_candidates = (("openai_api_key", "OPENAI_API_KEY"),)


class _GeminiProvider(_BaseProvider):
    name = "gemini-live"
    credential_candidates = (("gemini_api_key", "GEMINI_API_KEY"),)


_PLUGINS = {
    "openai-realtime": _OpenAIProvider,
    "gemini-live": _GeminiProvider,
}


def _cfg(mode: str = "realtime", provider: str = "openai-realtime") -> SimpleNamespace:
    return SimpleNamespace(
        voice=SimpleNamespace(mode=mode),
        brain=SimpleNamespace(
            reply_language="en",
            providers={},
            realtime=SimpleNamespace(
                provider=provider,
                fallback_provider=None,
                fallback_provider_2=None,
            ),
        ),
    )


def _fake_registry(monkeypatch, keys: set[str]) -> None:
    import jarvis.realtime.factory as factory

    monkeypatch.setattr(factory, "list_plugins", lambda _group: list(_PLUGINS))
    monkeypatch.setattr(factory, "load", lambda _group, name, protocol=None: _PLUGINS[name])

    def _get_secret(candidates):
        slot = candidates[0][0]
        family = "openai" if slot.startswith("openai") else "gemini"
        return f"{family}-key" if family in keys else None

    monkeypatch.setattr(factory, "get_secret_any", _get_secret)


@pytest.mark.parametrize(
    ("configured", "keys", "expected"),
    [
        ("openai-realtime", {"openai"}, "openai-realtime"),
        ("gemini-live", {"gemini"}, "gemini-live"),
        ("openai-realtime", {"gemini"}, "gemini-live"),
        ("gemini-live", {"openai"}, "openai-realtime"),
        ("openai-realtime", {"openai", "gemini"}, "openai-realtime"),
        ("openai-realtime", set(), None),
    ],
)
def test_key_aware_cross_family_resolution(
    monkeypatch, configured, keys, expected
) -> None:
    _fake_registry(monkeypatch, keys)
    config = _cfg(provider=configured)
    resolved = _resolve_realtime_provider(config)
    assert (resolved.name if resolved else None) == expected
    assert realtime_available_provider(config) == expected


def test_explicit_fallback_order_precedes_other_installed_plugins(monkeypatch):
    _fake_registry(monkeypatch, {"openai", "gemini"})
    config = _cfg(provider="openai-realtime")
    config.brain.realtime.fallback_provider = "gemini-live"

    assert [provider.name for provider in _provider_candidates(config)] == [
        "openai-realtime",
        "gemini-live",
    ]


def test_pipeline_mode_never_builds_realtime_session(monkeypatch):
    _fake_registry(monkeypatch, {"openai"})
    assert (
        build_realtime_session(
            cfg=_cfg(mode="pipeline"),
            bus=None,
            session_id="s",
            send_binary=None,
            send_json=None,
        )
        is None
    )


def test_realtime_tool_mode_defaults_to_compact_delegate_execution() -> None:
    assert VoiceConfig().realtime_tool_mode == "delegate"


def test_one_realtime_key_builds_without_a_classic_brain(monkeypatch) -> None:
    _fake_registry(monkeypatch, {"gemini"})

    session = build_realtime_session(
        cfg=_cfg(provider="openai-realtime"),
        bus=None,
        session_id="realtime-only",
        send_binary=lambda _data: None,
        send_json=lambda _message: None,
        brain=None,
    )

    assert session is not None
    assert session._brain is None
    assert session._tool_mode == "delegate"
    assert session._delegate_enabled is False
    assert [provider.name for provider in session._providers] == ["gemini-live"]


def test_realtime_without_any_key_degrades_to_pipeline(monkeypatch):
    _fake_registry(monkeypatch, set())
    assert (
        build_realtime_session(
            cfg=_cfg(),
            bus=None,
            session_id="s",
            send_binary=None,
            send_json=None,
        )
        is None
    )


def test_build_passes_every_keyed_family_for_handshake_fallback(monkeypatch):
    _fake_registry(monkeypatch, {"openai", "gemini"})
    session = build_realtime_session(
        cfg=_cfg(),
        bus=None,
        session_id="s",
        send_binary=lambda _data: None,
        send_json=lambda _message: None,
        half_duplex=True,
    )

    assert session is not None
    assert [provider.name for provider in session._providers] == [
        "openai-realtime",
        "gemini-live",
    ]
    assert session._half_duplex is True
