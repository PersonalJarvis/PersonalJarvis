"""Owner-approved specialist handoffs, separate from personal agent context."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jarvis.core.swarm_types import SwarmBrief, TeamCreate

from .requests import RequestInbox
from .store import SwarmAccessError, SwarmConflictError


class Participation:
    """Methods on the composition service; model tools receive only its request port."""

    async def _source_profile(self: Any, source_agent_id: str) -> dict[str, Any]:
        if self.profiles is None:
            raise SwarmAccessError("The ordinary-agent profile service is unavailable")
        try:
            profile = await self.profiles.profile(source_agent_id)
        except PermissionError as exc:
            raise SwarmAccessError(
                "The selected specialist is unavailable or no longer active"
            ) from exc
        if profile.get("id") != source_agent_id or profile.get("state") != "active":
            raise SwarmAccessError("An active, explicitly selected specialist is required")
        return profile

    async def request_swarm(
        self: Any, source_agent_id: str, brief: dict[str, Any]
    ) -> dict[str, Any]:
        profile = await self._source_profile(source_agent_id)
        return await self.storage.call(
            RequestInbox(self.root).enqueue, profile, SwarmBrief.model_validate(brief)
        )

    async def request_status(self: Any, source_agent_id: str, request_id: str) -> dict[str, Any]:
        await self._source_profile(source_agent_id)
        record = await self.storage.call(RequestInbox(self.root).get, request_id, source_agent_id)
        # This port does not grant access to the team's files or other members.
        return {key: record[key] for key in ("id", "state", "team_id")}

    async def list_requests(self: Any, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return await self.storage.call(RequestInbox(self.root).list, limit, offset)

    async def approve_request(self: Any, request_id: str, spec: TeamCreate) -> dict[str, Any]:
        inbox = RequestInbox(self.root)
        request = await self.storage.call(inbox.get, request_id)
        spec = spec.model_copy(update={"request_key": "proposal:" + request_id})
        digest = hashlib.sha256(
            json.dumps(spec.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        if request["state"] == "approved":
            if request.get("approval") != digest:
                raise SwarmConflictError("Approval retry changed the authorized settings")
            return await self.team(request["team_id"])
        await self._source_profile(request["source_agent_id"])
        await self.storage.call(
            inbox.decide,
            request_id,
            "approving",
            approval=digest,
            spec=spec.model_dump(mode="json"),
        )
        team = await self.create_team(spec)
        await self.assign_specialist(
            team["id"],
            request["source_agent_id"],
            request["brief"]["authorized_input"],
            "proposal:" + request_id,
        )
        await self.storage.call(
            inbox.decide, request_id, "approved", team_id=team["id"], approval=digest
        )
        return await self.team(team["id"])

    async def reject_request(self: Any, request_id: str) -> dict[str, Any]:
        return await self.storage.call(RequestInbox(self.root).decide, request_id, "rejected")

    async def assign_specialist(
        self: Any,
        team_id: str,
        source_agent_id: str,
        authorized_input: str,
        request_key: str,
        expected_version: int | None = None,
        expected_storage_generation: str | None = None,
    ) -> dict[str, Any]:
        from .specialists import assign

        profile = await self._source_profile(source_agent_id)
        store = await self.storage.call(self.registry.open, team_id)
        controller = await self._controller(store)
        if controller is None:
            raise SwarmConflictError("This team's active controller must assign its specialist")
        member = await self.storage.call(
            assign,
            store,
            controller,
            profile,
            authorized_input,
            request_key,
            expected_version,
            expected_storage_generation,
        )
        self._wake.set()
        return member

    async def revoke_specialist(self: Any, team_id: str, agent_id: str) -> dict[str, Any]:
        from .specialists import revoke

        store = await self.storage.call(self.registry.open, team_id)
        controller = await self._controller(store)
        if controller is None:
            raise SwarmConflictError("This team's active controller must revoke its specialist")
        await self.storage.call(revoke, store, controller, agent_id)
        for key, actor in list(self._worker_actors.items()):
            if key[0] == team_id and actor.agent_id == agent_id:
                task = self._workers.get(key)
                if task is not None:
                    task.cancel()
        return await self.storage.call(store.get_record, "agents", agent_id)
