"""Regression: a model round that dies AFTER tools ran must speak, not stay silent.

Live incident 2026-07-05 (session 3e27dd8e, 19:49:12): the turn executed 10+
tools, then the final model round returned ``finish_reason="error"`` (OpenRouter
mid-stream error on a ~224k-token prompt) with empty text. The empty-response
guard is (correctly) skipped when tool calls exist, so the turn counted as a
success with empty text — the user heard NOTHING (AD-OE6 violation).

Contract under test:
1. The manager returns an honest localized notice instead of empty text.
2. It does NOT fall through to the next provider — the executed tools would
   re-run their side effects on the retry.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig, SafetyConfig
from jarvis.core.protocols import BrainDelta, ExecutionContext, ToolResult
from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor
from tests.fixtures.brain.fake_brain import FakeBrain, tool_call_delta


class _RecallTool:
    """Benign read tool (wiki-recall shape): safe tier, no side-effect names."""

    name = "wiki-recall"
    description = "Fake wiki recall"
    risk_tier = "safe"
    schema: dict = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, args, ctx: ExecutionContext) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, output="wiki says: jarvis is a project")


@pytest.mark.asyncio
async def test_stream_error_after_tools_speaks_honest_notice() -> None:
    bus = EventBus()
    config = JarvisConfig()
    config.brain.primary = "claude-subscription"
    config.brain.providers["claude-subscription"] = BrainProviderConfig(
        model="haiku-model",
        deep_model="opus-model",
    )

    tool = _RecallTool()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    manager = BrainManager(
        config=config, bus=bus,
        tools={"wiki-recall": tool},
        tool_executor=executor,
    )
    manager._registry._loaded = True

    # Turn 1: the model calls the tool. Turn 2 (after the tool result): the
    # provider stream dies — finish_reason="error", no content (the live
    # OpenRouter mid-stream error shape).
    primary = FakeBrain(script=[
        [tool_call_delta("wiki-recall", {"query": "jarvis"}, call_id="c1")],
        [BrainDelta(finish_reason="error")],
    ])
    fallback = FakeBrain(text_response="I must not be called")

    manager._brain_cache[("claude-subscription", "haiku-model")] = primary
    manager._brain_cache[("claude-subscription", "opus-model")] = fallback
    manager._build_fallback_chain = lambda level: [
        ("claude-subscription", "haiku-model"),
        ("claude-subscription", "opus-model"),
    ]

    result = await manager.generate("was steht im wiki zu jarvis", use_history=False)

    assert isinstance(result, str) and result.strip(), (
        "a stream error after executed tools must produce a SPOKEN notice, "
        "never an empty (silent) turn"
    )
    # It must be the honest mid-answer notice, not the all-failed apology.
    assert "unerreichbar" not in result.lower()  # i18n-allow (matches production error text)
    assert "fehlgeschlagen" not in result.lower()  # i18n-allow (matches production error text)
    # The tool ran exactly once — no provider fall-through re-running it.
    assert len(tool.calls) == 1, (
        f"tool ran {len(tool.calls)}x — a fallback retry would re-run side effects"
    )
    assert len(fallback.calls) == 0, (
        "the chain must NOT fall through after tools already executed"
    )
