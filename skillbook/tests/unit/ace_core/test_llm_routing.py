"""Multi-provider LLM routing: default_llm picks the right adapter.

Covers:
- Explicit ``provider=`` argument wins over env var.
- ``SKB_BRAIN_PROVIDER`` env var picks the provider.
- Auto-detect tries providers in fixed order, returns first available.
- Unknown provider name raises MissingAdapterError.
- All providers unavailable raises MissingAdapterError with a useful hint.

Each provider adapter is exercised against a fake SDK to confirm:
- the right API key env var is read,
- the right SDK call is made,
- the response text comes back through the LLM Protocol.
"""

from __future__ import annotations

import sys
import types

import pytest

from skillbook.errors import MissingAdapterError


# ---------------------------------------------------------------------------
# Fake SDK installers
# ---------------------------------------------------------------------------


def _clear_all_keys(monkeypatch) -> None:
    for var in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "XAI_API_KEY",
        "GROK_API_KEY",
        "OPENAI_API_KEY",
        "SKB_BRAIN_PROVIDER",
        "SKB_CLAUDE_MODEL",
        "SKB_GEMINI_MODEL",
        "SKB_GROK_MODEL",
        "SKB_OPENAI_MODEL",
        "SKB_GROK_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _install_fake_anthropic(monkeypatch, *, response_text: str = "claude-said-OK") -> dict:
    captured: dict = {"keys": [], "calls": []}

    class _Block:
        def __init__(self, text: str) -> None:
            self.type = "text"
            self.text = text

    class _Message:
        def __init__(self, text: str) -> None:
            self.content = [_Block(text)]

    class _Messages:
        async def create(self, *, model: str, max_tokens: int, messages: list) -> _Message:
            captured["calls"].append({"model": model, "max_tokens": max_tokens, "messages": messages})
            return _Message(response_text)

    class _AsyncAnthropic:
        def __init__(self, *, api_key: str) -> None:
            captured["keys"].append(api_key)
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = _AsyncAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return captured


def _install_fake_genai(monkeypatch, *, response_text: str = "gemini-said-OK") -> dict:
    captured: dict = {"keys": [], "calls": []}

    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text

    class _AioModels:
        async def generate_content(self, *, model: str, contents, config) -> _Response:
            captured["calls"].append({"model": model, "contents": contents, "config": config})
            return _Response(response_text)

    class _Aio:
        def __init__(self) -> None:
            self.models = _AioModels()

    class _Client:
        def __init__(self, *, api_key: str) -> None:
            captured["keys"].append(api_key)
            self.aio = _Aio()

    fake_types = types.ModuleType("google.genai.types")
    fake_types.GenerateContentConfig = lambda **kw: kw  # type: ignore[attr-defined]
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = _Client  # type: ignore[attr-defined]
    fake_genai.types = fake_types  # type: ignore[attr-defined]
    fake_google = sys.modules.get("google") or types.ModuleType("google")
    fake_google.genai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    return captured


def _install_fake_openai(monkeypatch, *, response_text: str = "openai-said-OK") -> dict:
    captured: dict = {"keys": [], "base_urls": [], "calls": []}

    class _Message:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = _Message(content)

    class _Response:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        async def create(self, *, model: str, max_tokens: int, messages: list) -> _Response:
            captured["calls"].append({"model": model, "max_tokens": max_tokens, "messages": messages})
            return _Response(response_text)

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _AsyncOpenAI:
        def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
            captured["keys"].append(api_key)
            captured["base_urls"].append(base_url)
            self.chat = _Chat()

    fake = types.ModuleType("openai")
    fake.AsyncOpenAI = _AsyncOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake)
    return captured


# ---------------------------------------------------------------------------
# Adapter-level tests: each provider, isolated
# ---------------------------------------------------------------------------


async def test_claude_adapter_threads_key_and_returns_text(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    cap = _install_fake_anthropic(monkeypatch, response_text="claude-hi")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key-1")
    from skillbook.ace_core.llm import default_llm

    llm = default_llm(provider="claude")
    out = await llm.complete("ping")
    assert out == "claude-hi"
    assert cap["keys"] == ["ant-key-1"]
    assert cap["calls"][0]["model"].startswith("claude-")


async def test_gemini_adapter_threads_key_and_returns_text(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    cap = _install_fake_genai(monkeypatch, response_text="gem-hi")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key-1")
    from skillbook.ace_core.llm import default_llm

    llm = default_llm(provider="gemini")
    out = await llm.complete("ping")
    assert out == "gem-hi"
    assert cap["keys"] == ["gem-key-1"]


async def test_grok_adapter_uses_openai_sdk_with_xai_base_url(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    cap = _install_fake_openai(monkeypatch, response_text="grok-hi")
    monkeypatch.setenv("XAI_API_KEY", "xai-key-1")
    from skillbook.ace_core.llm import default_llm

    llm = default_llm(provider="grok")
    out = await llm.complete("ping")
    assert out == "grok-hi"
    assert cap["keys"] == ["xai-key-1"]
    assert cap["base_urls"] == ["https://api.x.ai/v1"]
    assert cap["calls"][0]["model"].startswith("grok-")


async def test_openai_adapter_uses_default_base_url(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    cap = _install_fake_openai(monkeypatch, response_text="oai-hi")
    monkeypatch.setenv("OPENAI_API_KEY", "oai-key-1")
    from skillbook.ace_core.llm import default_llm

    llm = default_llm(provider="openai")
    out = await llm.complete("ping")
    assert out == "oai-hi"
    assert cap["keys"] == ["oai-key-1"]
    assert cap["base_urls"] == [None]


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------


async def test_explicit_provider_arg_wins_over_env(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    _install_fake_anthropic(monkeypatch, response_text="claude-wins")
    _install_fake_genai(monkeypatch, response_text="gemini-wins")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("SKB_BRAIN_PROVIDER", "gemini")  # env says gemini
    from skillbook.ace_core.llm import default_llm

    llm = default_llm(provider="claude")  # explicit arg overrides
    out = await llm.complete("ping")
    assert out == "claude-wins"


async def test_env_var_picks_provider_when_arg_is_none(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    _install_fake_anthropic(monkeypatch)
    _install_fake_openai(monkeypatch, response_text="env-picked-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("SKB_BRAIN_PROVIDER", "openai")
    from skillbook.ace_core.llm import default_llm

    llm = default_llm()
    out = await llm.complete("ping")
    assert out == "env-picked-openai"


async def test_auto_detect_returns_first_available_in_fixed_order(monkeypatch) -> None:
    """Order is claude, gemini, grok, openai. With gemini + openai keys present
    (no claude), gemini wins because it comes earlier in the order."""
    _clear_all_keys(monkeypatch)
    _install_fake_genai(monkeypatch, response_text="gemini-auto")
    _install_fake_openai(monkeypatch, response_text="openai-auto")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    from skillbook.ace_core.llm import default_llm

    llm = default_llm()
    out = await llm.complete("ping")
    assert out == "gemini-auto"


async def test_unknown_provider_raises_missing_adapter(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    from skillbook.ace_core.llm import default_llm

    with pytest.raises(MissingAdapterError) as exc:
        default_llm(provider="bogus_provider")
    assert "unknown" in str(exc.value).lower()


async def test_no_provider_available_raises_with_useful_hint(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    from skillbook.ace_core.llm import default_llm

    with pytest.raises(MissingAdapterError) as exc:
        default_llm()
    hint = str(exc.value)
    assert "ANTHROPIC_API_KEY" in hint
    assert "GEMINI_API_KEY" in hint
    assert "OPENAI_API_KEY" in hint


async def test_provider_requested_but_key_missing_raises(monkeypatch) -> None:
    _clear_all_keys(monkeypatch)
    _install_fake_anthropic(monkeypatch)
    # key intentionally not set
    from skillbook.ace_core.llm import default_llm

    with pytest.raises(MissingAdapterError) as exc:
        default_llm(provider="claude")
    assert "unavailable" in str(exc.value).lower()
