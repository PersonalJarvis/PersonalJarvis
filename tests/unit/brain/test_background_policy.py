"""Background work never slides onto an API key once a subscription is connected."""

from __future__ import annotations

import time

import pytest

from jarvis.brain import background_policy as policy


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    policy.reset_for_tests()
    yield
    policy.reset_for_tests()


def _probe(states: dict[str, bool | None]):
    policy._probe_override = lambda name: states.get(name)


def test_cards_classify_billing():
    assert policy.subscription_capable("claude-cli")
    assert policy.subscription_capable("codex")
    assert policy.keyless_local("ollama")
    assert not policy.subscription_capable("openrouter")
    assert not policy.keyless_local("openrouter")


def test_key_only_install_keeps_its_keys():
    _probe({"claude-cli": False, "codex": False})
    result = policy.background_providers(["openrouter", "grok", "ollama"])
    assert not result.subscription_mode
    assert result.allowed == ("openrouter", "grok", "ollama")


def test_connected_subscription_drops_every_key():
    _probe({"claude-cli": True, "codex": None})
    result = policy.background_providers(["openrouter", "claude-cli", "grok", "codex", "ollama"])
    assert result.subscription_mode
    # codex carries an API-key slot: unknown login means it could bill the key.
    assert result.allowed == ("claude-cli", "ollama")


def test_signed_out_subscription_defers_instead_of_using_keys():
    _probe({"claude-cli": True})
    policy.background_providers(["claude-cli"])  # seen connected once
    policy.reset_for_tests()  # new process: probe cache gone, marker kept
    _probe({"claude-cli": False})
    result = policy.background_providers(["claude-cli", "openrouter", "gemini"])
    assert result.subscription_mode
    assert result.allowed == ()
    assert "waiting" in result.reason


def test_old_marker_expires_to_key_mode(monkeypatch):
    _probe({"claude-cli": True})
    policy.background_providers(["claude-cli"])
    policy.reset_for_tests()
    _probe({"claude-cli": False})
    later = time.time() + policy.SUBSCRIPTION_MEMORY_S + 60
    monkeypatch.setattr(policy.time, "time", lambda: later)
    result = policy.background_providers(["claude-cli", "openrouter"])
    assert not result.subscription_mode
    assert result.allowed == ("claude-cli", "openrouter")


def test_forget_returns_to_key_mode():
    _probe({"claude-cli": True})
    policy.background_providers(["claude-cli"])
    policy.forget()
    _probe({"claude-cli": False})
    assert not policy.background_providers(["openrouter"]).subscription_mode
