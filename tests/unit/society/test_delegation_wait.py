"""Only a reply to the actual request can resume its owning conversation."""

from dataclasses import replace

import pytest

from jarvis.core.delegated_work import collect_delegated_work
from jarvis.core.protocols import ChatTurn, current_chat_turn
from jarvis.society.delegation_wait import result_for
from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.runtime import SocietyRuntime
from tests.unit.society.test_lead_card import FakeChat


@pytest.fixture
async def rt(tmp_path):
    chat = FakeChat(tmp_path / "chat.db")
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, chat_service=lambda: chat)
    runtime.chat = chat
    await runtime.ensure_started()
    try:
        yield runtime
    finally:
        await runtime.close()


async def test_board_read_recovers_fast_answer_and_ignores_wrong_parent(rt):
    rt.scheduler.detach()
    target, _ = await rt.roster.create(name="Scout", title="Research", description="Research")
    accepted = []
    token = current_chat_turn.set(ChatTurn("original", "turn", "Audit", True, "trace"))
    try:
        with collect_delegated_work(accepted.append):
            request = await rt.say(
                from_agent="jarvis",
                to_agent=target.agent_id,
                text="Audit",
                msg_type=MsgType.QUERY,
            )
    finally:
        current_chat_turn.reset(token)
    assert request.payload["reply_session_id"] == "original"
    assert len(accepted) == 1
    await rt.store.append_and_publish(
        SocietyEnvelope(
            from_agent=target.agent_id,
            to_agent="jarvis",
            msg_type=MsgType.ANSWER,
            trace_id=request.trace_id,
            parent_event_id="wrong",
            payload={"text": "unrelated"},
        )
    )
    assert await accepted[0].probe() is None
    await rt.store.append_and_publish(
        SocietyEnvelope(
            from_agent=target.agent_id,
            to_agent="jarvis",
            msg_type=MsgType.ANSWER,
            trace_id=request.trace_id,
            parent_event_id=request.event_id,
            payload={"text": "Audit evidence", "reply_status": "done"},
        )
    )
    assert (await accepted[0].probe())["report"] == "Audit evidence"


async def test_silent_and_background_requests_do_not_hold_a_user_turn(rt):
    rt.scheduler.detach()
    target, _ = await rt.roster.create(name="Scout", title="Research", description="Research")
    accepted = []
    origin = ChatTurn("original", "turn", "Audit", True, "trace")
    token = current_chat_turn.set(origin)
    try:
        with collect_delegated_work(accepted.append):
            for policy in ("none", "on_error"):
                await rt.say(
                    from_agent="jarvis",
                    to_agent=target.agent_id,
                    text="FYI",
                    msg_type=MsgType.ASSIGN,
                    payload={"reply_policy": policy},
                )
            nested = current_chat_turn.set(replace(origin, direct_user=False))
            try:
                await rt.say(
                    from_agent="jarvis",
                    to_agent=target.agent_id,
                    text="Routine",
                    msg_type=MsgType.QUERY,
                )
            finally:
                current_chat_turn.reset(nested)
    finally:
        current_chat_turn.reset(token)
    assert accepted == []


async def test_blocked_and_question_results_never_become_success(rt):
    rt.scheduler.detach()
    request = SocietyEnvelope(
        from_agent="jarvis", to_agent="a", trace_id="assignment",
        msg_type=MsgType.ASSIGN, payload={"text": "task"}
    )
    await rt.store.append_and_publish(request)
    await rt.store.append_and_publish(
        SocietyEnvelope(
            from_agent="a",
            to_agent="jarvis",
            msg_type=MsgType.QUERY,
            parent_event_id=request.event_id,
            trace_id=request.trace_id,
            payload={"text": "Which project?"},
        )
    )
    assert (await result_for(rt.store, request))["status"] == "needs_input"
    await rt.store.append_and_publish(
        SocietyEnvelope(
            from_agent="a",
            to_agent="jarvis",
            msg_type=MsgType.RESULT,
            parent_event_id=request.event_id,
            trace_id=request.trace_id,
            payload={"status": "blocked", "done": "Missing access"},
        )
    )
    assert (await result_for(rt.store, request))["status"] == "blocked"


async def test_result_goes_to_original_chat_and_never_to_newest_or_deleted_chat(rt):
    chat = rt.chat
    args = {"provider": "openai", "model": "", "effort": "", "cwd": "", "surface": "jarvis"}
    original = chat.store.create_session(**args)
    newest = chat.store.create_session(**args)
    target, _ = await rt.roster.create(name="Scout", title="Research", description="Research")
    request = SocietyEnvelope(
        from_agent="jarvis",
        to_agent=target.agent_id,
        msg_type=MsgType.ASSIGN,
        trace_id="assignment-trace",
        payload={"reply_session_id": original.session_id},
    )
    await rt.report_to_lead(target, request, status="done", summary="Report")
    assert chat.notices[-1][0] == original.session_id
    assert chat.notices[-1][0] != newest.session_id
    chat.store.delete_session(original.session_id)
    await rt.report_to_lead(target, request, status="done", summary="Late report")
    assert len(chat.notices) == 1
