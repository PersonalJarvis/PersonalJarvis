"""Ordinary specialists propose work without acquiring execution authority."""

import json
from types import SimpleNamespace

import pytest

from jarvis.core.swarm_types import TeamCreate
from jarvis.society.swarm_port import RequestSwarmTool, SocietySwarmProfiles
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError
from tests.fakes.swarm_runtime import runtime


class Profiles:
    def __init__(self):
        self.active = True

    async def profile(self, source):
        return {
            "id": source,
            "name": "Researcher",
            "title": "Check facts",
            "focus": ["research"],
            "state": "active" if self.active else "archived",
        }


def brief(**changes):
    return {
        "name": "Research",
        "goal": "Check a specific fact",
        "acceptance": "Cite evidence",
        "authorized_input": "Only this approved context",
        "request_key": "proposal",
        **changes,
    }


@pytest.mark.asyncio
async def test_request_is_pending_only_idempotent_and_scoped(tmp_path):
    service = runtime(tmp_path)
    service.profiles = Profiles()
    try:
        record = await service.request_swarm("source", brief())
        assert record["state"] == "pending"
        assert not (tmp_path / "catalog.sqlite3").exists()
        assert await service.list_teams() == []
        assert await service.request_swarm("source", brief()) == record
        with pytest.raises(SwarmConflictError):
            await service.request_swarm("source", brief(goal="Changed"))
        with pytest.raises(SwarmAccessError):
            await service.request_status("other", record["id"])
        assert await service.request_status("source", record["id"]) == {
            "id": record["id"],
            "state": "pending",
            "team_id": None,
        }
        service.profiles.active = False
        with pytest.raises(SwarmAccessError):
            await service.request_swarm("source", brief(request_key="new"))
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_owner_approval_creates_one_unstarted_team_with_explicit_handoff(tmp_path):
    service = runtime(tmp_path)
    service.profiles = Profiles()
    try:
        record = await service.request_swarm("source", brief())
        spec = TeamCreate(name="Approved", goal="Check fact", request_key="owner")
        team = await service.approve_request(record["id"], spec)
        assert team["state"] == "created"
        assert (await service.approve_request(record["id"], spec))["id"] == team["id"]
        with pytest.raises(SwarmConflictError):
            await service.approve_request(record["id"], spec.model_copy(update={"goal": "Other"}))
        agents = await service.records(team["id"], "agents")
        assert len(agents) == 2
        assigned = next(agent for agent in agents if agent.get("source_agent_id"))
        assert assigned["role"] == "worker"
        store = service.registry.open(team["id"])
        from jarvis.swarm.specialists import context

        inputs = context(store, await service._controller(store), assigned["id"])
        assert inputs["authorized_input"] == brief()["authorized_input"]
        assert "history" not in json.dumps(inputs)
        assert len(await service.list_teams()) == 1
        assert (await service.request_status("source", record["id"]))["state"] == "approved"
        revoked = await service.revoke_specialist(team["id"], assigned["id"])
        assert revoked["state"] == "stopped"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_rejected_proposal_cannot_create_team(tmp_path):
    service = runtime(tmp_path)
    service.profiles = Profiles()
    try:
        record = await service.request_swarm("source", brief())
        await service.reject_request(record["id"])
        with pytest.raises(SwarmConflictError):
            await service.approve_request(
                record["id"], TeamCreate(name="Denied", goal="No work", request_key="owner")
            )
        assert await service.list_teams() == []
    finally:
        await service.stop()


class Roster:
    async def get(self, source):
        return SimpleNamespace(
            agent_id=source,
            name="Researcher",
            title="Review",
            focus=[],
            state="active",
            denies=[],
            private_history="NEVER COPY THIS",
        )


class SocietyStore:
    async def kill_switch(self):
        return False


@pytest.mark.asyncio
async def test_host_bound_tool_cannot_impersonate_or_read_another_source(tmp_path):
    service = runtime(tmp_path)
    rt = SimpleNamespace(roster=Roster(), store=SocietyStore(), swarm_requests=lambda: service)
    service.profiles = SocietySwarmProfiles(lambda: rt)
    try:
        tool = RequestSwarmTool(rt, "trusted-source")
        rejected = await tool.execute(dict(brief(), source_agent_id="other"), None)
        assert not rejected.success
        accepted = await tool.execute(brief(), None)
        assert accepted.success
        assert accepted.output["source_agent_id"] == "trusted-source"
        assert "NEVER COPY" not in json.dumps(accepted.output)
        other = RequestSwarmTool(rt, "other")
        assert not (await other.execute({"request_id": accepted.output["id"]}, None)).success
        assert not (tmp_path / "catalog.sqlite3").exists()
    finally:
        await service.stop()
