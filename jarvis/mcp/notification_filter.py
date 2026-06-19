"""Tolerant handling of non-standard MCP server notifications.

Background
---------
The MCP SDK's ``BaseSession._receive_loop`` validates every incoming
JSON-RPC notification against the ``ServerNotification`` union
(cancelled / progress / resources / tools / prompts / logging / ...).
When a server emits a method that is not in that union — observed live on
2026-06-01 as ``method='log'`` (likely meant to be ``notifications/message``
or a custom server log frame) — the strict union match fails with one
``literal_error`` per known variant (19 in total). The SDK already catches
that ``ValidationError`` and continues (a malformed notification never
crashes the loop), but it logs the full 19-error Pydantic dump at WARNING
via ``logging.warning(...)`` on the *root* logger, once per occurrence.
A chatty server then spams the log indefinitely.

We do not own ``BaseSession`` and must not monkeypatch the SDK. Instead we
attach a logging filter to the root logger that recognises exactly this
record, drops the loud dump, and re-emits a single concise DEBUG line
(server name when recoverable + the offending method). All other records —
valid notifications, circuit-breaker warnings, unrelated logging — pass
through untouched.

Public API
----------
- :class:`NotificationValidationFilter` — the ``logging.Filter`` itself
  (testable in isolation).
- :func:`install_notification_log_filter` — idempotently attaches one
  instance to the root logger. Call this once during MCP bootstrap.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# The SDK message is built as:
#   f"Failed to validate notification: {e}. Message was: {message.message.root}"
# where ``{e}`` is the ValidationError str (the multi-line N-error dump) and
# the trailing repr is the ``JSONRPCNotification`` root. We key off the stable
# prefix and pull the method out of the trailing repr when present.
_SDK_PREFIX = "Failed to validate notification:"

# ``... Message was: root=JSONRPCNotification(method='log', params=...)`` —
# tolerate single or double quotes and an optional ``root=`` wrapper.
_METHOD_RE = re.compile(r"method=['\"]([^'\"]*)['\"]")


class NotificationValidationFilter(logging.Filter):
    """Suppress the MCP SDK's loud notification-validation WARNING.

    Returns ``False`` for the offending record (so the logging stack drops
    it) after emitting one concise DEBUG line. Every other record returns
    ``True`` and is logged normally.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — never let formatting break logging
            return True

        if _SDK_PREFIX not in message:
            return True

        method = _extract_method(message)
        # Quiet, one-line, DEBUG-level breadcrumb instead of the 19-error dump.
        # We cannot recover the server name from the SDK's root-logger record,
        # so the method (and a generic hint) is the most we can offer here.
        log.debug(
            "MCP server sent an unrecognised notification (method=%r); dropped.",
            method if method is not None else "<unknown>",
        )
        return False


def _extract_method(message: str) -> str | None:
    """Pull the ``method='...'`` value out of the SDK's trailing repr."""
    match = _METHOD_RE.search(message)
    return match.group(1) if match else None


def install_notification_log_filter() -> NotificationValidationFilter:
    """Idempotently attach the filter to the root logger.

    The SDK logs via ``logging.warning(...)``, i.e. on the root logger, so
    the filter must live there. Safe to call multiple times: a second call
    reuses the already-attached instance instead of stacking duplicates.
    """
    root = logging.getLogger()
    for existing in root.filters:
        if isinstance(existing, NotificationValidationFilter):
            return existing
    filt = NotificationValidationFilter()
    root.addFilter(filt)
    return filt
