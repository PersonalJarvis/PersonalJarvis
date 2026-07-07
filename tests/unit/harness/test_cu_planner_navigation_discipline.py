"""Planner discipline: a 'find X's posts/news on a site' goal must NAVIGATE,
not turn the goal's descriptive words into a literal site search.

Root cause (2026-06-15 20:54 live run, exit 5): the voice goal "open Chrome and
search for the news post from Elon Musk on X" made the plan-first planner emit a
5-step plan whose final step was *type 'news' and press Enter*. The planner had
lifted the descriptive word "news" straight out of the goal ("the **news** post
from Elon Musk") and treated it as a search query. After the agent had correctly
navigated toward Elon Musk's X profile -- where the goal was effectively
satisfied -- that spurious step drove it onto ``x.com/search?q=news``, the wrong
page. The completion judge correctly rejected that state, the model then wandered
through stray dialogs trying to recover, the done-reject budget was exhausted,
and the mission reported a false failure.

The decisive contrast: four minutes later the same target asked as "research
Elon Musk's profile, what was his latest post" produced a plan with NO literal
keyword-search step, landed on ``x.com/elonmusk``, and verified done on the first
try. The variable that flipped the outcome was the planner's spurious search
step.

The fix adds a NAVIGATION-vs-SEARCH discipline to the planner system prompt so a
content-retrieval goal is decomposed into navigating to the target page (and
stopping), never into typing one of the goal's descriptor words ('news',
'latest', 'post', ...) into the site search box. Legitimate searches (a goal
that names an explicit topic to search FOR) are untouched.

All tests drive ``_run_screenshot_loop`` directly with fakes -- no real
screenshots, no real UIA, no real model calls.
"""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.core.protocols import HarnessResult, HarnessTask, Observation
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.harness.screenshot_only_loop import _PLANNER_SYSTEM_PROMPT, _run_screenshot_loop

# ---------------------------------------------------------------------------
# Fakes (compact, self-contained mirror of test_cu_loop_robustness.py)
# ---------------------------------------------------------------------------


class FakeVisionEngine:
    """Fresh observation per call (unique hash so the no-progress guard never
    trips), configurable window-title sequence."""

    def __init__(self, window_titles: list[str] | None = None) -> None:
        self.calls = 0
        self._titles = window_titles

    def _guess_active_app_hint(self, window_title_filter: str | None = None) -> str:
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


class PlanningBrain:
    """Routes planner / judge / executor calls to separate scripts and records
    the exact (system, user) pair each one saw."""

    def __init__(self, executor_script: list[str],
                 plan_script: list[str] | None = None,
                 judge_script: list[str] | None = None) -> None:
        self.executor_script = list(executor_script)
        self.plan_script = list(plan_script or [])
        self.judge_script = list(judge_script or [])
        self.planner_calls: list[tuple[str, str]] = []
        self.judge_calls: list[tuple[str, str]] = []
        self.executor_calls: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
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


def make_ctx(brain: PlanningBrain, *, verify: bool = True,
             titles: list[str] | None = None,
             step_budget: int = 12) -> ComputerUseContext:
    tools = {
        name: FakeTool(name)
        for name in ("open_app", "click", "type_text", "hotkey",
                     "click_element", "scroll")
    }
    return ComputerUseContext(
        vision_engine=FakeVisionEngine(window_titles=titles),
        brain_manager=brain,
        tool_executor=FakeExecutor(),
        tools=tools,
        bus=None,
        step_budget=step_budget,
        per_step_timeout_s=5.0,
        verify_after_each_step=verify,
        announce_progress=False,
    )


async def run_loop(ctx: ComputerUseContext, goal: str) -> list[HarnessResult]:
    task = HarnessTask(prompt=goal, timeout_s=30)
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
    monkeypatch.setattr(loop_mod, "_OPEN_APP_SETTLE_TIMEOUT_S", 0.0)


# A clean navigation plan for the Elon-Musk content goal: navigate to the
# profile and stop -- no literal keyword search step (the post-fix shape).
_NAV_PLAN = (
    '{"plan": ['
    '{"intent": "open chrome", "success": "a chrome window is open"},'
    '{"intent": "go to the x.com/elonmusk profile in the address bar", '
    '"success": "the elon musk profile page is shown"}'
    ']}'
)

# The exact failing voice goal (transcribed 2026-06-15 20:54): a content-
# retrieval goal whose descriptor word ("news") the planner must NOT search for.
_ELON_GOAL = "open chrome and search for elon musk's latest news post on x"


# ---------------------------------------------------------------------------
# The discipline lives in the planner prompt
# ---------------------------------------------------------------------------


def test_planner_prompt_carries_navigation_vs_search_discipline() -> None:
    """Direct guard: the planner system prompt must teach NAVIGATION-vs-SEARCH
    so a 'find someone's posts/news' goal is not decomposed into typing a
    descriptor word into the site search box."""
    low = _PLANNER_SYSTEM_PROMPT.lower()
    assert "navigation vs search" in low
    assert "search box" in low
    assert "do not type" in low
    # The specific descriptor words from the recorded failure must be named.
    assert "'news'" in low
    assert "'latest'" in low
    assert "'post'" in low


def test_planner_discipline_avoids_judge_routing_keywords() -> None:
    """ANTI-MISROUTE GUARD: the planner prompt must not contain the words the
    test fakes (and the live loop's prompt-content routing) use to recognise
    the done-judge / fail-feasibility prompts -- otherwise a planner call would
    be misclassified. (Memory: 'prompt nudge must avoid words judge/feasibility'.)
    """
    low = _PLANNER_SYSTEM_PROMPT.lower()
    assert "judge" not in low
    assert "feasibility" not in low


async def test_planner_receives_discipline_for_content_retrieval_goal() -> None:
    """Wiring: the recorded goal triggers the planner, and the planner call
    actually carries the navigation-vs-search discipline (exercises the real
    ``_make_plan`` -> ``_call_brain`` path, not just the module constant)."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        plan_script=[_NAV_PLAN],
        judge_script=['{"done": true, "proof": "elon musk profile visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    await run_loop(ctx, _ELON_GOAL)

    assert brain.planner_calls, "planner was not consulted for a content goal"
    system, _user = brain.planner_calls[0]
    low = system.lower()
    assert "navigation vs search" in low
    assert "do not type" in low
    assert "search box" in low


# ---------------------------------------------------------------------------
# Regression guard: the working path (live run that SUCCEEDED) stays green
# ---------------------------------------------------------------------------


async def test_content_goal_completes_when_plan_navigates_to_profile() -> None:
    """The desired post-fix behavior, mirroring the live run that SUCCEEDED:
    a 'find X's posts on a site' goal whose plan NAVIGATES to the profile (no
    spurious keyword search) reaches verified done. Guards that the fix does
    not break the working navigation flow."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "type", "text": "https://x.com/elonmusk"}',
            '{"action": "done"}',
        ],
        plan_script=[_NAV_PLAN],
        judge_script=[
            '{"done": true, "proof": "x.com/elonmusk profile and latest post visible"}',
        ],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, _ELON_GOAL)

    assert chunks[-1].exit_code == 0
    assert "verified" in (chunks[-1].stdout or "")
    assert len(brain.planner_calls) == 1


# ---------------------------------------------------------------------------
# Desktop-app preference + adaptive (non-straitjacket) planning
#
# Live failure (2026-06-16): "Open Discord and then go on the exampleserver
# Discord and look for the newest news." CU opened Discord IN A BROWSER (web)
# although a Discord DESKTOP app is installed, then wandered and gave up. Two
# changes:
#   (a) the planner must PREFER launching the installed desktop app via the
#       ``open_app`` action over opening the same app inside a browser, when
#       the goal names an app that exists as a desktop application;
#   (b) the planner straitjacket (rigid "exactly 3-7 steps", "decompose
#       properly", "NEVER collapse") is relaxed so the fast-tier model can
#       reason about a normally-phrased goal and use adaptive/reactive steps,
#       while KEEPING the navigation-vs-search guard intact.
# ---------------------------------------------------------------------------

# A goal that names a desktop app (Discord) phrased the way a person actually
# talks -- no step-by-step hand-holding.
_DISCORD_GOAL = (
    "open discord and then go on the exampleserver discord and "
    "look for the newest news"
)

# The post-fix desired plan: launch the DESKTOP app first (open_app), not a
# browser, then operate inside it.
_DISCORD_DESKTOP_PLAN = (
    '{"plan": ['
    '{"intent": "open the discord desktop app", '
    '"success": "the discord window is shown"},'
    '{"intent": "open the exampleserver server", '
    '"success": "the exampleserver server is selected"},'
    '{"intent": "read the newest announcement", '
    '"success": "the latest post is visible"}'
    ']}'
)


def test_planner_prompt_prefers_desktop_app_over_browser() -> None:
    """Direct guard: the planner system prompt must instruct the model to
    PREFER the installed desktop app (the open_app action) over opening the
    same app inside a browser, so 'open Discord' launches the Discord desktop
    app rather than Discord web."""
    low = _PLANNER_SYSTEM_PROMPT.lower()
    # The preference must be expressed and tied to the open_app action.
    assert "desktop app" in low
    assert "open_app" in low
    # It must explicitly steer AWAY from the browser for a named app.
    assert "browser" in low


def test_planner_prompt_is_not_a_rigid_straitjacket() -> None:
    """The over-prescriptive mandates that prevent the fast-tier model from
    reasoning about a natural goal must be relaxed: no hard 'exactly 3-7
    steps' cap, no 'NEVER collapse' decomposition order. The model is allowed
    to plan adaptively / react step by step."""
    low = _PLANNER_SYSTEM_PROMPT.lower()
    # Adaptivity must be permitted explicitly.
    assert "adapt" in low or "react" in low or "re-plan" in low
    # The rigid hard caps must be gone (they were the straitjacket).
    assert "3-7 steps" not in low
    assert "never collapse" not in low


def test_planner_prompt_keeps_navigation_discipline_after_relax() -> None:
    """Relaxing the straitjacket must NOT regress the anti-literal-search
    guard -- the navigation-vs-search discipline stays."""
    low = _PLANNER_SYSTEM_PROMPT.lower()
    assert "navigation vs search" in low
    assert "search box" in low
    assert "do not type" in low


async def test_desktop_app_goal_plans_open_app_not_browser() -> None:
    """Wiring: a natural 'open Discord and ...' goal triggers the planner, and
    the executor launches the DESKTOP app via open_app -- never a browser. The
    very first state-changing action must be open_app(discord)."""
    executor = FakeExecutor()
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "discord"}',
            '{"action": "done"}',
        ],
        plan_script=[_DISCORD_DESKTOP_PLAN],
        judge_script=['{"done": true, "proof": "exampleserver newest post visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    ctx.tool_executor = executor  # capture the exact tool calls
    await run_loop(ctx, _DISCORD_GOAL)

    assert brain.planner_calls, "planner was not consulted for the desktop-app goal"
    # The planner call must carry the desktop-app preference.
    system, _user = brain.planner_calls[0]
    low = system.lower()
    assert "desktop app" in low
    assert "open_app" in low
    # The FIRST tool the loop executed must be open_app for discord -- not a
    # browser launch, not a navigation to discord.com.
    assert executor.calls, "no tool was executed"
    first_tool, first_args = executor.calls[0]
    assert first_tool == "open_app"
    assert "discord" in str(first_args.get("app_name", "")).lower()


# ---------------------------------------------------------------------------
# Give-up REASON surfaced in the final HarnessResult (supports the readback
# layer: speak the real reason, not a bare exit code).
#
# The downstream voice readback can only narrate WHY a CU mission gave up if
# the reason text is actually present in the final HarnessResult. These guards
# lock that contract: the model's verified ``fail`` reason and the done-reject
# evidence both reach the final chunk's stderr.
# ---------------------------------------------------------------------------


async def test_fail_reason_present_in_final_result() -> None:
    """A genuinely-justified ``fail`` (feasibility check agrees the goal is
    blocked) surfaces the on-screen reason in the final HarnessResult so the
    readback layer can speak it -- exit 5 must NOT be a bare code."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "fail", "reason": "a login wall blocks the page"}',
        ],
        plan_script=['{"plan": []}'],
        # The fail-feasibility check routes to the judge script (its prompt
        # contains 'judge'); agree the give-up is justified and hand back the
        # verified reason.
        judge_script=[
            '{"give_up": true, "reason": "a login dialog covers the whole window"}',
        ],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "open chrome and read my private feed")

    final = chunks[-1]
    assert final.exit_code == 5
    combined = (final.stderr or "") + (final.stdout or "")
    # The verified, human-readable reason must be present (not just "exit 5").
    assert "login" in combined.lower()


async def test_done_reject_evidence_present_in_final_result() -> None:
    """When every completion attempt is rejected, the final HarnessResult must
    carry the judge's last evidence so the readback can explain WHY, instead of
    only a bare failure code."""
    brain = PlanningBrain(
        # The model keeps claiming done; the judge keeps rejecting with proof.
        executor_script=['{"action": "done"}'] * 4,
        plan_script=['{"plan": []}'],
        judge_script=[
            '{"done": false, "proof": "the chrome window is not open yet"}',
        ] * 4,
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "open chrome")

    final = chunks[-1]
    assert final.exit_code == 5
    combined = (final.stderr or "") + (final.stdout or "")
    # The judge's evidence string must reach the final result.
    assert "not open yet" in combined.lower()
