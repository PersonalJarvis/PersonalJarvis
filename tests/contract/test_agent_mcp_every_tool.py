"""Every Agent MCP tool, exercised for real — and a coverage floor that holds.

`test_agent_mcp_surface.py` pins WHAT the surface offers. This file proves each
of those tools actually works against a live society: it runs every one on its
success path and then asserts that the set it ran equals the set the surface
publishes.

That last assertion is the point. Adding a tool without exercising it here
fails the build, so the surface cannot grow a member nobody ever called — the
failure mode where a client is offered a tool that raises the first time
somebody tries it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from jarvis.agent_chat.store import AgentChatStore
from jarvis.core import runtime_refs
from jarvis.mcp.agents import TOOLS
from jarvis.mcp.agents import tools as agent_tools
from jarvis.society.runtime import SocietyRuntime
from tests.fakes.fake_agent_chat import FakeChatService


class Surface:
    """Calls tools the way a client does, and remembers which ones it ran."""

    def __init__(self) -> None:
        self.exercised: set[str] = set()

    async def call(self, tool: str, **args: Any) -> Any:
        self.exercised.add(tool)
        text = await agent_tools.call(tool, args)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    async def ok(self, tool: str, **args: Any) -> dict[str, Any]:
        """A call that must succeed — a refusal comes back as text, not JSON."""
        answer = await self.call(tool, **args)
        assert isinstance(answer, dict), f"{tool} refused: {answer}"
        return answer


@pytest.fixture
async def world(tmp_path: Path):
    """A society with a working (fake) chat, reachable as the tools reach it."""
    svc = FakeChatService(AgentChatStore(tmp_path / "agent_chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    runtime = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await runtime.ensure_started()
    app = FastAPI()
    app.state.society = runtime
    app.state.society_factory = lambda: runtime
    runtime_refs.set_web_app(app)
    try:
        yield runtime, svc
    finally:
        runtime_refs._reset_for_tests()
        await runtime.close()


async def test_every_published_tool_runs_against_a_live_society(world) -> None:
    rt, _svc = world
    s = Surface()

    # ---------------------------------------------------------- discovery
    status = await s.ok("ecosystem_status")
    assert status["kill_switch"] is False
    assert status["lead"] == "jarvis"

    caps = await s.ok("capabilities_list")
    assert "capabilities" in caps and caps["total"] == len(caps["capabilities"])
    filtered = await s.ok("capabilities_list", kind="plugin")
    assert all(c["kind"] == "plugin" for c in filtered["capabilities"])

    # ------------------------------------------------------------- roster
    scout = await s.ok(
        "agent_create",
        name="Scout",
        title="Research scout",
        description="Finds and checks facts.",
    )
    assert scout["created"] is True
    # Seat both with a provider so the chat path can run on a box with no keys.
    await rt.roster.update("scout", {"provider": "openai", "model": "gpt-5.2"})

    archivist = await s.ok(
        "agent_create", name="Archivist", title="Curator", description="Keeps the notes."
    )
    await rt.roster.update("archivist", {"provider": "openai", "model": "gpt-5.2"})
    assert archivist["agent"]["tier"] == "specialist"

    roster = await s.ok("agents_list")
    assert {a["name"] for a in roster["agents"]} >= {"Scout", "Archivist"}

    detail = await s.ok("agent_get", agent="Scout", recent_events=5)
    assert detail["agent"]["name"] == "Scout"
    # active_runs is a COUNT here, matching what /api/society/agents/{id} returns.
    assert detail["active_runs"] == 0

    # ------------------------------------------------------ conversation
    chat = await s.ok("agent_chat", agent="Scout", text="Are you ready?")
    assert chat["status"] == "answered"
    assert chat["reply"] == "Heard you: Are you ready?"

    note = await s.ok("agent_message", agent="Archivist", text="Filing this away.")
    assert note["delivered_to"] == "Archivist"

    assigned = await s.ok("agent_assign", agent="Scout", task="Check the changelog.")
    assert assigned["assigned_to"] == "Scout"
    assert assigned["outcome"] is not None

    # ------------------------------------------------------------ quests
    # The Quest Board: a job posted without naming anyone. Trusted Python picks
    # the taker, forging a teammate when nobody on the roster fits.
    posted = await s.ok("quest_post", text="Check the changelog for breaking changes.")
    quest_id = posted["quest"]["quest_id"]
    assert posted["taker"], "a quest must always end up with a taker"
    assert posted["why"], "the routing reason is what makes the choice reviewable"

    board_quests = await s.ok("quests_list")
    assert quest_id in {q["quest_id"] for q in board_quests["quests"]}
    assert (await s.ok("quests_list", limit=5))["total"] <= 5

    one = await s.ok("quest_get", quest_id=quest_id)
    assert one["quest"]["quest_id"] == quest_id
    assert one["events"], "a quest carries its own board trail"

    retried = await s.ok("quest_retry", quest_id=quest_id)
    assert retried["quest"]["quest_id"] == quest_id

    # Cancel a FRESH quest: the retried one has already reached a terminal
    # state on this fake-chat box, and a finished quest is not cancellable.
    second = await s.ok("quest_post", text="Summarise yesterday's board.")
    cancelled = await s.ok("quest_cancel", quest_id=second["quest"]["quest_id"])
    assert cancelled["quest"]["state"] in ("cancelled", "canceled", "failed", "done")

    # ------------------------------------------------------------- board
    inbox = await s.ok("agent_inbox", agent="Archivist")
    assert [e["msg_type"] for e in inbox["events"]] == ["SAY"]

    board = await s.ok("board_events", limit=50)
    assert board["last_seq"] >= 1
    trail = await s.ok("board_events", trace_id=assigned["trace_id"])
    assert len(trail["events"]) >= 1
    per_agent = await s.ok("board_events", agent="Scout")
    assert per_agent["events"], "the board must know Scout"

    # ------------------------------------------------------------- rooms
    opened = await s.ok("room_open", members=["Scout", "Archivist"], topic="Where do facts live?")
    room_id = opened["room"]["room_id"]
    assert opened["room"]["state"] == "running"

    listed = await s.ok("rooms_list")
    assert room_id in {r["room_id"] for r in listed["rooms"]}

    speaker = opened["room"]["members"][0]
    spoke = await s.ok("room_say", room_id=room_id, member=speaker, text="In the wiki.")
    assert spoke["room"]["message_count"] == 1

    settled = await s.ok("room_settle", room_id=room_id)
    assert settled["room"]["state"] == "settled"

    # -------------------------------------------------------- governance
    parked = await rt.approvals.enqueue(
        agent_id="scout",
        trace_id="approval-test",
        capability="send-email",
        action={"to": "someone@example.com"},
        summary="Send the summary out",
    )
    pending = await s.ok("approvals_list")
    assert parked.id in {a["id"] for a in pending["approvals"]}
    mine = await s.ok("approvals_list", agent="Scout")
    assert mine["total"] == 1

    resolved = await s.ok("approval_resolve", approval_id=parked.id, approve=False, note="not now")
    assert resolved["approval"]["state"] == "denied"
    assert (await s.ok("approvals_list"))["total"] == 0

    engaged = await s.ok("kill_switch", engage=True)
    assert engaged["engaged"] is True
    released = await s.ok("kill_switch", engage=False)
    assert released["engaged"] is False

    # -------------------------------------------------------- portability
    # The team as a portable bundle, and the same bundle applied back.
    bundle = await s.ok("ecosystem_export")
    assert bundle["kind"] == "jarvis.agent-ecosystem"
    assert {a["name"] for a in bundle["agents"]} >= {"Scout", "Archivist"}

    replayed = await s.ok("ecosystem_import", bundle=bundle, dry_run=True)
    assert replayed["dry_run"] is True
    # A superset: quest_post forged a generalist ("Runner") earlier, and it
    # travels in the bundle like any other teammate.
    assert set(replayed["updated"]) >= {"Archivist", "Scout"}

    # ------------------------------------------------------- the floor
    published = {t.name for t in TOOLS}
    missing = published - s.exercised
    assert not missing, (
        f"published but never exercised: {sorted(missing)}. "
        "Every tool this surface offers must run at least once here."
    )
    assert s.exercised == published
