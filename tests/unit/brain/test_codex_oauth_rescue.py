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


def test_vertex_project_path_is_rescued_without_a_key(monkeypatch):
    """Vertex AI on the Google Cloud project path stores NO key at all.

    Live 2026-08-17: ``[google].vertex_project`` was set, Application Default
    Credentials answered every call, and the check still pushed ``vertex`` into
    ``_dead_providers`` at every boot ("kein Key in ['vertex']") — so the
    router ran on the AI Studio account instead. The rescue must give the same
    answer the provider cards give (``KEYLESS_CREDENTIAL_PROBES``).
    """
    from jarvis.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "vertex_credential_configured", lambda: True)
    assert _keyless_provider_is_rescued_by_oauth("vertex") is True
    assert _keyless_provider_is_rescued_by_oauth("vertex-live") is True


def test_vertex_without_project_or_key_stays_dead(monkeypatch):
    from jarvis.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "vertex_credential_configured", lambda: False)
    assert _keyless_provider_is_rescued_by_oauth("vertex") is False
