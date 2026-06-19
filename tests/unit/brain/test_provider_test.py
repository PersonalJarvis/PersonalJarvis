"""Tests for the provider connectivity-test classifier + service.

The classifier turns a raw provider error message into an HONEST outcome status
that separates an *integration* problem (broken code / unreachable) from an
*account* state (invalid key / no credits / not configured). That distinction is
the whole point of the feature: a green "configured" badge says a key STRING is
stored, never that the provider answers — and a 401/403 proves the integration
reached the provider and only the credential/account was rejected.

Error strings below are VERBATIM from live probes against the real providers
(Anthropic 401, xAI 403 out-of-credits, OpenAI missing-key RuntimeError).
"""
from __future__ import annotations

import pytest

from jarvis.brain.provider_test import (
    PROVIDER_TEST_STATUSES,
    classify_provider_error,
)


def test_status_vocabulary_is_the_expected_closed_set() -> None:
    # Single source of truth — the Pydantic Literal + the TS union must mirror this.
    assert set(PROVIDER_TEST_STATUSES) == {
        "ok",
        "not_configured",
        "bad_key",
        "no_credits",
        "rate_limited",
        "model_unavailable",
        "unreachable",
        "error",
    }


def test_anthropic_401_invalid_key_is_bad_key() -> None:
    msg = (
        "AuthenticationError: Error code: 401 - {'type': 'error', 'error': "
        "{'type': 'authentication_error', 'message': 'invalid x-api-key'}}"
    )
    assert classify_provider_error(msg) == "bad_key"


def test_xai_403_out_of_credits_is_no_credits() -> None:
    msg = (
        "PermissionDeniedError: Error code: 403 - {'code': 'permission-denied', "
        "'error': 'Your team e6d8 has either used all available credits or reached "
        "its monthly spending limit. To continue making API requests, please "
        "purchase more credits or raise your spending limit.'}"
    )
    assert classify_provider_error(msg) == "no_credits"


def test_missing_key_runtimeerror_is_not_configured() -> None:
    msg = "RuntimeError: Kein OpenAI-API-Key gefunden (openai_api_key / OPENAI_API_KEY)."
    assert classify_provider_error(msg) == "not_configured"


def test_english_missing_key_is_not_configured() -> None:
    assert classify_provider_error("No API key found for provider") == "not_configured"


def test_openai_insufficient_quota_429_is_no_credits() -> None:
    msg = (
        "RateLimitError: Error code: 429 - {'error': {'message': 'You exceeded your "
        "current quota, please check your plan and billing details.', "
        "'code': 'insufficient_quota'}}"
    )
    assert classify_provider_error(msg) == "no_credits"


def test_plain_429_rate_limit_is_rate_limited() -> None:
    msg = "RateLimitError: Error code: 429 - rate limit exceeded, please slow down"
    assert classify_provider_error(msg) == "rate_limited"


def test_404_model_not_found_is_model_unavailable() -> None:
    msg = (
        "NotFoundError: Error code: 404 - {'error': {'message': "
        "'The model `gpt-9` does not exist or you do not have access to it.'}}"
    )
    assert classify_provider_error(msg) == "model_unavailable"


def test_connection_error_is_unreachable() -> None:
    assert classify_provider_error("APIConnectionError: Connection error.") == "unreachable"


def test_timeout_is_unreachable() -> None:
    assert classify_provider_error("timeout after 25.0s") == "unreachable"


def test_unknown_error_is_error() -> None:
    assert classify_provider_error("ValueError: something structurally broke") == "error"


def test_none_or_empty_is_error() -> None:
    assert classify_provider_error(None) == "error"
    assert classify_provider_error("") == "error"
