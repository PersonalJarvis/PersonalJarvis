"""Results follow trusted turn provenance, even after another chat opens."""

from jarvis.core.delegation import origin_metadata
from jarvis.core.protocols import ChatTurn, current_chat_turn
from jarvis.society.events import MsgType, SocietyEnvelope
from tests.unit.society import test_lead_card

rt = test_lead_card.rt


def session(runtime):
    return runtime.chat.store.create_session(
        provider="openai", model="test", effort="", cwd=str(runtime.data_dir), surface="jarvis",
    )


async def test_result_returns_to_origin_after_user_opens_another_chat(rt):
    original = session(rt)
    newer = session(rt)
    target, _ = await rt.roster.create(name="Scout")
    request = SocietyEnvelope(
        msg_type=MsgType.ASSIGN, from_agent="jarvis", to_agent=target.agent_id,
        trace_id="voice:legacy-prefix",
        payload={"text": "Find the release", "reply_surface": "chat",
                 "reply_session_id": original.session_id, "reply_policy": "always"},
    )
    await rt.report_to_lead(target, request, status="done", summary="Release found.")
    assert rt.chat.notices[0][0] == original.session_id != newer.session_id
    assert not [e for e in rt.published if type(e).__name__ == "AnnouncementRequested"]
    assert "Find the release" in rt.chat.notices[0][1]["report"]


async def test_voice_report_does_not_depend_on_an_open_text_chat(rt):
    target, _ = await rt.roster.create(name="Scout")
    request = SocietyEnvelope(
        msg_type=MsgType.ASSIGN, from_agent="jarvis", to_agent=target.agent_id,
        trace_id="request",
        payload={"text": "Find the release", "reply_surface": "voice", "reply_policy": "always"},
    )
    await rt.report_to_lead(target, request, status="blocked", summary="Access unavailable.")
    reports = [e for e in rt.published if type(e).__name__ == "AnnouncementRequested"]
    assert len(reports) == 1 and "blocked" in reports[0].report


async def test_deleted_origin_does_not_redirect_to_new_chat(rt):
    session(rt)
    target, _ = await rt.roster.create(name="Scout")
    request = SocietyEnvelope(
        msg_type=MsgType.ASSIGN, from_agent="jarvis", to_agent=target.agent_id,
        trace_id="request",
        payload={"text": "Task", "reply_surface": "chat", "reply_session_id": "deleted-chat"},
    )
    await rt.report_to_lead(target, request, status="done", summary="Finished.")
    assert not rt.chat.notices


def test_scheduled_turn_cannot_promote_itself_to_voice():
    token = current_chat_turn.set(ChatTurn("society:scout", "turn", "Task", False, "trace"))
    try:
        assert origin_metadata()["reply_surface"] == "none"
    finally:
        current_chat_turn.reset(token)
