"""Explicit synthetic reference workload, never imported or run at application boot.

SLO fixed before measurement: p95 ready-to-claim <=30 seconds with 1,000
concurrent fake workers and 1,000,000 pre-existing task/agent records. Browser
projection must remain <=64KiB and <=2Hz; frame time requires separate browser
evidence and is deliberately not claimed by this storage benchmark.
"""

from __future__ import annotations

import asyncio
import math
import time
from concurrent.futures import ThreadPoolExecutor

from jarvis.core.swarm_types import AgentRecord, BudgetLimits, TaskRecord, TaskSpec, TeamCreate
from jarvis.swarm.store import SwarmAccessError, _json, task_contract_hash

from .database import control_lane


def _task(identifier: str) -> TaskSpec:
    return TaskSpec(
        id=identifier,
        title="Verify arithmetic",
        description="Compute 19 * 23",
        acceptance="The result must equal 437",
        domain="general",
    )


def _seed(store, count):
    """Seed declared fake history using the production JSONB constraints/indexes."""
    agent = AgentRecord(
        id="history",
        team_id=store.team_id,
        name="Historical fixture",
        role="worker",
        state="completed",
    ).model_dump(mode="json")
    task = TaskRecord(
        **_task("history").model_dump(),
        team_id=store.team_id,
        state="succeeded",
        created_at=0,
        updated_at=0,
    ).model_dump(mode="json")
    for start in range(0, count // 2, 10_000):
        end = min(start + 10_000, count // 2)
        with store._tx(write=True) as connection:
            connection.raw.execute(
                "INSERT INTO agents (id,role,state,active,token_hash,record) "
                "SELECT 'historical-agent-'||n,'worker','completed',0,'unusable-fixture-token', "
                "%s::jsonb || jsonb_build_object('id','historical-agent-'||n) "
                "FROM generate_series(%s::bigint,%s::bigint) AS n",
                (_json(agent), start + 1, end),
            )
            connection.raw.execute(
                "INSERT INTO tasks (id,state,owner_id,fence,record) "
                "SELECT 'historical-task-'||n,'succeeded',NULL,0, "
                "%s::jsonb || jsonb_build_object('id','historical-task-'||n) "
                "FROM generate_series(%s::bigint,%s::bigint) AS n",
                (_json(task), start + 1, end),
            )


async def run_reference(registry, *, historical=1_000_000, workers=1000, hardware=None):
    """Return measured metrics using fake computation and real storage clients."""
    if historical < 0 or historical % 2 or not 1 <= workers <= 1000:
        raise ValueError("Use an even historical count and 1 to 1000 fake workers")
    import psutil

    process = psutil.Process()
    baseline_memory = process.memory_info().rss
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=64))
    control_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="swarm-control")

    async def control_call(function, *args, **kwargs):
        def call():
            with control_lane():
                return function(*args, **kwargs)

        return await loop.run_in_executor(control_pool, call)

    team = await asyncio.to_thread(
        registry.create,
        TeamCreate(
            name="Distributed reference benchmark",
            goal="Synthetic arithmetic and durability proof",
            request_key="reference-benchmark",
            mode="distributed",
            limits=BudgetLimits(
                concurrency=workers,
                worker_limit=str(historical // 2 + workers),
                token_budget="10000000000",  # noqa: S106 - synthetic quantity
                runtime_seconds=3600,
            ),
        ),
    )  # noqa: S106
    store = registry.open(team["id"])
    controller = store.acquire_controller("benchmark-controller", ttl=300)
    agents = [
        await asyncio.to_thread(store.add_agent, controller, f"Fixture {index}")
        for index in range(workers)
    ]
    seed_start = time.perf_counter()
    await asyncio.to_thread(_seed, store, historical)
    seed_seconds = time.perf_counter() - seed_start
    with store._tx(write=True) as connection:
        connection.raw.execute("ANALYZE tasks")
        connection.raw.execute("ANALYZE agents")
    store.renew_controller(controller, ttl=300)
    store.transition(controller, "running")
    tasks = [_task(f"benchmark-{index}") for index in range(workers)]
    await asyncio.to_thread(store.add_tasks, controller, tasks)
    # The entire ready batch becomes visible at one transaction commit. Measure
    # from this observed commit, independent of history-seeding wall time.
    ready_at = time.perf_counter()
    release = asyncio.Event()
    started = asyncio.Event()
    claimed = []
    queue_delays, execution_durations, snapshots = [], [], []
    peak_memory = baseline_memory
    renew_stop = asyncio.Event()
    delivered_events = set()

    async def maintenance():
        nonlocal peak_memory
        while not renew_stop.is_set():
            await control_call(store.renew_controller, controller, ttl=300)
            peak_memory = max(peak_memory, process.memory_info().rss)
            try:
                await asyncio.wait_for(renew_stop.wait(), 10)
            except TimeoutError:
                pass  # Intentional bounded sampling interval.

    async def world_projection():
        while not renew_stop.is_set():
            begin = time.perf_counter()
            world = await control_call(store.world)
            size = len(_json(world).encode("utf-8"))
            snapshots.append(
                dict(
                    size=size,
                    elapsed=time.perf_counter() - begin,
                    nodes=len(world["agents"]) + len(world["groups"]),
                )
            )
            try:
                await asyncio.wait_for(renew_stop.wait(), 0.5)
            except TimeoutError:
                pass  # At most two bounded snapshots per second.

    async def event_delivery():
        while not renew_stop.is_set():
            await control_call(store.pump_delivery, limit=100)
            hints = await control_call(registry.delivery.poll, store, "benchmark", limit=100)
            # This consumer observes durable references only; business effects
            # remain the independently committed, idempotent store operations.
            delivered_events.update(hint.event_id for hint in hints)
            await control_call(registry.delivery.ack_many, store, hints)
            try:
                await asyncio.wait_for(renew_stop.wait(), 0.5)
            except TimeoutError:
                pass  # A bounded consumer batch cannot monopolize controllers.

    async def worker(index):
        actor = await asyncio.to_thread(
            store.claim, controller, agents[index].agent_id, tasks[index].id
        )
        queue_delays.append(time.perf_counter() - ready_at)
        claimed.append(actor)
        if len(claimed) == workers:
            started.set()
        done = asyncio.Event()

        async def heartbeat():
            while not done.is_set():
                try:
                    await asyncio.wait_for(done.wait(), 25)
                except TimeoutError:
                    try:
                        await control_call(store.heartbeat, actor)
                    except SwarmAccessError:
                        current = await control_call(store.get_record, "tasks", actor.task_id)
                        if current["state"] == "succeeded" and current["fence"] == actor.task_fence:
                            return  # The final commit won the race with this heartbeat.
                        raise

        heartbeat_job = asyncio.create_task(heartbeat())
        try:
            await release.wait()
            begin = time.perf_counter()
            answer = 19 * 23
            reservation = await asyncio.to_thread(
                store.reserve, actor, "synthetic-call", "100", "0"
            )
            await asyncio.to_thread(store.reconcile, controller, reservation["id"], "100", "0")
            artifact = await asyncio.to_thread(
                store.capture_result, controller, actor, "Result", str(answer), "result"
            )
            verdict = dict(
                accepted=answer == 437,
                verifier_id="benchmark-arithmetic-verifier",
                kind="review",
                quality=1.0,
                contract_hash=task_contract_hash(tasks[index]),
            )
            await asyncio.to_thread(
                store.finish, actor, str(answer), [artifact["id"]], verdict, controller=controller
            )
            execution_durations.append(time.perf_counter() - begin)
        finally:
            done.set()
            await heartbeat_job

    jobs = [asyncio.create_task(worker(index)) for index in range(workers)]
    monitors = [
        asyncio.create_task(maintenance()),
        asyncio.create_task(world_projection()),
        asyncio.create_task(event_delivery()),
    ]
    peak_running = 0
    try:
        while not started.is_set():
            await asyncio.sleep(0.1)
            for job in jobs:
                if job.done() and job.exception() is not None:
                    raise job.exception()
        with store._tx() as connection:
            peak_running = connection.execute(
                "SELECT count(*) FROM attempts WHERE state='running'"
            ).fetchone()[0]
        release.set()
        await asyncio.gather(*jobs)
        elapsed = time.perf_counter() - ready_at
    finally:
        release.set()
        renew_stop.set()
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await asyncio.gather(*monitors)
        control_pool.shutdown(wait=True)
    with store._tx() as connection:
        history_counts = dict(
            agents=connection.execute("SELECT count(*) FROM agents WHERE active=0").fetchone()[0],
            tasks=connection.execute(
                "SELECT count(*) FROM tasks WHERE id LIKE 'historical-task-%'"
            ).fetchone()[0],
        )
        first_page_plan = connection.raw.execute(
            "EXPLAIN (FORMAT JSON) SELECT record FROM tasks ORDER BY __swarm_order LIMIT 50"
        ).fetchone()[0][0]["Plan"]
    with registry.database.transaction() as connection:
        catalog_rows = connection.execute("SELECT count(*) FROM teams").fetchone()[0]

    def percentile(values, percent):
        ordered = sorted(values)
        return ordered[max(0, math.ceil(len(ordered) * percent) - 1)]

    return dict(
        workload="Real PostgreSQL/Redis/S3 clients, synthetic arithmetic workers",
        redis_observed_durable_events=len(delivered_events),
        hardware=hardware or {},
        historical_records=history_counts,
        fake_workers=workers,
        peak_simultaneous_running_attempts=peak_running,
        db_pool_max=registry.config.max_connections,
        seed_seconds=seed_seconds,
        duration_seconds=elapsed,
        completed_tasks=len(execution_durations),
        throughput_tasks_per_second=len(execution_durations) / elapsed,
        queue_seconds={
            "p50": percentile(queue_delays, 0.5),
            "p95": percentile(queue_delays, 0.95),
            "max": max(queue_delays),
        },
        queue_slo_seconds=30,
        queue_slo_pass=percentile(queue_delays, 0.95) <= 30,
        snapshot_max_bytes=max(item["size"] for item in snapshots),
        snapshot_max_nodes=max(item["nodes"] for item in snapshots),
        snapshot_p95_seconds=percentile([item["elapsed"] for item in snapshots], 0.95),
        snapshots=len(snapshots),
        projection_size_pass=max(item["size"] for item in snapshots) <= 65536,
        browser_frame_time="UNPROVEN; requires a separate rendered browser benchmark",
        process_rss_baseline_bytes=baseline_memory,
        process_rss_peak_bytes=peak_memory,
        catalog_rows=catalog_rows,
        task_first_page_plan=first_page_plan,
        team_tokens_used=store.get()["tokens_used"],
    )
