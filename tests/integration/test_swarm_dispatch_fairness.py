"""One saturated team cannot consume every dispatch round or another team's quota."""

import asyncio
from collections import Counter

import pytest

from jarvis.core.swarm_types import BudgetLimits, TaskSpec, TeamCreate
from tests.fakes.swarm_runtime import runtime


class InferenceBarrier(asyncio.Event):
    """Keep admitted calls running until the fixture explicitly releases them."""

    def __init__(self, expected):
        super().__init__()
        self.expected = expected
        self.arrivals = 0
        self.full = asyncio.Event()

    async def wait(self):
        self.arrivals += 1
        if self.arrivals >= self.expected:
            self.full.set()
        return await super().wait()


@pytest.mark.asyncio
async def test_two_ready_teams_rotate_after_eight_claims(tmp_path):
    gate = InferenceBarrier(31)
    service = runtime(tmp_path, gate=gate)
    teams = []
    try:
        for index in range(2):
            team = await service.storage.call(
                service.registry.create,
                TeamCreate(
                    name=f"Team {index}",
                    goal="42",
                    request_key=str(index),
                    # Hold every first-call reservation concurrently; default spend
                    # limits intentionally cannot fund this saturation fixture.
                    limits=BudgetLimits(
                        concurrency=32, worker_limit="64", token_budget=str(1_000_000)
                    ),
                    tasks=[
                        TaskSpec(
                            id=f"work-{i:02}",
                            title="Compute",
                            description="42",
                            acceptance="Return 42",
                        )
                        for i in range(40)
                    ],
                ),
            )
            store = service.registry.open(team["id"])
            controller = await service._controller(store)
            teams.append(store.transition(controller, "running"))
        await service._tick_teams(teams)
        # Created asyncio jobs are not proof of admitted inference. Observe all
        # reservations reaching the blocked fake provider before checking fairness.
        async with asyncio.timeout(10):
            await gate.full.wait()
        assert gate.arrivals == 31
        counts = Counter(key[0] for key in service._workers)
        assert sum(counts.values()) == 31  # One process slot remains for lead attention.
        assert counts[teams[0]["id"]] == 16
        assert counts[teams[1]["id"]] == 15
        assert all(key[0] == actor.team_id for key, actor in service._worker_actors.items())
        for team in teams:
            current = await service.team(team["id"])
            assert current["state"] == "running", current["reason"]
            assert 0 < int(current["tokens_reserved"]) <= int(current["limits"]["token_budget"])
    finally:
        await service.stop()
