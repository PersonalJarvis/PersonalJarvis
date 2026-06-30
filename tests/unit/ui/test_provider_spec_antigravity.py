"""The Antigravity (Google-subscription) provider entry.

The per-provider model picker's curated antigravity list rides with the sibling
``model_catalog`` per-provider-picker feature (uncommitted in the shared tree);
adding it here would sweep that foreign work into this commit, so it is a tracked
one-line follow-up. The brain itself uses its default model regardless.
"""
from __future__ import annotations

from jarvis.brain import model_catalog
from jarvis.ui.web.provider_spec import get_spec


def test_antigravity_provider_spec():
    spec = get_spec("antigravity")
    assert spec is not None
    assert spec.tier == "brain"
    assert spec.brain_switchable is False
    assert spec.auth_mode == "antigravity"
    assert spec.secret_keys == ("gemini_api_key",)  # dual-billing: OAuth OR Gemini API key
    assert spec.login_cli is not None
    # agy has no `login` subcommand (it hangs) — the login is a bare interactive run.
    assert "login" not in spec.login_cli


def test_antigravity_excluded_from_live_catalog():
    # No /v1/models endpoint over OAuth — must not be live-fetched.
    assert "antigravity" not in model_catalog.CATALOG_PROVIDERS


def test_codex_provider_spec_is_subagent_only():
    spec = get_spec("codex")
    assert spec is not None
    assert spec.tier == "brain"
    assert spec.brain_switchable is False
    assert spec.auth_mode == "codex"
    assert spec.login_cli == ("codex", "login")
