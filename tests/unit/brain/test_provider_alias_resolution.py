"""PROVIDER_ALIASES must resolve the everyday brand names a user speaks.

Forensic 2026-06-27: the voice gate recognised "anthropic" / "chatgpt" as a
provider-switch target, but the manager's alias table only mapped "chatgpt".
"switch to anthropic" therefore passed "anthropic" straight through as a
canonical id, which is not a configured provider — the switch failed. The
gate's spoken aliases and the manager's resolution table must stay in lockstep.
"""
from __future__ import annotations

from jarvis.brain.manager import PROVIDER_ALIASES


def test_anthropic_resolves_to_claude_api() -> None:
    assert PROVIDER_ALIASES["anthropic"] == "claude-api"


def test_chatgpt_resolves_to_openai() -> None:
    assert PROVIDER_ALIASES["chatgpt"] == "openai"
