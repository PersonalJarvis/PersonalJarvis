"""The Computer-Use verifier writes its ``proof`` in the turn's output language.

Live bug 2026-06-27 (German voice session 07:47): "öffne meinen Explorer und  # i18n-allow: verbatim forensic quote of the live German voice utterance under test
navigiere zum Videos-Ordner" was read back as
"Erledigt — The file explorer window is open and navigated to the 'Videos'
folder, as shown" — a German frame ("Erledigt —") around an ENGLISH body. The
body is the verifier's ``proof``: the verifier LLM never learned the
conversation language, so it defaulted to English (the language of its own
system prompt). The German→English mismatch (and the symmetric EN/ES cases) is
a Runtime-Output-Language doctrine violation — an embedded LLM observation that
ignores the one resolved output language.

Fix: the turn's resolved output language (the ``brain.reply_language`` pin +
conversation stickiness, de/en/es) is threaded via ``HarnessTask.env`` into the
loop, and the verifier ``user_message`` is told to write ``proof`` in that
language. The spoken frame and the embedded body then share ONE language source.

All tests drive ``_run_screenshot_loop`` directly with fakes — no real
screenshots, no real UIA, no real model calls (mirrors
``test_cu_read_informational_goal.py``).
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
    _OUTPUT_LANGUAGE_ENV_KEY,
    _proof_language_directive,
    _run_screenshot_loop,
)

# ---------------------------------------------------------------------------
# Fakes (compact mirror of test_cu_read_informational_goal.py)
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


async def run_loop(ctx: ComputerUseContext, goal: str, *,
                   output_language: str | None = None) -> list[HarnessResult]:
    env = {_OUTPUT_LANGUAGE_ENV_KEY: output_language} if output_language else {}
    task = HarnessTask(prompt=goal, timeout_s=30, env=env)
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


# ---------------------------------------------------------------------------
# 1. The directive builder is a pure de/en/es lookup
# ---------------------------------------------------------------------------


def test_proof_language_directive_names_each_language() -> None:
    assert "german" in _proof_language_directive("de").lower()
    assert "english" in _proof_language_directive("en").lower()
    assert "spanish" in _proof_language_directive("es").lower()


def test_proof_language_directive_mentions_proof_field() -> None:
    # The directive must steer the `proof` field specifically — that is the only
    # part of the verdict that gets spoken back.
    assert "proof" in _proof_language_directive("de").lower()


def test_proof_language_directive_empty_for_none_or_unknown() -> None:
    # No resolved language (tests / minimal wiring) → no language coercion, the
    # historical behaviour. An unknown locale is treated the same way.
    assert _proof_language_directive(None) == ""
    assert _proof_language_directive("") == ""
    assert _proof_language_directive("fr") == ""


# ---------------------------------------------------------------------------
# 2. The verifier user_message carries the directive for the turn's language
# ---------------------------------------------------------------------------


def _judge_user_messages(brain: PlanningBrain) -> str:
    return "\n".join(user for _system, user in brain.judge_calls).lower()


async def test_generic_verifier_user_message_carries_german_directive() -> None:
    """A German turn → the generic verifier is told to write proof in German."""
    brain = PlanningBrain(
        executor_script=['{"action": "open_app", "name": "explorer"}',
                         '{"action": "done"}'],
        plan_script=['{"plan": []}'],
        judge_script=['{"done": true, "proof": "Der Explorer ist offen"}'],  # i18n-allow: simulated German runtime-output proof, content under test
    )
    ctx = make_ctx(brain, verify=True, titles=["Datei-Explorer"] * 5)
    await run_loop(ctx, "öffne den datei explorer", output_language="de")  # i18n-allow: simulated German user utterance, content under test

    assert brain.judge_calls, "the verifier was never consulted"
    assert "german" in _judge_user_messages(brain)


async def test_generic_verifier_user_message_carries_english_directive() -> None:
    brain = PlanningBrain(
        executor_script=['{"action": "open_app", "name": "explorer"}',
                         '{"action": "done"}'],
        plan_script=['{"plan": []}'],
        judge_script=['{"done": true, "proof": "The explorer is open"}'],
    )
    # A neutral title that does NOT contain the goal's app token, so the
    # deterministic open-app fast path is skipped and the LLM verifier runs.
    ctx = make_ctx(brain, verify=True, titles=["Workspace"] * 5)
    await run_loop(ctx, "open the file explorer", output_language="en")

    assert brain.judge_calls, "the verifier was never consulted"
    assert "english" in _judge_user_messages(brain)


async def test_read_verifier_user_message_carries_spanish_directive() -> None:
    """A read goal pinned to Spanish → the READ verifier writes proof in Spanish."""
    brain = PlanningBrain(
        executor_script=['{"action": "open_app", "name": "discord"}',
                         '{"action": "scroll", "direction": "down"}',
                         '{"action": "done"}'],
        plan_script=['{"plan": []}'],
        judge_script=['{"done": true, "proof": "los mensajes mas recientes ..."}'],
    )
    ctx = make_ctx(brain, verify=True, titles=["Discord"] * 5)
    await run_loop(
        ctx, "open discord and tell me what the newest messages say",
        output_language="es",
    )

    assert brain.judge_calls, "the verifier was never consulted"
    assert "spanish" in _judge_user_messages(brain)


async def test_no_output_language_leaves_verifier_unchanged() -> None:
    """Without a resolved language (no env) the verifier prompt is unchanged —
    no language coercion is injected (backward-compatible default)."""
    brain = PlanningBrain(
        executor_script=['{"action": "open_app", "name": "explorer"}',
                         '{"action": "done"}'],
        plan_script=['{"plan": []}'],
        judge_script=['{"done": true, "proof": "the explorer is open"}'],
    )
    # Neutral title (see English test) so the LLM verifier runs, not the fast path.
    ctx = make_ctx(brain, verify=True, titles=["Workspace"] * 5)
    await run_loop(ctx, "open the file explorer")  # no output_language

    assert brain.judge_calls, "the verifier was never consulted"
    joined = _judge_user_messages(brain)
    for marker in ("german", "english", "spanish"):
        assert marker not in joined, f"unexpected language directive: {marker!r}"
