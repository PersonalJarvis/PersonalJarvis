"""Retired pools outlive actual users and are reclaimed without unbounded registries."""

import asyncio
import copy
import gc
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.swarm.distributed.lifetime import wait_for_retirement
from jarvis.swarm.distributed.registry import PostgresTeamRegistry
from jarvis.swarm.registry_router import RegistryRouter
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, TeamRegistry
from tests.fakes.swarm_runtime import runtime
from tests.unit.swarm.test_distributed import config, secrets


class Resource:
    def __init__(self):
        self.closed = threading.Event()
        self.closes = 0

    def close(self):
        self.closes += 1
        self.closed.set()


def backend():
    return PostgresTeamRegistry(
        config(), secrets(), database=Resource(), objects=Resource(), delivery=Resource()
    )


def test_retirement_waits_for_live_store_then_closes_resources_once(tmp_path):
    old, new = backend(), backend()
    router = RegistryRouter(TeamRegistry(tmp_path))
    router.set_remote(old)
    store = old._store("a" * 32, "local-user")
    router.set_remote(new)
    router.collect_retired()
    assert not old.database.closed.is_set()
    assert store.registry.objects.closes == 0
    facade = copy.copy(store)
    with pytest.raises(SwarmAccessError, match="replaced"):
        old.open("a" * 32)
    del store
    gc.collect()
    assert not old.database.closed.is_set()
    del facade
    gc.collect()
    assert old.delivery.closed.wait(2)
    router.collect_retired()
    assert router.retired() == ()
    old.close()
    assert old.database.closes == old.objects.closes == old.delivery.closes == 1
    router.close()


def test_retirement_waits_for_active_registry_operation_even_without_a_store(tmp_path):
    old, new = backend(), backend()
    router = RegistryRouter(TeamRegistry(tmp_path))
    router.set_remote(old)
    started, release = threading.Event(), threading.Event()

    def operation():
        with old.lifetime.operation():
            started.set()
            assert release.wait(5)
            # Nested work already owned by this operation remains legal.
            with old.lifetime.operation():
                assert not old.database.closed.is_set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(operation)
        assert started.wait(2)
        router.set_remote(new)
        router.collect_retired()
        assert not old.database.closed.is_set()
        release.set()
        future.result(timeout=2)
    assert old.delivery.closed.wait(2)
    router.close()


def test_reconfiguration_refuses_unbounded_retained_pool_growth(tmp_path):
    router = RegistryRouter(TeamRegistry(tmp_path))
    stores = []
    for _ in range(9):
        current = backend()
        router.set_remote(current)
        stores.append(current._store("a" * 32, "local-user"))
    candidate = backend()
    with pytest.raises(SwarmConflictError, match="prior distributed"):
        router.set_remote(candidate)
    candidate.close()
    assert len(router.retired()) == 8
    stores.clear()
    gc.collect()
    for item in router.retired():
        assert item.delivery.closed.wait(2)
    router.close()


class BlockingResource(Resource):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def close(self):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("Synthetic cleanup was not released")
        super().close()


@pytest.mark.asyncio
async def test_late_store_release_after_service_stop_joins_real_cleanup_thread(tmp_path):
    service = runtime(tmp_path)
    registry = backend()
    blocked = BlockingResource()
    registry.database = blocked
    service.registry.set_remote(registry)
    stores = [registry._store("a" * 32, "local-user")]
    stopped = False
    try:
        await service.stop()
        stopped = True
        assert not blocked.closed.is_set()
        assert not any(thread.name == "swarm-retire_0" for thread in threading.enumerate())
        stores.clear()
        gc.collect()
        assert await asyncio.to_thread(blocked.entered.wait, 2)
        threads = [thread for thread in threading.enumerate() if thread.name == "swarm-retire_0"]
        assert len(threads) == 1 and not threads[0].daemon
        assert not await asyncio.to_thread(wait_for_retirement, 0.01)
        blocked.release.set()
        assert await asyncio.to_thread(wait_for_retirement, 3)
        assert not threads[0].is_alive()
        assert not any(thread.name == "swarm-retire_0" for thread in threading.enumerate())
        assert blocked.closes == registry.objects.closes == registry.delivery.closes == 1
    finally:
        stores.clear()
        blocked.release.set()
        gc.collect()
        if not stopped:
            await service.stop()
        assert await asyncio.to_thread(wait_for_retirement, 3)
