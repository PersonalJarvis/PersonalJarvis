"""The lead's team card: Jarvis knows its society from the roster, every turn.

Live failure 2026-09-03: asked "which agents do you have access to?" with a
freshly created Gmail agent on the island, Jarvis answered from the retired
sub-agent (mission worker) system — its prompt had never heard of the
society. The card is the fix, and these tests pin its contract:

1. a created agent is on the very next card (no restart, no REST listing);
2. the card is deterministic — same roster, same bytes;
3. archived agents vanish, paused ones say so, busy ones say so;
4. the card names each agent's hands and quotes its brief;
5. the rule block says what "agent" means and where the old system went;
6. the realtime helper hands the planner the live names;
7. the lead picks the fitting agent for a nameless delegation;
8. a lead-assigned RESULT reaches the person: a chat notice and a spoken
   completion.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.society import runtime as runtime_mod
from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.lead_card import (
    CARD_TITLE,
    lead_card_section,
    render_lead_card,
    society_agent_names,
)
from jarvis.society.runtime import SocietyRuntime


def _tool(name: str, desc: str = "x.") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor", schema={})


TOOLS = {
    "gmail": _tool("gmail", "Read and send mail."),
    "search-web": _tool("search-web", "Search the web."),
    "cli_gh": _tool("cli_gh", "GitHub."),
}


def tmp_path_of(rt: SocietyRuntime) -> Path:
    return rt.data_dir


class FakeChat:
    """Enough of AgentChatService to receive a notice in the front-page chat."""

    def __init__(self, db: Path) -> None:
        from jarvis.agent_chat.store import AgentChatStore

        self.store = AgentChatStore(db)
        self.notices: list[tuple[str, dict[str, Any]]] = []

    async def post_notice(self, session_id: str, payload: dict[str, Any]) -> None:
        self.notices.append((session_id, payload))


@pytest.fixture
async def rt(tmp_path: Path):
    published: list[Any] = []
    chat = FakeChat(tmp_path / "agent_chat.db")
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        brain_tools=lambda: TOOLS,
        chat_service=lambda: chat,
        event_publish=published.append,
    )
    runtime.published = published  # type: ignore[attr-defined]
    runtime.chat = chat  # type: ignore[attr-defined]
    await runtime.ensure_started()
    try:
        yield runtime
    finally:
        await runtime.close()


async def test_a_created_agent_is_on_the_next_card(rt: SocietyRuntime):
    before = lead_card_section(lead_name="George")
    assert before.startswith(CARD_TITLE)
    assert "No other agents yet" in before
    assert "Gmail agent" not in before

    await rt.roster.create(
        name="Gmail agent",
        title="Mailbox",
        description="Handle my mail. Never delete anything.",
        focus=["plugin:gmail"],
    )

    after = lead_card_section(lead_name="George")
    assert "- Gmail agent — Mailbox · specialist · idle · hands: gmail" in after
    assert 'brief: "Handle my mail. Never delete anything."' in after
    assert "No other agents yet" not in after
    assert "You are George, the lead" in after


async def test_the_card_is_byte_stable_between_roster_changes(rt: SocietyRuntime):
    await rt.roster.create(name="Scout", title="Researcher", focus=["core:search-web"])
    first = lead_card_section()
    assert first == lead_card_section() == lead_card_section()
    await rt.roster.update("scout", {"title": "Head of research"})
    assert lead_card_section() != first


async def test_archived_vanish_paused_and_busy_say_so(rt: SocietyRuntime):
    await rt.roster.create(name="Scout", focus=["core:search-web"])
    await rt.roster.create(name="Builder", focus=["cli:gh"])
    await rt.roster.create(name="Old", focus=[])
    await rt.roster.archive("old")
    await rt.roster.update("builder", {"state": "paused"})
    rt.scheduler.note_run_started("run-1", "scout")

    card = lead_card_section()
    assert "- Old" not in card
    assert "- Builder · specialist · paused" in card
    assert "- Scout · specialist · busy (1 run)" in card


def test_render_lists_hands_by_grant_mode():
    catalog = [
        SimpleNamespace(id="plugin:gmail", label="Gmail", connected=True),
        SimpleNamespace(id="plugin:cal", label="Calendar", connected=True),
        SimpleNamespace(id="plugin:off", label="Offline", connected=False),
    ]

    def agent(name: str, **over: Any) -> SimpleNamespace:
        base = dict(
            agent_id=name.lower(),
            name=name,
            title="",
            description="",
            tier="specialist",
            state="active",
            focus=[],
            grants=[],
            denies=[],
            grant_mode="all",
            updated_ms=1,
        )
        base.update(over)
        return SimpleNamespace(**base)

    card = render_lead_card(
        [
            agent("Jarvis", agent_id="jarvis", tier="lead"),
            agent("Everything"),
            agent("Picky", grant_mode="allowlist", grants=["plugin:cal", "plugin:off"]),
            agent("Bare", grant_mode="allowlist"),
            agent("Focused", focus=["plugin:gmail", "plugin:off"], denies=["plugin:cal"]),
        ],
        catalog,
        terminals=("T1", "T2"),
    )
    assert "- Jarvis" not in card  # the lead never lists itself as a teammate
    assert "- Everything · specialist · idle · hands: every connected tool" in card
    assert "- Picky · specialist · idle · hands: Calendar" in card  # disconnected hidden
    assert "- Bare · specialist · idle · hands: none granted yet" in card
    assert "- Focused · specialist · idle · hands: Gmail" in card
    assert (
        "Coding terminals open in the Agentic IDE right now (not society agents): T1, T2."
        in card
    )


def test_the_rule_block_names_the_tools_and_buries_the_old_system():
    card = render_lead_card([], [], lead_name="George")
    assert "delegate_to_agent" in card
    assert "society_status" in card
    assert "never from the retired sub-agent or mission-worker system" in card
    assert "spawn_worker is only for heavy background work" in card
    assert "Agentic IDE" in card and "are NOT agents of this society" in card


async def test_society_agent_names_feed_the_realtime_path(rt: SocietyRuntime):
    assert society_agent_names() == ()
    await rt.roster.create(name="Gmail agent", focus=["plugin:gmail"])
    await rt.roster.create(name="Scout", focus=["core:search-web"])
    await rt.roster.create(name="Sleeper", focus=[])
    await rt.roster.update("sleeper", {"state": "paused"})
    assert society_agent_names() == ("Gmail agent", "Scout")


def test_without_a_runtime_everything_is_quiet():
    runtime_mod.set_current_runtime(None)
    assert lead_card_section() == ""
    assert society_agent_names() == ()


async def test_the_lead_picks_the_agent_whose_hands_fit(rt: SocietyRuntime):
    await rt.roster.create(name="Gmail agent", focus=["plugin:gmail"])
    await rt.roster.create(name="Scout", focus=["core:search-web"])
    assert rt.pick_agent("answer the invoice mail in gmail").agent_id == "gmail-agent"
    assert rt.pick_agent("search the web for the best VPS").agent_id == "scout"
    assert rt.pick_agent("water the plants") is None


async def test_a_lead_assigned_result_reaches_the_person(rt: SocietyRuntime):
    chat: FakeChat = rt.chat  # type: ignore[attr-defined]
    chat.store.create_session(
        provider="openai", model="gpt-5.2", effort="", cwd=str(tmp_path_of(rt)), surface="jarvis"
    )
    gmail = await rt.roster.create(name="Gmail agent", focus=["plugin:gmail"])
    env = SocietyEnvelope(
        msg_type=MsgType.ASSIGN,
        from_agent="jarvis",
        to_agent=gmail[0].agent_id,
        trace_id="voice:abc",
        payload={"text": "Answer the invoice mail.", "lang": "de"},
    )

    await rt.report_to_lead(gmail[0], env, status="done", summary="Invoice answered.")

    assert len(chat.notices) == 1
    session_id, payload = chat.notices[0]
    assert chat.store.get_session(session_id).surface == "jarvis"
    assert payload["kind"] == "society_result"
    assert payload["agent_name"] == "Gmail agent"
    assert payload["status"] == "done"
    assert payload["text"] == "Invoice answered."
    published = rt.published  # type: ignore[attr-defined]
    announcements = [e for e in published if type(e).__name__ == "AnnouncementRequested"]
    assert len(announcements) == 1
    assert announcements[0].kind == "completion"
    assert announcements[0].language == "de"
    spoken = "Gmail agent ist fertig: Invoice answered."  # i18n-allow: spoken completion
    assert announcements[0].text == spoken
