"""LLM Protocol + multi-provider adapters (ADR-0004, amended).

Skillbook is provider-agnostic. The ``LLM`` Protocol is the only thing the
Reflector + Curator depend on; concrete adapters for Claude, Gemini, Grok
(xAI), and OpenAI live in this module and are constructed via
:func:`default_llm`.

Resolution order in ``default_llm()``:

  1. Explicit ``provider=`` argument from the caller (Jarvis-side wiring
     reads ``cfg.brain.primary`` and passes it).
  2. ``SKB_BRAIN_PROVIDER`` env var (``claude`` / ``gemini`` / ``grok`` /
     ``openai``).
  3. Auto-detect: try each provider in the fixed order above; return the
     first one whose SDK + API key are both present.

Each adapter is lazy: the SDK import + client construction only happen
when that provider is actually selected. So a user with only
``GEMINI_API_KEY`` doesn't need ``anthropic`` installed, and vice versa.

The previous in-tree ``MockLLM`` deterministic fallback lives in
``tests/fakes/llm.py:FakeLLM`` per ADR-0010 — production paths never fall
back to test doubles. The original ADR-0004 named Anthropic as the only
production LLM; that was the wrong default for this project (parent
``MEMORY.md``: "no Anthropic account, everything via cfg.brain.primary =
Gemini"). The multi-provider design here makes the active-brain choice
explicit at the call site.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from skillbook.errors import MissingAdapterError


@runtime_checkable
class LLM(Protocol):
    async def complete(self, prompt: str, *, max_tokens: int = 800) -> str: ...


_PROVIDER_ORDER = ("claude", "gemini", "grok", "openai")


def _try_claude_llm() -> LLM | None:
    """Anthropic / Claude. Env: ``ANTHROPIC_API_KEY``. SDK: ``anthropic``."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        return None
    if anthropic is None:
        return None

    model_name = os.environ.get("SKB_CLAUDE_MODEL", "claude-sonnet-4-6")
    client = anthropic.AsyncAnthropic(api_key=key)

    class _ClaudeLLM:
        async def complete(self, prompt: str, *, max_tokens: int = 800) -> str:
            msg = await client.messages.create(
                model=model_name,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            parts: list[str] = []
            for block in msg.content:
                if getattr(block, "type", None) == "text":
                    parts.append(getattr(block, "text", ""))
            return "".join(parts).strip()

    return _ClaudeLLM()


def _try_gemini_llm() -> LLM | None:
    """Google Gemini. Env: ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY``. SDK: ``google-genai``."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return None
    try:
        from google import genai  # type: ignore[import-not-found]
    except ImportError:
        return None
    if genai is None:
        return None

    model_name = os.environ.get("SKB_GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=key)

    class _GeminiLLM:
        async def complete(self, prompt: str, *, max_tokens: int = 800) -> str:
            config = genai.types.GenerateContentConfig(max_output_tokens=max_tokens)
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            return (response.text or "").strip()

    return _GeminiLLM()


def _try_grok_llm() -> LLM | None:
    """xAI / Grok via the OpenAI-compatible API. Env: ``XAI_API_KEY`` or
    ``GROK_API_KEY``. SDK: ``openai`` (reused with custom base_url)."""
    key = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
    if not key:
        return None
    try:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]
    except ImportError:
        return None

    model_name = os.environ.get("SKB_GROK_MODEL", "grok-2-latest")
    base_url = os.environ.get("SKB_GROK_BASE_URL", "https://api.x.ai/v1")
    client = AsyncOpenAI(api_key=key, base_url=base_url)

    class _GrokLLM:
        async def complete(self, prompt: str, *, max_tokens: int = 800) -> str:
            response = await client.chat.completions.create(
                model=model_name,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return (response.choices[0].message.content or "").strip()

    return _GrokLLM()


def _try_openai_llm() -> LLM | None:
    """OpenAI (ChatGPT API). Env: ``OPENAI_API_KEY``. SDK: ``openai``."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]
    except ImportError:
        return None

    model_name = os.environ.get("SKB_OPENAI_MODEL", "gpt-4o")
    client = AsyncOpenAI(api_key=key)

    class _OpenAILLM:
        async def complete(self, prompt: str, *, max_tokens: int = 800) -> str:
            response = await client.chat.completions.create(
                model=model_name,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return (response.choices[0].message.content or "").strip()

    return _OpenAILLM()


_PROVIDER_FACTORIES = {
    "claude": _try_claude_llm,
    "gemini": _try_gemini_llm,
    "grok": _try_grok_llm,
    "openai": _try_openai_llm,
}


def default_llm(*, provider: str | None = None) -> LLM:
    """Construct the LLM adapter for the active brain provider.

    - ``provider`` (caller-explicit) wins. Pass ``cfg.brain.primary`` from
      the Jarvis-side wiring to honor the UI's "Als aktiv" toggle.
    - Otherwise the ``SKB_BRAIN_PROVIDER`` env var is consulted.
    - Otherwise the four providers are tried in the order
      ``claude``, ``gemini``, ``grok``, ``openai`` and the first that has
      both its SDK and API key is returned.

    Raises :class:`MissingAdapterError` when no provider can be constructed.
    Production paths never silently fall back to test doubles — explicitly
    construct the deterministic fake from ``tests/fakes`` when needed.
    """
    if provider is None:
        provider = os.environ.get("SKB_BRAIN_PROVIDER")

    if provider:
        provider = provider.lower()
        if provider not in _PROVIDER_FACTORIES:
            raise MissingAdapterError(
                "llm",
                hint=(
                    f"unknown brain provider {provider!r}; "
                    f"supported: {sorted(_PROVIDER_FACTORIES.keys())}"
                ),
            )
        llm = _PROVIDER_FACTORIES[provider]()
        if llm is None:
            raise MissingAdapterError(
                "llm",
                hint=(
                    f"brain provider {provider!r} unavailable: "
                    f"its SDK and/or API key is missing"
                ),
            )
        return llm

    # Auto-detect: first provider with both SDK + key wins.
    for name in _PROVIDER_ORDER:
        llm = _PROVIDER_FACTORIES[name]()
        if llm is not None:
            return llm

    raise MissingAdapterError(
        "llm",
        hint=(
            "no brain provider available. Set one of "
            "ANTHROPIC_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY / "
            "XAI_API_KEY / OPENAI_API_KEY and install the matching extra "
            "([llm-claude], [llm-gemini], or [llm-openai] — Grok uses [llm-openai]). "
            "Or pass provider=... explicitly from the caller."
        ),
    )
