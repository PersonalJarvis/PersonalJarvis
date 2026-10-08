"""Why a provider refused a Hermes / OpenClaw model call, in words a person can act on.

A provider's HTTP status alone misleads: OpenAI answers an empty credit
balance with 429, OpenRouter with 402, and Anthropic refuses a Claude
subscription used by a third-party app with a 400 or a bare 429
``rate_limit_error`` while the plan still has room. Each of those reads as "rate limited, try
again in 30 s" — and no retry will ever succeed. This module tells them apart:

* **No credits / no quota** on a key -> 402 ``billing`` (a runtime does not
  retry it; Hermes classifies 402 as billing and stops).
* **Provider not reachable** (a local model server that is not running, no
  network) -> 503 ``provider_unreachable``.
* **Claude subscription**: Anthropic serves a subscription to third-party
  apps such as Hermes and OpenClaw only from the account's Extra Usage
  ("Third-party apps now draw from your extra usage, not your plan limits"),
  answering 400 or a bare 429 otherwise. The account's own usage report
  (``/api/oauth/usage``, the endpoint Claude Code reads; no inference) says
  whether Extra Usage is off or its monthly limit is spent, so a turn can
  fail at once (``login_blocked``) instead of after a runtime start.

Messages never copy the provider's response body; the body is only matched.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final

from jarvis.core.http_pool import SyncHttpClientPool

log = logging.getLogger(__name__)

#: Error codes providers use for an empty balance or a spent quota.
_BILLING_CODES: Final[frozenset[str]] = frozenset(
    {
        "insufficient_quota",
        "insufficient_credits",
        "billing_not_active",
        "billing_hard_limit_reached",
        "credit_balance_exhausted",
        "payment_required",
    }
)

#: Wording of the same, for providers that send no structured code. Matched in
#: lower case. Gemini's "limit: 0" means the key has no quota at all for the
#: model (free tier without access), which no wait fixes.
_BILLING_PHRASES: Final[tuple[str, ...]] = (
    "no credits",
    "insufficient credits",
    "insufficient balance",
    "credit balance is too low",
    "out of credits",
    "add more credits",
    "limit: 0,",
)

#: Transport failures, by class name across the SDKs (openai, anthropic,
#: httpx): the name is the stable capability signal, a class import is not.
_UNREACHABLE_ERRORS: Final[frozenset[str]] = frozenset(
    {"APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout"}
)

_HTTP_POOL: Final = SyncHttpClientPool(timeout_s=8.0)

_CLAUDE_USAGE_URL: Final[str] = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_OAUTH_BETA: Final[str] = "oauth-2025-04-20"

@dataclass(frozen=True, slots=True)
class Refusal:
    """What the runtime is told: HTTP status, machine code, plain message."""

    status: int
    code: str
    message: str


class ProviderRefusal(Exception):
    """A refusal already explained where its context was known (the login)."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _codes_and_text(exc: BaseException) -> tuple[set[str], str]:
    """The structured error codes and the lower-cased text, for matching only."""
    codes: set[str] = set()
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else None
    for source in (exc, error if isinstance(error, dict) else None):
        if source is None:
            continue
        for field in ("code", "type"):
            value = source.get(field) if isinstance(source, dict) else getattr(source, field, None)
            if isinstance(value, str) and value:
                codes.add(value.lower())
    return codes, f"{exc} {body if body is not None else ''}".lower()


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen and len(seen) < 5:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


#: How the gateway's providers are named in a message (the id otherwise).
_LABELS: Final[dict[str, str]] = {
    "claude-api": "Anthropic",
    "openai": "OpenAI",
    "openai-codex": "The ChatGPT subscription",
    "grok": "xAI (Grok)",
    "openrouter": "OpenRouter",
    "nvidia": "NVIDIA",
    "gemini": "Google Gemini",
    "ollama": "Ollama",
    "local-openai": "The local model server",
}


def _label(provider: str) -> str:
    return _LABELS.get(provider, provider)


def classify(provider: str, exc: BaseException) -> Refusal | None:
    """The actionable refusal behind ``exc``, or ``None`` for an ordinary error."""
    if isinstance(exc, ProviderRefusal):
        return exc.refusal
    status = _status(exc)
    codes, text = _codes_and_text(exc)
    if status in (400, 402, 403, 429) and "extra usage" in text:
        return _extra_usage_off("")
    if status == 402 or (
        status in (400, 403, 429)
        and (codes & _BILLING_CODES or any(phrase in text for phrase in _BILLING_PHRASES))
    ):
        return Refusal(
            402,
            "billing",
            f"{_label(provider)} has no credits or quota left on this key, so retrying "
            "will not help. Top it up with the provider, or choose another connected "
            "model in the agent's settings.",
        )
    if status is None and any(
        cls.__name__ in _UNREACHABLE_ERRORS for err in _chain(exc) for cls in type(err).__mro__
    ):
        return Refusal(
            503,
            "provider_unreachable",
            f"{_label(provider)} is not reachable. If it is a model server on this "
            "computer, start it; otherwise check the connection, or choose another "
            "model in the agent's settings.",
        )
    return None


# ------------------------------------------------------------ Claude login


def _extra_usage_off(model: str) -> Refusal:
    return Refusal(
        402,
        "extra_usage_off",
        f"Anthropic bills {model or 'Claude'} used by Hermes or OpenClaw on a Claude "
        "subscription as Extra Usage, never from the plan's limits, and Extra Usage "
        "is turned off for this Claude account, so retrying will not help. Turn it "
        "on at claude.ai under Settings → Usage, connect an Anthropic API key, or "
        "choose another provider in the agent's settings.",
    )


def _extra_usage_spent(model: str) -> Refusal:
    return Refusal(
        402,
        "extra_usage_spent",
        "This Claude account's Extra Usage limit for the month is spent, and "
        f"Anthropic bills {model or 'Claude'} used by Hermes or OpenClaw only as "
        "Extra Usage. Raise the limit at claude.ai under Settings → Usage, connect "
        "an Anthropic API key, or choose another provider in the agent's settings.",
    )


def login_refusal(usage: Any, model: str) -> Refusal | None:
    """Why Anthropic refuses a Claude-login call from Hermes or OpenClaw.

    Anthropic serves a subscription to third-party apps only from the
    account's Extra Usage ("Third-party apps now draw from your extra usage,
    not your plan limits", live 2026-10-07), so the plan's own windows say
    nothing here. ``None`` when the report explains nothing. Pure.
    """
    extra = usage.get("extra_usage") if isinstance(usage, dict) else None
    if not isinstance(extra, dict):
        return None
    if extra.get("is_enabled") is False:
        return _extra_usage_off(model)
    if extra.get("spend_limit_reached") is True:
        return _extra_usage_spent(model)
    return None


#: The last usage report per login, so routing does not ask on every turn.
_REPORTS: dict[str, tuple[float, Any]] = {}
_REPORT_TTL_S: Final = 120.0
_REPORT_FAILURE_TTL_S: Final = 10.0


def _claude_usage(token: str) -> Any:
    """The login's usage report, or ``None``. Blocking; never logs the token."""
    try:
        response = _HTTP_POOL.client().get(
            _CLAUDE_USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": _CLAUDE_OAUTH_BETA,
                "Accept": "application/json",
            },
        )
    except Exception as exc:  # noqa: BLE001 — no report means "no explanation", logged
        log.info("runtime gateway: Claude usage report unavailable (%s)", type(exc).__name__)
        return None
    if response.status_code != 200:
        log.info("runtime gateway: Claude usage report answered %s", response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        log.info("runtime gateway: Claude usage report was not JSON")
        return None


def login_blocked(token: str, model: str) -> Refusal | None:
    """A refusal known before any model call: Extra Usage off or spent.

    Blocking (one cached GET, no inference). Lets a turn fail at once with the
    reason instead of starting a runtime that can only be refused.
    """
    import hashlib
    import time

    key = hashlib.sha256(token.encode()).hexdigest()
    cached = _REPORTS.get(key)
    ttl = _REPORT_FAILURE_TTL_S if cached is not None and cached[1] is None else _REPORT_TTL_S
    if cached is not None and time.monotonic() - cached[0] < ttl:
        usage = cached[1]
    else:
        usage = _claude_usage(token)
        # Availability is read by several picker lists. A report outage is
        # unknown, not blocked; briefly cache it to avoid repeated timeouts.
        _REPORTS[key] = (time.monotonic(), usage)
    return login_refusal(usage, model)


async def explain_login_refusal(exc: BaseException, token: str, model: str) -> BaseException:
    """``exc`` replaced by a :class:`ProviderRefusal` when the account explains it."""
    status = _status(exc)
    if status not in (400, 402, 403, 429):
        return exc
    _codes, text = _codes_and_text(exc)
    if status in (400, 402, 403) and "extra usage" not in text:
        return exc  # an ordinary bad request, not a billing refusal
    usage = await asyncio.to_thread(_claude_usage, token)
    refusal = login_refusal(usage, model)
    if refusal is None and "extra usage" in text:
        refusal = _extra_usage_off(model)
    if refusal is None:
        return exc
    log.info("runtime gateway: Claude login refused %s (%s)", model, refusal.code)
    return ProviderRefusal(refusal)
