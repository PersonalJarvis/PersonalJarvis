"""Provider-agnostic tool delegation when the active talker can't call tools.

Some brain providers cannot emit tool_calls at runtime — the subscription-CLI
brains (Codex over the ChatGPT login, Antigravity over the Google login) drive a
CLI agent and drop ALL tools. A feature that depends on the talker emitting a
tool_call (Computer-Use, plugin reads, …) therefore breaks for whatever provider
the user happens to select. The fix is capability-driven, not a per-provider
hardcode:

* each brain reports its RUNTIME tool-calling capability via ``can_call_tools()``;
* when the active talker can't call tools AND a turn needs a tool/action, the
  fallback chain leads with a tool-capable provider so the action actually runs,
  while the tool-incapable provider stays as the conversational fallback.

These tests are deterministic — no live provider, no network.
"""
from __future__ import annotations

from typing import Any

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.plugins.brain.antigravity import AntigravityBrain
from jarvis.plugins.brain.codex import CodexBrain


# --------------------------------------------------------------------------
# Runtime capability: the subscription-CLI brains can't call tools.
# --------------------------------------------------------------------------

def test_codex_can_call_tools_only_with_api_key(monkeypatch) -> None:
    brain = CodexBrain()
    monkeypatch.setattr(brain, "_api_key", lambda: None)
    assert brain.can_call_tools() is False  # ChatGPT-login CLI path drops tools
    monkeypatch.setattr(brain, "_api_key", lambda: "sk-test-key")
    assert brain.can_call_tools() is True   # API-key path can emit tool_calls


def test_antigravity_cannot_call_tools() -> None:
    # The Google-subscription CLI (agy / gemini-cli) always drops tools.
    assert AntigravityBrain().can_call_tools() is False


# --------------------------------------------------------------------------
# Fallback-chain delegation.
# --------------------------------------------------------------------------

class _FakeTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _Inert:
    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("no exec in a chain-building test")


def _manager() -> BrainManager:
    config = JarvisConfig()
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        tool_executor=_Inert(),  # type: ignore[arg-type]
    )


def test_chain_leads_with_tool_capable_provider_when_active_cant_and_turn_needs_tools() -> None:
    """When the active talker can't call tools AND the turn needs a tool, the
    chain must LEAD with a tool-capable provider so the action runs."""
    mgr = _manager()
    mgr._active_name = "codex"  # type: ignore[attr-defined]
    mgr._active_can_call_tools = lambda: False  # type: ignore[assignment]
    mgr._first_tool_capable_provider = (  # type: ignore[assignment]
        lambda level: ("gemini", "gemini-3.5-flash")
    )
    mgr._turn_needs_tools = True
    chain = mgr._build_fallback_chain("deep")
    assert chain, "chain must not be empty"
    assert chain[0] == ("gemini", "gemini-3.5-flash"), (
        f"a tool-capable provider must lead a tool turn; got {chain[0]!r}"
    )


def test_chain_unchanged_for_pure_conversation_even_if_active_cant_call_tools() -> None:
    """A pure-conversation turn (no tool needed) must NOT be hijacked — the
    chosen provider keeps its voice. Delegation only triggers on tool turns."""
    mgr = _manager()
    mgr._active_name = "codex"  # type: ignore[attr-defined]
    mgr._active_can_call_tools = lambda: False  # type: ignore[assignment]
    mgr._first_tool_capable_provider = (  # type: ignore[assignment]
        lambda level: ("gemini", "gemini-3.5-flash")
    )
    mgr._turn_needs_tools = False
    chain = mgr._build_fallback_chain("deep")
    assert chain, "chain must not be empty"
    assert chain[0][0] == "codex", (
        f"a non-tool turn must keep the active provider leading; got {chain[0]!r}"
    )


def test_chain_unchanged_when_active_is_tool_capable() -> None:
    """A tool-capable active provider never delegates — even on a tool turn."""
    mgr = _manager()
    mgr._active_name = "gemini"  # type: ignore[attr-defined]
    mgr._active_can_call_tools = lambda: True  # type: ignore[assignment]
    called = {"hit": False}

    def _should_not_run(_level):  # noqa: ANN001
        called["hit"] = True
        return ("claude-api", "x")

    mgr._first_tool_capable_provider = _should_not_run  # type: ignore[assignment]
    mgr._turn_needs_tools = True
    chain = mgr._build_fallback_chain("deep")
    assert chain[0][0] == "gemini"
    assert called["hit"] is False, "must not look for a helper when active is capable"
