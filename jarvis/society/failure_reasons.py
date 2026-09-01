"""The typed vocabulary every society refusal, error and retry hint speaks.

A caller — the scheduler, a chat binding, the REST layer, the voice ACK path —
branches on the *code*, never on an error string. Each code carries one retry
action so a UI can say "try again later" versus "fix the key" versus "pick a
different teammate" without parsing anything.

``classify_error`` folds a provider exception (or its message) onto a code.
Precedence is deliberate: an authentication failure wins over a quota message
that mentions billing, because the fix is different ("add a key" versus "wait
or top up") and a wrong hint sends the person to the wrong screen.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

__all__ = [
    "FailureReason",
    "RetryAction",
    "classify_error",
    "retry_action",
]


class FailureReason(StrEnum):
    """Why an action, message or dispatch did not happen."""

    # Provider / model side
    AUTH_FAILED = "auth_failed"
    PROVIDER_QUOTA_LIMIT = "provider_quota_limit"
    RATE_LIMITED = "rate_limited"
    CONTEXT_OVERFLOW = "context_overflow"
    PROVIDER_ERROR = "provider_error"
    # Target resolution (relay)
    TARGET_UNKNOWN = "target_unknown"
    TARGET_AMBIGUOUS = "target_ambiguous"
    TARGET_BUSY = "target_busy"
    TARGET_OFFLINE = "target_offline"
    TARGET_PAUSED = "target_paused"
    # Society rules (scheduler)
    TIER_NOT_ALLOWED = "tier_not_allowed"
    DEPTH_EXCEEDED = "depth_exceeded"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONCURRENCY_CAP = "concurrency_cap"
    MESSAGE_CAP = "message_cap"
    KILL_SWITCH = "kill_switch"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    INVALID_RESULT = "invalid_result"
    # Everything else
    INTERNAL_ERROR = "internal_error"


class RetryAction(StrEnum):
    """What a caller should do about a :class:`FailureReason`."""

    RETRY_LATER = "retry_later"
    FIX_CONFIG = "fix_config"
    CHOOSE_TARGET = "choose_target"
    COMPACT = "compact"
    NEVER = "never"


_RETRY: Final[dict[FailureReason, RetryAction]] = {
    FailureReason.AUTH_FAILED: RetryAction.FIX_CONFIG,
    FailureReason.PROVIDER_QUOTA_LIMIT: RetryAction.RETRY_LATER,
    FailureReason.RATE_LIMITED: RetryAction.RETRY_LATER,
    FailureReason.CONTEXT_OVERFLOW: RetryAction.COMPACT,
    FailureReason.PROVIDER_ERROR: RetryAction.RETRY_LATER,
    FailureReason.TARGET_UNKNOWN: RetryAction.CHOOSE_TARGET,
    FailureReason.TARGET_AMBIGUOUS: RetryAction.CHOOSE_TARGET,
    FailureReason.TARGET_BUSY: RetryAction.RETRY_LATER,
    FailureReason.TARGET_OFFLINE: RetryAction.RETRY_LATER,
    FailureReason.TARGET_PAUSED: RetryAction.CHOOSE_TARGET,
    FailureReason.TIER_NOT_ALLOWED: RetryAction.NEVER,
    FailureReason.DEPTH_EXCEEDED: RetryAction.NEVER,
    FailureReason.BUDGET_EXHAUSTED: RetryAction.FIX_CONFIG,
    FailureReason.CONCURRENCY_CAP: RetryAction.RETRY_LATER,
    FailureReason.MESSAGE_CAP: RetryAction.NEVER,
    FailureReason.KILL_SWITCH: RetryAction.NEVER,
    FailureReason.BLOCKED_BY_POLICY: RetryAction.NEVER,
    FailureReason.INVALID_RESULT: RetryAction.NEVER,
    FailureReason.INTERNAL_ERROR: RetryAction.RETRY_LATER,
}


def retry_action(reason: FailureReason | str) -> RetryAction:
    """The retry hint for ``reason``; unknown strings read as internal errors."""
    try:
        code = FailureReason(reason)
    except ValueError:
        code = FailureReason.INTERNAL_ERROR
    return _RETRY[code]


# Ordered: the first matching group wins. Auth sits above quota on purpose —
# "invalid api key ... check your billing" is an auth problem.
_PATTERNS: Final[tuple[tuple[FailureReason, re.Pattern[str]], ...]] = (
    (
        FailureReason.AUTH_FAILED,
        re.compile(
            r"(unauthori[sz]ed|invalid[ _-]?api[ _-]?key|authentication|"
            r"forbidden|\b401\b|\b403\b|no api key|api key (is )?missing)",
            re.I,
        ),
    ),
    (
        FailureReason.PROVIDER_QUOTA_LIMIT,
        re.compile(
            r"(quota|insufficient[_ ]quota|billing|credit balance|exceeded your current|"
            r"usage limit|out of credits|payment required|\b402\b)",
            re.I,
        ),
    ),
    (
        FailureReason.RATE_LIMITED,
        re.compile(r"(rate[ _-]?limit|too many requests|\b429\b|overloaded|\b529\b)", re.I),
    ),
    (
        FailureReason.CONTEXT_OVERFLOW,
        re.compile(
            r"(context[ _-]?(window|length)|maximum context|too many tokens|"
            r"prompt is too long|input is too long|max_tokens|token limit)",
            re.I,
        ),
    ),
    (
        FailureReason.TARGET_OFFLINE,
        re.compile(r"(connection refused|not reachable|unreachable|offline|econnrefused)", re.I),
    ),
    (
        FailureReason.PROVIDER_ERROR,
        re.compile(
            r"(\b5\d\d\b|internal server error|bad gateway|service unavailable|timeout)", re.I
        ),
    ),
)


def classify_error(error: BaseException | str | None) -> FailureReason:
    """Fold an exception or message onto a :class:`FailureReason`.

    Looks at the exception's class name and message (and its cause chain), so
    a provider SDK's ``AuthenticationError`` classifies even with an empty
    message. Anything unrecognized is :attr:`FailureReason.INTERNAL_ERROR`.
    """
    if error is None:
        return FailureReason.INTERNAL_ERROR
    parts: list[str] = []
    if isinstance(error, BaseException):
        seen: set[int] = set()
        cur: BaseException | None = error
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            parts.append(type(cur).__name__)
            parts.append(str(cur))
            cur = cur.__cause__ or cur.__context__
    else:
        parts.append(str(error))
    text = " ".join(parts)
    for reason, pattern in _PATTERNS:
        if pattern.search(text):
            return reason
    return FailureReason.INTERNAL_ERROR
