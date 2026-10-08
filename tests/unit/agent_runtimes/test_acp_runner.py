"""An ACP runtime turn through the shared CLI pump, against a scripted agent."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
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
    cancel_on_delta: bool = False,
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
        if cancel_on_delta and event["kind"] == "text_delta":
            handle.cancel.set()

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


def test_acp_eof_without_prompt_result_is_incomplete(monkeypatch, tmp_path):
    _, events, _, _ = _turn(monkeypatch, tmp_path, "EOF_PARTIAL")
    assert _finished(events)["status"] == "error"
    assert "terminal result" in _finished(events)["error"]


def test_acp_token_exhaustion_keeps_partial_text_but_reports_error(monkeypatch, tmp_path):
    _, events, _, _ = _turn(monkeypatch, tmp_path, "TRUNCATE")
    assert _texts(events) == ["echo: TRUNCATE"]
    assert _finished(events)["status"] == "error"
    assert "max_tokens" in _finished(events)["error"]


def test_stop_sends_upstream_acp_cancel_before_terminating_bridge(monkeypatch, tmp_path):
    log = tmp_path / "frames.jsonl"
    monkeypatch.setenv("FAKE_ACP_LOG", str(log))
    _, events, _, _ = _turn(monkeypatch, tmp_path, "WAIT_CANCEL", cancel_on_delta=True)
    frames = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(frame.get("method") == "session/cancel" for frame in frames)
    assert _finished(events)["status"] == "cancelled"


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


@pytest.fixture
def model_gateway(monkeypatch):
    """A real loopback gateway with a fake provider, never a vendor connection."""
    import uvicorn
    from fastapi import FastAPI

    from jarvis.agent_runtimes import gateway
    from jarvis.core.protocols import BrainDelta
    from jarvis.ui.web.runtime_gateway_routes import router
    from jarvis.ui.web.surface_security import SurfaceSecurity

    state = {"limited": True, "calls": []}

    class Limit(Exception):
        status_code = 429
        headers = {"Retry-After": "60"}

    async def deltas(grant, model, request):
        state["calls"].append((grant, request))
        if state["limited"]:
            raise Limit("private provider body")
        yield BrainDelta(content="I review contributor pull requests for security issues.")
        yield BrainDelta(finish_reason="stop")

    monkeypatch.setattr(gateway, "_deltas", deltas)
    gateway.reset()
    app = FastAPI()
    app.include_router(router)
    secured = SurfaceSecurity(app, control_key_validator=lambda _token: False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(secured, log_level="critical", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{sock.getsockname()[1]}{gateway.BASE_PATH}", state
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        gateway.reset()
        assert not thread.is_alive()


@pytest.mark.parametrize("wait", [False, True], ids=["false-success", "long-retry"])
def test_gateway_failure_ends_the_real_acp_runner_and_recovery_keeps_the_question(
    monkeypatch,
    tmp_path,
    model_gateway,
    wait,
):
    from jarvis.agent_runtimes import gateway
    from jarvis.agent_runtimes.base import RuntimeLaunch, RuntimeStatus
    from jarvis.agent_runtimes.model_map import ModelRoute

    endpoint, state = model_gateway
    released = []
    invalidated = []

    class Driver:
        label = "Fake runtime"

        def detect(self):
            return RuntimeStatus("hermes", self.label, installed=True, ready=True)

        async def launch(self, turn):
            env = dict(os.environ)
            env.update(
                FAKE_ACP_GATEWAY=endpoint,
                FAKE_ACP_TOKEN=turn.route.api_key,
                FAKE_ACP_STORE=str(tmp_path / "gateway-store.json"),
            )
            if wait:
                env["FAKE_ACP_RETRY_WAIT"] = "1"
            return RuntimeLaunch(
                argv=[sys.executable, str(_AGENT)],
                env=env,
                cwd=tmp_path,
                acp_resume=turn.resume,
                release=lambda: released.append(1),
                invalidate=lambda: invalidated.append(1),
            )

    async def agent(_agent_id):
        return SimpleNamespace(
            agent_id="hermit", name="Hermit", denies=[], grants=[], grant_mode="all",
        )

    async def route(_cfg, provider, model, *, agent_id, account_id, session_id):
        return ModelRoute(
            provider,
            model,
            endpoint,
            "chat_completions",
            gateway.grant_token(agent_id, provider, account_id, scope=session_id),
        )

    monkeypatch.setattr(runner_acp, "driver", lambda _name: Driver())
    monkeypatch.setattr(runner_acp, "_agent", agent)
    monkeypatch.setattr(runner_acp, "_config", lambda: None)
    monkeypatch.setattr(runner_acp, "prepare_route", route)

    async def run(vendor=None):
        events = []

        async def emit(event):
            events.append(event)

        async def ask(*args):
            pytest.fail("The test asks no tool permission")

        handle = TurnHandle(
            session=_session(tmp_path, vendor=vendor, mode="ask"),
            turn_id="test-rate-limit",
            emit=emit,
            request_approval=ask,
            cancel=asyncio.Event(),
        )
        result = await asyncio.wait_for(
            rc.run_cli_turn(handle, "Was ist deine Aufgabe", "hermes-cli"),
            timeout=10,
        )
        return result, events

    vendor, events = asyncio.run(run())
    assert _finished(events)["status"] == "error"
    assert "HTTP 429" in _finished(events)["error"]
    assert "Try again in" in _finished(events)["error"]
    assert "/continue" not in _finished(events)["error"]
    assert _texts(events) == []
    assert len(state["calls"]) == 1 and released == [1]
    assert invalidated == [1]
    assert not gateway._FAILURES
    history = json.loads((tmp_path / "gateway-store.json").read_text(encoding="utf-8"))
    assert any("Was ist deine Aufgabe" in prompt for turns in history.values() for prompt in turns)

    # Simulate the provider's cooldown expiring, then resume the saved session.
    gateway._COOLDOWNS.clear()
    state["limited"] = False
    _, recovered = asyncio.run(run(vendor))
    assert _finished(recovered)["status"] == "done"
    assert _texts(recovered) == ["I review contributor pull requests for security issues."]
    assert len(state["calls"]) == 2 and released == [1, 1]
    assert invalidated == [1]  # Healthy recovery keeps its persistent gateway.
    assert not gateway._FAILURES


# ------------------------------------------- cancel, watchdog, containment


def _run_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str,
    *,
    env: dict[str, str] | None = None,
    mode: str = "bypass",
    cancel_after: float | None = None,
    ask_delay: float = 0.0,
) -> tuple[list[dict[str, Any]], Path]:
    """One turn against the fake agent; returns its events and frame log."""
    frames = tmp_path / "frames.log"

    async def fake_plan(handle: Any, runner: str, *, prompt: str, cwd: Path, resume, identity):
        child_env = dict(os.environ)
        child_env.update(FAKE_ACP_STORE=str(tmp_path / "store.json"), FAKE_ACP_LOG=str(frames))
        child_env.update(env or {})
        return rc.CliPlan(
            argv=[sys.executable, str(_AGENT)],
            env=child_env,
            stdin_text=None,
            shape="acp",
            vendor_session=None,
            keep_stdin=True,
            acp=AcpTurn(
                turn_id=handle.turn_id,
                cwd=str(cwd),
                prompt_text=prompt,
                auto_allow=mode == "bypass",
            ),
        )

    monkeypatch.setattr(runner_acp, "plan_runtime_turn", fake_plan)
    events: list[dict[str, Any]] = []

    async def emit(event: dict[str, Any]) -> None:
        events.append(event)

    async def ask(call_id: str, name: str, args: dict[str, Any], summary: str) -> str:
        await asyncio.sleep(ask_delay)
        return "allow"

    async def run() -> None:
        handle = TurnHandle(
            session=_session(tmp_path, vendor=None, mode=mode),
            turn_id="t-live",
            emit=emit,
            request_approval=ask,
            cancel=asyncio.Event(),
        )
        if cancel_after is not None:
            asyncio.get_running_loop().call_later(cancel_after, handle.cancel.set)
        await asyncio.wait_for(rc.run_cli_turn(handle, text, "hermes-cli"), timeout=60)

    asyncio.run(run())
    return events, frames


def _methods(frames: Path) -> list[str]:
    if not frames.is_file():
        return []
    return [
        str(json.loads(line).get("method") or "")
        for line in frames.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_stop_asks_the_runtime_to_cancel_before_killing_it(monkeypatch, tmp_path):
    """An OpenClaw run lives in its Gateway: killing the bridge alone never stops it."""
    started = time.monotonic()
    events, frames = _run_live(monkeypatch, tmp_path, "SLOW please", cancel_after=1.5)
    assert _finished(events)["status"] == "cancelled"
    assert "session/cancel" in _methods(frames)
    assert time.monotonic() - started < 15


def test_a_runtime_that_ignores_cancel_is_still_ended(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_ACP_CANCEL_GRACE_S", 0.5)
    events, frames = _run_live(
        monkeypatch, tmp_path, "SLOW", env={"FAKE_ACP_IGNORE_CANCEL": "1"}, cancel_after=1.0
    )
    assert _finished(events)["status"] == "cancelled"
    assert "session/cancel" in _methods(frames)


def test_a_runtime_that_never_opens_its_session_is_stopped(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_ACP_HANDSHAKE_TIMEOUT_S", 1.0)
    monkeypatch.setattr(rc, "_ACP_WATCH_TICK_S", 0.2)
    events, _ = _run_live(monkeypatch, tmp_path, "hi", env={"FAKE_ACP_MODE": "hang-after-init"})
    finished = _finished(events)
    assert finished["status"] == "error"
    assert "did not open its session" in finished["error"]


def test_a_silent_model_is_stopped_with_a_plain_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_ACP_IDLE_TIMEOUT_S", 1.0)
    monkeypatch.setattr(rc, "_ACP_WATCH_TICK_S", 0.2)
    events, frames = _run_live(monkeypatch, tmp_path, "SLOW")
    finished = _finished(events)
    assert finished["status"] == "error"
    assert "stopped responding" in finished["error"]
    assert "session/cancel" in _methods(frames)


def test_a_long_tool_call_is_not_a_stall(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_ACP_IDLE_TIMEOUT_S", 0.5)
    monkeypatch.setattr(rc, "_ACP_WATCH_TICK_S", 0.1)
    events, _ = _run_live(monkeypatch, tmp_path, "TOOLWAIT", cancel_after=2.5)
    # Ended by the person after 2.5 s, not by the 0.5 s watchdog.
    assert _finished(events)["status"] == "cancelled"


def test_an_open_approval_card_is_not_a_stall(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_ACP_IDLE_TIMEOUT_S", 0.5)
    monkeypatch.setattr(rc, "_ACP_WATCH_TICK_S", 0.1)
    events, _ = _run_live(monkeypatch, tmp_path, "ASK now", mode="ask", ask_delay=2.0)
    assert _finished(events)["status"] == "done"
    assert _texts(events) == ["permission: yes"]


def test_an_oversized_frame_ends_the_turn_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_READLINE_LIMIT", 64 * 1024)
    events, _ = _run_live(monkeypatch, tmp_path, "BIG", env={"FAKE_ACP_BIG_BYTES": "200000"})
    finished = _finished(events)
    assert finished["status"] == "error"
    assert "larger than" in finished["error"]


def test_a_runtime_that_exits_without_answering_is_an_error(monkeypatch, tmp_path):
    events, _ = _run_live(monkeypatch, tmp_path, "EXIT now")
    finished = _finished(events)
    assert finished["status"] == "error"
    assert "3" in (finished["error"] or "")


def test_a_cut_off_answer_is_retained_and_reported_incomplete(monkeypatch, tmp_path):
    events, _ = _run_live(monkeypatch, tmp_path, "MAXTOK")
    assert _finished(events)["status"] == "error"
    assert _texts(events) == ["echo: MAXTOK"]
    notices = [e["payload"] for e in events if e["kind"] == "notice"]
    assert [n["stop_reason"] for n in notices] == ["max_tokens"]


def test_a_detached_child_of_the_runtime_is_reaped_with_the_turn(monkeypatch, tmp_path):
    """Hermes starts shell commands in their own session (POSIX setsid);
    killpg never reaches them, the descendant tracker does (Windows: the job)."""
    import psutil

    child_file = tmp_path / "child.pid"
    events, _ = _run_live(
        monkeypatch, tmp_path, "SETSID", env={"FAKE_ACP_CHILD_FILE": str(child_file)}
    )
    assert _finished(events)["status"] == "done"
    pid = int(child_file.read_text(encoding="utf-8"))

    def running() -> bool:
        # Gone or a zombie both mean reaped; the pid can vanish between calls.
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    deadline = time.monotonic() + 5
    while running() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not running()
