"""Deterministic authority fixtures for durable Swarm storage tests."""

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from jarvis.core.swarm_types import TaskSpec, TeamCreate
from jarvis.swarm.store import TeamRegistry, task_contract_hash


@dataclass
class StorageClock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


def task(task_id="work", **kwargs):
    return TaskSpec(
        id=task_id,
        title=task_id,
        description="Produce a verified result",
        acceptance="Result is independently checked",
        **kwargs,
    )


def running_team(path: Path, *, clock=None, tasks=None, **kwargs):
    clock = clock or StorageClock()
    registry = TeamRegistry(path, clock=clock)
    team = registry.create(
        TeamCreate(
            name="Research",
            goal="Complete the requested work",
            request_key="create",
            tasks=[task()] if tasks is None else tasks,
            **kwargs,
        )
    )
    store = registry.open(team["id"])
    controller = store.acquire_controller("first-instance")
    store.transition(controller, "running")
    member = store.add_agent(controller, "Worker")
    return SimpleNamespace(
        registry=registry, store=store, controller=controller, member=member, clock=clock, team=team
    )


def accepted_result(fixture, actor, result="Verified result", evidence=None, **verdict):
    store = fixture.store
    if evidence is None:
        evidence = [
            store.capture_result(fixture.controller, actor, "Result", result, "result")["id"]
        ]
    verification = dict(
        accepted=True,
        verifier_id="trusted-verifier",
        kind="review",
        quality=1.0,
        contract_hash=task_contract_hash(store.read_task(actor, actor.task_id)),
        **verdict,
    )
    return store.finish(actor, result, evidence, verification, controller=fixture.controller)
