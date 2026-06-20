"""Loop-level robustness tests for the screenshot-only Computer-Use loop.

Covers the 2026-06-09 "not shippable" fix waves:

* Wave A — mid-task aborts: the empty-window-title false regression
  (Chrome & friends run in screenshot mode where ``window_title`` is empty,
  which the loop used to read as "the app window is gone"), single
  brain/parse failures killing the whole mission instead of retrying, and
  the ``_call_brain`` fallthrough that silently returned ``None``.

All tests drive ``_run_screenshot_loop`` directly with fakes — no real
screenshots, no real UIA, no real model calls.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.core.protocols import HarnessResult, HarnessTask, Observation
from jarvis.core.protocols import BrainDelta
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.harness.screenshot_only_loop import (
    CULoopError,
    _call_brain,
    _run_screenshot_loop,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeVisionEngine:
    """Yields a fresh observation per call (unique hash so the no-progress
    guard never trips) with a configurable window title sequence."""

    def __init__(self, window_titles: list[str] | None = None,
                 probe_titles: list[str] | None = None) -> None:
        self.calls = 0
        self._titles = window_titles
        # Cheap foreground-title probe (mirrors VisionEngine._guess_active_
        # app_hint): its own sequence so the settle-probe poll cadence is
        # testable independently of the observe() counter.
        self.probe_titles = list(probe_titles or [])
        self.probe_calls = 0

    def _guess_active_app_hint(self, window_title_filter: str | None = None) -> str:
        if self.probe_titles:
            idx = min(self.probe_calls, len(self.probe_titles) - 1)
            self.probe_calls += 1
            return self.probe_titles[idx]
        if self._titles is None:
            return ""
        return self._titles[min(self.calls, len(self._titles) - 1)]

    async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                      window_title_filter: str | None = None) -> Observation:
        idx = self.calls
        self.calls += 1
        title = ""
        if self._titles is not None:
            title = self._titles[min(idx, len(self._titles) - 1)]
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash=f"hash-{idx}",
            nodes=(),
            window_title=title,
            active_pid=0,
            source="screenshot_only",
            pruning_stats={},
        )


class FakeBrain:
    """``complete_text`` shim — answers from a script or handler.

    Records every (system, user) pair so tests can assert on the prompt the
    executor actually saw (e.g. that no false REGRESSION note was injected).
    """

    def __init__(
        self,
        script: list[str | Exception] | None = None,
        handler: Callable[[str, str], str] | None = None,
    ) -> None:
        self.script = list(script or [])
        self.handler = handler
        self.requests: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        if self.handler is not None:
            return self.handler(system, user)
        item = self.script.pop(0) if self.script else '{"action": "done"}'
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolResult:
    def __init__(self, success: bool = True, output: str = "ok") -> None:
        self.success = success
        self.output = output
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


class _StreamingBrain:
    """Minimal provider-shaped brain for CU provider-chain tests."""

    name = "streaming-brain"
    context_window = 8192
    supports_tools = False
    supports_vision = True

    def __init__(self, *, text: str = "", exc: Exception | None = None) -> None:
        self.text = text
        self.exc = exc
        self.calls = 0

    async def complete(self, req: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        if self.text:
            yield BrainDelta(content=self.text)
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: Any) -> float:
        return 0.0


class _FallbackChainManager:
    """BrainManager-shaped fake that exposes a configured provider chain."""

    active_provider = "primary"

    def __init__(self) -> None:
        self.primary = _StreamingBrain(exc=RuntimeError("primary down"))
        self.fallback = _StreamingBrain(text='{"action": "done"}')
        self.requested: list[tuple[str, str | None]] = []

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        return [("primary", "bad-model"), ("fallback", "good-model")]

    def _get_brain(self, name: str, model: str | None = None) -> _StreamingBrain:
        self.requested.append((name, model))
        if name == "primary":
            return self.primary
        if name == "fallback":
            return self.fallback
        raise AssertionError(f"unexpected provider {name!r}")


def make_ctx(brain: FakeBrain, *, titles: list[str] | None = None,
             verify: bool = False, step_budget: int = 10,
             bus: Any = None, announce_progress: bool = False,
             probe_titles: list[str] | None = None) -> ComputerUseContext:
    tools = {
        name: FakeTool(name)
        for name in ("open_app", "click", "type_text", "hotkey",
                     "click_element", "scroll")
    }
    return ComputerUseContext(
        vision_engine=FakeVisionEngine(
            window_titles=titles, probe_titles=probe_titles,
        ),
        brain_manager=brain,
        tool_executor=FakeExecutor(),
        tools=tools,
        bus=bus,
        step_budget=step_budget,
        per_step_timeout_s=5.0,
        verify_after_each_step=verify,
        announce_progress=announce_progress,
    )


async def run_loop(ctx: ComputerUseContext, goal: str) -> list[HarnessResult]:
    task = HarnessTask(prompt=goal, timeout_s=30)
    chunks: list[HarnessResult] = []
    async for chunk in _run_screenshot_loop(task, ctx):
        chunks.append(chunk)
    return chunks


@pytest.fixture(autouse=True)
def _isolate_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real desktop from these tests: no UIA enumeration,
    no win32 monitor probing."""
    async def _no_labels(timeout_s: float, max_n: int = 28) -> list[str]:
        return []

    monkeypatch.setattr(loop_mod, "_foreground_clickable_labels", _no_labels)
    monkeypatch.setattr(
        loop_mod, "_capture_monitor_geometry", lambda: (0, 0, 1920, 1080),
    )
    # Keep the UI-tree-source singleton hermetic between tests.
    monkeypatch.setattr(loop_mod, "_UI_TREE_SOURCE", None, raising=False)
    # Neutralize the post-open_app settle probe suite-wide (it sleeps up to
    # 1 s per launch on empty fake titles). The dedicated settle tests
    # re-enable it with their own explicit timeout/poll values.
    monkeypatch.setattr(loop_mod, "_OPEN_APP_SETTLE_TIMEOUT_S", 0.0)


# ---------------------------------------------------------------------------
# Wave A1/A2 — empty window title must not read as "app window gone"
# ---------------------------------------------------------------------------


async def test_empty_window_title_is_not_a_desktop_regression() -> None:
    """Chrome (and every _TEXT_HEAVY_HINTS app) runs in screenshot mode where
    the observation's window_title is EMPTY. The regression detector used to
    treat an empty title as "foreground fell to the desktop" and told the
    model to re-open the app it had just opened."""
    brain = FakeBrain(script=[
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain, titles=[""])  # title stays empty like live Chrome
    # Single-verb goal: stays on the planless path so the script order is
    # deterministic (compound goals now trigger the planner — Wave C).
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    # The second executor turn must NOT contain a false regression note.
    assert len(brain.requests) == 2
    _system, user = brain.requests[1]
    assert "REGRESSION" not in user


async def test_real_desktop_title_still_detected_as_regression() -> None:
    """A genuine fall to the desktop (Program Manager foreground) must still
    inject the regression note so recover-after-close keeps working."""
    brain = FakeBrain(script=[
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain, titles=["Program Manager"])
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    assert len(brain.requests) == 2
    _system, user = brain.requests[1]
    assert "REGRESSION" in user


# ---------------------------------------------------------------------------
# Wave A3 — single model failures retry instead of killing the mission
# ---------------------------------------------------------------------------


async def test_garbage_model_response_is_retried() -> None:
    brain = FakeBrain(script=[
        "THIS IS NOT JSON AT ALL",
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "mach das fenster groesser bitte")

    assert chunks[-1].exit_code == 0
    assert len(brain.requests) == 2


async def test_brain_exception_is_retried() -> None:
    brain = FakeBrain(script=[
        RuntimeError("provider hiccup"),
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "mach das fenster groesser bitte")

    assert chunks[-1].exit_code == 0
    assert len(brain.requests) == 2


async def test_repeated_model_failures_end_mission_cleanly() -> None:
    brain = FakeBrain(script=[
        "garbage one",
        "garbage two",
        "garbage three",
        '{"action": "done"}',  # must never be reached
    ])
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "mach das fenster groesser bitte")

    final = chunks[-1]
    assert final.is_final
    assert final.exit_code != 0
    assert final.stderr  # clean, explicit failure message
    assert len(brain.requests) == 3


# ---------------------------------------------------------------------------
# Wave A4 — _call_brain must raise on an unusable manager, never return None
# ---------------------------------------------------------------------------


async def test_call_brain_raises_on_unusable_manager() -> None:
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = object()  # no complete_text, no _get_brain, not callable
    obs = Observation(
        trace_id=uuid4(), timestamp_ns=time.time_ns(),
        screenshot_path=None, screenshot_hash="x",
    )
    with pytest.raises(CULoopError):
        await _call_brain(ctx, observation=obs, user_goal="g", history_text="")


async def test_call_brain_uses_provider_fallback_chain() -> None:
    """CU must not bypass BrainManager fallbacks.

    Live regression 2026-06-20: the normal chat turn fell back from a broken
    Antigravity CLI provider to Grok, but Computer-Use called only
    ``active_provider``. The CU loop then retried the same broken provider
    three times and exited with parse/confusion instead of using the configured
    fallback provider.
    """
    manager = _FallbackChainManager()
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = manager
    obs = Observation(
        trace_id=uuid4(),
        timestamp_ns=time.time_ns(),
        screenshot_path=None,
        screenshot_hash="x",
    )

    raw = await _call_brain(ctx, observation=obs, user_goal="open chrome", history_text="")

    assert raw == '{"action": "done"}'
    assert manager.primary.calls == 1
    assert manager.fallback.calls == 1
    assert manager.requested == [
        ("primary", "bad-model"),
        ("fallback", "good-model"),
    ]


def test_decide_native_batch_defined_only_once() -> None:
    """Guard against the duplicated-function merge accident: the module must
    define _decide_native_batch exactly once."""
    import inspect

    src = inspect.getsource(loop_mod)
    assert src.count("async def _decide_native_batch(") == 1


# ---------------------------------------------------------------------------
# Wave B — generic done-verification gated by verify_after_each_step
#
# "done" used to be taken on faith for every goal except calculators
# (compute) and play/submit (media) — so "open Chrome" could end with
# exit 0 after merely TYPING "chrome" into a search box. Now every "done"
# goes through a strict screenshot judge when verify_after_each_step is on.
# ---------------------------------------------------------------------------


class JudgingBrain(FakeBrain):
    """Routes executor calls and judge calls to separate scripts.

    Judge calls are recognised by the system prompt (every verifier prompt
    self-describes as a strict ... judge)."""

    def __init__(self, executor_script: list[str],
                 judge_script: list[str]) -> None:
        super().__init__()
        self.executor_script = list(executor_script)
        self.judge_script = list(judge_script)
        self.judge_calls: list[tuple[str, str]] = []
        self.executor_calls: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        if "judge" in system.lower():
            self.judge_calls.append((system, user))
            return self.judge_script.pop(0) if self.judge_script else (
                '{"done": false, "proof": ""}'
            )
        self.executor_calls.append((system, user))
        return self.executor_script.pop(0) if self.executor_script else (
            '{"action": "fail", "reason": "script exhausted"}'
        )


async def test_premature_done_is_rejected_and_mission_continues() -> None:
    """The live 'Öffne Chrome' failure: the model declares done without
    having opened anything. The judge must reject it, the loop must keep
    working, and only a verified done may end the mission."""
    brain = JudgingBrain(
        executor_script=[
            '{"action": "done"}',                       # premature
            '{"action": "open_app", "name": "chrome"}',  # real work
            '{"action": "done"}',                       # now verifiable
        ],
        judge_script=[
            '{"done": false, "proof": "no chrome window visible"}',
            '{"done": true, "proof": "chrome window in foreground"}',
        ],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    final = chunks[-1]
    assert final.exit_code == 0
    assert "verified" in final.stdout
    assert len(brain.judge_calls) == 2
    # The rejection must be taught back to the model.
    assert any("no chrome window visible" in user
               for _s, user in brain.executor_calls[1:])


async def test_done_accepted_directly_when_verification_disabled() -> None:
    brain = JudgingBrain(
        executor_script=['{"action": "done"}'],
        judge_script=[],
    )
    ctx = make_ctx(brain, verify=False)
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    assert len(brain.judge_calls) == 0


async def test_open_app_done_is_verified_without_an_llm_call() -> None:
    """'oeffne chrome' + a foreground title containing 'chrome' is proof
    enough (2026-06-10 latency plan Task 4). The vision done-judge — one
    extra LLM call plus reject-loop risk — is reserved for goals the window
    title cannot prove."""
    brain = FakeBrain([
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(
        brain, verify=True,
        titles=["Program Manager", "New Tab - Google Chrome"],
    )
    results = await run_loop(ctx, "oeffne chrome")

    assert results[-1].exit_code == 0
    # 2 executor think calls only — NO third judge call.
    assert len(brain.requests) == 2


async def test_open_app_waits_for_the_window_before_next_think(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """open_app is a fire-and-forget Popen. Observing immediately catches the
    pre-launch desktop and burns a full think round on a stale frame (latency
    plan Task 6). The loop polls the cheap foreground-title probe until the
    app's window is up, THEN observes."""
    monkeypatch.setattr(loop_mod, "_OPEN_APP_SETTLE_TIMEOUT_S", 5.0)
    monkeypatch.setattr(loop_mod, "_OPEN_APP_SETTLE_POLL_S", 0.005)
    brain = FakeBrain([
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(
        brain, verify=True,
        titles=["Program Manager", "New Tab - Google Chrome"],
        # Probe sequence: window appears on the third poll.
        probe_titles=[
            "Program Manager", "Program Manager", "New Tab - Google Chrome",
        ],
    )
    results = await run_loop(ctx, "oeffne chrome")

    assert results[-1].exit_code == 0
    engine = ctx.vision_engine
    assert engine.probe_calls >= 3, "settle probe did not poll for the window"
    # Still exactly 2 think calls — the settle wait replaced the wasted
    # stale-frame round, it did not add LLM cost.
    assert len(brain.requests) == 2


async def test_open_app_settle_gives_up_when_window_never_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A window that never appears must not wedge the loop: the probe gives
    up after its timeout and the mission continues (and may fail honestly)."""
    monkeypatch.setattr(loop_mod, "_OPEN_APP_SETTLE_POLL_S", 0.005)
    monkeypatch.setattr(loop_mod, "_OPEN_APP_SETTLE_TIMEOUT_S", 0.05)
    brain = FakeBrain([
        '{"action": "open_app", "name": "spotify"}',
        '{"action": "fail", "reason": "window never appeared"}',
    ])
    ctx = make_ctx(
        brain,
        titles=["Program Manager"],
        probe_titles=["Program Manager"],
    )
    start = time.monotonic()
    results = await run_loop(ctx, "oeffne spotify")

    assert time.monotonic() - start < 1.0, "settle probe wedged the loop"
    assert results[-1].is_final


async def test_mission_profile_summary_is_emitted() -> None:
    """Every mission ends with one '[cu] mission profile:' line breaking the
    wall time into phases (latency plan Task 7) — the measurement that keeps
    the latency work honest and debuggable from a single log line."""
    brain = FakeBrain([
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain, titles=["New Tab - Google Chrome"])
    results = await run_loop(ctx, "oeffne chrome")

    assert results[-1].exit_code == 0
    stderr = "".join(r.stderr or "" for r in results)
    assert "[cu] mission profile:" in stderr
    assert "think=" in stderr
    assert "observe=" in stderr
    assert "act=" in stderr


async def test_loop_overhead_without_llm_is_subsecond() -> None:
    """Everything that is not the brain call must stay near-zero. Regression
    net for future blocking additions on the step path (the class of bug
    behind BUG-CU-ANNOUNCE-BLOCK: 6-10 s of TTS wait per step)."""
    brain = FakeBrain(
        ['{"action": "hotkey", "keys": "ctrl+l"}'] * 4 + ['{"action": "done"}']
    )
    ctx = make_ctx(brain, titles=["Some App"], step_budget=12)
    start = time.monotonic()
    await run_loop(ctx, "tu was in der app")
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, (
        f"5 fake-brain steps took {elapsed:.2f}s of pure loop overhead"
    )


async def test_open_goal_without_title_proof_still_pays_the_judge() -> None:
    """The deterministic title check must only ever SKIP the judge when it
    proves the goal; a non-matching title falls through to the LLM judge
    unchanged (never a false 'done', never a false reject)."""
    brain = JudgingBrain(
        executor_script=[
            '{"action": "open_app", "name": "spotify"}',
            '{"action": "done"}',
        ],
        judge_script=['{"done": true, "proof": "spotify main window visible"}'],
    )
    # Title never mentions spotify (e.g. minimized to tray) -> judge decides.
    ctx = make_ctx(brain, verify=True, titles=["Program Manager", ""])
    results = await run_loop(ctx, "oeffne spotify")

    assert results[-1].exit_code == 0
    assert len(brain.judge_calls) == 1


async def test_repeated_done_rejections_fail_cleanly() -> None:
    """A model that insists on a wrong done must not loop forever: after the
    reject budget the mission ends with an explicit failure."""
    brain = JudgingBrain(
        executor_script=['{"action": "done"}'] * 10,
        judge_script=['{"done": false, "proof": "goal not visible"}'] * 10,
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    final = chunks[-1]
    assert final.is_final
    assert final.exit_code != 0
    assert "not" in final.stderr.lower()  # explicit "not achieved" reason
    assert len(brain.judge_calls) == 3    # reject budget, then clean fail


# ---------------------------------------------------------------------------
# Wave C — plan-first for ALL multi-step goals, not just music playback
#
# The planner existed but was gated on play/search goals only, so
# "open Chrome and navigate to X" ran purely reactive and lost the thread.
# The music-specific SEARCH DISCIPLINE block also leaked into every planned
# turn regardless of the goal.
# ---------------------------------------------------------------------------


class PlanningBrain(FakeBrain):
    """Routes planner / judge / executor calls to separate scripts."""

    def __init__(self, executor_script: list[str],
                 plan_script: list[str] | None = None,
                 judge_script: list[str] | None = None) -> None:
        super().__init__()
        self.executor_script = list(executor_script)
        self.plan_script = list(plan_script or [])
        self.judge_script = list(judge_script or [])
        self.planner_calls: list[tuple[str, str]] = []
        self.judge_calls: list[tuple[str, str]] = []
        self.executor_calls: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        low = system.lower()
        if "planner" in low:
            self.planner_calls.append((system, user))
            return self.plan_script.pop(0) if self.plan_script else '{"plan": []}'
        if "judge" in low:
            self.judge_calls.append((system, user))
            return self.judge_script.pop(0) if self.judge_script else (
                '{"done": true, "proof": "ok"}'
            )
        self.executor_calls.append((system, user))
        return self.executor_script.pop(0) if self.executor_script else (
            '{"action": "fail", "reason": "script exhausted"}'
        )


_CHROME_PLAN = (
    '{"plan": ['
    '{"intent": "open chrome", "success": "a chrome window is open"},'
    '{"intent": "open the settings menu", "success": "the menu is visible"},'
    '{"intent": "click settings", "success": "the settings page is shown"}'
    ']}'
)


async def test_compound_goal_activates_planner() -> None:
    """'open X and do Y' is a multi-step goal and must get an ordered plan,
    exactly like the music goals that were already plan-first."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "settings page visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")

    assert chunks[-1].exit_code == 0
    assert len(brain.planner_calls) == 1


async def test_search_discipline_not_injected_for_non_music_goals() -> None:
    """The Spotify SEARCH DISCIPLINE block must only reach music/search
    goals — a navigation goal gets a clean plan prompt."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "ok"}'],
    )
    ctx = make_ctx(brain, verify=True)
    await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")

    assert brain.executor_calls, "executor was never consulted"
    for _system, user in brain.executor_calls:
        assert "FORBIDDEN SHORTCUT" not in user
        assert "play a song" not in user


class SlowAnnouncementBus:
    """A bus whose publish blocks like the live TTS announcement path.

    Only ``AnnouncementRequested`` is slow — mirroring production, where the
    announcement handler synthesizes TTS inline while the liveness-event
    handlers are cheap."""

    def __init__(self, block_s: float = 0.75) -> None:
        self.block_s = block_s
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)
        if type(event).__name__ == "AnnouncementRequested":
            await asyncio.sleep(self.block_s)


async def test_progress_announcement_does_not_block_the_loop() -> None:
    """BUG-CU-ANNOUNCE-BLOCK (2026-06-10): ``bus.publish`` awaits typed
    subscribers uncapped (bus.py) and ``pipeline._on_announcement`` runs the
    full TTS synthesis inside that dispatch, so every spoken
    'Schritt N von M erledigt.' froze the CU loop for 6-10 s (measured live,
    log 20:46). The loop must fire announcements without awaiting them."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "ok"}'],
    )
    bus = SlowAnnouncementBus(block_s=0.75)
    ctx = make_ctx(brain, verify=True, bus=bus, announce_progress=True)

    start = time.monotonic()
    chunks = await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")  # i18n-allow: German voice-command test fixture
    elapsed = time.monotonic() - start

    assert chunks[-1].exit_code == 0
    # With the old blocking publish, the single announced state change costs
    # >= block_s on the loop's own wall clock. Non-blocking must stay well
    # under one block interval.
    assert elapsed < 0.5, f"loop blocked on announcement publish ({elapsed:.2f}s)"

    # The announcement must still go out (fire-and-forget, not dropped).
    pending = set(getattr(loop_mod, "_ANNOUNCE_TASKS", set()))
    if pending:
        await asyncio.wait(pending, timeout=2.0)
    progress = [e for e in bus.events if getattr(e, "kind", "") == "progress"]
    assert progress, "progress announcement was dropped instead of fired in background"


async def test_plan_step_advances_on_state_change_not_on_wait() -> None:
    """Step tracking must count successful STATE-CHANGING actions. The old
    ' ok '-substring heuristic also counted pure waits, so the >>> marker
    ran ahead of reality."""
    brain = PlanningBrain(
        executor_script=[
            # One real action + one pure wait in a single batch.
            '[{"action": "open_app", "name": "chrome"},'
            ' {"action": "wait", "ms": 1}]',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "ok"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")

    assert chunks[-1].exit_code == 0
    assert len(brain.executor_calls) == 2
    _system, user = brain.executor_calls[1]
    # One state change happened -> the CURRENT step is plan step 2.
    assert "2. >>>" in user
    assert "3. >>>" not in user


async def test_done_judge_sees_fresh_screenshot_after_batch_action() -> None:
    """Review finding 2026-06-09: in a batch like [open_app, done] the judge
    used to be handed the screenshot from BEFORE open_app ran, spuriously
    rejecting a completed goal. The done-gate must re-observe when the batch
    already executed a state-changing action."""
    brain = JudgingBrain(
        executor_script=[
            '[{"action": "open_app", "name": "chrome"},'
            ' {"action": "done"}]',
        ],
        judge_script=['{"done": true, "proof": "chrome visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    # One observe for the step + one fresh observe for the verifier.
    assert ctx.vision_engine.calls == 2


async def test_verify_first_fallback_keeps_search_discipline_for_music() -> None:
    """Review finding 2026-06-09: when the planner fails on a music goal, the
    VERIFY FIRST fallback prompt must still carry the SEARCH DISCIPLINE
    anti-shortcut block."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "click", "x": 500, "y": 500}',
            # End the test at call 2 via a verified done. (Was a bare fail; the
            # 2026-06-15 fail-gate now re-plans a premature fail, so it no
            # longer terminates instantly. The 2nd executor PROMPT — the thing
            # this test inspects — is built identically either way.)
            '{"action": "done"}',
        ],
        plan_script=['{"plan": []}'],  # planner returns nothing usable
        judge_script=['{"done": true, "proof": "track playing"}'],
    )
    ctx = make_ctx(brain, verify=True)
    await run_loop(ctx, "spiel shape of you")

    assert len(brain.executor_calls) == 2
    _system, user = brain.executor_calls[1]
    assert "VERIFY FIRST" in user
    assert "FORBIDDEN SHORTCUT" in user


def test_calculator_goals_never_pay_the_planner() -> None:
    """Review finding 2026-06-09: arithmetic phrased with 'und' ("rechne 7
    und 3 zusammen") must not trigger the planner round-trip — compute goals
    stay on the cheap stateless path."""
    assert loop_mod._goal_needs_plan("rechne 7 und 3 zusammen") is False
    assert loop_mod._goal_needs_plan("berechne 8 mal 8 und sag es mir") is False
    # Non-compute compound goals still plan.
    assert loop_mod._goal_needs_plan("oeffne chrome und geh zu github") is True


# ---------------------------------------------------------------------------
# Wave D — per-step latency: capped screenshots, no duplicate UIA work,
# screenshot + UIA label enumeration in parallel.
# ---------------------------------------------------------------------------


async def test_observation_image_is_capped_for_the_model(tmp_path) -> None:
    """The loop used to ship the raw full-resolution screenshot (4K on the
    maintainer's box) to the vision model every step. It must be capped to
    the vision-friendly 2048px longest side / JPEG like the router path."""
    import base64
    import io
    import os

    from PIL import Image

    # Noise so the PNG actually exceeds the byte budget like a real
    # 4K desktop screenshot does (a flat color would compress to a no-op).
    big = Image.frombytes("RGB", (2600, 1600), os.urandom(2600 * 1600 * 3))
    png_path = tmp_path / "big.png"
    big.save(png_path, format="PNG")

    obs = Observation(
        trace_id=uuid4(), timestamp_ns=time.time_ns(),
        screenshot_path=str(png_path), screenshot_hash="big",
    )
    block = await loop_mod._load_observation_image(obs)

    assert block is not None
    img = Image.open(io.BytesIO(base64.b64decode(block.data_b64)))
    assert max(img.size) <= 2048
    assert block.mime == "image/jpeg"


async def test_observe_requests_screenshot_mode_not_composite() -> None:
    """The loop never reads observation.nodes — it enumerates clickable
    labels separately. Asking the engine for mode='auto' (composite for most
    apps) paid a full UIA enumeration per step for nothing."""
    seen_modes: list[str] = []

    class ModeRecordingEngine(FakeVisionEngine):
        async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                          window_title_filter: str | None = None) -> Observation:
            seen_modes.append(mode)
            return await super().observe(
                mode=mode, cancel_token=cancel_token,
                window_title_filter=window_title_filter,
            )

    brain = FakeBrain(script=['{"action": "done"}'])
    ctx = make_ctx(brain)
    ctx.vision_engine = ModeRecordingEngine()
    chunks = await run_loop(ctx, "mach das fenster zu bitte")

    assert chunks[-1].exit_code == 0
    assert seen_modes and all(m == "screenshot" for m in seen_modes)


async def test_screenshot_and_label_enumeration_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per step, the screenshot and the UIA label enumeration are independent
    I/O — they must start together instead of back-to-back."""
    import asyncio as aio

    starts: dict[str, float] = {}

    class SlowEngine(FakeVisionEngine):
        async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                          window_title_filter: str | None = None) -> Observation:
            starts.setdefault("observe", time.monotonic())
            await aio.sleep(0.15)
            return await super().observe(mode=mode, cancel_token=cancel_token)

    async def slow_labels(timeout_s: float, max_n: int = 28) -> list[str]:
        starts.setdefault("labels", time.monotonic())
        await aio.sleep(0.15)
        return []

    monkeypatch.setattr(loop_mod, "_foreground_clickable_labels", slow_labels)
    brain = FakeBrain(script=['{"action": "done"}'])
    ctx = make_ctx(brain)
    ctx.vision_engine = SlowEngine()
    chunks = await run_loop(ctx, "mach das fenster zu bitte")

    assert chunks[-1].exit_code == 0
    assert set(starts) == {"observe", "labels"}
    # Both started within the same step tick — not after each other's sleep.
    assert abs(starts["observe"] - starts["labels"]) < 0.1


def test_ui_tree_source_is_built_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """_foreground_clickable_labels used to construct a fresh UI tree source
    on every step; ``_get_ui_tree_source`` must build once and reuse."""
    import jarvis.vision.tree_factory as tree_factory

    built = {"count": 0}

    class _FakeTreeSource:
        pass

    def fake_factory() -> Any:
        built["count"] += 1
        return _FakeTreeSource()

    monkeypatch.setattr(tree_factory, "make_ui_tree_source", fake_factory)
    monkeypatch.setattr(loop_mod, "_UI_TREE_SOURCE", None, raising=False)

    s1 = loop_mod._get_ui_tree_source()
    s2 = loop_mod._get_ui_tree_source()

    assert built["count"] == 1
    assert s1 is s2


async def test_verify_first_directive_after_state_changing_action() -> None:
    """With verification on, the executor turn AFTER a state-changing action
    must carry an explicit verify-first directive naming that action, so the
    model checks the fresh screenshot before acting again."""
    brain = JudgingBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        judge_script=['{"done": true, "proof": "chrome visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    assert len(brain.executor_calls) == 2
    _system, user = brain.executor_calls[1]
    assert "VERIFY FIRST" in user
    assert "open_app" in user
