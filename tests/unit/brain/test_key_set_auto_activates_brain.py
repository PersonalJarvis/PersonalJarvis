"""Setting an API key must promote that provider to the active brain when the
current active provider is dead — the #1 fresh-install symptom.

Forensic: a downloader has no ``jarvis.toml`` (it is gitignored), so ``brain.primary``
is the packaged code-default (e.g. ``claude-api``) they have NO key for. They open
API Keys, paste their ONE key (say OpenRouter), and nothing happens — the dead
default stays active and the brain keeps reporting "not configured". The fix: on a
``SecretConfigured(set)`` for a brain provider, if the active provider has no usable
credential, switch to and persist the just-keyed provider.

This must NOT become an autonomous self-switch (the USER-ONLY / provider_lock
mandate): it fires only in direct response to the user's own key-set action and
never overrides a provider the user deliberately selected (one that already has a
usable key/login).
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainTierConfig, load_config
from jarvis.core.events import SecretConfigured


def _cfg(*, primary: str):
    cfg = load_config()
    cfg.brain.primary = primary
    cfg.brain.router = BrainTierConfig(
        provider=primary,
        fallback_provider=primary,
    )
    return cfg


def _manager(primary: str) -> tuple[BrainManager, EventBus]:
    bus = EventBus()
    mgr = BrainManager.from_tier_config("router", _cfg(primary=primary), bus)
    mgr.attach_to_bus(bus)
    return mgr, bus


@pytest.mark.asyncio
async def test_key_set_promotes_when_active_provider_is_dead(monkeypatch) -> None:
    # Fresh install: active provider (claude-api default) has no usable credential.
    mgr, bus = _manager("claude-api")
    assert mgr.active_provider == "claude-api"
    monkeypatch.setattr(mgr, "_active_has_usable_credential", lambda: False)
    # Don't touch the real jarvis.toml — persist through a stubbed writer.
    persisted: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_primary",
        lambda name: persisted.append(name),
    )

    # User pastes their ONLY key — OpenRouter.
    await bus.publish(SecretConfigured(key="openrouter_api_key", action="set"))

    assert mgr.active_provider == "openrouter"
    assert persisted == ["openrouter"]  # survives a restart


@pytest.mark.asyncio
async def test_key_set_does_not_override_a_working_active_provider(monkeypatch) -> None:
    # The user has a deliberately-chosen, working active provider. Adding a
    # SECOND key for a different provider must NOT hijack their choice.
    mgr, bus = _manager("claude-api")
    monkeypatch.setattr(mgr, "_active_has_usable_credential", lambda: True)
    switched: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_primary",
        lambda name: switched.append(name),
    )

    await bus.publish(SecretConfigured(key="gemini_api_key", action="set"))

    assert mgr.active_provider == "claude-api"
    assert switched == []


@pytest.mark.asyncio
async def test_key_set_for_a_non_brain_slot_is_ignored(monkeypatch) -> None:
    # An STT/TTS key (groq) is not a brain provider — it must never move the brain.
    mgr, bus = _manager("claude-api")
    monkeypatch.setattr(mgr, "_active_has_usable_credential", lambda: False)
    switched: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_primary",
        lambda name: switched.append(name),
    )

    await bus.publish(SecretConfigured(key="groq_api_key", action="set"))

    assert mgr.active_provider == "claude-api"
    assert switched == []


@pytest.mark.asyncio
async def test_delete_action_never_switches(monkeypatch) -> None:
    mgr, bus = _manager("claude-api")
    monkeypatch.setattr(mgr, "_active_has_usable_credential", lambda: False)
    monkeypatch.setattr("jarvis.core.config_writer.set_brain_primary", lambda name: None)

    await bus.publish(SecretConfigured(key="openrouter_api_key", action="delete"))

    assert mgr.active_provider == "claude-api"


def test_active_has_usable_credential_reflects_key_presence(monkeypatch) -> None:
    # The helper is what decides "dead default vs. deliberate choice" — lock its
    # two branches without depending on which keys the test host actually has.
    mgr, _bus = _manager("claude-api")

    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda specs: None)
    monkeypatch.setattr(
        "jarvis.brain.manager._keyless_provider_is_rescued_by_oauth", lambda name: False
    )
    assert mgr._active_has_usable_credential() is False

    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda specs: "sk-present")
    assert mgr._active_has_usable_credential() is True


def test_active_has_usable_credential_true_via_oauth(monkeypatch) -> None:
    # A keyless provider rescued by a connected OAuth login counts as usable —
    # it must not be overridden even with no API key in any slot.
    mgr, _bus = _manager("claude-api")
    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda specs: None)
    monkeypatch.setattr(
        "jarvis.brain.manager._keyless_provider_is_rescued_by_oauth", lambda name: True
    )
    assert mgr._active_has_usable_credential() is True
