"""Owner retries and publication must not revoke unrelated live execution."""

import asyncio

import pytest

from jarvis.core.swarm_types import TaskSpec, TeamCreate
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, task_contract_hash
from tests.fakes.swarm_runtime import runtime, terminal


@pytest.mark.asyncio
async def test_duplicate_start_preserves_running_attempt_and_controller(tmp_path):
    gate = asyncio.Event()
    service = runtime(tmp_path, gate=gate)
    try:
        team = await service.create_team(
            TeamCreate(
                name="Retry",
                goal="42",
                request_key="retry",
                tasks=[TaskSpec(id="work", title="Work", description="42", acceptance="Return 42")],
            )
        )
        await service.control(team["id"], "start")
        await asyncio.wait_for(service.brain_factory.started.wait(), 10)
        actor = next(iter(service._worker_actors.values()))
        controller = service._controllers[team["id"]]
        current = await service.team(team["id"])
        duplicate = await service.control(
            team["id"],
            "start",
            expected_version=current["version"],
            expected_storage_generation=current.get("storage_generation", ""),
        )
        assert duplicate["version"] == current["version"]
        assert service._controllers[team["id"]] == controller
        assert not service._tokens[(team["id"], "work")].is_cancelled()
        service.registry.open(team["id"]).validate_actor(actor, writing=True)
        gate.set()
        assert (await terminal(service, team["id"]))["state"] == "succeeded"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_publication_preserves_active_lead_token_and_fences_stale_epoch(tmp_path):
    service = runtime(tmp_path)
    try:
        team = await service.create_team(
            TeamCreate(
                name="Publish",
                goal="42",
                request_key="publish",
                tasks=[
                    TaskSpec(id=key, title=key, description="42", acceptance="Return 42")
                    for key in ("done", "lead-active")
                ],
            )
        )
        store = service.registry.open(team["id"])
        controller = await service._controller(store)
        store.transition(controller, "running")
        member = store.add_agent(controller, "Worker")
        actor = store.claim(controller, member.agent_id, "done")
        artifact = store.capture_result(controller, actor, "answer.txt", "42", "result")
        task = store.read_task(actor, "done")
        store.finish(
            actor,
            "42",
            [artifact["id"]],
            {
                "accepted": True,
                "kind": "review",
                "verifier_id": "trusted-test-grader",
                "contract_hash": task_contract_hash(task),
            },
            controller=controller,
        )
        lead = store.claim(controller, team["lead_id"], "lead-active")
        version = store.get()["version"]
        with pytest.raises(SwarmConflictError, match="restored"):
            await service.control(
                team["id"], "pause", expected_version=version, expected_storage_generation="stale"
            )
        with pytest.raises(SwarmConflictError, match="restored"):
            await service.publish(
                team["id"],
                artifact["id"],
                "stale",
                expected_version=version,
                expected_storage_generation="stale",
            )
        assert store.records("publications") == []
        with store._tx() as connection:
            assert connection.execute("SELECT count(*) FROM destinations").fetchone()[0] == 0
        published = await service.publish(
            team["id"],
            artifact["id"],
            "selected",
            expected_version=version,
            expected_storage_generation="",
        )
        assert published["state"] == "published"
        store.validate_actor(lead, writing=True)
        with pytest.raises(SwarmAccessError):
            store.queue_publication(controller, None, artifact["id"], "artifact", "missing-lead")
    finally:
        await service.stop()
