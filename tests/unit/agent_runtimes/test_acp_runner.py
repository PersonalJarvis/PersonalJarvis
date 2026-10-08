"""An ACP runtime turn through the shared CLI pump, against a scripted agent."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from jarvis.agent_chat import runner_acp
from jarvis.agent_chat import runner_cli as rc
from jarvis.agent_chat.runner_api import TurnHandle
from jarvis.agent_chat.store import AgentChatSession
from jarvis.agent_runtimes.acp import AcpTurn

_AGENT = Path(__file__).resolve().parents[2] / "fakes" / "fake_acp_agent.py"


def _session(tmp_path: Path, *, vendor: str | None, mode: str) -> AgentChatSession:
    return AgentChatSession(
        session_id="society:hermit",
        title="Hermit",
        provider="local-openai",
        model="fake-model",
        effort="",
        cwd=str(tmp_path),
        permission_mode=mode,
        vendor_session=vendor,
        created_ms=0,
        updated_ms=0,
        message_count=0,
        preview="",
        surface="society",
        runtime="hermes",
    )


def _turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str,
    *,
    vendor: str | None = None,
    mode: str = "bypass",
    answer: str = "allow",
    report_session: str | None = None,
    argv: list[str] | None = None,
    released: list[int] | None = None,
) -> tuple[str | None, list[dict[str, Any]], list[tuple[str, str]], list[str | None]]:
    resumes: list[str | None] = []

    async def fake_plan(handle: Any, runner: str, *, prompt: str, cwd: Path, resume, identity):
        resumes.append(resume)
        env = dict(os.environ)
        env["FAKE_ACP_STORE"] = str(tmp_path / "store.json")
        return rc.CliPlan(
            argv=argv or [sys.executable, str(_AGENT)],
            env=env,
            stdin_text=None,
            shape="acp",
            vendor_session=None,
            keep_stdin=True,
            acp=AcpTurn(
                turn_id=handle.turn_id,
                cwd=str(cwd),
                prompt_text=prompt,
                resume=resume,
                auto_allow=handle.session.permission_mode == "bypass",
                auto_deny=handle.session.permission_mode == "plan",
                report_session=report_session,
            ),
            after_turn=(lambda: released.append(1)) if released is not None else None,
        )

    monkeypatch.setattr(runner_acp, "plan_runtime_turn", fake_plan)
    events: list[dict[str, Any]] = []
    asked: list[tuple[str, str]] = []

    async def emit(event: dict[str, Any]) -> None:
        events.append(event)

    async def ask(call_id: str, name: str, args: dict[str, Any], summary: str) -> str:
        asked.append((call_id, name))
        return answer

    handle = TurnHandle(
        session=_session(tmp_path, vendor=vendor, mode=mode),
        turn_id="t1",
        emit=emit,
        request_approval=ask,
        cancel=asyncio.Event(),
    )
    vendor_out = asyncio.run(rc.run_cli_turn(handle, text, "hermes-cli"))
    return vendor_out, events, asked, resumes


def _finished(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e["payload"] for e in events if e["kind"] == "turn_finished")


def _texts(events: list[dict[str, Any]]) -> list[str]:
    return [e["payload"]["text"] for e in events if e["kind"] == "assistant_text"]


def test_the_runtime_runners_count_as_cli_seats():
    assert rc.supports_cli_runner("hermes-cli")
    assert rc.supports_cli_runner("openclaw-cli")


def test_a_fresh_turn_streams_text_reasoning_and_usage(monkeypatch, tmp_path):
    vendor, events, asked, _ = _turn(monkeypatch, tmp_path, "hello")
    assert vendor  # the ACP session id is kept for the next turn
    assert _texts(events) == ["echo: hello"]
    kinds = [e["kind"] for e in events]
    assert "reasoning" in kinds and "text_delta" in kinds
    finished = _finished(events)
    assert finished["status"] == "done" and finished["error"] is None
    assert finished["usage"]["input_tokens"] == 11
    assert asked == []


def test_a_resumed_turn_reopens_the_session_and_hides_the_replay(monkeypatch, tmp_path):
    first, _, _, _ = _turn(monkeypatch, tmp_path, "one")
    second, events, _, resumes = _turn(monkeypatch, tmp_path, "two", vendor=first)
    assert second == first
    assert resumes == [first]
    # The replayed history of "one" never reaches the chat a second time.
    assert _texts(events) == ["echo: two"]
    store = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))
    assert store[first] == ["one", "two"]


def test_a_lost_session_is_retried_fresh(monkeypatch, tmp_path):
    vendor, events, _, resumes = _turn(monkeypatch, tmp_path, "again", vendor="gone-123")
    assert resumes == ["gone-123", None]
    assert vendor and vendor != "gone-123"
    assert _texts(events) == ["echo: again"]
    assert _finished(events)["status"] == "done"


def test_a_jarvis_tool_call_reaches_the_timeline_under_its_mcp_name(monkeypatch, tmp_path):
    _, events, _, _ = _turn(monkeypatch, tmp_path, "TOOL please")
    calls = [e["payload"] for e in events if e["kind"] == "tool_call"]
    results = [e["payload"] for e in events if e["kind"] == "tool_result"]
    assert [c["name"] for c in calls] == ["mcp__jarvis__society_memory_recall"]
    assert calls[0]["input"] == {"query": "hi"}
    assert results[0]["output"] == "recalled" and results[0]["is_error"] is False
    assert _texts(events) == ["used the tool"]


def test_ask_mode_puts_the_runtimes_permission_on_the_chat_card(monkeypatch, tmp_path):
    _, events, asked, _ = _turn(monkeypatch, tmp_path, "ASK now", mode="ask", answer="allow")
    assert asked == [("tc-ask", "terminal")]
    assert _texts(events) == ["permission: yes"]
    # The card has a tool row to sit on.
    assert any(e["kind"] == "tool_call" and e["payload"]["call_id"] == "tc-ask" for e in events)


def test_a_denied_permission_selects_the_reject_option(monkeypatch, tmp_path):
    _, events, _, _ = _turn(monkeypatch, tmp_path, "ASK now", mode="ask", answer="deny")
    assert _texts(events) == ["permission: no"]


def test_bypass_answers_the_runtime_without_a_card(monkeypatch, tmp_path):
    _, events, asked, _ = _turn(monkeypatch, tmp_path, "ASK now", mode="bypass")
    assert asked == []
    assert _texts(events) == ["permission: yes"]


def test_a_prompt_error_finishes_the_turn_with_that_error(monkeypatch, tmp_path):
    _, events, _, _ = _turn(monkeypatch, tmp_path, "FAIL")
    finished = _finished(events)
    assert finished["status"] == "error"
    assert "model down" in (finished["error"] or "")


def test_a_fixed_session_key_is_what_the_chat_keeps(monkeypatch, tmp_path):
    vendor, _, _, _ = _turn(monkeypatch, tmp_path, "hi", report_session="agent:main:main")
    assert vendor == "agent:main:main"


def test_plan_mode_refuses_the_runtimes_permission_without_a_card(monkeypatch, tmp_path):
    _, events, asked, _ = _turn(monkeypatch, tmp_path, "ASK now", mode="plan")
    assert asked == []
    assert _texts(events) == ["permission: no"]


def test_a_runtime_that_keeps_running_after_its_answer_is_ended(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_ACP_EXIT_GRACE_S", 0.5)
    released: list[int] = []
    _, events, _, _ = _turn(monkeypatch, tmp_path, "HANG after this", released=released)
    finished = _finished(events)
    assert finished["status"] == "done", finished
    assert finished["duration_ms"] < 60_000
    assert released == [1]


def test_the_turn_slot_is_released_when_the_runtime_cannot_start(monkeypatch, tmp_path):
    released: list[int] = []
    missing = str(tmp_path / "no-such-runtime.exe")
    _, events, _, _ = _turn(monkeypatch, tmp_path, "hi", argv=[missing], released=released)
    assert _finished(events)["status"] == "error"
    assert released == [1]


def test_the_turn_slot_is_released_once_after_a_normal_turn(monkeypatch, tmp_path):
    released: list[int] = []
    _turn(monkeypatch, tmp_path, "hello", released=released)
    assert released == [1]
