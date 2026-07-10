"""Regression: `_first_tool_capable_provider` must honor the SAME provider
health checks the chain walk applies a few lines later in `generate()`
(manager.py, dead_providers + rate_tracker checks just before the per-attempt
try/except).

Before the fix, `_first_tool_capable_provider()` filtered only registry
availability + `_brain_can_call_tools()` — never `self._dead_providers` nor
`self._rate_tracker.is_available(...)`. A dead-listed or rate-limited-but-
tool-capable candidate could still win the router-LEAD slot; the chain walk
then skipped it silently (it never actually answers) while
`self._router_lead_key` kept pointing at it, so `_is_router_lead` misfired for
the REAL lead and the toolless fall-through gate accepted a tool-incapable
provider's answer as final instead of falling through correctly.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


class _ToolIncapableBrain:
    supports_tools = False


class _ToolCapableBrain:
    supports_tools = True


def _manager() -> BrainManager:
    mgr = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    # Not among the gemini/claude-api/openai candidates below, so none of
    # them get skipped for being "the active talker".
    mgr._active_name = "codex"  # type: ignore[attr-defined]
    # Deterministic per-provider model names, independent of tier defaults.
    mgr._fast_model = lambda name: f"{name}-fast"  # type: ignore[method-assign]
    mgr._registry._loaded = True
    mgr._registry._classes = {  # type: ignore[attr-defined]
        "gemini": object, "claude-api": object, "openai": object,
    }
    return mgr


def test_lead_skips_a_dead_listed_provider_even_if_tool_capable() -> None:
    mgr = _manager()
    # A = gemini: tool-incapable, would be skipped anyway.
    mgr._brain_cache[("gemini", "gemini-fast")] = _ToolIncapableBrain()
    # B = claude-api: tool-capable but dead-listed THIS session (e.g. a
    # missing-key failure earlier this turn/session).
    mgr._dead_providers.add("claude-api")
    mgr._brain_cache[("claude-api", "claude-api-fast")] = _ToolCapableBrain()
    # C = openai: healthy AND tool-capable — the only provider the chain
    # walk would actually reach.
    mgr._brain_cache[("openai", "openai-fast")] = _ToolCapableBrain()

    lead = mgr._first_tool_capable_provider("fast")

    assert lead == ("openai", "openai-fast"), (
        "a dead-listed provider must never win the router-lead slot, even "
        "though it is registered and reports tool support"
    )


def test_lead_skips_a_rate_limited_provider_even_if_tool_capable() -> None:
    mgr = _manager()
    mgr._brain_cache[("gemini", "gemini-fast")] = _ToolIncapableBrain()
    # B = claude-api: not dead, but in a rate-limit cooldown right now.
    mgr._brain_cache[("claude-api", "claude-api-fast")] = _ToolCapableBrain()
    mgr._rate_tracker.mark_rate_limited("claude-api", "claude-api-fast")
    mgr._brain_cache[("openai", "openai-fast")] = _ToolCapableBrain()

    lead = mgr._first_tool_capable_provider("fast")

    assert lead == ("openai", "openai-fast"), (
        "a rate-limited provider must never win the router-lead slot — the "
        "chain walk would skip it during its own cooldown check"
    )


def test_lead_still_picks_the_first_healthy_tool_capable_provider() -> None:
    """Sanity check: with nothing dead/rate-limited, order is unaffected."""
    mgr = _manager()
    mgr._brain_cache[("gemini", "gemini-fast")] = _ToolCapableBrain()
    mgr._brain_cache[("claude-api", "claude-api-fast")] = _ToolCapableBrain()
    mgr._brain_cache[("openai", "openai-fast")] = _ToolCapableBrain()

    lead = mgr._first_tool_capable_provider("fast")

    assert lead == ("gemini", "gemini-fast")
