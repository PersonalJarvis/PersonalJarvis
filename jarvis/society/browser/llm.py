"""Roster provider → browser-use LLM spec (class name, model, explicit key).

The spec is plain data handed to the runner over the pipe; the runner
instantiates the class. Keys come from the app's secret store
(``get_provider_secret`` / ``get_secret``), never from environment files the
agent could read. A provider browser-use cannot drive is a typed failure,
not a guess — the agent then hears "no browser model for provider X".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from ..failure_reasons import FailureReason

__all__ = ["BrowserLLMSpec", "LLMUnavailable", "llm_spec_for"]


class LLMUnavailable(RuntimeError):
    def __init__(self, reason: FailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BrowserLLMSpec:
    provider: str
    cls: str
    model: str
    api_key: str | None = None
    base_url: str | None = None

    def to_request(self) -> dict[str, Any]:
        out: dict[str, Any] = {"class": self.cls, "model": self.model}
        if self.api_key:
            out["api_key"] = self.api_key
        if self.base_url:
            out["base_url"] = self.base_url
        return out


#: provider id (agent_chat catalog) → (browser-use class, default model, base_url, needs_key)
_MAP: Final[dict[str, tuple[str, str, str | None, bool]]] = {
    "anthropic": ("ChatAnthropic", "claude-sonnet-4-6", None, True),
    "claude-api": ("ChatAnthropic", "claude-sonnet-4-6", None, True),
    "openai": ("ChatOpenAI", "gpt-5", None, True),
    "gemini": ("ChatGoogle", "gemini-2.5-flash", None, True),
    "groq": ("ChatGroq", "llama-3.3-70b-versatile", None, True),
    "openrouter": ("ChatOpenRouter", "openai/gpt-5", None, True),
    "grok": ("ChatOpenAI", "grok-4", "https://api.x.ai/v1", True),
    "ollama": ("ChatOllama", "llama3.1:8b", None, False),
    "local-openai": ("ChatOpenAI", "", "http://127.0.0.1:8080/v1", False),
}


def llm_spec_for(
    provider: str, model: str = "", *, secret: Any = None, base_url: str = ""
) -> BrowserLLMSpec:
    """The browser-use LLM for ``provider``; raises :class:`LLMUnavailable`.

    ``secret`` is the key lookup (defaults to the app's ``get_provider_secret``);
    ``base_url`` overrides the default endpoint for OpenAI-compatible hosts.
    """
    pid = (provider or "").strip().lower()
    entry = _MAP.get(pid)
    if entry is None:
        raise LLMUnavailable(
            FailureReason.BLOCKED_BY_POLICY, f"no browser model for provider {provider!r}"
        )
    cls, default_model, default_base, needs_key = entry
    key: str | None = None
    if needs_key:
        lookup = secret
        if lookup is None:
            from jarvis.core.config import get_provider_secret

            lookup = get_provider_secret
        key = lookup(pid) or None
        if not key:
            raise LLMUnavailable(FailureReason.AUTH_FAILED, f"no API key stored for {pid}")
    return BrowserLLMSpec(
        provider=pid,
        cls=cls,
        model=(model or "").strip() or default_model,
        api_key=key,
        base_url=(base_url or "").strip() or default_base,
    )
