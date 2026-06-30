"""A depleted-credits 429 must be classified as a DEAD provider (account_blocked),
not a transient rate-limit.

Live forensic (2026-06-28): Gemini returned HTTP 429 with the body "Your prepayment
credits are depleted." Because `_is_account_blocked_exc` did not know that phrasing
AND the brain loop short-circuited on any "429" to the transient rate-limit branch,
the depleted Gemini was never marked dead — so a single empty provider kept leading
the chain every turn and bricked voice/chat even though the user's OpenRouter key
was funded and working. Terminal billing must mark the provider dead so the chain
crosses to whatever provider family actually has a usable key (AP-22).
"""
from __future__ import annotations

from jarvis.brain.manager import (
    _DEAD_LIST_KINDS,
    _classify_provider_error,
    _is_account_blocked_exc,
)

GEMINI_DEPLETED = (
    "Your prepayment credits are depleted. Please go to AI Studio at "
    "https://ai.studio/projects to manage your project and billing."
)

# Live probe 2026-06-30: a funded OpenRouter account whose per-key spend cap is
# used up. Returns 403 for every PAID model while free models still answer. The
# old word-list knew "credits"/"spending limit" but not "key limit"/"total limit",
# so this slipped to call_fail → the spend-capped provider was never dead-listed →
# it kept leading the chain every turn and bricked voice ("can't reach my model")
# even though Gemini was READY. AP-22, the OpenRouter twin of the Gemini case above.
OPENROUTER_KEY_LIMIT = (
    "Error code: 403 - {'error': {'message': 'Key limit exceeded (total limit). "
    "Manage it using https://openrouter.ai/settings/keys', 'code': 403}}"
)


def test_depleted_prepayment_credits_is_account_blocked():
    assert _is_account_blocked_exc(GEMINI_DEPLETED) is True


def test_depleted_credits_429_classifies_as_account_blocked_not_rate_limit():
    # The real error also carries an HTTP 429. It must NOT win the rate-limit
    # branch (which keeps the dead provider in rotation) — terminal billing is
    # account_blocked, which dead-lists the provider so the chain crosses family.
    msg = "Error code: 429 - " + GEMINI_DEPLETED
    assert _classify_provider_error(msg, default="call_fail") == "account_blocked"


def test_openai_style_insufficient_quota_is_account_blocked():
    # Cross-provider coverage: the same class of terminal-billing wording from a
    # different vendor must also dead-list, never read as a transient 429.
    msg = "Error code: 429 - You exceeded your current quota, please check your plan and billing."
    assert _classify_provider_error(msg, default="call_fail") == "account_blocked"


def test_openrouter_key_limit_exceeded_is_account_blocked():
    # Runtime half of the spend-cap bug: the capped key must dead-list OpenRouter so
    # the chain crosses to another funded family instead of leading every turn.
    assert _is_account_blocked_exc(OPENROUTER_KEY_LIMIT) is True
    assert _classify_provider_error(OPENROUTER_KEY_LIMIT, default="call_fail") == "account_blocked"


def test_bare_401_invalid_key_is_terminal_bad_key():
    # A live 401 (invalid/expired/wrong-account key) carries ONLY the numeric code,
    # none of the "missing key" wording. Word-list-first classification let it fall
    # through to call_fail → a dead key (e.g. claude-api with no Anthropic account,
    # 401 in the live log) was retried EVERY turn instead of dead-listed. Code-first:
    # a 401 is terminal and must dead-list.
    msg = (
        "AuthenticationError: Error code: 401 - {'type': 'error', 'error': "
        "{'type': 'authentication_error', 'message': 'invalid x-api-key'}}"
    )
    assert _classify_provider_error(msg, default="call_fail") == "bad_key"


def test_dead_list_kinds_cover_every_terminal_state():
    # The chain-loop dead-lists exactly these kinds. A terminal credential/account
    # state (missing key, blocked account, invalid key) must cross-family fallback;
    # a transient rate_limit must NOT (it takes the cooldown path instead).
    assert "missing_key" in _DEAD_LIST_KINDS
    assert "account_blocked" in _DEAD_LIST_KINDS
    assert "bad_key" in _DEAD_LIST_KINDS
    assert "rate_limit" not in _DEAD_LIST_KINDS
    assert "call_fail" not in _DEAD_LIST_KINDS
