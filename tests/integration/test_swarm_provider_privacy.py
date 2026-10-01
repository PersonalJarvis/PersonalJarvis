"""Provider failures stay useful without persisting remote error payloads."""

import json
import logging
from decimal import Decimal

import pytest

from jarvis.brain.swarm_factory import SwarmProvider
from jarvis.core.swarm_types import TaskSpec, TeamCreate
from tests.fakes.swarm_runtime import runtime, terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 429, 503])
async def test_provider_error_body_is_absent_from_durable_state_and_owner_views(
    tmp_path, caplog, status
):
    caplog.set_level(logging.DEBUG)
    marker = "private-error-body-should-never-be-persisted"

    class FailingBrain:
        async def complete(self, request):
            failure = RuntimeError(marker)
            failure.status_code = status
            raise failure
            yield  # pragma: no cover - model the streaming protocol

    class Factory:
        async def create(self):
            return SwarmProvider(FailingBrain(), "test-api", "test-model", Decimal("1"))

        def failed(self, provider):
            return  # This fake deliberately has no timed recovery.

    root = tmp_path / "swarm"
    service = runtime(root)
    service.brain_factory = Factory()
    try:
        team = await service.create_team(
            TeamCreate(
                name="Provider privacy",
                goal="Verify provider privacy",
                request_key="privacy",
                tasks=[TaskSpec(id="one", title="One", description="42", acceptance="Return 42")],
            )
        )
        await service.control(team["id"], "start")
        final = await terminal(service, team["id"])
        assert final["state"] == ("failed" if status == 400 else "blocked")
        owner_payloads = [final, await service.world(team["id"])]
        for collection in ("tasks", "events", "artifacts"):
            owner_payloads.append(await service.records(team["id"], collection))
        assert marker not in json.dumps(owner_payloads)
    finally:
        await service.stop()
    assert marker not in caplog.text
    # Also inspect persisted WAL/database bytes, not just filtered API projections.
    for path in root.rglob("*"):
        if path.is_file():
            assert marker.encode() not in path.read_bytes(), path.name
