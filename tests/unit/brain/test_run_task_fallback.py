"""BrainManager.run_task — the Tool Model leads, then the cross-family chain;
task-only tools and written delivery for scheduled tasks.

Live defect 2026-08-24: the active provider (OpenRouter, 0 credits) answered
every scheduled turn with ``APIStatusError 402`` in 0.4 s and NOTHING retried.
Live defect 2026-09-02 (BUG-212): one retry on one other family was not
enough either — OpenRouter 402, Gemini 429, and the Vertex Tool Model the
user had configured under API Keys (state "ready") was never asked. A
scheduled turn now walks :meth:`BrainManager._task_provider_chain`: the Tool
Model first, then every credential-ready, tool-capable provider of another
family, capped — and never switches the persistent active provider.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain import manager as manager_mod
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ToolResult


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.schema: dict[str, Any] = {}


class _NullExecutor:
    async def execute(self, *a: Any, **kw: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _Registry:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def available(self) -> list[str]:
        return list(self._names)

    def failed(self) -> dict[str, str]:
        return {}


class _Credit402(Exception):
    pass


_DEFAULT_CHAIN: list[tuple[str, str | None]] = [
    ("openrouter", "openrouter-tool"),
    ("gemini", "gemini-tool"),
    ("openai", "openai-tool"),
]


def _manager(
    monkeypatch: pytest.MonkeyPatch,
    *,
    active: str = "openrouter",
    providers: list[str] | None = None,
    ready_chain: list[tuple[str, str | None]] | None = None,
) -> BrainManager:
    """A manager whose hoisted Tool Model chain is ``ready_chain``."""
    mgr = BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={"gmail": _FakeTool("gmail")},
        tool_executor=_NullExecutor(),  # type: ignore[arg-type]
    )
    mgr._active_name = active
    mgr._registry = _Registry(providers or ["openrouter", "gemini", "openai"])  # type: ignore[assignment]
    monkeypatch.setattr(mgr, "_fast_model", lambda name: f"{name}-fast")
    monkeypatch.setattr(mgr, "_deep_model", lambda name: f"{name}-deep")
    monkeypatch.setattr(mgr, "_tool_model_credential_ready", lambda name: True)
    monkeypatch.setattr(mgr, "_get_brain", lambda name, model=None: SimpleNamespace(
        name=name, model=model,
    ))
    chain = list(_DEFAULT_CHAIN if ready_chain is None else ready_chain)
    monkeypatch.setattr(mgr, "_hoist_tool_model", lambda base: list(chain))
    return mgr


def _install_dispatch(
    monkeypatch: pytest.MonkeyPatch, mgr: BrainManager, outcomes: dict[str, Any],
) -> list[tuple[str, str | None, dict[str, Any]]]:
    """``outcomes[provider]`` is either a text or an exception to raise."""
    calls: list[tuple[str, str | None, dict[str, Any]]] = []

    def _build(brain: Any, *, tools_override: Any = None, tool_context: Any = None,
               **_: Any) -> Any:
        calls.append((brain.name, brain.model, dict(tool_context or {})))

        class _Dispatcher:
            async def dispatch(self, text: str, **kw: Any) -> Any:
                outcome = outcomes[brain.name]
                if isinstance(outcome, Exception):
                    raise outcome
                return SimpleNamespace(text=outcome)

        return _Dispatcher()

    monkeypatch.setattr(mgr, "_build_dispatcher", _build)
    return calls


# ----------------------------------------------------------------------
# Provider order
# ----------------------------------------------------------------------

async def test_the_configured_tool_model_leads_not_the_chat_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user set Vertex under API Keys as the Tool Model; the chat runs on
    OpenRouter. A scheduled turn asks Vertex first."""
    mgr = _manager(monkeypatch, active="openrouter", ready_chain=[
        ("vertex", "gemini-3.7-flash"), ("openrouter", "openrouter-tool"),
    ])
    calls = _install_dispatch(monkeypatch, mgr, {
        "vertex": "brief from vertex", "openrouter": "never reached",
    })

    out = await mgr.run_task(prompt="p", allowed_tools=("gmail",), model_tier="fast")

    assert out == "brief from vertex"
    assert [(c[0], c[1]) for c in calls] == [("vertex", "gemini-3.7-flash")]
    assert mgr._active_name == "openrouter", "the persistent provider is never switched"


async def test_402_moves_on_to_the_next_family(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch)
    calls = _install_dispatch(monkeypatch, mgr, {
        "openrouter": _Credit402("Error code: 402 - Insufficient credits"),
        "gemini": "digest from gemini",
    })

    out = await mgr.run_task(prompt="p", allowed_tools=("gmail",), model_tier="fast")

    assert out == "digest from gemini"
    assert [c[0] for c in calls] == ["openrouter", "gemini"]


async def test_the_whole_chain_is_walked_not_just_one_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter 402 AND Gemini 429 used to end the turn; the third ready
    provider must still get its chance."""
    mgr = _manager(monkeypatch)
    calls = _install_dispatch(monkeypatch, mgr, {
        "openrouter": _Credit402("Error code: 402 - Insufficient credits"),
        "gemini": RuntimeError("Error code: 429 - rate limit exceeded"),
        "openai": "brief from openai",
    })

    out = await mgr.run_task(prompt="p", model_tier="fast")

    assert out == "brief from openai"
    assert [c[0] for c in calls] == ["openrouter", "gemini", "openai"]


def test_the_chain_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    ready = [(f"p{i}", f"p{i}-tool") for i in range(8)]
    mgr = _manager(monkeypatch, ready_chain=ready)
    chain = mgr._task_provider_chain("fast")
    assert len(chain) == manager_mod._TASK_MAX_ATTEMPTS
    assert chain == ready[: manager_mod._TASK_MAX_ATTEMPTS]


def test_deep_tier_swaps_in_the_deep_model(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch)
    assert mgr._task_provider_chain("fast")[0] == ("openrouter", "openrouter-tool")
    assert mgr._task_provider_chain("deep")[0] == ("openrouter", "openrouter-deep")


def test_the_real_hoist_skips_same_family_and_dead_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the real Tool Model hoist: codex shares the openai family with
    openai-api; a dead-listed gemini is skipped; grok stays."""
    mgr = _manager(monkeypatch, active="openai-api",
                   providers=["openai-api", "codex", "gemini", "grok"])
    monkeypatch.delattr(mgr, "_hoist_tool_model")
    monkeypatch.setattr(mgr, "_tool_model_provider", lambda: "")
    monkeypatch.setattr(mgr, "_tool_model_base_chain", lambda: [
        ("openai-api", None), ("codex", None), ("gemini", None), ("grok", None),
    ])
    mgr._dead_providers.add("gemini")

    chain = mgr._task_provider_chain("fast")

    assert [name for name, _ in chain] == ["openai-api", "grok"]


async def test_all_failing_reports_every_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch)
    _install_dispatch(monkeypatch, mgr, {
        "openrouter": _Credit402("Error code: 402 - Insufficient credits"),
        "gemini": RuntimeError("Error code: 429 - rate limit exceeded"),
        "openai": RuntimeError("Error code: 401 - invalid api key"),
    })
    with pytest.raises(RuntimeError) as info:
        await mgr.run_task(prompt="p", model_tier="fast")
    msg = str(info.value)
    assert msg.startswith("all brain providers failed: ")
    assert "openrouter: _Credit402: Error code: 402" in msg
    assert "gemini: RuntimeError: Error code: 429" in msg
    assert "openai: RuntimeError: Error code: 401" in msg


async def test_non_credential_error_propagates_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(monkeypatch)
    calls = _install_dispatch(monkeypatch, mgr, {
        "openrouter": ValueError("tool schema rejected"),
        "gemini": "never reached",
    })
    with pytest.raises(ValueError):
        await mgr.run_task(prompt="p", model_tier="fast")
    assert [c[0] for c in calls] == ["openrouter"]


async def test_no_ready_candidate_tries_the_active_provider_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing credential-ready: the active provider runs alone so the
    caller sees its real error, not a generic 'all failed'."""
    mgr = _manager(monkeypatch, providers=["openrouter"], ready_chain=[])
    calls = _install_dispatch(monkeypatch, mgr, {
        "openrouter": _Credit402("Error code: 402 - Insufficient credits"),
    })
    with pytest.raises(_Credit402):
        await mgr.run_task(prompt="p", model_tier="fast")
    assert [(c[0], c[1]) for c in calls] == [("openrouter", "openrouter-fast")]


# ----------------------------------------------------------------------
# Turn shape
# ----------------------------------------------------------------------

async def test_task_turn_declares_written_unattended_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(monkeypatch)
    calls = _install_dispatch(monkeypatch, mgr, {"openrouter": "ok"})
    await mgr.run_task(prompt="p", model_tier="deep")
    assert calls == [(
        "openrouter", "openrouter-deep", {"delivery": "written", "unattended": True},
    )]


def test_remember_grant_loads_task_only_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """``remember`` is not a router tool, yet a granted task can use it."""
    mgr = _manager(monkeypatch)
    assert "remember" not in mgr._tools
    sel = mgr._select_task_tools(("gmail", "remember"))
    assert set(sel) == {"gmail", "remember"}
    assert sel["remember"].name == "remember"
    # cached — the same instance on the next task
    assert mgr._select_task_tools(("remember",))["remember"] is sel["remember"]


def test_unknown_task_only_grant_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch)
    monkeypatch.setattr(manager_mod, "_TASK_ONLY_TOOLS", frozenset({"remember", "no-such"}))
    sel = mgr._select_task_tools(("no-such",))
    assert sel == {}


def test_spawn_tools_never_task_only() -> None:
    assert not any("spawn" in name for name in manager_mod._TASK_ONLY_TOOLS)


def test_build_dispatcher_accepts_tool_context() -> None:
    """Regression guard (live 2026-08-24): ``run_task`` passes
    ``tool_context`` to the REAL ``_build_dispatcher``; the fallback tests
    above monkeypatch that method, so only a signature check catches a
    missing keyword — the first automation ever run died on exactly that
    TypeError."""
    import inspect

    from jarvis.brain.manager import BrainManager

    params = inspect.signature(BrainManager._build_dispatcher).parameters
    assert "tool_context" in params
