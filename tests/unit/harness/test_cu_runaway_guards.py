"""Runaway/visibility fixes for the CU loop (live failure 2026-06-10 20:46).

Live evidence (data/jarvis_desktop.log): a "open Chrome -> find news on X"
mission got stuck circling the taskbar — open_app 'chrome' was SUPPRESSED
eight times and the repeated-click toggle-stop fired three times, yet the
loop kept grinding for ~2 minutes until the mission budget killed it with
exit 4. Meanwhile the (wrongly counting) "Schritt N von 6 erledigt"
announcements claimed steady progress up to "6 von 6".

Pinned here:

* Guard-hit cap — a mission whose actions keep getting guard-blocked
  (suppressed relaunch, repeated-click toggle-stop) ends with an explicit
  failure long before the step/time budget.
* Progress announcements are OFF by default (``announce_progress``): the
  per-action counter inflates past reality (it counts ok-actions, not plan
  steps), so the spoken "Schritt N von M erledigt" was misinformation.
"""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import HarnessResult, HarnessTask, Observation
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.harness.screenshot_only_loop import _run_screenshot_loop

# ---------------------------------------------------------------------------
# Fakes (mirroring tests/unit/harness/test_cu_loop_robustness.py)
# ---------------------------------------------------------------------------


class FakeVisionEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                      window_title_filter: str | None = None) -> Observation:
        idx = self.calls
        self.calls += 1
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash=f"hash-{idx}",
            nodes=(),
            window_title="",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={},
        )


class FakeBrain:
    """Handler-based complete_text shim."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        return self.handler(system, user)


class FakeToolResult:
    def __init__(self) -> None:
        self.success = True
        self.output = "ok"
        self.error = ""


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool: Any, args: dict[str, Any], *,
                      user_utterance: str = "", trace_id: Any = None) -> FakeToolResult:
        self.calls.append((tool.name, dict(args)))
        return FakeToolResult()


class FakeBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


def make_ctx(brain: FakeBrain, *, bus: FakeBus | None = None,
             announce_progress: bool | None = None) -> ComputerUseContext:
    tools = {
        name: FakeTool(name)
        for name in ("open_app", "click", "type_text", "hotkey",
                     "click_element", "scroll")
    }
    kwargs: dict[str, Any] = {}
    if announce_progress is not None:
        kwargs["announce_progress"] = announce_progress
    return ComputerUseContext(
        vision_engine=FakeVisionEngine(),
        brain_manager=brain,
        tool_executor=FakeExecutor(),
        tools=tools,
        bus=bus,
        step_budget=30,
        per_step_timeout_s=5.0,
        verify_after_each_step=False,
        **kwargs,
    )


async def run_loop(ctx: ComputerUseContext, goal: str) -> list[HarnessResult]:
    task = HarnessTask(prompt=goal, timeout_s=60)
    chunks: list[HarnessResult] = []
    async for chunk in _run_screenshot_loop(task, ctx):
        chunks.append(chunk)
    return chunks


@pytest.fixture(autouse=True)
def _isolate_host(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_labels(timeout_s: float, max_n: int = 28) -> list[str]:
        return []

    monkeypatch.setattr(loop_mod, "_foreground_clickable_labels", _no_labels)
    monkeypatch.setattr(
        loop_mod, "_capture_monitor_geometry", lambda: (0, 0, 1920, 1080),
    )
    monkeypatch.setattr(loop_mod, "_UI_TREE_SOURCE", None, raising=False)


# ---------------------------------------------------------------------------
# Guard-hit cap: circling missions end early and honestly
# ---------------------------------------------------------------------------


async def test_repeated_suppressed_relaunches_end_the_mission() -> None:
    # The model insists on open_app 'chrome' every step. Launch #1 executes;
    # every further one is suppressed by the per-mission launch cap. The
    # mission must end with an explicit failure after the guard-hit cap —
    # NOT grind through the whole step budget (exit 4) like the live run.
    brain = FakeBrain(lambda s, u: '{"action": "open_app", "name": "chrome"}')
    ctx = make_ctx(brain)

    chunks = await run_loop(ctx, "oeffne chrome")

    final = chunks[-1]
    assert final.exit_code == 5, f"expected explicit fail, got {final.exit_code}"
    assert "circling" in final.stderr
    # 1 real launch + the suppressed ones; far fewer brain calls than the
    # step budget would have allowed.
    assert len(brain.requests) <= loop_mod._MAX_GUARD_HITS + 2


async def test_repeated_blocked_clicks_end_the_mission() -> None:
    # Same cap for the repeated-click toggle-stop: the model keeps clicking
    # the same dead spot; the toggle guard blocks each repeat; after the cap
    # the mission ends instead of looping to the budget.
    brain = FakeBrain(lambda s, u: '{"action": "click", "x": 500, "y": 983}')
    ctx = make_ctx(brain)

    chunks = await run_loop(ctx, "oeffne chrome")

    final = chunks[-1]
    assert final.exit_code == 5
    assert "circling" in final.stderr


# ---------------------------------------------------------------------------
# Progress announcements: off by default, opt-in via announce_progress
# ---------------------------------------------------------------------------


async def test_progress_announcements_off_by_default() -> None:
    # Default context: the per-step "Schritt N von M erledigt" announcements
    # must NOT be published — the counter counts ok-actions, not verified
    # plan steps, and spoke "6 von 6 erledigt" on a mission that then failed
    # (live 2026-06-10). Off by default.
    calls = {"n": 0}

    def handler(system: str, user: str) -> str:
        if "desktop-automation planner" in system:
            return ('{"plan": [{"intent": "open chrome", "success": "w"},'
                    ' {"intent": "search x", "success": "r"}]}')
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"action": "open_app", "name": "chrome"}'
        return '{"action": "done"}'

    bus = FakeBus()
    ctx = make_ctx(FakeBrain(handler), bus=bus)

    chunks = await run_loop(ctx, "oeffne chrome und suche auf x")  # i18n-allow: German voice-command test fixture

    assert chunks[-1].exit_code == 0
    announcements = [e for e in bus.published
                     if isinstance(e, AnnouncementRequested)]
    assert announcements == [], (
        f"progress announcements must be off by default, got "
        f"{[a.text for a in announcements]}"
    )


async def test_progress_announcements_opt_in() -> None:
    # announce_progress=True restores the old behaviour (one throttled
    # spoken milestone per completed state change).
    calls = {"n": 0}

    def handler(system: str, user: str) -> str:
        if "desktop-automation planner" in system:
            return ('{"plan": [{"intent": "open chrome", "success": "w"},'
                    ' {"intent": "search x", "success": "r"}]}')
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"action": "open_app", "name": "chrome"}'
        return '{"action": "done"}'

    bus = FakeBus()
    ctx = make_ctx(FakeBrain(handler), bus=bus, announce_progress=True)

    chunks = await run_loop(ctx, "oeffne chrome und suche auf x")  # i18n-allow: German voice-command test fixture

    assert chunks[-1].exit_code == 0
    announcements = [e for e in bus.published
                     if isinstance(e, AnnouncementRequested)]
    assert len(announcements) == 1
    assert "Schritt 1" in announcements[0].text
