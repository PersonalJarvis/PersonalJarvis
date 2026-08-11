"""A spent free-tier DAY must be explained in words, not dumped as raw JSON.

Live forensic (2026-08-11): the Tool Model tab went red with "Not working —
Google Gemini: ClientError: 429 Too Many Requests. {…500 characters of nested
JSON…}". The one fact that mattered — Google allows 20 generate_content
requests per DAY for gemini-3.6-flash on a free key, and they were spent — sat
in the middle of that body. The same key was at that moment serving a live
realtime session perfectly well, because the Live API draws on a different
quota, so the card read as "your key is broken" while the key was fine. Every
retry reproduced the identical wall of JSON, which made the wrong conclusion
look confirmed.

The status classification is deliberately unchanged: a daily cap does not come
back before midnight Pacific, so the runtime must keep treating it as terminal
for the session and cross to another provider family (AP-22). Only the wording
the user reads is at stake here.
"""
from __future__ import annotations

from jarvis.brain.provider_test import (
    NO_CREDITS,
    classify_provider_error,
    explain_provider_error,
)

# The live body, verbatim in shape: prose summary line + the QuotaFailure twin.
GEMINI_FREE_TIER_DAY = (
    "ClientError: 429 Too Many Requests. {'message': '{\\n \"error\": {\\n "
    '"code": 429,\\n "message": "You exceeded your current quota, please check '
    "your plan and billing details. For more information on this error, head "
    "to: https://ai.google.dev/gemini-api/docs/rate-limits. \\\\n* Quota "
    "exceeded for metric: generativelanguage.googleapis.com/"
    "generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash"
    '\\nPlease retry in 12.029047747s.",\\n "status": "RESOURCE_EXHAUSTED",\\n '
    '"details": [\\n {\\n "@type": "type.googleapis.com/google.rpc.QuotaFailure"'
    ',\\n "violations": [\\n {\\n "quotaMetric": "generativelanguage.googleapis'
    '.com/generate_content_free_tier_requests",\\n "quotaId": "GenerateRequests'
    'PerDayPerProjectPerModel-FreeTier",\\n "quotaDimensions": {\\n "location": '
    '"global",\\n "model": "gemini-3.6-flash"\\n },\\n "quotaValue": "20"\\n }\\n '
    "]\\n }\\n ]\\n }\\n}', 'status': 'Too Many Requests'}"
)


def test_free_tier_day_limit_names_the_number_and_the_model():
    said = explain_provider_error(GEMINI_FREE_TIER_DAY)
    assert "20 requests per day" in said
    assert "gemini-3.6-flash" in said


def test_free_tier_day_limit_clears_the_key_and_names_the_reset():
    said = explain_provider_error(GEMINI_FREE_TIER_DAY)
    # The single misreading this exists to prevent.
    assert "key itself is valid" in said
    assert "midnight Pacific" in said
    # …and the raw JSON does not survive into the card text.
    assert "quotaId" not in said
    assert "RESOURCE_EXHAUSTED" not in said


def test_free_tier_day_limit_survives_a_truncated_prose_line():
    """A transport that drops the prose summary still leaves the machine twin."""
    machine_only = (
        '{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", '
        '"quotaDimensions": {"model": "gemini-3.6-flash"}, "quotaValue": "20"}'
    )
    said = explain_provider_error(machine_only)
    assert "20 requests per day" in said
    assert "gemini-3.6-flash" in said


def test_unrecognised_errors_keep_their_raw_text():
    """An unknown shape must not be flattened into a confident wrong sentence."""
    assert explain_provider_error("Error code: 401 - invalid x-api-key") == ""
    assert explain_provider_error("Connection error.") == ""
    assert explain_provider_error("") == ""
    assert explain_provider_error(None) == ""


def test_a_rate_limit_that_is_not_the_free_tier_day_cap_is_left_alone():
    """Per-MINUTE throttling clears by itself — different advice, so no rewrite."""
    per_minute = (
        "429 Too Many Requests. Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_requests_per_model_per_minute, "
        "limit: 10, model: gemini-3.6-flash"
    )
    assert explain_provider_error(per_minute) == ""


def test_classification_is_unchanged_by_the_wording():
    """The runtime must still treat a spent day as terminal, not transient."""
    assert classify_provider_error(GEMINI_FREE_TIER_DAY) == NO_CREDITS
