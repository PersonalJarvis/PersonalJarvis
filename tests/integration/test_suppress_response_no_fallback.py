"""Regression test: the suppress_response tool must NOT trigger a fallback.

Bug 2026-04-29: fire-and-forget tools like ``spawn_worker`` set
``suppress_response=True`` → ``ToolUseLoop`` writes ``final_agg.text=""``
and ``finish_reason="suppress_response"``.

Previously: the empty-response guard in ``BrainManager.generate``
misinterpreted this as a safety block and fell back to the next provider.
Every provider in the chain then called ``spawn_worker`` again, and the
second/third call hit the ``JARVIS_DEPTH`` recursion guard.

Fix: empty text + tool calls + ``finish_reason="suppress_response"`` is a
LEGITIMATE turn completion, not a fallback trigger.
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
    """Brain calls spawn_worker (suppress_response=True). The empty-text guard
    must NOT try the next provider in the chain."""
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
    fallback = FakeBrain(text_response="I must not be called")

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

    # The utterance explicitly requests delegation ("Agent", "Hintergrund"),
    # so the explicit-delegation gate (spawn_gate.llm_spawn_allowed,
    # 2026-07-18) lets the LLM-chosen spawn through — while containing NO
    # force_spawn_phrases entry, so the strict force-spawn hoist stays quiet
    # and the LLM path is genuinely exercised.
    result = await manager.generate(
        "ein Agent soll das im Hintergrund erledigen",  # i18n-allow
        use_history=False,
    )

    # The spawn tool was called EXACTLY ONCE — not twice (no fallback).
    assert len(spawn_tool.calls) == 1, (
        f"spawn_worker should only be called once — got "
        f"{len(spawn_tool.calls)} calls. Fallback cascade active?"
    )

    # Primary brain was called, fallback brain was NOT.
    assert len(primary.calls) >= 1
    assert len(fallback.calls) == 0, (
        f"Fallback brain was called {len(fallback.calls)}x — should be 0. "
        f"Empty-response guard false-fired on a suppress_response turn."
    )

    # Result may be empty (suppress_response) — the UI gets a
    # JarvisAgentAnnouncement via the bus, not via the BrainManager return value.
    assert isinstance(result, str)
    # Critical: the result must NOT contain the "all failed" message,
    # even when the text is empty (suppress_response is NOT a failure).
    assert "fehlgeschlagen" not in result  # i18n-allow (matches production error text)
    assert "Keine Brain-Provider" not in result  # i18n-allow (matches production error text)
    assert "unerreichbar" not in result.lower()


@pytest.mark.asyncio
async def test_truly_empty_response_still_triggers_fallback():
    """Backward compatibility: a brain that TRULY does nothing (no text,
    no tool calls, no suppress_response) may still fall back."""
    bus = EventBus()
    config = JarvisConfig()
    config.brain.primary = "claude-subscription"
    config.brain.providers["claude-subscription"] = BrainProviderConfig(
        model="haiku-model",
        deep_model="opus-model",
    )
    manager = BrainManager(config=config, bus=bus, tools={})
    manager._registry._loaded = True

    # Empty stream: no content, no tool_call.
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
