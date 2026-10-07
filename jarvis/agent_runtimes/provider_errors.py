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
* **Context too long** -> 400 ``context_length_exceeded`` with the words
  "context length exceeded", which both runtimes read as "compress the
  conversation and send it again".
* **Took too long** (a read timeout while the model was still working) -> 504
  ``timeout``, never "not reachable".
* **Model not offered**, **tools not supported**, **tool schema refused** ->
  404 ``model_not_found`` / 400 ``tools_unsupported`` / 400
  ``invalid_tool_schema``: deterministic, so nothing retries them.
* **Claude subscription**: Anthropic serves a subscription to third-party
  apps such as Hermes and OpenClaw only from the account's Extra Usage
  ("Third-party apps now draw from your extra usage, not your plan limits"),
  answering 400 or a bare 429 otherwise. The account's own usage report
  (``/api/oauth/usage``, the endpoint Claude Code reads; no inference) says
  whether Extra Usage is off or its monthly limit is spent, so a turn can
  fail at once (``login_blocked``) instead of after a runtime start.

Messages never copy the provider's response body; the body is only matched.
:func:`describe` gives a bounded, redacted line for the log, so a failed
turn can be diagnosed without reproducing it.
"""

from __future__ import annotations

import asyncio
import logging
import re
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
#: A connect failure means nothing answered; it is checked before the
#: timeouts below, because the SDKs wrap a connect timeout in their timeout
#: class.
_UNREACHABLE_ERRORS: Final[frozenset[str]] = frozenset(
    {"APIConnectionError", "ConnectError", "ConnectTimeout"}
)
_CONNECT_ERRORS: Final[frozenset[str]] = frozenset({"ConnectError", "ConnectTimeout"})

#: The model was reached and did not answer in time (a long thinking phase,
#: a slow local prefill): worth another try, never "start the server".
_TIMEOUT_ERRORS: Final[frozenset[str]] = frozenset(
    {
        "APITimeoutError",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TimeoutException",
        "TimeoutError",
    }
)

#: A request longer than the model's context window, in each provider's
#: words (OpenAI/OpenRouter/vLLM, Anthropic, Gemini, llama.cpp). Matched in
#: lower case, on a 4xx or a status-less stream error only.
_OVERFLOW_CODES: Final[frozenset[str]] = frozenset(
    {"context_length_exceeded", "string_above_max_length", "request_too_large"}
)
_OVERFLOW_PHRASES: Final[tuple[str, ...]] = (
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "exceeds the maximum number of tokens",
    "input token count",
    "max_model_len",
    "maximum allowed input length",
    "reduce the length of the messages",
)

_MODEL_MISSING_CODES: Final[frozenset[str]] = frozenset({"model_not_found"})
_TOOLS_UNSUPPORTED_PHRASES: Final[tuple[str, ...]] = (
    "does not support tools",
    "tools are not supported",
    "tool use is not supported",
    "does not support function calling",
    "function calling is not enabled",
    "tool calling is not supported",
)
_TOOL_SCHEMA_CODES: Final[frozenset[str]] = frozenset(
    {"invalid_function_parameters", "invalid_tool_schema"}
)
_TOOL_SCHEMA_PHRASES: Final[tuple[str, ...]] = (
    "invalid schema for function",
    "input_schema",
    "function_declarations",
    "json schema is invalid",
)

#: Codes after which the Jarvis turn ends at once: no retry, compression or
#: wait inside the runtime changes the answer (a rate limit carries the
#: cooldown the person is told about). Every other failure goes back to the
#: runtime, whose own retry and compression handle it.
TERMINAL_CODES: Final[frozenset[str]] = frozenset(
    {
        "billing",
        "extra_usage_off",
        "extra_usage_spent",
        "provider_auth",
        "claude_login_expired",
        "model_not_found",
        "invalid_tool_schema",
        "tools_unsupported",
        "rate_limited",
    }
)

#: Key- and token-shaped strings masked in :func:`describe`.
_SECRET_RE: Final = re.compile(
    r"(sk-[A-Za-z0-9_\-]{6,}|AIza[0-9A-Za-z_\-]{10,}|jrg_[A-Za-z0-9_\-]{6,}"
    r"|[Bb]earer\s+[A-Za-z0-9._\-]{10,}|eyJ[A-Za-z0-9._\-]{20,})"
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
    for value in (
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        # google-genai: ``code`` is the HTTP status, ``status`` its name.
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value < 600:
            return value
    return None


def _codes_and_text(exc: BaseException) -> tuple[set[str], str]:
    """The structured error codes and the lower-cased text, for matching only."""
    codes: set[str] = set()
    body = getattr(exc, "body", None)
    if body is None:
        body = getattr(exc, "details", None)  # google-genai
    error = body.get("error", body) if isinstance(body, dict) else None
    for source in (exc, error if isinstance(error, dict) else None):
        if source is None:
            continue
        for field in ("code", "type", "status"):
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


def _class_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for err in _chain(exc) for cls in type(err).__mro__}


def classify(provider: str, exc: BaseException, model: str = "") -> Refusal | None:
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
    if (status in (400, 413, 422) or status is None) and (
        codes & _OVERFLOW_CODES or any(phrase in text for phrase in _OVERFLOW_PHRASES)
    ):
        return Refusal(
            400,
            "context_length_exceeded",
            "Context length exceeded: the conversation is longer than "
            f"{model or 'the model'}'s context window on {_label(provider)}. Compress "
            "or shorten the conversation and send it again.",
        )
    if status in (400, 422) and any(phrase in text for phrase in _TOOLS_UNSUPPORTED_PHRASES):
        return Refusal(
            400,
            "tools_unsupported",
            f"{model or 'This model'} on {_label(provider)} cannot call tools, which the "
            "agent needs. Choose a model with tool support in the agent's settings.",
        )
    if status in (400, 422) and (
        codes & _TOOL_SCHEMA_CODES or any(phrase in text for phrase in _TOOL_SCHEMA_PHRASES)
    ):
        return Refusal(
            400,
            "invalid_tool_schema",
            f"{_label(provider)} refused one of the agent's tool definitions, so the "
            "request cannot succeed as it is. Turn off the newest tool or MCP server, or "
            "choose another model.",
        )
    if status == 404 or (status in (400, None) and codes & _MODEL_MISSING_CODES):
        return Refusal(
            404,
            "model_not_found",
            f"{_label(provider)} does not offer the model "
            f"{model or 'selected for this agent'} (or its server address is wrong). "
            "Choose another model in the agent's settings, or pull it first if it is a "
            "local model.",
        )
    if status is None:
        names = _class_names(exc)
        if names & _CONNECT_ERRORS:
            return _unreachable(provider)
        if names & _TIMEOUT_ERRORS:
            return Refusal(
                504,
                "timeout",
                f"{_label(provider)} took too long to answer {model or 'this request'}. "
                "Try again; a long request on a slow or busy model can take minutes.",
            )
        if names & _UNREACHABLE_ERRORS:
            return _unreachable(provider)
    return None


def _unreachable(provider: str) -> Refusal:
    return Refusal(
        503,
        "provider_unreachable",
        f"{_label(provider)} is not reachable. If it is a model server on this "
        "computer, start it; otherwise check the connection, or choose another "
        "model in the agent's settings.",
    )


def gateway_refusal(provider: str, exc: BaseException, model: str = "") -> Refusal:
    """Every provider failure as the status, code and message a runtime gets."""
    found = classify(provider, exc, model)
    if found is not None:
        return found
    status = _status(exc)
    if status == 429:
        return Refusal(429, "rate_limited", f"{provider} returned HTTP 429 (rate limit).")
    if status in (401, 403):
        return Refusal(
            401,
            "provider_auth",
            f"{_label(provider)} refused the saved key. Check it in Settings → API keys.",
        )
    if status in (503, 529):
        return Refusal(
            503,
            "provider_overloaded",
            f"{_label(provider)} is overloaded right now (HTTP {status}). Try again shortly.",
        )
    if status is not None and 400 <= status < 500:
        return Refusal(
            400,
            "invalid_request",
            f"{_label(provider)} rejected the request (HTTP {status}, {type(exc).__name__}).",
        )
    return Refusal(502, "provider_error", f"{provider} could not answer ({type(exc).__name__}).")


def is_terminal(code: str) -> bool:
    """Whether a failure with ``code`` ends the Jarvis turn at once."""
    return code in TERMINAL_CODES


def describe(exc: BaseException, limit: int = 400) -> str:
    """A one-line, bounded, redacted account of ``exc`` for the log.

    Provider error messages name the parameter, code and limit that failed;
    they do not echo the conversation. Anything shaped like a key or bearer
    token is masked, and the line is cut at ``limit`` characters.
    """
    status = _status(exc)
    codes, _text = _codes_and_text(exc)
    message = _SECRET_RE.sub("[redacted]", " ".join(str(exc).split()))
    if len(message) > limit:
        message = message[:limit] + "…"
    parts = [type(exc).__name__]
    if status is not None:
        parts.append(f"status={status}")
    if codes:
        parts.append("codes=" + ",".join(sorted(codes)[:4]))
    return f"{' '.join(parts)}: {message}"


# ------------------------------------------------------------ Claude login


def _login_expired() -> Refusal:
    return Refusal(
        401,
        "claude_login_expired",
        "Anthropic refused the Claude Code login this agent runs on: it expired or was "
        "signed out. Open Claude Code once to renew it, or connect an Anthropic API key "
        "in Settings → API keys.",
    )


def login_expired() -> ProviderRefusal:
    """The refusal for an agent routed to a Claude login that is not live."""
    return ProviderRefusal(_login_expired())


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
    if cached is not None and time.monotonic() - cached[0] < _REPORT_TTL_S:
        usage = cached[1]
    else:
        usage = _claude_usage(token)
        if usage is not None:
            _REPORTS[key] = (time.monotonic(), usage)
    return login_refusal(usage, model)


async def explain_login_refusal(exc: BaseException, token: str, model: str) -> BaseException:
    """``exc`` replaced by a :class:`ProviderRefusal` when the account explains it."""
    status = _status(exc)
    if status == 401:
        return login_expired()
    if status not in (400, 402, 403, 429):
        return exc
    _codes, text = _codes_and_text(exc)
    if status in (400, 402, 403) and "extra usage" not in text:
        # A 403 on a login is the login itself; a 400 is an ordinary bad request.
        return login_expired() if status == 403 else exc
    usage = await asyncio.to_thread(_claude_usage, token)
    refusal = login_refusal(usage, model)
    if refusal is None and "extra usage" in text:
        refusal = _extra_usage_off(model)
    if refusal is None:
        return exc
    log.info("runtime gateway: Claude login refused %s (%s)", model, refusal.code)
    return ProviderRefusal(refusal)
