"""Intelligent router (2026-06-21 mandate: "choose wisely among ALL tools").

When the ACTIVE talker cannot emit tool_calls (for example a provider/model
path without tool-call support), a tool-capable provider LEADS every
substantive turn and the LLM picks the tool via its tool-use loop + system
prompt (no signal-word list decides the tool). If the router picks NO tool (pure
conversation), the turn FALLS THROUGH to the chosen talker so the user keeps
their selected brain's voice. Tool-capable talkers are unaffected.

Gated by `[brain.routing].intelligent_router` (default True; the reversible kill
switch → false restores the prior narrow action-intent delegation).

Unit tests pin `_build_fallback_chain` (chain ordering + the router-lead marker);
one integration test drives `generate()` to prove the fall-through.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from tests.fixtures.brain.fake_brain import FakeBrain

TALKER_PROVIDER = "openrouter"
TALKER_MODEL = "openrouter-model"


class _FakeSpawn:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _Inert:
    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("no exec in a chain-building test")


def _manager(*, intelligent: bool = True) -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.routing.intelligent_router = intelligent
    mgr = BrainManager(
        config=cfg, bus=EventBus(),
        tools={"spawn_worker": _FakeSpawn()}, tool_executor=_Inert(),  # type: ignore[arg-type]
    )
    mgr._active_name = TALKER_PROVIDER  # type: ignore[attr-defined]
    mgr._active_can_call_tools = lambda: False  # type: ignore[assignment]
    mgr._first_tool_capable_provider = (  # type: ignore[assignment]
        lambda level: ("gemini", "gemini-3.5-flash")
    )
    return mgr


# --------------------------------------------------------------------------
# Chain build: the router lead.
# --------------------------------------------------------------------------

def test_substantive_turn_on_tool_incapable_talker_leads_with_router() -> None:
    mgr = _manager(intelligent=True)
    mgr._turn_substantive = True
    chain = mgr._build_fallback_chain("deep")
    assert chain[0] == ("gemini", "gemini-3.5-flash"), "tool-capable router must lead"
    assert mgr._router_lead_key == ("gemini", "gemini-3.5-flash"), (
        "the router lead must be marked so the loop can fall through if it picks no tool"
    )


def test_smalltalk_turn_does_not_get_a_router_lead() -> None:
    mgr = _manager(intelligent=True)
    mgr._turn_substantive = False  # smalltalk → chosen talker answers directly
    chain = mgr._build_fallback_chain("deep")
    assert chain[0][0] == TALKER_PROVIDER
    assert mgr._router_lead_key is None


def test_tool_capable_talker_keeps_its_own_loop() -> None:
    mgr = _manager(intelligent=True)
    mgr._active_can_call_tools = lambda: True  # type: ignore[assignment]
    mgr._turn_substantive = True
    chain = mgr._build_fallback_chain("deep")
    assert chain[0][0] == TALKER_PROVIDER, "a tool-capable talker is not pre-empted"
    assert mgr._router_lead_key is None


def test_flag_off_falls_back_to_narrow_action_intent_delegation() -> None:
    mgr = _manager(intelligent=False)
    mgr._turn_substantive = True
    # flag off: a NON-action substantive turn must NOT get a router lead...
    mgr._turn_needs_tools = False
    chain_conv = mgr._build_fallback_chain("deep")
    assert chain_conv[0][0] == TALKER_PROVIDER
    assert mgr._router_lead_key is None
    # ...but an action-intent turn still delegates (legacy), with no fall-through.
    mgr._turn_needs_tools = True
    chain_action = mgr._build_fallback_chain("deep")
    assert chain_action[0] == ("gemini", "gemini-3.5-flash")
    assert mgr._router_lead_key is None, "legacy delegation has no fall-through marker"


# --------------------------------------------------------------------------
# Fall-through: router picks no tool → chosen talker answers (its voice).
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_with_no_tool_falls_through_to_chosen_talker() -> None:
    """End-to-end: the router lead gets first crack; on a pure-conversation turn
    it picks no tool, so the chosen (tool-incapable) talker produces the answer —
    the user keeps their selected brain's voice."""
    cfg = JarvisConfig()
    cfg.brain.routing.intelligent_router = True
    cfg.brain.primary = TALKER_PROVIDER
    cfg.brain.providers[TALKER_PROVIDER] = BrainProviderConfig(
        model=TALKER_MODEL, deep_model=TALKER_MODEL
    )
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    mgr._registry._loaded = True
    mgr._active_can_call_tools = lambda: False  # type: ignore[assignment]
    mgr._first_tool_capable_provider = (  # type: ignore[assignment]
        lambda level: ("gemini", "gemini-flash")
    )

    router = FakeBrain(text_response="ROUTER_TALKED")   # plain text, no tool
    talker = FakeBrain(text_response="TALKER_ANSWER")
    mgr._brain_cache[("gemini", "gemini-flash")] = router
    mgr._brain_cache[(TALKER_PROVIDER, TALKER_MODEL)] = talker

    reply = await mgr.generate(
        "Erzähl mir bitte etwas über die Geschichte von Rom", use_history=False  # i18n-allow
    )

    assert "TALKER_ANSWER" in reply, "must fall through to the chosen talker's voice"
    assert "ROUTER_TALKED" not in reply, "the router's no-tool answer is discarded"
    assert len(router.calls) == 1, "the router must get first crack at tool selection"
    assert len(talker.calls) == 1, "the chosen talker must produce the final answer"


@pytest.mark.asyncio
async def test_streaming_path_does_not_double_speak_the_router_lead() -> None:
    """Regression (reviewer-found): on the STREAMING voice path the router lead's
    plaintext is streamed to TTS token-by-token DURING dispatch — so a no-tool
    router answer would be spoken, THEN the fall-through talker would speak again
    (double answer). The router lead must NOT stream its conversational text; only
    the chosen talker's answer reaches TTS."""
    cfg = JarvisConfig()
    cfg.brain.routing.intelligent_router = True
    cfg.brain.primary = TALKER_PROVIDER
    cfg.brain.providers[TALKER_PROVIDER] = BrainProviderConfig(
        model=TALKER_MODEL, deep_model=TALKER_MODEL
    )
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    mgr._registry._loaded = True
    mgr._active_can_call_tools = lambda: False  # type: ignore[assignment]
    mgr._first_tool_capable_provider = (  # type: ignore[assignment]
        lambda level: ("gemini", "gemini-flash")
    )
    mgr._brain_cache[("gemini", "gemini-flash")] = FakeBrain(text_response="ROUTER_TALKED")
    mgr._brain_cache[(TALKER_PROVIDER, TALKER_MODEL)] = FakeBrain(text_response="TALKER_ANSWER")

    chunks: list[str] = []
    async for c in mgr.generate_stream(
        "Erzähl mir bitte etwas über die Geschichte von Rom", use_history=False  # i18n-allow
    ):
        chunks.append(c)
    streamed = "".join(chunks)

    assert "TALKER_ANSWER" in streamed, "the chosen talker's answer must be spoken"
    assert "ROUTER_TALKED" not in streamed, (
        "the router lead's conversational text must NOT be streamed to TTS "
        "(double-speak regression)"
    )
