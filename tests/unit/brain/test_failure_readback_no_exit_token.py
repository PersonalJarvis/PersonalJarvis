"""A FAILED action must never speak a raw machine token like "exit 1".

Live forensic 2026-06-28 (voice session 16:57): the user asked for a sub-agent
to write an HTML file; a garbled follow-up turn dispatched a harness that failed
with a non-zero exit code, and Jarvis spoke the single word-pair "exit 1" — the
opaque ``ToolResult.error`` (``dispatch_to_harness`` emits ``f"exit {code}"``)
returned VERBATIM to the user.

Root cause: ``BrainManager._run_local_action_fast_path`` (and the spawn /
leaked-tool recovery paths) returned ``result.error`` directly as the spoken
text. The Computer-Use path was already protected via
``cu_failure_readback``; these action / spawn paths were not.

Contract (the maintainer's directive, all supported languages — de/en/es):
when an action fails or cannot be made sense of, Jarvis communicates that
intelligently — never a bare ``exit N``, never a hardcoded German string on an
English/Spanish turn, never an empty/silent drop.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from jarvis.brain.local_action_gate import (
    LocalActionMode,
    LocalActionPlan,
    LocalToolCall,
)
from jarvis.brain.manager import BrainManager
from jarvis.voice.action_phrases import extract_speakable_reason

_BARE_EXIT = re.compile(r"\bexit\s*-?\d", re.IGNORECASE)


def _has_letters(text: str) -> bool:
    return bool(re.search(r"[A-Za-zÀ-ÿ]", text or ""))


# ---------------------------------------------------------------------------
# extract_speakable_reason — the opaque-token gate (pure, no LLM)
# ---------------------------------------------------------------------------


def test_extract_reason_rejects_bare_exit_token() -> None:
    assert extract_speakable_reason("exit 1", None) is None
    assert extract_speakable_reason("exit 137", {}) is None
    assert extract_speakable_reason("(exit 2)", None) is None


def test_extract_reason_rejects_empty_and_numeric() -> None:
    assert extract_speakable_reason(None, None) is None
    assert extract_speakable_reason("", None) is None
    assert extract_speakable_reason("   ", None) is None
    assert extract_speakable_reason("255", None) is None


def test_extract_reason_forwards_human_stderr_from_output_dict() -> None:
    out = {"harness": "screenshot", "exit_code": 1, "stderr": "Permission denied", "stdout": ""}
    reason = extract_speakable_reason("exit 1", out)
    assert reason is not None
    assert "Permission denied" in reason
    assert _BARE_EXIT.search(reason) is None


def test_extract_reason_forwards_human_error_string() -> None:
    reason = extract_speakable_reason("The file could not be written", None)
    assert reason == "The file could not be written"


def test_extract_reason_rejects_diagnostic_noise() -> None:
    # A "[cu] mission profile" telemetry dump is not a human reason.
    out = {"stderr": "[cu] mission profile: steps=3 total=9.5s act=3.0s", "exit_code": 5}
    assert extract_speakable_reason("exit 5", out) is None


# ---------------------------------------------------------------------------
# DIRECT local-action fast path — the path that fired in the transcript
# ---------------------------------------------------------------------------


class _FailingExecutor:
    """tool_executor whose tool fails with the opaque ``exit N`` token."""

    def __init__(self, *, error: str = "exit 1", output=None) -> None:
        self._error = error
        self._output = output

    async def execute(self, tool, args, *, user_utterance, trace_id):  # noqa: ANN001
        return SimpleNamespace(success=False, output=self._output, error=self._error)


class _FakeBus:
    async def publish(self, event) -> None:  # noqa: ANN001
        pass


def _make_manager(executor, *, reply_language: str = "auto") -> BrainManager:
    mgr = BrainManager.__new__(BrainManager)
    mgr._config = SimpleNamespace(
        local_action=SimpleNamespace(
            enabled=True, harness_timeout_s=30.0, direct_timeout_s=3.0
        )
    )
    mgr._bus = _FakeBus()
    mgr._tool_executor = executor
    mgr._local_action_tools = {"open_app": object()}
    mgr._cost_meter = None
    mgr._reply_language = reply_language
    mgr._turn_detected_lang = ""
    # No _readback_composer attribute -> render_readback uses the canned fallback,
    # so these assertions test the DETERMINISTIC fallback (the worst case).
    return mgr


def _direct_plan(_text: str) -> LocalActionPlan:
    return LocalActionPlan(
        mode=LocalActionMode.DIRECT,
        tool_calls=(LocalToolCall(name="open_app", args={"app_name": "x"}),),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", ["de", "en", "es", "auto"])
async def test_direct_failure_never_speaks_exit_token(monkeypatch, pin) -> None:
    mgr = _make_manager(_FailingExecutor(error="exit 1"), reply_language=pin)
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", _direct_plan)

    reply = await mgr._run_local_action_fast_path("do the thing")

    assert reply is not None and reply.strip(), "a failure must not be silent"
    assert _BARE_EXIT.search(reply) is None, f"raw exit token leaked: {reply!r}"
    assert _has_letters(reply), f"reply is not a real sentence: {reply!r}"


@pytest.mark.asyncio
async def test_direct_failure_forwards_human_reason(monkeypatch) -> None:
    out = {"exit_code": 1, "stderr": "Disk is full", "stdout": ""}
    mgr = _make_manager(_FailingExecutor(error="exit 1", output=out), reply_language="en")
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", _direct_plan)

    reply = await mgr._run_local_action_fast_path("do the thing")

    assert "Disk is full" in reply, f"human reason dropped: {reply!r}"
    assert _BARE_EXIT.search(reply) is None


@pytest.mark.asyncio
async def test_english_pin_failure_is_not_hardcoded_german(monkeypatch) -> None:
    mgr = _make_manager(_FailingExecutor(error="exit 1"), reply_language="en")
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", _direct_plan)

    reply = await mgr._run_local_action_fast_path("do the thing")

    # The historical German hardcoded fallbacks ("nicht geklappt", "Aktion")  # i18n-allow
    # must not surface on an English-pinned turn.
    assert "geklappt" not in reply.lower()
    assert "konnte nicht" not in reply.lower()  # i18n-allow


# ---------------------------------------------------------------------------
# _honest_failure_readback — shared helper behind every spawn / tool path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["de", "en", "es"])
async def test_honest_failure_readback_generic_all_languages(lang) -> None:
    mgr = BrainManager.__new__(BrainManager)
    mgr._reply_language = lang
    result = SimpleNamespace(success=False, output=None, error="exit 1")

    reply = await mgr._honest_failure_readback(
        result,
        user_text="do it",
        situation="The action could not be completed.",
        generic_key="action_failed_generic",
        reason_key="action_failed_reason",
        lang=lang,
    )

    assert reply.strip()
    assert _BARE_EXIT.search(reply) is None
    assert _has_letters(reply)


@pytest.mark.asyncio
async def test_honest_failure_readback_with_reason() -> None:
    mgr = BrainManager.__new__(BrainManager)
    mgr._reply_language = "en"
    result = SimpleNamespace(
        success=False,
        output={"exit_code": 1, "stderr": "Network unreachable"},
        error="exit 1",
    )

    reply = await mgr._honest_failure_readback(
        result,
        user_text="do it",
        situation="The background helper could not be started.",
        generic_key="spawn_failed_generic",
        reason_key="spawn_failed_reason",
        lang="en",
    )

    assert "Network unreachable" in reply
    assert _BARE_EXIT.search(reply) is None
