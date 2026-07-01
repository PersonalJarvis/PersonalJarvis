"""Regressions-Test: suppress_response-Tool darf KEINEN Fallback ausloesen.

Bug 2026-04-29: Fire-and-Forget-Tools wie ``spawn_worker`` setzen
``suppress_response=True`` → ``ToolUseLoop`` schreibt ``final_agg.text=""``
und ``finish_reason="suppress_response"``.

Frueher: Der Empty-Response-Guard im ``BrainManager.generate`` hat dies als
Safety-Block missinterpretiert und auf den naechsten Provider gefallen. Jeder
Provider in der Chain hat dann erneut ``spawn_worker`` gerufen, das
zweite/dritte Mal lief in den ``JARVIS_DEPTH``-Recursion-Guard.

Fix: Empty-Text + Tool-Calls + ``finish_reason="suppress_response"`` ist ein
LEGITIMER Turn-Abschluss, kein Fallback-Trigger.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.core.protocols import BrainDelta, ExecutionContext, ToolResult
from tests.fixtures.brain.fake_brain import FakeBrain, tool_call_delta


class _SuppressResponseTool:
    """Fake-Tool das ``spawn_worker`` simuliert: fire-and-forget, leerer Output."""

    name = "spawn_worker"
    description = "Fake spawn_worker"
    risk_tier = "monitor"
    suppress_response = True
    schema: dict = {
        "type": "object",
        "properties": {
            "utterance": {"type": "string"},
            "action": {"type": "string"},
        },
        "required": ["utterance", "action"],
    }

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, args, ctx: ExecutionContext) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, output="", artifacts=())


@pytest.mark.asyncio
async def test_suppress_response_does_not_trigger_fallback():
    """Brain ruft spawn_worker (suppress_response=True). Empty-Text-Guard
    darf NICHT den naechsten Provider in der Chain probieren."""
    bus = EventBus()
    config = JarvisConfig()
    config.brain.primary = "claude-subscription"
    config.brain.providers["claude-subscription"] = BrainProviderConfig(
        model="haiku-model",
        deep_model="opus-model",
    )

    spawn_tool = _SuppressResponseTool()

    # Build manager with ToolExecutor wired up so spawn_worker can execute.
    from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor
    from jarvis.core.config import SafetyConfig

    safety = SafetyConfig()
    evaluator = RiskTierEvaluator(safety)
    approval = ApprovalWorkflow(bus)
    executor = ToolExecutor(bus, evaluator, approval)

    manager = BrainManager(
        config=config, bus=bus,
        tools={"spawn_worker": spawn_tool},
        tool_executor=executor,
    )
    manager._registry._loaded = True

    # Primary brain emits ONE tool_use for spawn_worker (no text).
    primary = FakeBrain(
        script=[
            [tool_call_delta(
                "spawn_worker",
                {"utterance": "Spawn the sub-agents.", "action": "spawn"},
                call_id="call_1",
            )],
        ],
    )
    fallback = FakeBrain(text_response="ich darf nicht aufgerufen werden")

    manager._brain_cache[("claude-subscription", "haiku-model")] = primary
    manager._brain_cache[("claude-subscription", "opus-model")] = fallback

    manager._build_fallback_chain = lambda level: [
        ("claude-subscription", "haiku-model"),
        ("claude-subscription", "opus-model"),
    ]

    # Bypass the signalless-turn gate (added 2026-06-27) that removes
    # spawn_worker from tool-less/non-action utterances.  The gate is correct
    # production behaviour — but this test is NOT about turn routing; it only
    # verifies that suppress_response does not cascade into a fallback.  We
    # disable the gate here so spawn_worker stays visible to the FakeBrain and
    # we can observe the suppress_response path end-to-end.
    manager._hide_action_tools_on_signalless_turn = lambda tools, user_text: tools  # type: ignore[method-assign]

    # Utterance enthaelt KEIN Verb aus spawn_verbs, sodass die Force-Spawn-
    # Heuristik nicht greift und der LLM-Pfad wirklich getestet wird.
    result = await manager.generate("erkläre mir das mal", use_history=False)

    # Spawn-Tool wurde GENAU EINMAL gerufen — nicht zweimal (kein Fallback).
    assert len(spawn_tool.calls) == 1, (
        f"spawn_worker sollte nur einmal gerufen werden — bekam "
        f"{len(spawn_tool.calls)} Calls. Fallback-Cascade aktiv?"
    )

    # Primary brain wurde gerufen, fallback brain NICHT.
    assert len(primary.calls) >= 1
    assert len(fallback.calls) == 0, (
        f"Fallback-Brain wurde {len(fallback.calls)}x gerufen — sollte 0 sein. "
        f"Empty-Response-Guard hat false-fired auf suppress_response-Turn."
    )

    # Result darf leer sein (suppress_response) — UI bekommt OpenClawAnnouncement
    # ueber den Bus, nicht ueber das BrainManager-return.
    assert isinstance(result, str)
    # Kritisch: result darf NICHT die "alle fehlgeschlagen"-Message enthalten,
    # auch wenn der Text leer ist (suppress_response ist KEIN Fail).
    assert "fehlgeschlagen" not in result
    assert "Keine Brain-Provider" not in result
    assert "unerreichbar" not in result.lower()


@pytest.mark.asyncio
async def test_truly_empty_response_still_triggers_fallback():
    """Backward-Compatibility: ein Brain das WIRKLICH nichts tut (kein Text,
    keine Tool-Calls, kein suppress_response) darf weiterhin fallback'n."""
    bus = EventBus()
    config = JarvisConfig()
    config.brain.primary = "claude-subscription"
    config.brain.providers["claude-subscription"] = BrainProviderConfig(
        model="haiku-model",
        deep_model="opus-model",
    )
    manager = BrainManager(config=config, bus=bus, tools={})
    manager._registry._loaded = True

    # Empty stream: kein content, kein tool_call.
    empty = FakeBrain(script=[[BrainDelta(content="", finish_reason="stop")]])
    fallback = FakeBrain(text_response="Hallo von Fallback")

    manager._brain_cache[("claude-subscription", "haiku-model")] = empty
    manager._brain_cache[("claude-subscription", "opus-model")] = fallback

    manager._build_fallback_chain = lambda level: [
        ("claude-subscription", "haiku-model"),
        ("claude-subscription", "opus-model"),
    ]

    result = await manager.generate("hi", use_history=False)
    assert "Fallback" in result
    assert len(fallback.calls) == 1
