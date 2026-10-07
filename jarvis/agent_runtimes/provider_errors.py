"""Why a provider refused a Hermes / OpenClaw model call, in words a person can act on.

A provider's HTTP status alone misleads: OpenAI answers an empty credit
balance with 429, OpenRouter with 402, and Anthropic refuses a Claude
subscription used outside Claude Code with a bare 429 ``rate_limit_error``
while the plan still has room. Each of those reads as "rate limited, try
again in 30 s" — and no retry will ever succeed. This module tells them apart:

* **No credits / no quota** on a key -> 402 ``billing`` (a runtime does not
  retry it; Hermes classifies 402 as billing and stops).
* **Provider not reachable** (a local model server that is not running, no
  network) -> 503 ``provider_unreachable``.
* **Claude subscription**: Anthropic serves a subscription outside Claude Code
  only through the account's Extra Usage for most models. A 429 on the login
  is explained from the account's own usage report (``/api/oauth/usage``, the
  endpoint Claude Code reads; it spends no inference): the plan window really
  is used up, or Extra Usage is turned off / its monthly limit is spent.

Messages never copy the provider's response body; the body is only matched.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
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

#: The plan windows in the usage report, named the way a person reads them.
_CLAUDE_WINDOWS: Final[tuple[tuple[str, str], ...]] = (
    ("five_hour", "5-hour"),
    ("seven_day", "weekly"),
    ("seven_day_opus", "weekly Opus"),
    ("seven_day_sonnet", "weekly Sonnet"),
)


@dataclass(frozen=True, slots=True)
class Refusal:
    """What the runtime is told: HTTP status, machine code, plain message."""

    status: int
    code: str
    message: str
    #: Seconds until the refusal lifts, when the provider says so.
    retry_after: float | None = None


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


def _reset(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def login_refusal(usage: Any, model: str, *, now: datetime | None = None) -> Refusal | None:
    """Why Anthropic refused a Claude-login call, from the account's usage report.

    ``None`` when the report explains nothing (then it was an ordinary rate
    limit). Pure, so it is tested without the network.
    """
    if not isinstance(usage, dict):
        return None
    now = now or datetime.now(UTC)
    for field, name in _CLAUDE_WINDOWS:
        window = usage.get(field)
        if not isinstance(window, dict):
            continue
        utilization = window.get("utilization")
        if not isinstance(utilization, int | float) or utilization < 100:
            continue
        reset = _reset(window.get("resets_at"))
        wait = max(0.0, (reset - now).total_seconds()) if reset else None
        when = ""
        if reset is not None:
            local = reset.astimezone()
            when = f" It resets at {local:%H:%M} on {local:%d.%m.}"
        return Refusal(
            429,
            "rate_limited",
            f"Your Claude plan's {name} limit is used up.{when}. Choose another "
            "connected model in the agent's settings to keep working now.",
            retry_after=wait,
        )
    extra = usage.get("extra_usage")
    if not isinstance(extra, dict):
        return None
    shown = model or "This Claude model"
    if extra.get("is_enabled") is False:
        return Refusal(
            402,
            "extra_usage_off",
            f"Anthropic runs {shown} for Hermes and OpenClaw on your Claude "
            "subscription only as Extra Usage (paid on top of the plan), and Extra "
            "Usage is turned off for your Claude account, so retrying will not help. "
            "Turn it on at claude.ai under Settings → Usage, run this agent on a "
            "Claude Haiku model (covered by your plan), connect an Anthropic API key, "
            "or choose another provider in the agent's settings.",
        )
    if extra.get("spend_limit_reached") is True:
        return Refusal(
            402,
            "extra_usage_spent",
            f"Your Claude Extra Usage limit for this month is spent, and Anthropic runs "
            f"{shown} for Hermes and OpenClaw only as Extra Usage. Raise the limit at "
            "claude.ai under Settings → Usage, or choose another provider in the "
            "agent's settings.",
        )
    return None


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


async def explain_login_refusal(exc: BaseException, token: str, model: str) -> BaseException:
    """``exc`` replaced by a :class:`ProviderRefusal` when the account explains it."""
    if _status(exc) != 429:
        return exc
    usage = await asyncio.to_thread(_claude_usage, token)
    refusal = login_refusal(usage, model)
    if refusal is None:
        return exc
    log.info("runtime gateway: Claude login refused %s (%s)", model, refusal.code)
    return ProviderRefusal(refusal)
