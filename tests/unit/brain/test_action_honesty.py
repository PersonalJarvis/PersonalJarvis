"""Execution-state guards for action promises in model output."""

from __future__ import annotations

import pytest

from jarvis.brain.action_honesty import (
    action_not_started_phrase,
    has_deferred_action_claim,
    replace_unbacked_action_claim,
)
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from tests.fixtures.brain.fake_brain import FakeBrain


@pytest.mark.parametrize(
    "text",
    [
        (
            "Das kann ich gerne für dich nachschauen. "  # i18n-allow: German runtime fixture
            "Einen Moment, ich werfe einen Blick "  # i18n-allow: German runtime fixture
            "in dein Wiki und sage dir gleich Bescheid."  # i18n-allow: German runtime fixture
        ),
        "Ich schaue gleich in dein Wiki und melde mich.",  # i18n-allow: German runtime fixture
        "One moment, I'll check your Wiki and get back to you.",
        (
            "Un momento, voy a revisar tu wiki "  # i18n-allow: Spanish runtime fixture
            "y te digo enseguida."  # i18n-allow: Spanish runtime fixture
        ),
    ],
)
def test_deferred_action_claims_are_detected(text: str) -> None:
    assert has_deferred_action_claim(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "I checked the calculation: the result is 42.",
        "You can check the Wiki from the sidebar.",
        "Looking at the supplied data, the total is 42.",
        "In einem Wiki stehen verlinkte Seiten und "  # i18n-allow: German runtime fixture
        "Versionsverläufe.",  # i18n-allow: German runtime fixture
    ],
)
def test_grounded_or_explanatory_text_is_not_a_deferred_claim(text: str) -> None:
    assert has_deferred_action_claim(text) is False


def test_unbacked_claim_is_replaced_with_honest_localized_result() -> None:
    original = "Give me a moment; I'll open that and report back."

    assert replace_unbacked_action_claim(
        original,
        executed_tools=(),
        language="en",
    ) == action_not_started_phrase("en")


def test_executed_tool_makes_the_same_wording_grounded() -> None:
    original = "Give me a moment; I'll open that and report back."

    assert (
        replace_unbacked_action_claim(
            original,
            executed_tools={"open_app"},
            language="en",
        )
        == original
    )


def test_action_not_started_phrase_supports_every_runtime_language() -> None:
    assert action_not_started_phrase("de")
    assert action_not_started_phrase("en")
    assert action_not_started_phrase("es")


@pytest.mark.asyncio
async def test_brain_manager_replaces_a_provider_promise_without_tool_evidence() -> None:
    cfg = JarvisConfig()
    cfg.brain.primary = "openrouter"
    cfg.brain.providers["openrouter"] = BrainProviderConfig(
        model="test-model",
        deep_model="test-model",
    )
    manager = BrainManager(config=cfg, bus=EventBus(), tools={})
    manager._registry._loaded = True
    manager._active_can_call_tools = lambda: True  # type: ignore[method-assign]
    manager._brain_cache[("openrouter", "test-model")] = FakeBrain(
        text_response="One moment, I'll check that and get back to you."
    )

    reply = await manager.generate("Tell me something interesting.", use_history=False)

    assert reply == action_not_started_phrase("en")


def test_packaged_persona_forbids_narrating_a_future_action() -> None:
    from jarvis.brain.persona_loader import default_persona_prompt

    prompt = default_persona_prompt()

    assert "emit the tool call without narrating a future action first" in prompt
    assert "a promise is not execution evidence" in prompt
