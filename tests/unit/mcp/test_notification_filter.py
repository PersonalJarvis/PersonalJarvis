"""Unit tests for the MCP notification log filter.

The MCP SDK's ``BaseSession._receive_loop`` validates every incoming
JSON-RPC notification against the ``ServerNotification`` union. When a
server sends a non-standard / out-of-spec method (observed live:
``method='log'``), the strict union match fails with one ``literal_error``
per known variant (19 in total) and the SDK logs the whole dump at
WARNING via ``logging.warning(...)`` on the root logger.

The SDK already swallows the exception and continues — the only damage is
the loud, repeating 19-line WARNING. These tests pin the behaviour of
``jarvis.mcp.notification_filter``: the loud record is dropped and a single
concise DEBUG line (server name when known + method) is emitted instead.
Valid notifications and unrelated log records pass through untouched.
"""
from __future__ import annotations

import logging

import pytest

from jarvis.mcp.notification_filter import (
    NotificationValidationFilter,
    install_notification_log_filter,
)

# A realistic sample of the SDK's loud record: the message starts with
# "Failed to validate notification: <N> validation errors ..." and ends
# with "Message was: <repr of the JSONRPCNotification root>".
_LOUD_MESSAGE = (
    "Failed to validate notification: 19 validation errors for ServerNotification\n"
    "CancelledNotification.method\n"
    "  Input should be 'notifications/cancelled' [type=literal_error, "
    "input_value='log', input_type=str]\n"
    "ProgressNotification.method\n"
    "  Input should be 'notifications/progress' [type=literal_error, "
    "input_value='log', input_type=str]. "
    "Message was: root=JSONRPCNotification(method='log', "
    "params={'data': 'hello'}, jsonrpc='2.0')"
)


def _make_record(msg: str, level: int = logging.WARNING) -> logging.LogRecord:
    return logging.LogRecord(
        name="root",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


# ----------------------------------------------------------------------
# Filter behaviour
# ----------------------------------------------------------------------


def test_loud_notification_warning_is_dropped() -> None:
    """The 19-error WARNING from the SDK must NOT be propagated."""
    filt = NotificationValidationFilter()
    record = _make_record(_LOUD_MESSAGE)
    # filter() returning False => the record is suppressed by the logging stack.
    assert filt.filter(record) is False


def test_unknown_method_is_logged_once_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """A dropped notification re-surfaces as a single concise DEBUG line."""
    filt = NotificationValidationFilter()
    record = _make_record(_LOUD_MESSAGE)
    with caplog.at_level(logging.DEBUG, logger="jarvis.mcp.notification_filter"):
        result = filt.filter(record)
    assert result is False
    # Exactly one DEBUG record, and it must name the offending method.
    debug_records = [
        r for r in caplog.records if r.name == "jarvis.mcp.notification_filter"
    ]
    assert len(debug_records) == 1
    assert debug_records[0].levelno == logging.DEBUG
    msg = debug_records[0].getMessage()
    assert "log" in msg
    # The concise line must NOT carry the 19-error dump.
    assert "19 validation errors" not in msg
    assert "literal_error" not in msg
    # One line only — no multi-line Pydantic dump.
    assert "\n" not in msg.strip()


def test_valid_notification_records_pass_through() -> None:
    """Unrelated / valid log records are never suppressed."""
    filt = NotificationValidationFilter()
    record = _make_record("MCPClient[gmail] ready - 12 tools", level=logging.INFO)
    assert filt.filter(record) is True


def test_other_warning_records_pass_through() -> None:
    """A WARNING that is not the notification-validation dump is untouched."""
    filt = NotificationValidationFilter()
    record = _make_record("circuit-breaker OPEN (60s) after 3 errors")
    assert filt.filter(record) is True


# ----------------------------------------------------------------------
# Installation idempotency
# ----------------------------------------------------------------------


def test_install_is_idempotent() -> None:
    """Installing twice attaches exactly one filter to the root logger."""
    root = logging.getLogger()
    before = list(root.filters)
    try:
        install_notification_log_filter()
        install_notification_log_filter()
        installed = [
            f for f in root.filters if isinstance(f, NotificationValidationFilter)
        ]
        assert len(installed) == 1
    finally:
        root.filters = before


def test_installed_filter_suppresses_root_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: a root logging.warning with the loud dump is suppressed
    and replaced by a single DEBUG line on the jarvis logger.
    """
    root = logging.getLogger()
    before = list(root.filters)
    try:
        install_notification_log_filter()
        with caplog.at_level(logging.DEBUG):
            # This mirrors exactly what the SDK does: logging.warning(...).
            logging.warning(_LOUD_MESSAGE)
        # The loud WARNING must be gone from the captured records.
        loud = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "19 validation errors" in r.getMessage()
        ]
        assert loud == []
        # And a concise DEBUG line must have been emitted instead.
        concise = [
            r
            for r in caplog.records
            if r.name == "jarvis.mcp.notification_filter"
            and r.levelno == logging.DEBUG
        ]
        assert len(concise) == 1
    finally:
        root.filters = before
