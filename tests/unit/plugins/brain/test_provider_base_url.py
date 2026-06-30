"""W1a: each brain provider passes the resolved base_url to its SDK client."""
from __future__ import annotations

from typing import Any

import jarvis.core.config as cfg
from jarvis.core.config import BrainConfig, BrainProviderConfig, JarvisConfig


class _FakeOpenAI:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeOpenAI.last_kwargs = kwargs


class _FakeAnthropic:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeAnthropic.last_kwargs = kwargs


class _FakeGenaiClient:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeGenaiClient.last_kwargs = kwargs


def _override(provider_id: str, url: str, monkeypatch) -> None:
    conf = JarvisConfig(brain=BrainConfig(providers={provider_id: BrainProviderConfig(base_url=url)}))
    monkeypatch.setattr(cfg, "load_config", lambda: conf)
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-test")


def _no_override(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-test")


# ── OpenRouter ────────────────────────────────────────────────────────────
def test_openrouter_uses_override(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _override("openrouter", "https://proxy/p/openrouter/v1", monkeypatch)
    from jarvis.plugins.brain.openrouter import OpenRouterBrain

    OpenRouterBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://proxy/p/openrouter/v1"
    assert _FakeOpenAI.last_kwargs["api_key"] == "sk-test"


def test_openrouter_default_without_override(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _no_override(monkeypatch)
    from jarvis.plugins.brain.openrouter import OpenRouterBrain

    OpenRouterBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://openrouter.ai/api/v1"


# ── OpenAI ────────────────────────────────────────────────────────────────
def test_openai_uses_override(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _override("openai", "https://proxy/p/openai/v1", monkeypatch)
    from jarvis.plugins.brain.openai import OpenAIBrain

    OpenAIBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://proxy/p/openai/v1"


def test_openai_no_override_omits_base_url(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _no_override(monkeypatch)
    from jarvis.plugins.brain.openai import OpenAIBrain

    OpenAIBrain()._ensure_client()
    # No override + no vendor default → base_url omitted so the SDK uses its own default.
    assert "base_url" not in _FakeOpenAI.last_kwargs


# ── claude-api (Anthropic) ─────────────────────────────────────────────────
def test_claude_api_uses_override(monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)
    _override("claude-api", "https://proxy/p/claude-api", monkeypatch)
    from jarvis.plugins.brain.claude_api import ClaudeAPIBrain

    ClaudeAPIBrain()._ensure_client()
    assert _FakeAnthropic.last_kwargs["base_url"] == "https://proxy/p/claude-api"
    assert _FakeAnthropic.last_kwargs["api_key"] == "sk-test"


def test_claude_api_no_override_omits_base_url(monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)
    _no_override(monkeypatch)
    from jarvis.plugins.brain.claude_api import ClaudeAPIBrain

    ClaudeAPIBrain()._ensure_client()
    assert "base_url" not in _FakeAnthropic.last_kwargs


# ── Gemini ────────────────────────────────────────────────────────────────
def test_gemini_uses_override(monkeypatch):
    from google import genai

    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)
    _override("gemini", "https://proxy/p/gemini", monkeypatch)
    from jarvis.plugins.brain.gemini import GeminiBrain

    GeminiBrain()._ensure_client()
    http_opts = _FakeGenaiClient.last_kwargs.get("http_options")
    assert http_opts is not None
    base = getattr(http_opts, "base_url", None) or (
        http_opts.get("base_url") if isinstance(http_opts, dict) else None
    )
    assert base == "https://proxy/p/gemini"


def test_gemini_no_override_omits_http_options(monkeypatch):
    from google import genai

    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)
    _no_override(monkeypatch)
    from jarvis.plugins.brain.gemini import GeminiBrain

    GeminiBrain()._ensure_client()
    assert "http_options" not in _FakeGenaiClient.last_kwargs
    assert _FakeGenaiClient.last_kwargs["api_key"] == "sk-test"
