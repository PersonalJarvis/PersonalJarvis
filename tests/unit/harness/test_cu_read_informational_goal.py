"""Informational-goal discipline: a 'open X and TELL ME what's in it' goal must
SCROLL to the newest content, READ it, and only report done once the actual
content is on screen -- not the instant the app window is open.

Root cause (2026-06-19 12:27 live run, session 12:28): the voice goal "open my
Discord and tell me exactly what's going on in the current BridgeMind channels"
drove an 8-step Computer-Use mission that opened Discord, navigated to the
BridgeMind server / #general channel, closed a Nitro popup, and emitted ``done``
-- with ZERO scroll actions. The generic single-frame verifier rubber-stamped
"The Discord window is open, showing the 'BridgeMind' server and the '# general'"
and the mission was spoken back as complete. The user never learned what the
newest messages actually said: an INFORMATIONAL request can never be answered if
the loop treats "app is visibly open" as the whole goal.

The fix introduces a READ goal class: such a goal (a) gets a READING-discipline
block in the executor system prompt (scroll to the newest messages, do not
declare done on a bare open app), (b) is judged by a stricter READ verifier that
demands the proof QUOTE the actual on-screen content (an open/empty/unread view
is not enough), (c) skips the deterministic "open <app>" window-title fast path
so the content check always runs, (d) on a done-reject is told to scroll down and
read, and (e) carries its proof through to stdout uncut so the verifier's
observation (the message content) is what the readback layer speaks.

All tests drive ``_run_screenshot_loop`` directly with fakes -- no real
screenshots, no real UIA, no real model calls. Fakes mirror
test_cu_planner_navigation_discipline.py.
"""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.core.protocols import HarnessResult, HarnessTask, Observation
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.harness.screenshot_only_loop import (
    _READ_DISCIPLINE_BLOCK,
    _READ_VERIFIER_SYSTEM_PROMPT,
    _goal_needs_reading,
    _run_screenshot_loop,
)

# ---------------------------------------------------------------------------
# Fakes (compact mirror of test_cu_planner_navigation_discipline.py)
# ---------------------------------------------------------------------------


class FakeVisionEngine:
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


# The exact failing voice goal (transcribed 2026-06-19 12:27), lightly
# normalised the way the dispatcher hands a prompt to the loop.
_DISCORD_READ_GOAL = (
    "open discord and tell me exactly what's going on in the bridgemind channels"
)


# ---------------------------------------------------------------------------
# 1. The classifier: informational vs pure-action goals
# ---------------------------------------------------------------------------


def test_goal_needs_reading_detects_informational_goals() -> None:
    informational = [
        "öffne discord und sag mir was in den bridgemind channels abgeht",
        "open discord and tell me what's going on in the channels",
        "lies mir die neuesten nachrichten vor",
        "was steht in meinem discord",
        "zeig mir die neuesten nachrichten auf dem bridgemind server",
        "what's happening on the bridgemind discord",
        "fasse mir die letzten nachrichten zusammen",
        "check what's new in slack",
        "open discord and look for the newest news",
        "what are the latest messages in general",
    ]
    for goal in informational:
        assert _goal_needs_reading(goal) is True, goal


def test_goal_needs_reading_ignores_pure_action_goals() -> None:
    actions = [
        "öffne discord",
        "open chrome",
        "spiel ein lied auf spotify",
        "rechne 8 mal 8",
        "mach einen screenshot",
        "klick auf den senden button",
        # "sag ihm/ihr" is DICTATION, not a read-to-me request
        "schreib tom eine nachricht und sag ihm dass ich später komme",
    ]
    for goal in actions:
        assert _goal_needs_reading(goal) is False, goal


# ---------------------------------------------------------------------------
# 2. The READ verifier prompt demands content, not a bare open app
# ---------------------------------------------------------------------------


def test_read_verifier_prompt_demands_content_not_just_open() -> None:
    low = _READ_VERIFIER_SYSTEM_PROMPT.lower()
    # Routing: the loop + test fakes recognise a verifier prompt by "judge".
    assert "judge" in low
    # Anti-misroute: must not be mistaken for the planner / fail-feasibility prompts.
    assert "planner" not in low
    assert "feasibility" not in low
    # The discipline: open/visible app is not enough; the proof must be content.
    assert "open" in low
    assert ("not enough" in low or "not sufficient" in low)
    assert ("content" in low or "message" in low)


def test_read_discipline_block_mandates_scroll_to_newest() -> None:
    low = _READ_DISCIPLINE_BLOCK.lower()
    assert "scroll" in low
    assert ("newest" in low or "bottom" in low or "latest" in low)
    # It must forbid declaring done on a bare-open app.
    assert "done" in low


# ---------------------------------------------------------------------------
# 3. Wiring: a read goal is judged by the READ verifier (not the generic one)
# ---------------------------------------------------------------------------


async def test_informational_goal_uses_read_verifier() -> None:
    """The done-gate for a read goal must consult the READ verifier, whose
    prompt is about a 'reading' task -- proving the content check (not the
    generic open-window check) ran."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "discord"}',
            '{"action": "scroll", "direction": "down"}',
            '{"action": "done"}',
        ],
        plan_script=['{"plan": []}'],
        judge_script=[
            '{"done": true, "proof": "newest messages in #general: '
            'Alice: deploy is green, Bob: shipping now"}',
        ],
    )
    ctx = make_ctx(brain, verify=True, titles=["Discord"] * 5)
    chunks = await run_loop(ctx, _DISCORD_READ_GOAL)

    assert brain.judge_calls, "the verifier was never consulted"
    assert any("reading" in system.lower() for system, _u in brain.judge_calls), (
        "a read goal must be judged by the READ verifier, not the generic one"
    )
    assert chunks[-1].exit_code == 0


# ---------------------------------------------------------------------------
# 4. The READ goal skips the deterministic open-app fast path
# ---------------------------------------------------------------------------


async def test_read_goal_skips_open_app_title_fast_path() -> None:
    """A read goal whose app is already in the foreground title must STILL run
    the content verifier -- the 'window title proves the app is open' shortcut
    (which never reads content) must not satisfy an informational request."""
    # The 4x repeats bound the loop via the done-reject budget (_MAX_DONE_REJECTS
    # = 3): on the 4th rejected done the mission ends as a verified failure. If
    # that constant is ever raised, add matching script entries.
    brain = PlanningBrain(
        executor_script=['{"action": "done"}'] * 4,
        plan_script=['{"plan": []}'],
        # The READ verifier refuses: only the open window is visible, no content.
        judge_script=[
            '{"done": false, "proof": "only the discord window is open, '
            'no messages are visible yet"}',
        ]
        * 4,
    )
    # 'discord' is in the foreground title for every frame: the open-app fast
    # path WOULD fire here if it were not skipped for read goals.
    ctx = make_ctx(brain, verify=True, titles=["Discord"] * 8)
    chunks = await run_loop(ctx, _DISCORD_READ_GOAL)

    # The verifier ran (the fast path was skipped) and kept rejecting the
    # content-free "done", so the mission ends as a verified failure, never a
    # false success off the window title.
    assert brain.judge_calls, "fast path fired -- the content verifier never ran"
    final = chunks[-1]
    assert final.exit_code != 0
    combined = (final.stderr or "") + (final.stdout or "")
    assert "no messages" in combined.lower()


# ---------------------------------------------------------------------------
# 5. The message content reaches stdout UNCUT (so the readback speaks it)
# ---------------------------------------------------------------------------


async def test_read_goal_surfaces_full_message_content_in_stdout() -> None:
    """On success the verifier's observation (the actual messages) must reach
    stdout without the 80-char truncation the action path uses, so the readback
    layer can speak the content the user asked for -- not a clipped fragment."""
    proof = (
        "newest messages in #general: Alice: the deploy is finally green; "
        "Bob: shipping the v2 release now; Carol: thanks everyone, see you tomorrow"
    )
    assert len(proof) > 120  # longer than the action-path 80-char cap
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "discord"}',
            '{"action": "scroll", "direction": "down"}',
            '{"action": "done"}',
        ],
        plan_script=['{"plan": []}'],
        judge_script=['{"done": true, "proof": "' + proof + '"}'],
    )
    ctx = make_ctx(brain, verify=True, titles=["Discord"] * 5)
    chunks = await run_loop(ctx, _DISCORD_READ_GOAL)

    final = chunks[-1]
    assert final.exit_code == 0
    out = final.stdout or ""
    # The tail of the observation (past 80 chars) must survive for the readback.
    assert "see you tomorrow" in out
    assert "shipping the v2 release now" in out


# ---------------------------------------------------------------------------
# 6. A premature done (content not yet read) is rejected with a scroll hint
# ---------------------------------------------------------------------------


async def test_premature_done_on_read_goal_is_told_to_scroll() -> None:
    """When the model declares done before any content is on screen, the
    rejection fed back into history must steer it to SCROLL to the newest
    messages -- not the generic 'pick another action' nudge."""
    captured: list[str] = []
    brain = PlanningBrain(
        # 1st done is premature; after the scroll the 2nd done is accepted.
        executor_script=[
            '{"action": "done"}',
            '{"action": "scroll", "direction": "down"}',
            '{"action": "done"}',
        ],
        plan_script=['{"plan": []}'],
        judge_script=[
            '{"done": false, "proof": "the channel is open but no messages '
            'are rendered yet"}',
            '{"done": true, "proof": "newest messages: Dana: standup at 10, '
            'Eli: PR merged"}',
        ],
    )

    orig = brain.complete_text

    async def _spy(*, system: str, user: str) -> str:
        captured.append(f"{system}\n{user}")
        return await orig(system=system, user=user)

    brain.complete_text = _spy  # type: ignore[method-assign]

    ctx = make_ctx(brain, verify=True, titles=["Discord"] * 6)
    chunks = await run_loop(ctx, _DISCORD_READ_GOAL)

    assert chunks[-1].exit_code == 0
    # After the rejected done, an executor turn must have been told to scroll.
    joined = "\n".join(captured).lower()
    assert "scroll" in joined


# ---------------------------------------------------------------------------
# 7. A goal with BOTH a read verb and a submit verb is still a read goal, and
#    the pending-verify early-termination path also carries the full content.
# ---------------------------------------------------------------------------


def test_goal_needs_reading_wins_over_compound_submit_verb() -> None:
    """A goal that pairs a READ verb with a send/submit verb (which arms the
    pending-verify path) is still classified as a read goal -- the content the
    user asked for must be reported even though the goal also acts."""
    assert _goal_needs_reading(
        "tell me what's in the slack general channel and send me a summary"
    ) is True


async def test_read_and_submit_goal_surfaces_full_content_via_pending_verify() -> None:
    """A read+submit goal fires the pending-verify early-termination path after a
    state-changing click. That path must ALSO carry the read verifier's full
    observation to stdout (not clip it to 80 chars), or the readback speaks a
    truncated fragment instead of the content the user asked for."""
    proof = (
        "newest messages in #general: Alice: the deploy is finally green; "
        "Bob: shipping the v2 release now; Carol: thanks everyone, see you tomorrow"
    )
    assert len(proof) > 120  # longer than the action-path 80-char cap
    brain = PlanningBrain(
        # The click (a state change on a verification goal) arms pending_verify;
        # the next step's pending-verify block consults the verifier and ends.
        executor_script=[
            '{"action": "click", "x": 500, "y": 500, "target": "general channel"}',
        ],
        plan_script=['{"plan": []}'],
        judge_script=['{"done": true, "proof": "' + proof + '"}'],
    )
    ctx = make_ctx(brain, verify=True, titles=["Slack"] * 5)
    chunks = await run_loop(
        ctx, "tell me what's in the slack general channel and send me a summary"
    )

    final = chunks[-1]
    assert final.exit_code == 0
    out = final.stdout or ""
    assert "see you tomorrow" in out
    assert "shipping the v2 release now" in out
