"""Client-safe error text helpers (jarvis/ui/web/error_text.py)."""

from __future__ import annotations

import logging

import pytest

from jarvis.ui.web.error_text import LOG_HINT, diagnostic_text, internal_error


def test_internal_error_hides_exception_and_logs_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = RuntimeError("C:/secret/internal/path")
    try:
        raise error
    except RuntimeError as exc:
        with caplog.at_level(logging.WARNING, logger="jarvis.ui.web.error_text"):
            message = internal_error("config write", exc)

    assert message == f"config write failed. {LOG_HINT}"
    assert "internal/path" not in message
    record = caplog.records[-1]
    assert record.exc_info is not None
    assert record.exc_info[1] is error


def test_diagnostic_text_is_one_line_redacted_and_capped() -> None:
    exc = FileNotFoundError(
        "npx not found api_key=sk-ant-abcdefghijklmnopqrstuvwxyz0123\n"
        "Traceback (most recent call last)"
    )
    text = diagnostic_text(exc)
    assert text.startswith("FileNotFoundError: npx not found")
    assert "Traceback" not in text
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz0123" not in text

    long_text = diagnostic_text(RuntimeError("no such file " * 100), max_chars=50)
    assert len(long_text) == 51
    assert long_text.endswith("…")


def test_diagnostic_text_without_message_is_the_type_name() -> None:
    assert diagnostic_text(TimeoutError()) == "TimeoutError"
