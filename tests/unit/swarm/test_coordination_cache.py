"""Redis observations are bounded, optional and never expand PG authority."""

import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jarvis.swarm.control_requests import drain, enqueue
from jarvis.swarm.distributed.coordination_cache import TTL_SECONDS, RedisCoordinationCache
from jarvis.swarm.store import SwarmAccessError
from tests.fakes.swarm_storage import running_team


class ObservedStore:
    def __init__(self, store):
        self.store = store
        self.depth = 0

    def __getattr__(self, name):
        return getattr(self.store, name)

    @contextmanager
    def _tx(self, **kwargs):
        with self.store._tx(**kwargs) as connection:
            self.depth += 1
            try:
                yield connection
            finally:
                self.depth -= 1


class FakeRedis:
    def __init__(self, store):
        self.store = store
        self.values = {}
        self.ttls = {}
        self.fail = False
        self.commands = []

    def pipeline(self, *, transaction):
        assert self.store.depth == 0
        assert transaction is False
        self.commands = []
        return self

    def __enter__(self):
        if self.fail:
            raise ConnectionError("Synthetic Redis loss")
        return self

    def __exit__(self, *args):
        return False

    def set(self, key, value, *, ex):
        self.commands.append(("set", key, value, ex))

    def get(self, key):
        self.commands.append(("get", key))

    def execute(self):
        assert self.store.depth == 0
        result = []
        for command in self.commands:
            if command[0] == "set":
                _, key, value, ttl = command
                self.values[key], self.ttls[key] = value, ttl
                result.append(True)
            else:
                result.append(self.values.get(command[1]))
        return result


def prepared(tmp_path):
    fixture = running_team(tmp_path)
    coordinator = fixture.store.add_agent(fixture.controller, "Coordinator", role="coordinator")
    request = enqueue(
        fixture.store,
        coordinator,
        "report_summary",
        {"reason": "Report scoped progress", "summary": "Work is ready", "task_ids": ["work"]},
        "summary",
    )
    assert drain(fixture.store, fixture.controller)[0]["state"] == "applied"
    observed = ObservedStore(fixture.store)
    redis = FakeRedis(observed)
    delivery = SimpleNamespace(config=SimpleNamespace(namespace="cache_test"), client=lambda: redis)
    cache = RedisCoordinationCache(delivery)
    return fixture, coordinator, request, observed, redis, cache


def test_cache_consumer_resolves_pg_report_and_keeps_redis_outside_transactions(tmp_path):
    fixture, coordinator, request, observed, redis, cache = prepared(tmp_path)
    assert cache.refresh(observed) == 3
    peers = fixture.store.discover(fixture.member, "work")
    enriched = cache.enrich(observed, fixture.member, peers)
    selected = next(peer for peer in enriched if peer["id"] == coordinator.agent_id)
    hint = selected["coordination_observation"]
    assert hint["source"] == "redis" and hint["authority"] == "hint"
    assert hint["observed_report"]["id"] == request["id"]
    assert hint["observed_report"]["task_ids"] == ["work"]
    assert set(redis.ttls.values()) == {TTL_SECONDS}
    raw = json.dumps(redis.values)
    assert fixture.controller.token not in raw
    assert fixture.member.token not in raw
    assert coordinator.token not in raw
    assert "Work is ready" not in raw


def test_cache_loss_and_expiry_use_honest_pg_fallback(tmp_path):
    fixture, coordinator, _, observed, redis, cache = prepared(tmp_path)
    cache.refresh(observed)
    fixture.clock.now += TTL_SECONDS + 1
    peers = fixture.store.discover(fixture.member, "work")
    assert all(
        peer["coordination_observation"]["source"] == "postgresql"
        for peer in cache.enrich(observed, fixture.member, peers)
    )
    redis.fail = True
    assert cache.refresh(observed) == 0
    selected = next(
        peer
        for peer in cache.enrich(observed, fixture.member, peers)
        if peer["id"] == coordinator.agent_id
    )
    assert selected["coordination_observation"]["observed_report"] is not None


def test_forged_cross_team_hint_never_changes_authoritative_peer_or_scope(tmp_path):
    fixture, coordinator, _, observed, _, cache = prepared(tmp_path)
    cache.refresh(observed)
    cached = cache._observations[(fixture.team["id"], coordinator.agent_id)]
    cached.update(team_id="b" * 32, role="lead", generation=999, report_id="c" * 32)
    peers = fixture.store.discover(fixture.member, "work")
    selected = next(
        peer
        for peer in cache.enrich(observed, fixture.member, peers)
        if peer["id"] == coordinator.agent_id
    )
    assert selected["role"] == "coordinator" and selected["generation"] == 1
    assert selected["coordination_observation"]["source"] == "postgresql"
    with pytest.raises(SwarmAccessError):
        cache.enrich(observed, replace(fixture.member, team_id="b" * 32), peers)
