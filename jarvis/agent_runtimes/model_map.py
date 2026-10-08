"""Map a society agent's Jarvis provider + model onto an external runtime.

API-backed agents on Hermes or OpenClaw reach their model through Jarvis' own model
gateway (``gateway.py``): the runtime is configured with one provider —
Jarvis, on the app's loopback server — and a per-agent token in its process
environment (:data:`KEY_ENV_VAR`). Jarvis answers with its own provider
plugins, so the runtime never holds a vendor key or login and its config is
the same for every provider. API-key and local providers speak Chat
Completions; the ChatGPT subscription (``openai-codex``) speaks Responses.

This module decides which providers an agent can use right now (a saved key,
a local server with an address, a signed-in subscription) and builds the
route. Claude subscriptions use each runtime's native Claude CLI adapter.
The official CLI owns login and renewal; Jarvis never forwards its bearer
to the HTTP gateway or substitutes an API key for a selected subscription.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, replace
from typing import Any, Final, Literal

log = logging.getLogger(__name__)

Transport = Literal["chat_completions", "anthropic_messages", "responses", "claude_cli"]

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
    #: Supports the official Claude CLI's native subscription transport.
    claude_login: bool = False
    #: May authenticate without a key: Google Cloud Application Default
    #: Credentials for a configured project (Vertex AI).
    cloud_project: bool = False


#: Jarvis provider id (``agent_chat.catalog`` / ``core.config``) -> endpoint.
_ENDPOINTS: Final[dict[str, _Endpoint]] = {
    "claude-api": _Endpoint("https://api.anthropic.com", claude_login=True),
    "openai": _Endpoint("https://api.openai.com/v1"),
    "grok": _Endpoint("https://api.x.ai/v1"),
    "openrouter": _Endpoint("https://openrouter.ai/api/v1"),
    "nvidia": _Endpoint("https://integrate.api.nvidia.com/v1"),
    "gemini": _Endpoint("https://generativelanguage.googleapis.com/v1beta/openai"),
    "vertex": _Endpoint("https://aiplatform.googleapis.com", cloud_project=True),
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
    context_window: int = 32_768
    max_output_tokens: int | None = None
    claude_binary: str = ""
    claude_config_dir: str = ""

    def env(self) -> dict[str, str]:
        """Only the selected transport's credential or native account location."""
        if self.transport == "claude_cli":
            from jarvis.agent_runtimes.base import child_env
            from jarvis.claude_auth import _cli_credential_env

            selected = (
                {"CLAUDE_CONFIG_DIR": self.claude_config_dir} if self.claude_config_dir else {}
            )
            native = _cli_credential_env(child_env(selected)) or {}
            if native.get("USER") and not os.environ.get("USER"):
                selected["USER"] = native["USER"]
            return selected
        return {KEY_ENV_VAR: self.api_key} if self.api_key else {}


def supported_providers() -> frozenset[str]:
    """Jarvis providers an external runtime can run on (no secrets read)."""
    return frozenset(_ENDPOINTS)


def subscription_providers() -> frozenset[str]:
    """Providers served by Jarvis' model gateway on the person's subscription."""
    return frozenset(name for name, endpoint in _ENDPOINTS.items() if endpoint.subscription)


@dataclass(frozen=True, slots=True)
class _NativeClaude:
    binary_path: str
    config_dir: str = ""


def claude_subscription_status(account_id: str = "") -> _NativeClaude | None:
    """Ask the selected account's native CLI without reading its bearer."""
    from jarvis import agent_accounts
    from jarvis.agent_chat.catalog import ACCESS_ACCOUNTS
    from jarvis.agent_runtimes.base import child_env
    from jarvis.claude_auth import ClaudeAuthService

    requested = account_id if account_id not in ACCESS_ACCOUNTS else ""
    account = (agent_accounts.resolve(requested) if requested
               else agent_accounts.active_account("claude"))
    if account is None or account.platform != "claude":
        raise RouteUnavailable("The selected Claude subscription account no longer exists.")
    inherited = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    env = child_env({"CLAUDE_CONFIG_DIR": inherited} if inherited else {})
    env = agent_accounts.spawn_env("claude", account.id, base=env)
    service = ClaudeAuthService()
    binary = service._resolve_binary()
    if binary is None:
        return None
    status = service._probe_cli_auth(binary, env=env)
    method = (status.auth_method or "").lower() if status is not None else ""
    if status is not None and status.logged_in and method in {"claude.ai", "oauth", "claudeai"}:
        return _NativeClaude(binary, env.get("CLAUDE_CONFIG_DIR", ""))
    return None


def _api_key(provider: str, credential: str | None) -> str | None:
    """The provider's real API key (the Agents-tier key wins), never a login."""
    from jarvis.core.config import get_jarvis_agent_secret

    key = (get_jarvis_agent_secret(provider) or credential or "").strip() or None
    if key is not None and provider == "claude-api" and key.startswith(_CLAUDE_LOGIN_PREFIX):
        # A Claude subscription login saved in the API-key slot is not an API
        # key: Anthropic's API refuses it as one ("OAuth access token is invalid").
        return None
    return key


def _saved_key(provider: str, endpoint: _Endpoint) -> str | None:
    from jarvis.core.config import resolve_provider_endpoint

    resolved = resolve_provider_endpoint(
        provider, vendor_default_base_url=endpoint.default_base_url
    )
    return _api_key(provider, resolved.credential)


def uses_native_claude(provider: str, account_id: str = "") -> bool:
    """Select the native seat independently of login availability.

    A disconnected subscription stays a subscription, never a paid fallback.
    Existing unpinned agents keep using their saved API key when one exists.
    """
    from jarvis.agent_chat.catalog import API_KEY_ACCOUNT

    endpoint = _ENDPOINTS.get(provider)
    if endpoint is None or not endpoint.claude_login or account_id == API_KEY_ACCOUNT:
        return False
    if not account_id and _saved_key(provider, endpoint) is not None:
        return False
    return True


def login_providers() -> list[str]:
    """Default routes that use the native subscription CLI. Blocking."""
    return [
        name for name in _ENDPOINTS
        if uses_native_claude(name) and claude_subscription_status()
    ]


def access_choices() -> dict[str, list[str]]:
    """Per provider that can pay two ways (Claude: API key or Claude Code
    login), the ways that work right now: ``"api"`` and/or ``"subscription"``.
    The "New agent" dialog offers exactly these. Blocking (keyring)."""
    choices: dict[str, list[str]] = {}
    for name, endpoint in _ENDPOINTS.items():
        if not endpoint.claude_login:
            continue
        found: list[str] = []
        if _saved_key(name, endpoint) is not None:
            found.append("api")
        if claude_subscription_status() is not None:
            found.append("subscription")
        if found:
            choices[name] = found
    return choices


def access_blocked() -> dict[str, dict[str, str]]:
    """Native plan availability is independent of HTTP Extra Usage settings.

    Keep the API field for compatible clients; native quota failures surface
    from actual user-started turns, never from background inference probes.
    """
    return {}


def supports(provider: str) -> bool:
    return provider in _ENDPOINTS


def is_local(provider: str) -> bool:
    """Whether ``provider`` is a model server the person runs themselves."""
    endpoint = _ENDPOINTS.get(provider)
    return endpoint is not None and endpoint.local_server


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
    if uses_native_claude(provider, account_id):
        if claude_subscription_status(account_id) is None:
            raise RouteUnavailable(
                "This agent needs a connected Claude Code subscription. Connect Claude "
                "in Settings → API keys. No API key was used."
            )
        return model.strip() or _default_model(config, provider) or "sonnet"
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
    from jarvis.core.config import resolve_provider_endpoint

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
    key = _api_key(provider, resolved.credential)
    if endpoint.claude_login:
        if key is None:
            raise RouteUnavailable(
                "This agent runs on an Anthropic API key, and none is saved. Connect "
                "one in Settings → API keys, or switch the agent to the subscription."
            )
    if key is None and endpoint.cloud_project:
        from jarvis.core.config import vertex_credential_configured

        if vertex_credential_configured():
            key = ""  # the Cloud project signs the requests (no key)
    if key is None and not endpoint.keyless and not endpoint.claude_login:
        raise RouteUnavailable(
            f"No API key is saved for {provider}. Connect it in Settings → API keys."
        )
    chosen = model.strip() or _default_model(config, provider)
    if not chosen:
        raise RouteUnavailable(f"Choose a model for {provider} first.")
    return chosen


def route_for(
    config: Any,
    provider: str,
    model: str,
    *,
    agent_id: str = "",
    account_id: str = "",
    session_id: str = "",
) -> ModelRoute:
    """The agent's route through Jarvis' gateway. Blocking (keyring): call it
    in a thread. The token speaks for ``agent_id`` on ``provider`` (and, for
    the subscription, on that Codex ``account_id``)."""
    chosen = _checked_model(config, provider, model, account_id=account_id)
    from jarvis.agent_runtimes import gateway
    from jarvis.agent_runtimes.base import home_key
    from jarvis.agent_runtimes.model_limits import resolve_limits
    from jarvis.brain.model_catalog import ModelCatalog

    limits = resolve_limits(config, provider, chosen, ModelCatalog().cached_model(provider, chosen))
    if uses_native_claude(provider, account_id):
        status = claude_subscription_status(account_id)
        if status is None:
            raise RouteUnavailable(
                "The Claude Code subscription disconnected. Reconnect it in Settings."
            )
        return ModelRoute(
            provider=provider, model=chosen, base_url="", transport="claude_cli", api_key=None,
            context_window=limits.context_window, max_output_tokens=limits.max_output_tokens,
            claude_binary=status.binary_path,
            claude_config_dir=status.config_dir,
        )
    try:
        gateway.check_cooldown(provider, chosen, account_id)
    except gateway.GatewayError as exc:
        raise RouteUnavailable(str(exc)) from exc
    base_url = gateway.base_url()
    if not base_url:
        raise RouteUnavailable("Jarvis' model gateway is not up yet. Try again in a moment.")
    token = gateway.grant_token(
        agent_id, provider, account_id,
        scope=home_key(agent_id, session_id) if session_id else "",
    )
    gateway.register_model(token, chosen, limits)
    return ModelRoute(
        provider=provider,
        model=chosen,
        base_url=base_url,
        transport="responses" if _ENDPOINTS[provider].subscription else "chat_completions",
        api_key=token,
        context_window=limits.context_window,
        max_output_tokens=limits.max_output_tokens,
    )


async def prepare_route(
    config: Any, provider: str, model: str, *, agent_id: str = "", account_id: str = "",
    session_id: str = "",
) -> ModelRoute:
    """Refresh catalog metadata before writing either runtime's configuration."""
    from jarvis.agent_runtimes import gateway

    route = await asyncio.to_thread(
        route_for, config, provider, model, agent_id=agent_id, account_id=account_id,
        session_id=session_id,
    )
    if route.transport == "claude_cli":
        return route
    assert route.api_key is not None
    grant = gateway.verify(route.api_key)
    assert grant is not None
    limits = await gateway.refresh_model_limits(grant, route.model, config)
    gateway.register_model(route.api_key, route.model, limits)
    return replace(
        route, context_window=limits.context_window, max_output_tokens=limits.max_output_tokens
    )
