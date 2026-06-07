"""Unit tests for the CodexBrain plugin (lost-module rebuild).

CodexBrain is a thin OpenAI-chat brain that authenticates with the **Codex**
API-key slot (``codex_openai_api_key``), falling back to the general OpenAI key.
This makes "Codex" selectable as an independent brain provider, separate from the
plain ``openai`` provider, so the user can run e.g. brain=codex + subagent=gemini.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.brain.codex import CodexBrain


def test_name_and_capabilities() -> None:
    brain = CodexBrain()
    assert brain.name == "codex"
    assert brain.supports_tools is True


def test_ensure_client_raises_without_any_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.core.config.get_provider_secret", lambda _p: None)
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *_a, **_k: None)
    brain = CodexBrain()
    with pytest.raises(RuntimeError):
        brain._ensure_client()


def test_ensure_client_uses_codex_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _FakeAsyncOpenAI:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    # codex slot resolves first
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret",
        lambda p: "sk-codex-key" if p == "codex" else None,
    )
    brain = CodexBrain()
    brain._ensure_client()
    assert captured["api_key"] == "sk-codex-key"
