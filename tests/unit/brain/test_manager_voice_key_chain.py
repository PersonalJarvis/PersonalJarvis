"""Text turns and routines stay off the realtime voice key (mandate 2026-09-29).

Live 2026-09-29: five chat turns reached the OpenAI key a user had added for
GPT-Live after OpenRouter ran out of credits, and its deep model spent $6.82.
The key pays for the voice call and its thinking model; any other turn uses it
only when no other provider is left.
"""

from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, BrainTierConfig, JarvisConfig


def _manager(
    monkeypatch: pytest.MonkeyPatch,
    *,
    active: str,
    available: list[str],
    mode: str = "realtime",
) -> BrainManager:
    config = JarvisConfig()
    config.brain.primary = active
    config.brain.realtime = BrainTierConfig(provider="openai-live", model="gpt-live-1")
    config.brain.providers["openai"] = BrainProviderConfig(
        model="gpt-5.5", deep_model="gpt-5.5-pro"
    )
    config.voice.mode = mode
    manager = BrainManager(config=config, bus=EventBus(), tools={})
    manager._registry._loaded = True
    monkeypatch.setattr(manager._registry, "available", lambda: list(available))
    manager._active_name = active
    manager._dead_providers = set()
    manager._configured_fallbacks = []
    return manager


def _providers(chain: list[tuple[str, str | None]]) -> set[str]:
    return {provider for provider, _ in chain}


@pytest.mark.parametrize("level", ["fast", "deep"])
def test_voice_key_leaves_the_turn_chain_while_another_provider_remains(
    monkeypatch: pytest.MonkeyPatch, level: str
) -> None:
    manager = _manager(monkeypatch, active="grok", available=["grok", "openrouter", "openai"])

    chain = manager._build_fallback_chain(level)

    assert "openai" not in _providers(chain)
    assert chain[0][0] == "grok"


def test_voice_key_primary_hands_the_turn_to_another_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, active="openai", available=["grok", "openai"])

    chain = manager._build_fallback_chain("deep")

    assert _providers(chain) == {"grok"}


def test_single_key_install_keeps_answering_on_the_voice_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, active="openai", available=["openai"])

    chain = manager._build_fallback_chain("deep")

    assert chain[0] == ("openai", "gpt-5.5-pro")


def test_pipeline_voice_leaves_openai_in_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(
        monkeypatch, active="openai", available=["grok", "openai"], mode="pipeline"
    )

    chain = manager._build_fallback_chain("fast")

    assert chain[0] == ("openai", "gpt-5.5")


def test_tool_lead_prefers_a_family_off_the_voice_key(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch, active="codex", available=["codex", "openai", "grok"])
    monkeypatch.setattr(manager, "_brain_can_call_tools", lambda name, model: True)

    assert manager._first_tool_capable_provider("fast")[0] == "grok"


def test_voice_key_still_leads_tools_when_no_other_family_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, active="codex", available=["codex", "openai"])
    monkeypatch.setattr(manager, "_brain_can_call_tools", lambda name, model: name != "codex")

    assert manager._first_tool_capable_provider("fast")[0] == "openai"


def test_scheduled_routine_chain_skips_the_voice_key(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch, active="grok", available=["grok", "openai"])
    monkeypatch.setattr(
        manager,
        "_hoist_tool_model",
        lambda chain: [("openai", "gpt-5.5"), ("grok", "grok-4.3")],
    )

    chain = manager._task_provider_chain("deep")

    assert _providers(chain) == {"grok"}


def test_voice_key_stays_when_the_alternative_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, active="openai", available=["grok", "openai"])
    monkeypatch.setattr(
        manager._rate_tracker, "is_available", lambda name, model=None: name != "grok"
    )

    chain = manager._build_fallback_chain("fast")

    assert "openai" in _providers(chain)


def test_action_turn_keeps_the_voice_key_when_no_alternative_calls_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, active="openai", available=["antigravity", "openai"])
    monkeypatch.setattr(
        manager, "_brain_can_call_tools", lambda name, model: name != "antigravity"
    )
    manager._turn_needs_tools = True

    chain = manager._build_fallback_chain("fast")

    assert chain[0][0] == "openai"


def test_legacy_tool_delegation_keeps_its_voice_key_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, active="codex", available=["codex", "openai", "grok"])
    manager._config.brain.routing.intelligent_router = False
    manager._turn_needs_tools = True
    monkeypatch.setattr(manager, "_brain_can_call_tools", lambda name, model: name == "openai")

    chain = manager._build_fallback_chain("fast")

    assert chain[0][0] == "openai"


def test_a_runtime_switch_to_the_voice_key_family_is_honoured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A person who says "switch to openai" gets openai, not a silent stand-in."""
    manager = _manager(monkeypatch, active="openai", available=["grok", "openai"])
    manager._switched_to = "openai"

    chain = manager._build_fallback_chain("deep")

    assert chain[0] == ("openai", "gpt-5.5-pro")
