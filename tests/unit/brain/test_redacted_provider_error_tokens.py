"""Refusals that survived a redacting transport must still be actionable.

Some transports never hand us a provider's prose body. The Codex app-server is
the extreme case: it keeps a JSON-RPC method name and a numeric code and drops
the message entirely, so a ChatGPT plan that ran out of voice quota used to
reach the user as ``rejected thread/realtime/start (code -32603)`` and classify
as ``error`` — "probably an integration bug", which sends someone hunting a
defect instead of waiting out a throttle or checking their plan.

The one detail such a transport can safely forward is a bounded machine token
(``rate_limit_exceeded``, ``usage_limit_reached``) — no account, prompt, or
identity data. These tests pin that those tokens land on the right status, and
that adding them did not blur the line the whole dead-list path depends on:
a transient throttle must never be mistaken for an exhausted account.
"""

from __future__ import annotations

import pytest

from jarvis.brain.provider_test import (
    _RATE_LIMIT_TOKEN_MARKERS,
    BILLING_LIMIT_MARKERS,
    classify_provider_error,
)


@pytest.mark.parametrize(
    "message",
    [
        "Codex app-server rejected thread/realtime/start: rate_limit_exceeded",
        "{'error': {'type': 'rate_limit_exceeded'}}",
        "Too many requests",
    ],
)
def test_throttle_token_without_an_http_status_is_rate_limited(message: str) -> None:
    assert classify_provider_error(message) == "rate_limited"


@pytest.mark.parametrize(
    "message",
    [
        "Codex app-server rejected thread/realtime/start: usage_limit_reached",
        "{'error': {'type': 'quota_exceeded'}}",
    ],
)
def test_exhausted_account_token_is_no_credits(message: str) -> None:
    assert classify_provider_error(message) == "no_credits"


def test_underscore_token_is_needed_because_the_prose_form_misses_it() -> None:
    """The reason these entries exist at all, pinned so nobody 'tidies' them.

    The space-separated prose markers cannot match a token spelling, so without
    the twins below an exhausted plan reads as an integration bug.
    """
    assert "usage limit" in BILLING_LIMIT_MARKERS
    assert "usage limit" not in "usage_limit_reached"
    assert classify_provider_error("usage_limit_reached") == "no_credits"


def test_no_throttle_token_can_dead_list_a_funded_account() -> None:
    """The hard invariant the billing list documents, now across both lists.

    A rate limit clears by itself; if any billing marker were a substring of a
    throttle message, one burst would permanently retire a working provider.
    """
    for token in _RATE_LIMIT_TOKEN_MARKERS:
        assert not any(marker in token for marker in BILLING_LIMIT_MARKERS), token
        assert classify_provider_error(token) == "rate_limited"


def test_an_unrecognised_redacted_refusal_stays_an_honest_error() -> None:
    """No token, no guess: the classifier must not invent an account state."""
    assert (
        classify_provider_error(
            "Codex app-server rejected thread/realtime/start (code -32603)."
        )
        == "error"
    )
