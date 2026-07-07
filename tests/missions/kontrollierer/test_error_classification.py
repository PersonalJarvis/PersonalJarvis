"""Unit matrix for the worker-error -> error_class mapping (pure function).

Real-world inputs from live incidents: the 2026-07-06 expired Claude OAuth
401, the 2026-06-08 codex refresh-token death, the 2026-06-10 Claude Max
session-limit, the 2026-05-28 zero-output startup timeout.
"""
from __future__ import annotations

import pytest

from jarvis.missions.events import MISSION_ERROR_CLASSES
from jarvis.missions.kontrollierer.orchestrator import _classify_worker_error


@pytest.mark.parametrize(
    "err,expected",
    [
        # 2026-07-06 live text (expired Claude Max OAuth token)
        (
            "Failed to authenticate. API Error: 401 Invalid authentication credentials",
            "provider_auth",
        ),
        ("Failed to refresh token. Please log in again.", "provider_auth"),
        ("Not logged in · Please run /login", "provider_auth"),
        ("You've hit your usage limit. Try again at 7:40 PM.", "provider_quota"),
        ("You've hit your session limit · resets 11:10pm", "provider_quota"),
        ("Credit balance is too low", "provider_quota"),
        ("429 Too Many Requests", "provider_quota"),
        ("503 Service Unavailable", "provider_unreachable"),
        ("upstream is overloaded, please try again", "provider_unreachable"),
        (
            "subprocess produced no output within 120s startup timeout",
            "worker_timeout",
        ),
        ("Compilation failed: missing semicolon", None),
        ("", None),
    ],
)
def test_classification_matrix(err: str, expected: str | None) -> None:
    assert _classify_worker_error(err) == expected


def test_structured_timeout_flag_wins() -> None:
    assert _classify_worker_error("", timed_out=True) == "worker_timeout"
    # Even a classifiable text defers to the structured flag.
    assert _classify_worker_error("401", timed_out=True) == "worker_timeout"


def test_all_returned_tokens_are_in_the_closed_set() -> None:
    samples = [
        "401 Unauthorized", "usage limit", "503", "overloaded", "timeout",
    ]
    for s in samples:
        token = _classify_worker_error(s)
        assert token is None or token in MISSION_ERROR_CLASSES
