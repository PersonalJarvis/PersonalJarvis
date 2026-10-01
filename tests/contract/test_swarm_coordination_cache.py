"""Actual Redis presence/coordination observations and PG fallback contracts."""

import json

from jarvis.swarm.control_requests import drain, enqueue
from tests.contract.test_swarm_distributed import pytestmark, registries, running  # noqa: F401


def test_real_redis_coordination_cache_is_consumed_by_authorized_discovery(registries):  # noqa: F811
    make, _ = registries
    registry = make()
    fixture = running(registry)
    coordinator = fixture.store.add_agent(fixture.controller, "Coordinator", role="coordinator")
    request = enqueue(
        fixture.store,
        coordinator,
        "report_summary",
        {"reason": "Report group progress", "summary": "Worker is active", "task_ids": ["work"]},
        "group-summary",
    )
    assert drain(fixture.store, fixture.controller)[0]["state"] == "applied"
    cache = registry.delivery.coordination
    assert cache.refresh(fixture.store) == 3
    client = registry.delivery.client()
    key = cache.key(fixture.team["id"], coordinator.agent_id)
    assert 0 < client.ttl(key) <= 60
    raw = client.get(key)
    assert fixture.actor.token not in raw and fixture.controller.token not in raw
    assert json.loads(raw)["report_id"] == request["id"]
    peer = next(
        peer
        for peer in fixture.store.discover(fixture.actor, "work")
        if peer["id"] == coordinator.agent_id
    )
    assert peer["coordination_observation"]["source"] == "redis"
    assert peer["coordination_observation"]["observed_report"]["id"] == request["id"]
    client.delete(key)
    cache.discard(fixture.team["id"])
    peer = next(
        peer
        for peer in fixture.store.discover(fixture.actor, "work")
        if peer["id"] == coordinator.agent_id
    )
    assert peer["coordination_observation"]["source"] == "postgresql"
    assert fixture.store.heartbeat(fixture.actor)
