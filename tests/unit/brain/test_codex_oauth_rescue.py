"""Pre-boot key check must NOT dead-list a connected codex ChatGPT-OAuth login.

B1 (open-source AP-22): codex authenticates via ~/.codex/auth.json (OAuth login),
not an API key in PROVIDER_SECRET_CANDIDATES. A ChatGPT-subscription-only user
(no OPENAI_API_KEY) was getting codex pushed into _dead_providers at boot, which
emptied the chain → every chat AND voice turn returned the provider-down apology.
A keyless-but-OAuth-connected codex must survive the key check.
"""
from __future__ import annotations

from jarvis.brain.manager import _keyless_provider_is_rescued_by_oauth


def test_codex_keyless_but_oauth_connected_is_rescued(monkeypatch):
    import jarvis.plugins.brain.codex as codex_mod
    monkeypatch.setattr(codex_mod, "_codex_oauth_connected", lambda: True)
    assert _keyless_provider_is_rescued_by_oauth("codex") is True


def test_codex_keyless_no_oauth_is_not_rescued(monkeypatch):
    import jarvis.plugins.brain.codex as codex_mod
    monkeypatch.setattr(codex_mod, "_codex_oauth_connected", lambda: False)
    assert _keyless_provider_is_rescued_by_oauth("codex") is False


def test_api_key_provider_is_never_rescued():
    # Only OAuth-login brains get the rescue; an ordinary API provider stays dead.
    assert _keyless_provider_is_rescued_by_oauth("openai") is False
    assert _keyless_provider_is_rescued_by_oauth("openrouter") is False
