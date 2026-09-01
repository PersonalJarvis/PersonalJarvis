"""M1 exit criterion (MASTERPLAN §8): two seeded agents exchange typed messages
end to end on a headless box — REST in, board out, no GPU, no audio, no LLM,
fakes only.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router


class FakeManager:
    async def dispatch(self, *, prompt: str, **kwargs) -> str:
        return "m-1"


def test_two_agents_exchange_typed_messages_headless(tmp_path: Path) -> None:
    manager = FakeManager()
    runtime = SocietyRuntime(tmp_path, mission_manager=lambda: manager)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime

    with TestClient(app) as c:
        # Seed.
        scout = c.post(
            "/api/society/agents",
            json={"name": "Scout", "title": "Research scout", "tier": "orchestrator"},
        ).json()["agent"]
        archivist = c.post(
            "/api/society/agents",
            json={"name": "Archivist", "title": "Knowledge curator"},
        ).json()["agent"]
        assert scout["tier"] == "orchestrator" and archivist["tier"] == "specialist"

        # Scout asks Archivist a typed QUERY; the scheduler routes it into the inbox.
        query = c.post(
            "/api/society/agents/archivist/message",
            json={
                "text": "Do we know the VPS provider?",
                "from_agent": "scout",
                "msg_type": "QUERY",
            },
        ).json()["event"]
        assert query["msg_type"] == "QUERY" and query["seq"] == 1

        inbox = c.get("/api/society/agents/archivist/inbox").json()["events"]
        assert [e["msg_type"] for e in inbox] == ["QUERY"]

        # Archivist answers on the same trace.
        answer = c.post(
            "/api/society/agents/scout/message",
            json={
                "text": "Yes: Hetzner, see the wiki.",
                "from_agent": "archivist",
                "msg_type": "ANSWER",
                "trace_id": query["trace_id"],
                "payload": {"refs": ["wiki:society/archivist/vps.md"]},
            },
        ).json()["event"]
        assert answer["parent_event_id"] is None and answer["trace_id"] == query["trace_id"]

        # The board shows the exchange in order, typed, attributed.
        thread = c.get("/api/society/events", params={"trace_id": query["trace_id"]}).json()[
            "events"
        ]
        assert [(e["from_agent"], e["to_agent"], e["msg_type"]) for e in thread] == [
            ("scout", "archivist", "QUERY"),
            ("archivist", "scout", "ANSWER"),
        ]
        assert thread[1]["payload"]["refs"] == ["wiki:society/archivist/vps.md"]

        # An orchestrator may assign; a specialist may not — both visible on the board.
        ok = c.post(
            "/api/society/agents/archivist/assign",
            json={"task": "Curate it.", "from_agent": "scout"},
        ).json()
        assert ok["outcome"]["msg_type"] == "CLAIM"
        no = c.post(
            "/api/society/agents/scout/assign", json={"task": "Nope.", "from_agent": "archivist"}
        ).json()
        assert no["outcome"]["msg_type"] == "VETO"
        assert no["outcome"]["payload"]["reason"] == "tier_not_allowed"

        status = c.get("/api/society/status").json()
        assert status["agents"] == 3  # jarvis + scout + archivist
        assert status["kill_switch"] is False
