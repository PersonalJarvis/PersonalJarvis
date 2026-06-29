"""Sensitive-screen handoff + consequential-action guard in the CU system prompt
(audit 🔴 #4 + #5, prompt-level slice).

#3's SECURITY block defends against the screen *lying* (prompt injection). This
block covers the orthogonal case where the sensitive screen is a *legitimate*
part of the goal: a real login / 2FA / CAPTCHA the agent reaches honestly, and an
irreversible Buy/Pay/Send/Delete it was never asked to perform. The agent must
NOT type the user's secret itself (it holds none — AP-2) and must NOT plow through
a consequential action off-goal; it hands off to the human via ``fail`` instead.

These are deterministic presence checks for the guidance block. The heavier
deterministic detection + pause/poll resume (reusing the UAC elevation-clearance
pattern) is the tracked follow-up, not covered here.
"""
from __future__ import annotations

from jarvis.harness import screenshot_only_loop as sol


def test_block_names_sensitive_screens():
    p = sol._SYSTEM_PROMPT.lower()
    assert "password" in p
    assert "2fa" in p or "two-factor" in p
    assert "captcha" in p
    assert "sign in" in p or "log in" in p or "login" in p


def test_block_forbids_typing_secrets_and_hands_off():
    p = sol._SYSTEM_PROMPT.lower()
    assert "do not type" in p or "never type" in p
    assert "hand off" in p or "handoff" in p
    assert "human" in p


def test_block_names_consequential_actions():
    p = sol._SYSTEM_PROMPT.lower()
    assert "buy" in p
    assert "pay" in p
    assert "delete" in p
    assert "send" in p


def test_block_ties_consequential_to_goal():
    p = sol._SYSTEM_PROMPT.lower()
    assert "consequential" in p
    assert "goal" in p
