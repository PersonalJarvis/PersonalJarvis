"""Reversible Computer-Use engine switch ([computer_use].engine).

Covers (a) the harness selector that picks the maintained vs the frozen
"june13" engine per config, and (b) a runtime smoke proving the frozen
2026-06-10 engine (jarvis/harness/screenshot_only_loop_june13.py) still drives a
trivial mission to completion against TODAY's codebase — the compatibility claim
the switch relies on.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import jarvis.harness.screenshot_only_loop_june13 as june13
import jarvis.plugins.harness.computer_use as harness
from jarvis.core.protocols import HarnessTask, Observation
from jarvis.harness.computer_use_context import ComputerUseContext

# --- minimal fakes (mirror tests/unit/harness/test_cu_loop_robustness.py) -----


class _FakeVision:
    def __init__(self, titles: list[str] | None = None) -> None:
        self.calls = 0
        self._titles = titles

    def _guess_active_app_hint(self, _f: str | None = None) -> str:
        if not self._titles:
            return ""
        return self._titles[min(self.calls, len(self._titles) - 1)]

    async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                      window_title_filter: str | None = None) -> Observation:
        idx = self.calls
        self.calls += 1
        title = self._titles[min(idx, len(self._titles) - 1)] if self._titles else ""
        return Observation(
            trace_id=uuid4(), timestamp_ns=time.time_ns(), screenshot_path=None,
            screenshot_hash=f"h-{idx}", nodes=(), window_title=title, active_pid=0,
            source="screenshot_only", pruning_stats={},
        )


class _FakeBrain:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def complete_text(self, *, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


class _FakeExecutor:
    async def execute(self, tool: Any, args: dict[str, Any], *,
                      user_utterance: str = "", trace_id: Any = None) -> Any:
        return SimpleNamespace(success=True, output="ok", error="")


def _ctx(brain: _FakeBrain, vision: _FakeVision) -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=vision, brain_manager=brain, tool_executor=_FakeExecutor(),
        tools={}, verify_after_each_step=False, step_budget=5,
    )


# --- runtime smoke: the frozen june13 engine still runs on today's codebase ---


async def test_june13_engine_runs_a_trivial_mission_to_completion():
    # A reply that satisfies BOTH the action parser (action=done) and any
    # verifier parser (done=true + proof), so the loop terminates cleanly
    # regardless of which path the frozen engine takes for this goal.
    brain = _FakeBrain('{"action": "done", "done": true, "proof": "the app is open"}')
    ctx = _ctx(brain, _FakeVision(titles=["New Tab - Google Chrome"]))
    task = HarnessTask(prompt="oeffne chrome", timeout_s=10.0)

    chunks = []
    async for chunk in june13._run_screenshot_loop(task, ctx, cancel_token=None):
        chunks.append(chunk)

    assert chunks, "frozen june13 engine produced no output (crashed?)"
    assert chunks[-1].is_final
    assert chunks[-1].exit_code == 0
    assert brain.calls >= 1  # it actually drove the brain


# --- selector: [computer_use].engine picks the right module -------------------


def _patch_engine(monkeypatch, value: str) -> None:
    monkeypatch.setattr(
        "jarvis.core.config.load_config",
        lambda: SimpleNamespace(computer_use=SimpleNamespace(engine=value)),
    )


def test_selector_routes_june13_to_guarded_v2(monkeypatch):
    _patch_engine(monkeypatch, "june13")
    loop = harness._resolve_run_cu_loop()
    assert loop.__module__ == "jarvis.cu.engine"


def test_selector_routes_current_to_guarded_v2(monkeypatch):
    _patch_engine(monkeypatch, "current")
    loop = harness._resolve_run_cu_loop()
    assert loop.__module__ == "jarvis.cu.engine"


def test_selector_picks_v2_when_configured(monkeypatch):
    _patch_engine(monkeypatch, "v2")
    loop = harness._resolve_run_cu_loop()
    assert loop.__module__ == "jarvis.cu.engine"


def test_selector_refuses_unguarded_legacy_engines_on_macos(monkeypatch):
    _patch_engine(monkeypatch, "current")
    monkeypatch.setattr(harness.sys, "platform", "darwin")

    loop = harness._resolve_run_cu_loop()

    assert loop.__module__ == "jarvis.cu.engine"


def test_selector_routes_stable_to_guarded_v2(monkeypatch):
    _patch_engine(monkeypatch, "stable")
    loop = harness._resolve_run_cu_loop()
    assert loop.__module__ == "jarvis.cu.engine"


def test_selector_falls_back_to_default_engine_on_config_error(monkeypatch):
    # The default engine is v2 since the rebuild; a config-read failure must
    # land on the same default, never on an older engine.
    def _boom() -> Any:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("jarvis.core.config.load_config", _boom)
    loop = harness._resolve_run_cu_loop()
    assert loop.__module__ == "jarvis.cu.engine"
