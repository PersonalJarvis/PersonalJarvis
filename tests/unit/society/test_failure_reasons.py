"""The typed failure vocabulary: every code has a retry hint, auth beats quota."""

from __future__ import annotations

import pytest

from jarvis.society.failure_reasons import (
    FailureReason,
    RetryAction,
    classify_error,
    retry_action,
)


def test_every_reason_has_a_retry_action():
    for reason in FailureReason:
        assert isinstance(retry_action(reason), RetryAction)


def test_unknown_string_reads_as_internal_error():
    assert retry_action("no-such-code") == retry_action(FailureReason.INTERNAL_ERROR)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP 401 Unauthorized", FailureReason.AUTH_FAILED),
        ("invalid_api_key: check your billing settings", FailureReason.AUTH_FAILED),
        ("insufficient_quota: exceeded your current quota", FailureReason.PROVIDER_QUOTA_LIMIT),
        ("429 Too Many Requests", FailureReason.RATE_LIMITED),
        ("prompt is too long: 210000 > maximum context", FailureReason.CONTEXT_OVERFLOW),
        ("ECONNREFUSED 127.0.0.1:11434", FailureReason.TARGET_OFFLINE),
        ("502 Bad Gateway", FailureReason.PROVIDER_ERROR),
        ("something odd", FailureReason.INTERNAL_ERROR),
        ("", FailureReason.INTERNAL_ERROR),
    ],
)
def test_classify_error_by_message(message: str, expected: FailureReason):
    assert classify_error(message) == expected


def test_classify_error_reads_the_exception_class_name():
    class AuthenticationError(Exception):
        pass

    assert classify_error(AuthenticationError("")) == FailureReason.AUTH_FAILED


def test_classify_error_walks_the_cause_chain():
    try:
        try:
            raise RuntimeError("rate limit exceeded")
        except RuntimeError as inner:
            raise ValueError("wrapped") from inner
    except ValueError as outer:
        assert classify_error(outer) == FailureReason.RATE_LIMITED


def test_none_is_internal_error():
    assert classify_error(None) == FailureReason.INTERNAL_ERROR
