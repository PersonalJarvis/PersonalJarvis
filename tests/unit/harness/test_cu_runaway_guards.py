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

import json
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


# ---------------------------------------------------------------------------
# Toggle-stop vs. dropdown navigation (live failure 2026-06-17)
# ---------------------------------------------------------------------------


def _click_sequence_handler(actions: list[dict[str, Any]]):
    """Brain shim: serve a trivial plan, then walk an action sequence.

    A suppressed (toggle-stopped) click still consumes one brain turn but
    leaves no executor call, so the sequence index advances on every
    non-planner turn regardless of whether the click executed.
    """
    seq = {"i": 0}

    def handler(system: str, user: str) -> str:
        if "desktop-automation planner" in system:
            return '{"plan": []}'
        i = seq["i"]
        seq["i"] += 1
        return json.dumps(actions[min(i, len(actions) - 1)])

    return handler


async def test_distinct_dropdown_rows_are_not_suppressed_as_toggle_thrash() -> None:
    # Live failure 2026-06-17 (Energieoptionen mission): the model navigated a
    # vertically stacked dropdown, clicking four DIFFERENT rows that share an x
    # and sit a dropdown-row apart in y (711,378 / 405 / 341 / 365). The 4th —
    # a brand-new target — was wrongly suppressed as a "toggle-thrash" because
    # it fell within the coarse tolerance of two earlier, DIFFERENT rows. With
    # no click executed the screen froze and the no-progress guard aborted with
    # "3 identical screenshots". Distinct rows must each execute; only a genuine
    # repeat of the SAME point is a toggle.
    actions = [
        {"action": "click", "x": 711, "y": 378, "target": "row A"},
        {"action": "click", "x": 711, "y": 405, "target": "row B"},
        {"action": "click", "x": 711, "y": 341, "target": "row C"},
        {"action": "click", "x": 711, "y": 365, "target": "Nie"},
        {"action": "done"},
    ]
    ctx = make_ctx(FakeBrain(_click_sequence_handler(actions)))

    chunks = await run_loop(ctx, "change the screen timeout dropdown to never")

    clicks = [args for (name, args) in ctx.tool_executor.calls if name == "click"]
    assert len(clicks) == 4, (
        f"all 4 distinct dropdown rows must execute, got {len(clicks)} "
        "(the 4th was wrongly suppressed as a toggle-thrash)"
    )
    assert chunks[-1].exit_code == 0


async def test_identical_point_repeat_is_still_suppressed_as_toggle() -> None:
    # Regression guard for the fix above: a GENUINE toggle-thrash — the exact
    # same point clicked over and over (a play/pause button that flips the icon
    # so the no-progress hash guard never trips) — must still be stopped. The
    # 1st and 2nd clicks execute; the 3rd identical click is suppressed.
    actions = [
        {"action": "click", "x": 500, "y": 500, "target": "play"},
        {"action": "click", "x": 500, "y": 500, "target": "play"},
        {"action": "click", "x": 500, "y": 500, "target": "play"},
        {"action": "done"},
    ]
    ctx = make_ctx(FakeBrain(_click_sequence_handler(actions)))

    await run_loop(ctx, "scroll the list down")

    clicks = [args for (name, args) in ctx.tool_executor.calls if name == "click"]
    assert len(clicks) == 2, (
        f"the 3rd identical click must be suppressed as a toggle, got "
        f"{len(clicks)} executed clicks"
    )


# ---------------------------------------------------------------------------
# No-progress abort: honest cause attribution (live failure 2026-06-17)
# ---------------------------------------------------------------------------


class _SequencedVisionEngine:
    """Vision engine with a scripted screenshot-hash sequence.

    The first ``distinct`` frames carry unique hashes; every frame after that
    is the same frozen hash, so the no-progress guard trips a few steps in
    (after any earlier clicks had a chance to be guard-suppressed).
    """

    def __init__(self, distinct: int) -> None:
        self.calls = 0
        self.distinct = distinct

    async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                      window_title_filter: str | None = None) -> Observation:
        idx = self.calls
        self.calls += 1
        h = f"frame-{idx}" if idx < self.distinct else "frozen"
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash=h,
            nodes=(),
            window_title="",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={},
        )


def make_ctx_with_engine(brain: FakeBrain, engine: Any) -> ComputerUseContext:
    ctx = make_ctx(brain)
    ctx.vision_engine = engine
    return ctx


async def test_no_progress_message_blames_guard_when_actions_were_suppressed() -> None:
    # Live failure 2026-06-17: the screen froze because the toggle guard kept
    # SUPPRESSING the model's clicks (nothing was executed), yet the abort
    # message claimed "the click target is unreactive or off-screen" — a
    # misdiagnosis. When guard-blocked actions preceded the freeze, the
    # message must attribute the stall to the suppression, not to a dead target.
    brain = FakeBrain(_click_sequence_handler(
        [{"action": "click", "x": 500, "y": 500, "target": "play"}] * 8
    ))
    ctx = make_ctx_with_engine(brain, _SequencedVisionEngine(distinct=2))

    chunks = await run_loop(ctx, "scroll the list down")

    final = chunks[-1]
    assert final.exit_code == loop_mod._FAIL_EXIT_CODE
    assert "no progress" in final.stderr
    assert "suppressed" in final.stderr, (
        f"the abort must name the guard suppression, got: {final.stderr!r}"
    )
    assert "off-screen" not in final.stderr


async def test_no_progress_message_blames_dead_target_when_nothing_suppressed() -> None:
    # The honest counterpart: when NO action was guard-blocked and the screen
    # is simply frozen (clicks land on dead space), the original "unreactive or
    # off-screen" diagnosis is correct and must be preserved.
    actions = [
        {"action": "click", "x": 100, "y": 100, "target": "a"},
        {"action": "click", "x": 900, "y": 900, "target": "b"},
        {"action": "click", "x": 100, "y": 900, "target": "c"},
        {"action": "click", "x": 900, "y": 100, "target": "d"},
    ]
    brain = FakeBrain(_click_sequence_handler(actions))
    ctx = make_ctx_with_engine(brain, _SequencedVisionEngine(distinct=0))

    chunks = await run_loop(ctx, "scroll the list down")

    final = chunks[-1]
    assert final.exit_code == loop_mod._FAIL_EXIT_CODE
    assert "no progress" in final.stderr
    assert "off-screen" in final.stderr
    assert "suppressed" not in final.stderr


# ---------------------------------------------------------------------------
# No-progress abort: verify BEFORE failing (user forensic 2026-06-20)
# ---------------------------------------------------------------------------
# A fully-loaded page is static, so the very thing a SUCCESS looks like —
# three byte-identical screenshots — is indistinguishable, by hash alone,
# from a stuck session. Live forensic: "open x.com and the Angela Merkel
# profile" actually reached the open profile page, but the frozen screen
# tripped the no-progress guard and the mission was reported FAILED. Before
# aborting, the loop must run the done-verifier ONCE against the current
# screenshot: a proven goal ends as a verified success, not a stuck failure.


def _verify_at_freeze_handler(*, verdict_done: bool) -> Any:
    """Brain shim: trivial plan, repeated click, and a scripted completion
    verdict whenever a STRICT-judge prompt arrives (its unique marker is the
    verdict format ``"done": true|false``, which never appears in the
    executor system prompt)."""

    proof = (
        "the @AngelaMerkel profile page is open on screen"
        if verdict_done
        else "only an empty page frame is visible, no profile loaded"
    )

    def handler(system: str, user: str) -> str:
        if "desktop-automation planner" in system:
            return '{"plan": []}'
        if '"done": true|false' in system:  # any completion judge
            return json.dumps({"done": verdict_done, "proof": proof})
        return json.dumps(
            {"action": "click", "x": 500, "y": 500, "target": "page"}
        )

    return handler


async def test_no_progress_at_an_achieved_goal_verifies_as_success() -> None:
    # The fix: when the screen freezes but the verifier confirms the goal IS
    # achieved on the current screenshot, the mission ends as a clean success
    # (exit 0 with the proof), NOT a "3 identical screenshots" failure.
    brain = FakeBrain(_verify_at_freeze_handler(verdict_done=True))
    ctx = make_ctx_with_engine(brain, _SequencedVisionEngine(distinct=2))

    chunks = await run_loop(ctx, "navigate to the angela merkel profile on x.com")

    final = chunks[-1]
    assert final.exit_code == 0, (
        f"a frozen screen AT the achieved goal must verify as success, not "
        f"abort as stuck; got exit {final.exit_code}: {final.stderr!r}"
    )
    assert "no progress" not in final.stderr
    assert "done" in final.stdout.lower()


async def test_no_progress_still_fails_when_verifier_rejects_the_goal() -> None:
    # The other half of the contract: a frozen screen where the goal is NOT
    # achieved (verifier says done:false) must still end as an honest failure.
    # The verify-before-fail gate may only RESCUE a real success, never paper
    # over a genuine stall.
    brain = FakeBrain(_verify_at_freeze_handler(verdict_done=False))
    ctx = make_ctx_with_engine(brain, _SequencedVisionEngine(distinct=2))

    chunks = await run_loop(ctx, "navigate to the angela merkel profile on x.com")

    final = chunks[-1]
    assert final.exit_code == loop_mod._FAIL_EXIT_CODE
    assert "no progress" in final.stderr


async def test_no_progress_does_not_rescue_a_frozen_play_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Safety guard for the verify-before-fail gate: a GENUINE play/submit stall
    # must NOT be falsely rescued. For a media goal (matches _VERIFY_GOAL_RE)
    # the done-verifier uses the two-frame MOTION check; on a frozen screen
    # Frame A and Frame B are byte-identical, so it short-circuits to
    # (False, "screen frozen ...") WITHOUT ever asking the judge. So even though
    # this handler would answer the judge "done", that judge call never happens
    # and the mission still ends as an honest failure. This pins the structural
    # reason a real play-stall cannot be rescued by the new gate.
    monkeypatch.setattr(loop_mod, "_VERIFY_FRAME_GAP_S", 0.0, raising=False)
    brain = FakeBrain(_verify_at_freeze_handler(verdict_done=True))
    ctx = make_ctx_with_engine(brain, _SequencedVisionEngine(distinct=2))

    chunks = await run_loop(ctx, "play the angela merkel video on x.com")

    final = chunks[-1]
    assert final.exit_code == loop_mod._FAIL_EXIT_CODE, (
        f"a frozen PLAY goal must stay a failure (two-frame motion gate), got "
        f"exit {final.exit_code}: {final.stdout!r}"
    )
    assert "no progress" in final.stderr
