"""Map a society agent's Jarvis provider + model onto an external runtime.

Hermes and OpenClaw both accept any OpenAI-compatible endpoint and any
Anthropic Messages endpoint as a named custom provider, with the key read
from an environment variable. That one shape is the most basic and longest
stable configuration either project has, so every supported Jarvis provider
is expressed through it: the endpoint comes from
``jarvis.core.config.resolve_provider_endpoint`` (overrides and the team
proxy included) and the key travels only in the runtime process' environment
as :data:`KEY_ENV_VAR` — never in a file (AP-12).

Subscription seats (Claude Code, Codex, …) are not offered: driving those
logins from another vendor's agent loop is not a path Jarvis can keep
working across updates, and Hermes would bill a Claude Max login as paid
extra usage. Vertex needs service-account auth neither runtime receives here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Literal

log = logging.getLogger(__name__)

Transport = Literal["chat_completions", "anthropic_messages"]

#: The environment variable both runtimes read the model key from.
KEY_ENV_VAR: Final[str] = "JARVIS_RUNTIME_API_KEY"

#: The provider name Jarvis registers inside the runtime's config.
RUNTIME_PROVIDER_NAME: Final[str] = "jarvis"


@dataclass(frozen=True, slots=True)
class _Endpoint:
    transport: Transport
    #: Vendor default base URL; ``None`` = the person must configure one.
    default_base_url: str | None
    keyless: bool = False
    #: Appended to a configured server root (local servers store the root).
    path_suffix: str = ""


#: Jarvis provider id (``agent_chat.catalog`` / ``core.config``) -> endpoint.
_ENDPOINTS: Final[dict[str, _Endpoint]] = {
    "claude-api": _Endpoint("anthropic_messages", "https://api.anthropic.com"),
    "openai": _Endpoint("chat_completions", "https://api.openai.com/v1"),
    "grok": _Endpoint("chat_completions", "https://api.x.ai/v1"),
    "openrouter": _Endpoint("chat_completions", "https://openrouter.ai/api/v1"),
    "nvidia": _Endpoint("chat_completions", "https://integrate.api.nvidia.com/v1"),
    "gemini": _Endpoint(
        "chat_completions", "https://generativelanguage.googleapis.com/v1beta/openai"
    ),
    "ollama": _Endpoint("chat_completions", None, keyless=True, path_suffix="/v1"),
    "local-openai": _Endpoint("chat_completions", None, keyless=True, path_suffix="/v1"),
}


class RouteUnavailable(Exception):
    """The agent's model cannot run on the runtime; the message is user-facing."""


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """Everything a runtime config needs to reach the agent's model."""

    provider: str
    model: str
    base_url: str
    transport: Transport
    #: ``None`` for a keyless local server.
    api_key: str | None

    def env(self) -> dict[str, str]:
        """The child environment that carries the key (empty when keyless)."""
        return {KEY_ENV_VAR: self.api_key} if self.api_key else {}


def supported_providers() -> frozenset[str]:
    """Jarvis providers an external runtime can run on (no secrets read)."""
    return frozenset(_ENDPOINTS)


def supports(provider: str) -> bool:
    return provider in _ENDPOINTS


def usable_providers(config: Any) -> list[str]:
    """Supported providers that can run right now: a saved key, or a keyless
    local server with an address. Blocking (keyring): call it in a thread.

    The model picker offers only these on Hermes / OpenClaw, so a Claude
    subscription without an Anthropic API key never looks like a choice.
    """
    usable: list[str] = []
    for provider in sorted(_ENDPOINTS):
        try:
            route_for(config, provider, "probe")
        except RouteUnavailable:
            continue
        except Exception:  # noqa: BLE001 — one unreadable provider must not empty the list
            log.warning("agent runtimes: provider %s could not be checked", provider, exc_info=True)
            continue
        usable.append(provider)
    return usable


def _default_model(config: Any, provider: str) -> str:
    providers = getattr(getattr(config, "brain", None), "providers", None) or {}
    configured = str(getattr(providers.get(provider), "model", "") or "")
    if configured:
        return configured
    from jarvis.brain.manager import get_tier_default_model

    return get_tier_default_model("router", provider) or ""


def _local_root(provider: str, configured: str | None) -> str | None:
    if provider == "ollama":
        from jarvis.plugins.brain.ollama import default_server_root, normalize_server_root

        return normalize_server_root(configured or default_server_root())
    if configured:
        from jarvis.plugins.brain.ollama import normalize_server_root

        return normalize_server_root(configured)
    return None


def route_for(config: Any, provider: str, model: str) -> ModelRoute:
    """Resolve the endpoint, key and model. Blocking (keyring): call in a thread."""
    endpoint = _ENDPOINTS.get(provider)
    if endpoint is None:
        raise RouteUnavailable(
            f"The {provider or 'selected'} seat cannot drive Hermes or OpenClaw. "
            "Pick a model that runs on an API key or a local server."
        )
    from jarvis.core.config import resolve_provider_endpoint

    resolved = resolve_provider_endpoint(
        provider, vendor_default_base_url=endpoint.default_base_url
    )
    base_url = resolved.base_url
    if endpoint.path_suffix and not resolved.via_proxy:
        root = _local_root(provider, base_url)
        base_url = f"{root}{endpoint.path_suffix}" if root else None
    if not base_url:
        raise RouteUnavailable(
            f"{provider} has no server address yet. Add it on the provider's card first."
        )
    key = (resolved.credential or "").strip() or None
    if key is None and not endpoint.keyless:
        raise RouteUnavailable(
            f"No API key is saved for {provider}. Connect it in Settings → API keys."
        )
    chosen = model.strip() or _default_model(config, provider)
    if not chosen:
        raise RouteUnavailable(f"Choose a model for {provider} first.")
    return ModelRoute(
        provider=provider,
        model=chosen,
        base_url=base_url.rstrip("/"),
        transport=endpoint.transport,
        api_key=key,
    )
