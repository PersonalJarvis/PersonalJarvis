"""The per-turn override: a caller-picked brain that never moves the live one.

The typed chat picks provider / model / effort per chat. The first attempt at
that (2026-08-24, reverted) switched the manager's active brain for the pick,
so the voice changed provider as a side effect. These tests pin the contract
of the replacement (``jarvis/brain/turn_override.py``): the chain is exactly
the pick, the pick runs on its own cached instance, the effort and tool
context reach the request, and every piece of manager state a voice turn
reads is untouched afterwards.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import _TURN_OVERRIDE, BrainManager
from jarvis.brain.turn_override import TurnOverride, TurnReceipt
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from tests.fixtures.brain.fake_brain import FakeBrain

TALKER = "openrouter"
TALKER_MODEL = "openrouter-model"
PICK = "openai"
PICK_MODEL = "gpt-x"


def _manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.routing.intelligent_router = True
    cfg.brain.primary = TALKER
    cfg.brain.providers[TALKER] = BrainProviderConfig(model=TALKER_MODEL, deep_model=TALKER_MODEL)
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    mgr._registry._loaded = True
    return mgr


def _seed(
    mgr: BrainManager, *, scoped_text: str, unscoped_text: str = "WRONG_SEAT"
) -> tuple[FakeBrain, FakeBrain]:
    """The pick twice: the chat's scoped instance and the voice's unscoped one."""
    scoped = FakeBrain(text_response=scoped_text)
    unscoped = FakeBrain(text_response=unscoped_text)
    mgr._brain_cache[(f"{PICK}@agent", PICK_MODEL)] = scoped
    mgr._brain_cache[(PICK, PICK_MODEL)] = unscoped
    return scoped, unscoped


class _Tool:
    def __init__(self, name: str, risk_tier: str = "safe") -> None:
        self.name = name
        self.risk_tier = risk_tier
        self.schema: dict[str, Any] = {}


# ------------------------------------------------------------------ chain


def test_override_chain_is_exactly_the_pick() -> None:
    mgr = _manager()
    _seed(mgr, scoped_text="x")
    mgr._turn_substantive = True
    token = _TURN_OVERRIDE.set(TurnOverride(provider=PICK, model=PICK_MODEL))
    try:
        chain = mgr._build_fallback_chain("deep")
    finally:
        _TURN_OVERRIDE.reset(token)
    assert chain == [(PICK, PICK_MODEL)], "no cross-provider stand-in for a picked model"
    assert mgr._router_lead_key is None


def test_override_on_a_tool_incapable_pick_keeps_the_router_lead() -> None:
    mgr = _manager()
    scoped, unscoped = _seed(mgr, scoped_text="x")
    unscoped.supports_tools = False  # the capability probe reads the unscoped instance
    mgr._turn_substantive = True
    seen: dict[str, Any] = {}

    def _first(level: str, *, exclude: str | None = None) -> tuple[str, str | None]:
        seen["exclude"] = exclude
        return ("gemini", "gemini-flash")

    mgr._first_tool_capable_provider = _first  # type: ignore[assignment]
    token = _TURN_OVERRIDE.set(TurnOverride(provider=PICK, model=PICK_MODEL))
    try:
        chain = mgr._build_fallback_chain("deep")
    finally:
        _TURN_OVERRIDE.reset(token)
    assert chain == [("gemini", "gemini-flash"), (PICK, PICK_MODEL)]
    assert mgr._router_lead_key == ("gemini", "gemini-flash")
    assert seen["exclude"] == PICK, "the lead is picked around the PICK, not the active brain"


def test_without_an_override_the_chain_is_the_classic_one() -> None:
    mgr = _manager()
    mgr._turn_substantive = False
    assert _TURN_OVERRIDE.get() is None
    chain = mgr._build_fallback_chain("fast")
    assert chain and chain[0][0] == TALKER


# --------------------------------------------------------------- generate


@pytest.mark.asyncio
async def test_override_never_moves_the_live_brain() -> None:
    """The regression that got the first attempt reverted: a chat pick changed the voice brain."""
    mgr = _manager()
    scoped, unscoped = _seed(mgr, scoped_text="PICK_ANSWER")
    active_before = mgr._active_name
    providers_before = {k: v.model for k, v in mgr._config.brain.providers.items()}
    dead_before = set(mgr._dead_providers)
    history_before = list(mgr._history)
    override = TurnOverride(provider=PICK, model=PICK_MODEL, reasoning_effort="xhigh")

    reply = await mgr.generate(
        "Erzähl mir bitte etwas über die Geschichte von Rom",  # i18n-allow: a substantive turn
        use_history=False,
        turn_override=override,
    )

    assert "PICK_ANSWER" in reply
    assert scoped.calls and unscoped.calls == [], "the answer came from the chat's own seat"
    assert scoped.calls[0].reasoning_effort == "xhigh"
    assert mgr._active_name == active_before
    assert {k: v.model for k, v in mgr._config.brain.providers.items()} == providers_before
    assert set(mgr._dead_providers) == dead_before
    assert list(mgr._history) == history_before
    assert _TURN_OVERRIDE.get() is None, "the override is reset after the turn"
    assert override.receipt.provider == PICK and override.receipt.model == PICK_MODEL
    assert override.receipt.finish_reason


@pytest.mark.asyncio
async def test_a_dead_listed_provider_still_answers_under_an_override() -> None:
    mgr = _manager()
    scoped, _ = _seed(mgr, scoped_text="STILL_HERE")
    mgr._dead_providers.add(PICK)  # the voice's key for it failed earlier this session
    reply = await mgr.generate(
        "Erzähl mir bitte etwas über die Geschichte von Rom",  # i18n-allow: a substantive turn
        use_history=False,
        turn_override=TurnOverride(provider=PICK, model=PICK_MODEL),
    )
    assert "STILL_HERE" in reply
    assert PICK in mgr._dead_providers, "and the voice's dead-list is not rewritten either"


@pytest.mark.asyncio
async def test_a_failing_override_turn_does_not_dead_list_the_provider_for_the_voice() -> None:
    mgr = _manager()
    mgr._brain_cache[(f"{PICK}@agent", PICK_MODEL)] = FakeBrain(fail_on_call=0)
    mgr._brain_cache[(PICK, PICK_MODEL)] = FakeBrain(text_response="unused")
    await mgr.generate(
        "Erzähl mir bitte etwas über die Geschichte von Rom",  # i18n-allow: a substantive turn
        use_history=False,
        turn_override=TurnOverride(provider=PICK, model=PICK_MODEL),
    )
    assert PICK not in mgr._dead_providers
    assert (PICK, PICK_MODEL) not in mgr._dead_provider_models


@pytest.mark.asyncio
async def test_a_fall_through_to_the_pick_never_announces_a_brain_switch() -> None:
    """Review 2026-08-25: the router lead falls through to the pick at chain index 1,
    and that used to publish BrainProviderSwitched — the sidebar then showed
    "Brain -> <chat model>" although the live brain had not moved."""
    from jarvis.core.events import BrainProviderSwitched

    mgr = _manager()
    scoped, unscoped = _seed(mgr, scoped_text="PICK_ANSWER")
    unscoped.supports_tools = False  # the pick cannot call tools -> a lead runs first
    lead = FakeBrain(text_response="LEAD_TALKED")  # picks no tool -> falls through
    # The lead runs under the chat's scope too (every instance of an overridden turn does).
    mgr._brain_cache[("gemini@agent", "gemini-flash")] = lead
    mgr._brain_cache[("gemini", "gemini-flash")] = FakeBrain(text_response="UNSCOPED_LEAD")
    mgr._first_tool_capable_provider = (  # type: ignore[assignment]
        lambda level, *, exclude=None: ("gemini", "gemini-flash")
    )
    switches: list[BrainProviderSwitched] = []

    async def _capture(event: BrainProviderSwitched) -> None:
        switches.append(event)

    mgr._bus.subscribe(BrainProviderSwitched, _capture)

    reply = await mgr.generate(
        "Erzähl mir bitte etwas über die Geschichte von Rom",  # i18n-allow: a substantive turn
        use_history=False,
        turn_override=TurnOverride(provider=PICK, model=PICK_MODEL),
    )

    assert "PICK_ANSWER" in reply and "LEAD_TALKED" not in reply
    assert lead.calls and scoped.calls, "the lead ran first, the pick answered"
    assert switches == [], "a pick answering after its lead is the plan, not a switch"
    assert mgr._active_name == TALKER


# ------------------------------------------------------- tools and kwargs


def test_override_tools_merge_after_the_gates_and_plan_filters_last() -> None:
    mgr = _manager()
    base = {"run_shell": _Tool("run_shell", "ask"), "wiki_recall": _Tool("wiki_recall")}
    extra = {"Read": _Tool("Read"), "Write": _Tool("Write", "ask")}
    merged = mgr._apply_turn_override_tools(base, TurnOverride(provider=PICK, tools_extra=extra))
    assert set(merged) == {"run_shell", "wiki_recall", "Read", "Write"}

    def read_only(tools: dict[str, Any]) -> dict[str, Any]:
        return {n: t for n, t in tools.items() if getattr(t, "risk_tier", "") == "safe"}

    planned = mgr._apply_turn_override_tools(
        base, TurnOverride(provider=PICK, tools_extra=extra, tool_filter=read_only)
    )
    assert set(planned) == {"wiki_recall", "Read"}
    # A None surface means "the manager's own tools" — the override adds to those.
    assert set(
        mgr._apply_turn_override_tools(None, TurnOverride(provider=PICK, tools_extra=extra))
    ) == set(extra)


def test_override_dispatch_kwargs_carry_effort_context_and_ceiling() -> None:
    assert BrainManager._override_dispatch_kwargs(None) == {}
    plain = TurnOverride(provider=PICK)
    assert BrainManager._override_dispatch_kwargs(plain) == {}
    full = TurnOverride(
        provider=PICK,
        reasoning_effort="low",
        tool_context={"approval_surface": "interactive", "cwd": "/work"},
        max_turns=7,
    )
    assert BrainManager._override_dispatch_kwargs(full) == {
        "reasoning_effort": "low",
        "tool_context": {"approval_surface": "interactive", "cwd": "/work"},
        "max_turns": 7,
    }


# ------------------------------------------------------------ cache scope


def test_scoped_instances_are_separate_and_evicted_with_their_provider() -> None:
    mgr = _manager()
    scoped, unscoped = _seed(mgr, scoped_text="x")
    assert mgr._get_brain(PICK, PICK_MODEL, scope="agent") is scoped
    assert mgr._get_brain(PICK, PICK_MODEL) is unscoped
    mgr.apply_provider_model(PICK, "gpt-y")
    assert (f"{PICK}@agent", PICK_MODEL) not in mgr._brain_cache
    assert (PICK, PICK_MODEL) not in mgr._brain_cache


def test_receipt_usage_is_the_agent_chat_shape() -> None:
    receipt = TurnReceipt()
    assert receipt.usage() == {}
    receipt.record(
        provider=PICK,
        model=PICK_MODEL,
        tokens_in=10,
        tokens_out=3,
        cost_usd=0.5,
        finish_reason="ok",
    )
    assert receipt.usage() == {"input_tokens": 10, "output_tokens": 3, "cost_usd": 0.5}


# ------------------------------------------------------------ system_extra


def _prompt_manager() -> BrainManager:
    """A manager with ``__init__`` bypassed — only what ``_build_system_prompt`` reads."""
    from jarvis.core.config import load_config

    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = None
    m._system_prompt_extra = "ROUTER DISCIPLINE BLOCK"
    m._wiki_context_suffix = ""
    m._reply_language = "auto"
    m._active_turn_identity = None
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = False
    m._config = cfg
    return m


def test_system_prompt_is_byte_identical_without_system_extra() -> None:
    m = _prompt_manager()
    baseline = m._build_system_prompt()
    token = _TURN_OVERRIDE.set(TurnOverride(provider=PICK))
    try:
        with_override = m._build_system_prompt()
    finally:
        _TURN_OVERRIDE.reset(token)
    assert with_override == baseline


def test_system_prompt_carries_system_extra_after_the_manager_extra() -> None:
    m = _prompt_manager()
    addendum = "LOCAL MODELS ASSISTANT BRIEFING 7f3a"
    token = _TURN_OVERRIDE.set(TurnOverride(provider=PICK, system_extra=addendum))
    try:
        prompt = m._build_system_prompt()
    finally:
        _TURN_OVERRIDE.reset(token)
    assert addendum in prompt
    assert prompt.index("ROUTER DISCIPLINE BLOCK") < prompt.index(addendum)
    assert addendum not in m._build_system_prompt(), "the addendum lives for one turn only"
