"""BrainManager.run_task — cross-family provider fallback, task-only tools and
written delivery for scheduled tasks (Automations, 2026-08-24).

Live defect: the active provider (OpenRouter, 0 credits) answered every
scheduled turn with ``APIStatusError 402`` in 0.4 s and NOTHING retried, so
every automation failed at its action step. The chat path walks a fallback
chain; the task path must at least retry once on another credential family
(AP-22) — and never switch the persistent active provider (user-only lock).
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


def _manager(monkeypatch: pytest.MonkeyPatch, *, active: str = "openrouter",
             providers: list[str] | None = None) -> BrainManager:
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


async def test_402_retries_once_on_a_different_family(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch)
    calls = _install_dispatch(monkeypatch, mgr, {
        "openrouter": _Credit402("Error code: 402 - Insufficient credits"),
        "gemini": "digest from gemini",
    })

    out = await mgr.run_task(prompt="p", allowed_tools=("gmail",), model_tier="fast")

    assert out == "digest from gemini"
    assert [(c[0], c[1]) for c in calls] == [
        ("openrouter", "openrouter-fast"), ("gemini", "gemini-fast"),
    ]
    assert mgr._active_name == "openrouter", "the persistent provider is never switched"


async def test_fallback_skips_same_family_and_dead_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # codex shares the "openai" family with openai-api; gemini is dead-listed.
    mgr = _manager(monkeypatch, active="openai-api",
                   providers=["openai-api", "codex", "gemini", "grok"])
    mgr._dead_providers.add("gemini")
    assert mgr._task_fallback_provider() == "grok"


async def test_fallback_requires_a_usable_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch)
    monkeypatch.setattr(mgr, "_tool_model_credential_ready", lambda name: name == "openai")
    assert mgr._task_fallback_provider() == "openai"


async def test_both_failing_reports_both(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch)
    _install_dispatch(monkeypatch, mgr, {
        "openrouter": _Credit402("Error code: 402 - Insufficient credits"),
        "gemini": RuntimeError("Error code: 429 - rate limit exceeded"),
    })
    with pytest.raises(RuntimeError) as info:
        await mgr.run_task(prompt="p", model_tier="fast")
    msg = str(info.value)
    assert msg.startswith("all brain providers failed: ")
    assert "openrouter: _Credit402: Error code: 402" in msg
    assert "gemini: RuntimeError: Error code: 429" in msg


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


async def test_no_usable_fallback_reraises_original(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch, providers=["openrouter"])
    _install_dispatch(monkeypatch, mgr, {
        "openrouter": _Credit402("Error code: 402 - Insufficient credits"),
    })
    with pytest.raises(_Credit402):
        await mgr.run_task(prompt="p", model_tier="fast")


async def test_task_turn_declares_written_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _manager(monkeypatch)
    calls = _install_dispatch(monkeypatch, mgr, {"openrouter": "ok"})
    await mgr.run_task(prompt="p", model_tier="deep")
    assert calls == [("openrouter", "openrouter-deep", {"delivery": "written"})]


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
    assert params["tool_context"].kind is inspect.Parameter.KEYWORD_ONLY
