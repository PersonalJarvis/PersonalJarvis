"""Map a society agent's Jarvis provider + model onto an external runtime.

Every agent on Hermes or OpenClaw reaches its model through Jarvis' own model
gateway (``gateway.py``): the runtime is configured with one provider —
Jarvis, on the app's loopback server — and a per-agent token in its process
environment (:data:`KEY_ENV_VAR`). Jarvis answers with its own provider
plugins, so the runtime never holds a vendor key or login and its config is
the same for every provider. API-key and local providers speak Chat
Completions; the ChatGPT subscription (``openai-codex``) speaks Responses.

This module decides which providers an agent can use right now (a saved key,
a local server with an address, a signed-in subscription) and builds the
route. A Claude subscription is not offered: its login works only inside
Claude Code, and Hermes would bill it as paid extra usage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Literal

log = logging.getLogger(__name__)

Transport = Literal["chat_completions", "anthropic_messages", "responses"]

#: The environment variable both runtimes read the model key from.
KEY_ENV_VAR: Final[str] = "JARVIS_RUNTIME_API_KEY"

#: A Claude subscription (OAuth) token, never an Anthropic API key.
_CLAUDE_LOGIN_PREFIX: Final[str] = "sk-ant-oat"

#: The provider name Jarvis registers inside the runtime's config.
RUNTIME_PROVIDER_NAME: Final[str] = "jarvis"


@dataclass(frozen=True, slots=True)
class _Endpoint:
    #: Vendor default base URL; ``None`` = the person must configure one.
    default_base_url: str | None
    keyless: bool = False
    #: A local server: it needs a server address the person configured.
    local_server: bool = False
    #: Served by Jarvis' own model gateway on the person's subscription.
    subscription: bool = False


#: Jarvis provider id (``agent_chat.catalog`` / ``core.config``) -> endpoint.
_ENDPOINTS: Final[dict[str, _Endpoint]] = {
    "claude-api": _Endpoint("https://api.anthropic.com"),
    "openai": _Endpoint("https://api.openai.com/v1"),
    "grok": _Endpoint("https://api.x.ai/v1"),
    "openrouter": _Endpoint("https://openrouter.ai/api/v1"),
    "nvidia": _Endpoint("https://integrate.api.nvidia.com/v1"),
    "gemini": _Endpoint("https://generativelanguage.googleapis.com/v1beta/openai"),
    "ollama": _Endpoint(None, keyless=True, local_server=True),
    "local-openai": _Endpoint(None, keyless=True, local_server=True),
    "openai-codex": _Endpoint(None, subscription=True),
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


def subscription_providers() -> frozenset[str]:
    """Providers served by Jarvis' model gateway on the person's subscription."""
    return frozenset(name for name, endpoint in _ENDPOINTS.items() if endpoint.subscription)


def supports(provider: str) -> bool:
    return provider in _ENDPOINTS


def usable_providers(config: Any) -> list[str]:
    """Supported providers that can run right now: a saved key, a keyless
    local server with an address, or a signed-in subscription (listed
    first: it spends no API key). Blocking (keyring): call it in a thread.
    """
    usable: list[str] = []
    for provider in sorted(_ENDPOINTS, key=lambda name: (not _ENDPOINTS[name].subscription, name)):
        try:
            _checked_model(config, provider, "probe")
        except RouteUnavailable:  # not connected: the provider is simply not offered
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


def _checked_model(config: Any, provider: str, model: str, *, account_id: str = "") -> str:
    """The model the agent runs on, once the provider is known to be able to
    answer; raises :class:`RouteUnavailable` with the reason in plain words."""
    endpoint = _ENDPOINTS.get(provider)
    if endpoint is None:
        raise RouteUnavailable(
            f"The {provider or 'selected'} seat cannot drive Hermes or OpenClaw. "
            "Pick a model that runs on an API key, a local server or the ChatGPT "
            "subscription."
        )
    if endpoint.subscription:
        from jarvis.agent_runtimes import gateway

        if not gateway.subscription_ready(account_id):
            raise RouteUnavailable(
                "The ChatGPT subscription is not signed in. Connect it in Settings → API "
                "keys first."
            )
        chosen = model.strip() or _default_model(config, provider)
        if not chosen:
            # The agent was created without a model: Codex's own first pick.
            from jarvis.agent_chat.catalog import CODEX_FALLBACK_MODELS

            chosen = CODEX_FALLBACK_MODELS[0].id if CODEX_FALLBACK_MODELS else ""
        if not chosen:
            raise RouteUnavailable("Choose a ChatGPT model for this agent first.")
        return chosen
    from jarvis.core.config import get_jarvis_agent_secret, resolve_provider_endpoint

    resolved = resolve_provider_endpoint(
        provider, vendor_default_base_url=endpoint.default_base_url
    )
    base_url = resolved.base_url
    if endpoint.local_server and not resolved.via_proxy:
        base_url = _local_root(provider, base_url)
    if not base_url:
        raise RouteUnavailable(
            f"{provider} has no server address yet. Add it on the provider's card first."
        )
    # The Agents-tier key wins over the shared one, exactly as the gateway uses it.
    key = (get_jarvis_agent_secret(provider) or resolved.credential or "").strip() or None
    if key is not None and provider == "claude-api" and key.startswith(_CLAUDE_LOGIN_PREFIX):
        # A Claude subscription login saved in the API-key slot. Jarvis' own
        # Claude Code seats can use it; Anthropic's API refuses it ("OAuth
        # access token is invalid"), so it is not an API key here.
        raise RouteUnavailable(
            "The saved Anthropic credential is a Claude subscription login, not an "
            "API key. Hermes and OpenClaw need an Anthropic API key, or pick another "
            "provider."
        )
    if key is None and not endpoint.keyless:
        raise RouteUnavailable(
            f"No API key is saved for {provider}. Connect it in Settings → API keys."
        )
    chosen = model.strip() or _default_model(config, provider)
    if not chosen:
        raise RouteUnavailable(f"Choose a model for {provider} first.")
    return chosen


def route_for(
    config: Any, provider: str, model: str, *, agent_id: str = "", account_id: str = ""
) -> ModelRoute:
    """The agent's route through Jarvis' gateway. Blocking (keyring): call it
    in a thread. The token speaks for ``agent_id`` on ``provider`` (and, for
    the subscription, on that Codex ``account_id``)."""
    chosen = _checked_model(config, provider, model, account_id=account_id)
    from jarvis.agent_runtimes import gateway

    base_url = gateway.base_url()
    if not base_url:
        raise RouteUnavailable("Jarvis' model gateway is not up yet. Try again in a moment.")
    return ModelRoute(
        provider=provider,
        model=chosen,
        base_url=base_url,
        transport="responses" if _ENDPOINTS[provider].subscription else "chat_completions",
        api_key=gateway.grant_token(agent_id, provider, account_id),
    )
