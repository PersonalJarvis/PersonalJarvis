"""Every coding agent can ask and have its plan approved (jarvis/agent_chat/turn_prompts.py).

Claude Code asks mid-turn over its control protocol; every other CLI asks at
the end of its turn with a ``jarvis-ask`` block, and a finished plan-mode turn
gets a plan card. These tests drive both paths through the real service, store
and CLI pump; only the vendor process is a small Python stand-in.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from jarvis.agent_chat import runner_cli as rc
from jarvis.agent_chat import turn_prompts
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.questions import QuestionAnswer
from jarvis.agent_chat.runner_api import TurnHandle
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatSession, AgentChatStore

_ASK = {
    "questions": [
        {
            "question": "Which database?",
            "options": [
                {"label": "SQLite", "description": "One file"},
                {"label": "Postgres", "description": "A server"},
            ],
            "recommendation_reason": "Nothing to run.",
        },
        {"question": "Where does it run?", "options": [{"label": "Laptop"}, {"label": "VPS"}]},
    ]
}


def _reply_with_block(payload: Any = _ASK) -> str:
    return f"I need two decisions.\n\n```{turn_prompts.ASK_FENCE}\n{json.dumps(payload)}\n```\n"


# ------------------------------------------------------------------ parsing


def test_the_last_valid_block_is_the_card() -> None:
    specs = turn_prompts.parse_ask_block(_reply_with_block())
    assert specs is not None and [s.question for s in specs] == [
        "Which database?",
        "Where does it run?",
    ]
    # A bare list of questions is accepted too.
    listed = turn_prompts.parse_ask_block(_reply_with_block(_ASK["questions"][:1]))
    assert listed is not None and len(listed) == 1
    # No block, broken JSON, or questions that do not validate: plain text.
    assert turn_prompts.parse_ask_block("Which database do you want?") is None
    assert turn_prompts.parse_ask_block(f"```{turn_prompts.ASK_FENCE}\n{{oops\n```") is None
    one_option = {"questions": [{"question": "A?", "options": [{"label": "only"}]}]}
    assert turn_prompts.parse_ask_block(_reply_with_block(one_option)) is None
    # An ordinary code block is never a question.
    assert turn_prompts.parse_ask_block(f"```json\n{json.dumps(_ASK)}\n```") is None


def test_the_frontend_hides_the_same_fence() -> None:
    root = Path(__file__).resolve().parents[3]
    source = (
        root / "jarvis/ui/web/frontend/src/components/agentchat/askFence.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r'export const ASK_FENCE = "([^"]+)"', source)
    assert match is not None and match.group(1) == turn_prompts.ASK_FENCE


# ------------------------------------------------------------------ the service


async def _service(
    tmp_path: Path, *, mode: str = "auto", provider: str = "openai-codex"
) -> tuple[AgentChatService, str, list[tuple[str, str | None]]]:
    svc = AgentChatService(AgentChatStore(":memory:"))
    session = svc.store.create_session(
        provider=provider,
        model="",
        effort="",
        cwd=str(tmp_path),
        permission_mode=mode,
        surface="agent",
    )
    sent: list[tuple[str, str | None]] = []

    async def send(session_id: str, text: str, *_: Any, **kwargs: Any) -> str:
        assert session_id == session.session_id
        sent.append((text, kwargs.get("display_text")))
        return "next-turn"

    svc.send = send  # type: ignore[method-assign]
    return svc, session.session_id, sent


async def _finish_turn(
    svc: AgentChatService, sid: str, text: str, *, status: str = "done", turn_id: str = "t1"
) -> None:
    await svc._emit(sid, make_event("turn_started", {"turn_id": turn_id}))
    await svc._emit(sid, make_event("assistant_text", {"turn_id": turn_id, "text": text}))
    await svc._emit(sid, make_event("turn_finished", {"turn_id": turn_id, "status": status}))


def _kinds(svc: AgentChatService, sid: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in svc.store.list_events(sid) if e["kind"] == kind]


def test_an_ask_block_becomes_a_card_and_the_answers_the_next_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, sent = await _service(tmp_path)
        await _finish_turn(svc, sid, _reply_with_block())
        await svc._open_turn_prompts(sid, "t1", "codex-cli")
        (card,) = _kinds(svc, sid, "question_required")
        assert card["deferred"] is True and card["turn_id"] == "t1"
        qid = card["question_id"]

        assert await svc.answer_turn_question(sid, qid, index=0, option_index=1)
        assert not await svc.answer_turn_question(sid, qid, index=0, option_index=0)
        (progress,) = _kinds(svc, sid, "question_progress")
        assert progress["answers"][0]["answer"] == "Postgres" and progress["answers"][1] is None
        assert sent == []

        assert await svc.answer_turn_question(sid, qid, index=1, text="A Raspberry Pi")
        (resolved,) = _kinds(svc, sid, "question_resolved")
        assert [a["answer"] for a in resolved["answers"]] == ["Postgres", "A Raspberry Pi"]
        ((prompt, shown),) = sent
        assert "Which database? -> Postgres" in prompt and "A Raspberry Pi" in prompt
        assert shown == "Which database? — Postgres\nWhere does it run? — A Raspberry Pi"
        # Closed: no second answer, no second message.
        assert not await svc.answer_turn_question(sid, qid, index=1, text="again")
        assert len(sent) == 1

    asyncio.run(scenario())


def test_closing_the_card_leaves_the_rest_to_the_agent(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, sent = await _service(tmp_path)
        await _finish_turn(svc, sid, _reply_with_block())
        await svc._open_turn_prompts(sid, "t1", "codex-cli")
        qid = _kinds(svc, sid, "question_required")[0]["question_id"]
        assert await svc.answer_turn_question(sid, qid, index=0, option_index=1)
        assert await svc.skip_turn_question(sid, qid)
        (resolved,) = _kinds(svc, sid, "question_resolved")
        assert [(a["answer"], a["source"]) for a in resolved["answers"]] == [
            ("Postgres", "person"),
            ("Laptop", "skipped"),
        ]
        ((prompt, _),) = sent
        assert "left to you; go with your recommendation (Laptop)" in prompt

    asyncio.run(scenario())


def test_a_new_turn_closes_an_unanswered_card(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, sent = await _service(tmp_path)
        await _finish_turn(svc, sid, _reply_with_block())
        await svc._open_turn_prompts(sid, "t1", "codex-cli")
        qid = _kinds(svc, sid, "question_required")[0]["question_id"]
        # The person typed something else instead: that turn moved on.
        await _finish_turn(svc, sid, "ok", turn_id="t2")
        assert not await svc.answer_turn_question(sid, qid, index=0, option_index=0)
        assert not await svc.skip_turn_question(sid, qid)
        assert sent == []

    asyncio.run(scenario())


def test_no_card_for_a_failed_turn_or_plain_text(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, _ = await _service(tmp_path)
        await _finish_turn(svc, sid, _reply_with_block(), status="error")
        await svc._open_turn_prompts(sid, "t1", "codex-cli")
        await _finish_turn(svc, sid, "Done. Anything else?", turn_id="t2")
        await svc._open_turn_prompts(sid, "t2", "codex-cli")
        assert _kinds(svc, sid, "question_required") == []
        assert _kinds(svc, sid, "plan_ready") == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("provider", "runner", "build"),
    [
        ("openai-codex", "codex-cli", "full-access"),
        ("antigravity", "agy-cli", "skip-permissions"),
        ("grok-build", "grok-cli", "bypassPermissions"),
        ("opencode", "opencode-cli", "auto"),
        ("kimi", "kimi-cli", "auto"),
        ("cursor", "cursor-cli", "auto"),
        ("claude-api", "claude-cli", "bypassPermissions"),
    ],
)
def test_a_finished_plan_gets_a_build_card(
    tmp_path: Path, provider: str, runner: str, build: str
) -> None:
    async def scenario() -> None:
        svc, sid, sent = await _service(tmp_path, mode="plan", provider=provider)
        await _finish_turn(svc, sid, "1. Add the table\n2. Wire the route")
        await svc._open_turn_prompts(sid, "t1", runner)
        (card,) = _kinds(svc, sid, "plan_ready")
        assert card == {"turn_id": "t1", "build_mode": build}

        assert await svc.resolve_turn_plan(sid, "t1", "build")
        assert svc.store.get_session(sid).permission_mode == build
        assert _kinds(svc, sid, "session_updated") == [{"permission_mode": build}]
        assert _kinds(svc, sid, "plan_resolved") == [{"turn_id": "t1", "decision": "build"}]
        assert sent == [(turn_prompts.PLAN_GO_AHEAD, None)]
        assert not await svc.resolve_turn_plan(sid, "t1", "build")

    asyncio.run(scenario())


def test_keep_planning_only_closes_the_card(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, sent = await _service(tmp_path, mode="plan")
        await _finish_turn(svc, sid, "1. Add the table")
        await svc._open_turn_prompts(sid, "t1", "codex-cli")
        with pytest.raises(ValueError):
            await svc.resolve_turn_plan(sid, "t1", "maybe")
        assert await svc.resolve_turn_plan(sid, "t1", "keep")
        assert svc.store.get_session(sid).permission_mode == "plan"
        assert sent == []
        # Outside plan mode a finished turn is just a reply.
        svc.store.update_session(sid, permission_mode="auto")
        await _finish_turn(svc, sid, "Done.", turn_id="t2")
        await svc._open_turn_prompts(sid, "t2", "codex-cli")
        assert len(_kinds(svc, sid, "plan_ready")) == 1

    asyncio.run(scenario())


# ------------------------------------------------------------------ the CLI runner


def _session(tmp_path: Path, provider: str, *, surface: str, mode: str = "") -> AgentChatSession:
    return AgentChatSession(
        session_id="s1",
        title="",
        provider=provider,
        model="",
        effort="",
        cwd=str(tmp_path),
        permission_mode=mode,
        vendor_session=None,
        created_ms=0,
        updated_ms=0,
        message_count=0,
        preview="",
        surface=surface,
    )


def _prompt_seen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: str, provider: str, surface: str
) -> str:
    seen: list[str] = []

    async def once(handle: Any, user_text: str, *_: Any, **__: Any) -> Any:
        seen.append(user_text)
        return rc._Outcome(
            status="done", error=None, usage={}, cost_usd=None, vendor_session=None
        )

    monkeypatch.setattr(rc, "_run_cli_once", once)

    async def emit(_ev: dict[str, Any]) -> None:
        return None

    async def ask(*_: object) -> str:
        return "deny"

    handle = TurnHandle(
        session=_session(tmp_path, provider, surface=surface),
        turn_id="t1",
        emit=emit,
        request_approval=ask,
        cancel=asyncio.Event(),
    )
    asyncio.run(rc.run_cli_turn(handle, "build the thing", runner))
    return seen[0]


@pytest.mark.parametrize(
    ("runner", "provider"),
    [
        ("codex-cli", "openai-codex"),
        ("agy-cli", "antigravity"),
        ("grok-cli", "grok-build"),
        ("opencode-cli", "opencode"),
        ("kimi-cli", "kimi"),
        ("cursor-cli", "cursor"),
        ("dsh-cli", "deepseek-harness"),
    ],
)
def test_every_cli_without_a_question_channel_learns_the_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: str, provider: str
) -> None:
    prompt = _prompt_seen(monkeypatch, tmp_path, runner, provider, "agent")
    assert prompt == turn_prompts.ASK_PROTOCOL + "build the thing"


def test_claude_and_other_surfaces_keep_their_own_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _prompt_seen(monkeypatch, tmp_path, "claude-cli", "claude-api", "agent") == (
        "build the thing"
    )
    assert _prompt_seen(monkeypatch, tmp_path, "codex-cli", "openai-codex", "jarvis") == (
        "build the thing"
    )


def _fake_claude(requests: list[dict[str, Any]], out: Path) -> list[str]:
    """A Claude Code stand-in: asks each control request, records the answers."""
    body = f"""
import json, sys
requests = {json.dumps(requests)!r}
answers = []
def send(obj):
    sys.stdout.write(json.dumps(obj) + chr(10)); sys.stdout.flush()
send({{"type": "system", "subtype": "init", "session_id": "v1"}})
pending = list(json.loads(requests))
if pending:
    send(pending.pop(0))
for line in sys.stdin:
    try:
        obj = json.loads(line)
    except ValueError:
        continue
    if obj.get("type") != "control_response":
        continue
    if obj["response"]["request_id"] == "jarvis-init":
        continue
    answers.append(obj["response"]["response"])
    if pending:
        send(pending.pop(0))
        continue
    break
open({str(out)!r}, "w", encoding="utf-8").write(json.dumps(answers))
send({{"type": "result", "subtype": "success", "result": "ok", "session_id": "v1"}})
"""
    return [sys.executable, "-c", body]


class _FakeChat:
    """The service surface a Claude turn reaches: the question card and the store."""

    def __init__(self) -> None:
        self.asked: list[Any] = []
        self.updates: list[dict[str, Any]] = []
        self.store = self

    async def ask_questions(self, session_id: str, specs: Any, **_: Any) -> list[QuestionAnswer]:
        self.asked.append(specs)
        return [QuestionAnswer("Blue", 1, "person")]

    def update_session(self, session_id: str, **fields: Any) -> None:
        self.updates.append(fields)


def _claude_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    requests: list[dict[str, Any]],
    *,
    mode: str,
    decision: str = "allow",
) -> tuple[list[dict[str, Any]], _FakeChat, list[dict[str, Any]]]:
    out = tmp_path / "answers.json"
    plan = rc.CliPlan(
        _fake_claude(requests, out),
        dict(os.environ),
        rc.claude_stream_input("hi"),
        "claude",
        "v1",
        keep_stdin=True,
        control_init=rc.claude_control_init(),
    )
    monkeypatch.setitem(rc._PLANNERS, "claude-cli", lambda **kw: plan)
    events: list[dict[str, Any]] = []
    chat = _FakeChat()

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    async def ask(*_: object) -> str:
        return decision

    handle = TurnHandle(
        session=_session(tmp_path, "claude-api", surface="agent", mode=mode),
        turn_id="t1",
        emit=emit,
        request_approval=ask,
        cancel=asyncio.Event(),
        control_service=chat,
    )
    asyncio.run(rc.run_cli_turn(handle, "hi", "claude-cli"))
    return json.loads(out.read_text(encoding="utf-8")), chat, events


def _can_use(tool: str, tool_input: dict[str, Any], request_id: str = "r1") -> dict[str, Any]:
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "can_use_tool",
            "tool_name": tool,
            "input": tool_input,
            "tool_use_id": f"call-{request_id}",
        },
    }


def test_claude_asks_on_the_question_card_and_reads_the_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    question = {
        "question": "Which colour?",
        "header": "Colour",
        "options": [{"label": "Red"}, {"label": "Blue"}],
        "multiSelect": False,
    }
    answers, chat, _ = _claude_turn(
        monkeypatch,
        tmp_path,
        [_can_use("AskUserQuestion", {"questions": [question]})],
        mode="default",
    )
    ((spec,),) = chat.asked
    assert spec.question == "Which colour?" and [o.label for o in spec.options] == ["Red", "Blue"]
    assert answers == [
        {
            "behavior": "allow",
            "updatedInput": {"questions": [question], "answers": {"Which colour?": "Blue"}},
        }
    ]


def test_an_approved_claude_plan_leaves_plan_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answers, chat, events = _claude_turn(
        monkeypatch, tmp_path, [_can_use("ExitPlanMode", {"plan": "1. Do it"})], mode="plan"
    )
    (body,) = answers
    assert body["behavior"] == "allow"
    assert body["updatedPermissions"] == [
        {"type": "setMode", "mode": "bypassPermissions", "destination": "session"}
    ]
    assert chat.updates == [{"permission_mode": "bypassPermissions"}]
    assert {"permission_mode": "bypassPermissions"} in [
        e["payload"] for e in events if e["kind"] == "session_updated"
    ]


def test_a_declined_claude_plan_stays_in_plan_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answers, chat, _ = _claude_turn(
        monkeypatch,
        tmp_path,
        [_can_use("ExitPlanMode", {"plan": "1. Do it"})],
        mode="plan",
        decision="deny",
    )
    assert answers == [{"behavior": "deny", "message": "The person declined this action."}]
    assert chat.updates == []


# ------------------------------------------------------------------ prompts on argv


def _npm_layout(tmp_path: Path, shim: str, *target: str) -> Path:
    """An npm global prefix: ``<shim>.cmd`` beside ``node_modules/...``."""
    real = tmp_path.joinpath("node_modules", *target)
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("", encoding="utf-8")
    cmd = tmp_path / f"{shim}.cmd"
    cmd.write_text("@echo off\r\n", encoding="utf-8")
    return cmd


def test_a_multi_line_prompt_skips_the_batch_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # cmd.exe ends a command at the first line break: through ``opencode.cmd``
    # the task after the protocol paragraph never reached the agent.
    cmd = _npm_layout(tmp_path, "opencode", "opencode-ai", "bin", "opencode.exe")
    monkeypatch.setattr(rc, "_which", lambda *names: str(cmd))
    prefix = rc.opencode_argv_prefix()
    assert prefix == [str(tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe")]
    plan = rc.plan_opencode(
        prompt="line one\n\nline two", cwd=tmp_path, model="", effort="",
        permission_mode="default", resume=None,
    )
    assert plan.argv[-1] == "line one\n\nline two"

    from jarvis.core import path_augment

    monkeypatch.setattr(path_augment, "resolve_node_executable", lambda: "node-bin")
    kimi = _npm_layout(tmp_path, "kimi", "@moonshot-ai", "kimi-code", "dist", "main.mjs")
    monkeypatch.setattr(rc, "_which", lambda *names: str(kimi))
    assert rc.kimi_argv_prefix() == [
        "node-bin",
        str(tmp_path / "node_modules" / "@moonshot-ai" / "kimi-code" / "dist" / "main.mjs"),
    ]


def test_a_shim_that_stays_gets_the_prompt_on_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lonely = tmp_path / "dsh.cmd"
    lonely.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setattr(rc, "_which", lambda *names: str(lonely))
    assert rc.dsh_argv_prefix() == [str(lonely)]  # no package beside it: the shim stays
    plan = rc.plan_dsh(
        prompt="first\n\n  second\nthird", cwd=tmp_path, model="", effort="",
        permission_mode="", resume=None,
    )
    assert plan.argv[-1] == "first second third"
    assert rc._argv_prompt(["/usr/bin/opencode"], "a\nb") == "a\nb"
