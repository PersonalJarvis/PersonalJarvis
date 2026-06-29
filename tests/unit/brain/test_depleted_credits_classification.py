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

from jarvis.brain.manager import _classify_provider_error, _is_account_blocked_exc

GEMINI_DEPLETED = (
    "Your prepayment credits are depleted. Please go to AI Studio at "
    "https://ai.studio/projects to manage your project and billing."
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
