"""REST contracts for resumable mission supervisor-tool approvals."""

from __future__ import annotations

import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.bus import EventBus
from jarvis.core.events import ActionApprovalRequired, ActionApproved, ActionDenied
from jarvis.missions.tool_approvals import MissionToolApprovalCoordinator
from jarvis.ui.web.missions_routes import router


class _Manager:
    async def mission(self, mission_id: str) -> object | None:
        return SimpleNamespace(id=mission_id) if mission_id == "mission-1" else None


def _app(bus: EventBus) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.mission_manager = _Manager()
    app.state.mission_tool_approvals = MissionToolApprovalCoordinator(bus)
    return app


async def _seed(
    bus: EventBus,
    *,
    mission_id: str = "mission-1",
) -> ActionApprovalRequired:
    event = ActionApprovalRequired(
        trace_id=uuid4(),
        tool_name="gmail/send_message",
        risk_tier="ask",
        reason="risk_tier",
        args_preview="{'to': 'person@example.test', 'api_key': '<redacted>'}",
        expires_at_ns=time.time_ns() + 30_000_000_000,
        mission_id=mission_id,
        worker_id="worker-1",
    )
    await bus.publish(event)
    return event


@pytest.mark.asyncio
async def test_list_and_approve_resume_only_the_selected_trace() -> None:
    bus = EventBus()
    app = _app(bus)
    approved: list[ActionApproved] = []

    async def _capture(event: ActionApproved) -> None:
        approved.append(event)

    bus.subscribe(ActionApproved, _capture)
    pending = await _seed(bus)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/missions/mission-1/tool-approvals")
        decided = await client.post(
            f"/api/missions/mission-1/tool-approvals/{pending.trace_id}/approve"
        )
        listed_after = await client.get("/api/missions/mission-1/tool-approvals")

    assert listed.status_code == 200
    assert listed.json()["approvals"] == [
        {
            "trace_id": str(pending.trace_id),
            "mission_id": "mission-1",
            "worker_id": "worker-1",
            "tool_name": "gmail/send_message",
            "risk_tier": "ask",
            "reason": "risk_tier",
            "args_preview": pending.args_preview,
            "requested_at_ns": pending.timestamp_ns,
            "expires_at_ns": pending.expires_at_ns,
        }
    ]
    assert decided.status_code == 200
    assert decided.json()["decision"] == "approved"
    assert len(approved) == 1
    assert approved[0].trace_id == pending.trace_id
    assert listed_after.json()["approvals"] == []


@pytest.mark.asyncio
async def test_deny_is_first_wins_and_wrong_mission_cannot_decide() -> None:
    bus = EventBus()
    app = _app(bus)
    denied: list[ActionDenied] = []

    async def _capture(event: ActionDenied) -> None:
        denied.append(event)

    bus.subscribe(ActionDenied, _capture)
    pending = await _seed(bus)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        wrong = await client.post(
            f"/api/missions/other/tool-approvals/{pending.trace_id}/deny",
            json={"reason": "wrong mission"},
        )
        denied_response = await client.post(
            f"/api/missions/mission-1/tool-approvals/{pending.trace_id}/deny",
            json={"reason": "user_denied"},
        )
        second = await client.post(
            f"/api/missions/mission-1/tool-approvals/{pending.trace_id}/approve"
        )

    assert wrong.status_code == 404
    assert denied_response.status_code == 200
    assert second.status_code == 404
    assert len(denied) == 1
    assert denied[0].trace_id == pending.trace_id
    assert denied[0].reason == "user_denied"


@pytest.mark.asyncio
async def test_expired_approval_is_never_listed_or_decidable() -> None:
    bus = EventBus()
    app = _app(bus)
    expired = ActionApprovalRequired(
        trace_id=uuid4(),
        tool_name="gmail/send_message",
        risk_tier="ask",
        args_preview="{}",
        expires_at_ns=time.time_ns() - 1,
        mission_id="mission-1",
    )
    await bus.publish(expired)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/missions/mission-1/tool-approvals")
        decided = await client.post(
            f"/api/missions/mission-1/tool-approvals/{expired.trace_id}/approve"
        )

    assert listed.json()["approvals"] == []
    assert decided.status_code == 404
