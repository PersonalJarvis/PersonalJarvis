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


def _live_jcfg(*, brain_primary: str) -> SimpleNamespace:
    """GPT-Live is the voice: the OpenAI key belongs to the voice call."""
    return SimpleNamespace(
        voice=SimpleNamespace(mode="realtime"),
        brain=SimpleNamespace(
            primary=brain_primary,
            realtime=SimpleNamespace(provider="openai-live"),
        ),
    )


def test_primary_on_the_voice_key_hands_flash_work_to_another_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mandate 2026-09-29: the GPT-Live key pays for the voice call only."""
    keys = {"openai_api_key": "sk-test", "gemini_api_key": "gk-test"}
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: keys.get(key)
    )

    ack_cfg = make_ack_config(provider="follow_brain")
    provider = _build_flash_provider(_live_jcfg(brain_primary="openai"), ack_cfg)

    assert ack_cfg.provider == "gemini"
    assert type(provider).__name__ == "GeminiFlashAck"


def test_single_voice_key_install_still_gets_a_flash_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the voice key as the only credential, flash work stays on it
    rather than on a local server that may not exist (AP-22)."""
    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, env_fallback=None: "sk-test" if key == "openai_api_key" else None,
    )

    ack_cfg = make_ack_config(provider="follow_brain")
    _build_flash_provider(_live_jcfg(brain_primary="openai"), ack_cfg)

    assert ack_cfg.provider == "openai"


def test_pipeline_voice_keeps_following_an_openai_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a realtime voice the key is an ordinary brain key."""
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: "sk-test"
    )
    jcfg = _live_jcfg(brain_primary="openai")
    jcfg.voice.mode = "pipeline"

    ack_cfg = make_ack_config(provider="follow_brain")
    _build_flash_provider(jcfg, ack_cfg)

    assert ack_cfg.provider == "openai"
