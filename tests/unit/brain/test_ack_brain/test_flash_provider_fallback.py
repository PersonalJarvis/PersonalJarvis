"""Tests for the Flash-Brain's key-aware ``follow_brain`` fallback (AP-22).

When ``brain.primary`` points at a provider the Flash-Brain has no adapter
for (openrouter, claude_api), ``_build_flash_provider`` used to fall back to
a hardcoded literal ``"gemini"`` — bricking the tier for any downloader whose
only configured key is for a different provider. The fallback must instead
pick the first REGISTRY family with a usable credential.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.factory import _build_flash_provider
from tests.unit.brain.test_ack_brain.conftest import make_ack_config


def _jcfg(*, brain_primary: str) -> SimpleNamespace:
    return SimpleNamespace(brain=SimpleNamespace(primary=brain_primary))


def test_falls_back_to_openai_when_only_openai_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brain.primary="claude-api" has no Flash adapter; only an OpenAI key is
    configured — the fallback must resolve to "openai", never "gemini"."""

    def _fake_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        return "sk-test" if key == "openai_api_key" else None

    monkeypatch.setattr("jarvis.core.config.get_secret", _fake_get_secret)

    ack_cfg = make_ack_config(provider="follow_brain")
    provider = _build_flash_provider(_jcfg(brain_primary="claude-api"), ack_cfg)

    assert ack_cfg.provider == "openai"
    assert type(provider).__name__ == "OpenAIMiniAck"


def test_falls_back_to_gemini_when_only_gemini_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        return "gk-test" if key == "gemini_api_key" else None

    monkeypatch.setattr("jarvis.core.config.get_secret", _fake_get_secret)

    ack_cfg = make_ack_config(provider="follow_brain")
    provider = _build_flash_provider(_jcfg(brain_primary="claude-api"), ack_cfg)

    assert ack_cfg.provider == "gemini"
    assert type(provider).__name__ == "GeminiFlashAck"


def test_falls_back_to_gemini_when_only_a_realtime_scoped_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-key install whose only credential came from the Realtime card
    must still resolve the Flash tier to that family (2026-07-21 Mac forensic:
    the strict slot scoping bricked every non-realtime brain tier)."""

    def _fake_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        return "rt-test" if key == "realtime_gemini_api_key" else None

    monkeypatch.setattr("jarvis.core.config.get_secret", _fake_get_secret)

    ack_cfg = make_ack_config(provider="follow_brain")
    provider = _build_flash_provider(_jcfg(brain_primary="claude-api"), ack_cfg)

    assert ack_cfg.provider == "gemini"
    assert type(provider).__name__ == "GeminiFlashAck"


def test_falls_back_to_ollama_when_no_key_present_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama needs no credential (local endpoint) — it is the last-resort
    keyless fallback when no cloud provider has a usable key."""
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: None
    )

    ack_cfg = make_ack_config(provider="follow_brain")
    provider = _build_flash_provider(_jcfg(brain_primary="claude-api"), ack_cfg)

    assert ack_cfg.provider == "ollama"
    assert type(provider).__name__ == "OllamaFlashAck"


def test_does_not_change_ack_behavior_when_primary_has_a_flash_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When brain.primary IS a REGISTRY family, the fallback path is never
    consulted — no credential lookup happens at all."""
    called = False

    def _fail_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("jarvis.core.config.get_secret", _fail_get_secret)

    ack_cfg = make_ack_config(provider="follow_brain")
    provider = _build_flash_provider(_jcfg(brain_primary="openai"), ack_cfg)

    assert ack_cfg.provider == "openai"
    assert type(provider).__name__ == "OpenAIMiniAck"
    assert called is False
