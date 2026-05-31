"""Provider-agnostic LLM configuration for the Optimistic Execution prototype.

Reads settings from an env dict (test path) or from dotenv + os.environ
(production path). No network, no GPU, no OS-specific code here — pure config.

Keys (all optional, all have defaults):
    LLM_BACKEND       "http" | "mock"  (default: "http")
    LLM_BASE_URL      OpenAI-compatible base URL (default: local Ollama)
    LLM_MODEL         Model name (default: "qwen2.5:7b")
    LLM_API_KEY       Bearer token; empty string is treated as None
    LLM_TIMEOUT       Request timeout in seconds, float (default: 120.0)
    LLM_SYSTEM_PROMPT Optional system prompt injected into every request
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMSettings:
    """Immutable, provider-agnostic LLM configuration.

    Frozen so it can be safely shared across coroutines and passed by value.
    Hardware-agnostic: only base_url/model/api_key drive the provider choice.
    """

    backend: str            # "http" | "mock"
    base_url: str           # OpenAI-compatible base URL
    model: str              # Model identifier (provider-specific)
    api_key: str | None     # Bearer token; None → no Authorization header
    timeout: float          # httpx request timeout in seconds
    system_prompt: str | None  # Prepended to every conversation; None → omit

    @property
    def use_mock(self) -> bool:
        """Return True when the backend is 'mock' (no real network calls)."""
        return self.backend == "mock"


def load_settings(env: dict[str, str] | None = None) -> LLMSettings:
    """Build an LLMSettings from environment variables.

    Args:
        env: If provided, read from this dict instead of os.environ.
             This is the test entry-point — pass an explicit dict to avoid
             touching the real environment.
             If None, call dotenv.load_dotenv() first to populate os.environ,
             then read from os.environ.

    Returns:
        A frozen LLMSettings with defaults applied for any missing key.
    """
    if env is None:
        # Production path: load .env file into os.environ if present.
        try:
            import dotenv
            dotenv.load_dotenv()
        except ImportError:
            pass  # python-dotenv is optional; env vars may already be set
        source: dict[str, str] = dict(os.environ)
    else:
        source = env

    def _get(key: str, default: str | None = None) -> str | None:
        return source.get(key, default)

    backend = _get("LLM_BACKEND", "http") or "http"
    base_url = _get("LLM_BASE_URL", "http://localhost:11434/v1") or "http://localhost:11434/v1"
    model = _get("LLM_MODEL", "qwen2.5:7b") or "qwen2.5:7b"

    # Normalise api_key: empty / whitespace-only → None
    raw_key = _get("LLM_API_KEY", None)
    api_key: str | None = raw_key.strip() if raw_key and raw_key.strip() else None

    # Timeout: parse float, fall back to 120.0 on any error
    raw_timeout = _get("LLM_TIMEOUT", "120.0") or "120.0"
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 120.0

    system_prompt = _get("LLM_SYSTEM_PROMPT", None) or None

    return LLMSettings(
        backend=backend,
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=timeout,
        system_prompt=system_prompt,
    )
