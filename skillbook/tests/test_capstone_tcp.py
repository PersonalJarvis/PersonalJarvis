"""Capstone variant: full ACE loop over real TCP transport (not the InProcess fake).

Closes the last "implemented but never on the hot-path" gap from FORENSICS:
AsyncioTcpTransport was unit-tested in isolation but the capstone loop ran
exclusively against the InProcess transport fake. This test runs the same
7-step scenario (timeout -> guardrail -> reflect -> curate -> sync ->
follow-up succeeds on both) over two AsyncioTcpTransport instances bound to
free 127.0.0.1 ports — the gossip envelope crosses an actual socket.

Because TCP delivery is asynchronous (unlike InProcess's synchronous gossip),
the test polls the peer's rule set with a short timeout instead of asserting
immediately after sync_once().
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

from skillbook.ace_core.models import Task, TaskStatus
from skillbook.agent import AgentInstance
from skillbook.p2p_sync.tcp_transport import AsyncioTcpTransport
from tests.fakes.llm import FakeLLM
from tests.fakes.symcon import FakeSymconActor


pytestmark = pytest.mark.capstone


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.05) -> None:
    """Poll predicate() until truthy or raise asyncio.TimeoutError."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if await predicate():
            return
        if asyncio.get_event_loop().time() > deadline:
            raise asyncio.TimeoutError(f"predicate never became true within {timeout}s")
        await asyncio.sleep(interval)


async def _build_tcp_pair(tmp_path: Path, seed: int):
    port_a = _free_port()
    port_b = _free_port()
    t_a = AsyncioTcpTransport(
        listen_host="127.0.0.1", listen_port=port_a,
        peer_addrs=[("127.0.0.1", port_b)],
    )
    t_b = AsyncioTcpTransport(
        listen_host="127.0.0.1", listen_port=port_b,
        peer_addrs=[("127.0.0.1", port_a)],
    )
    await t_a.start()
    await t_b.start()
    a = await AgentInstance.build(
        peer_id=f"A_tcp_{seed}",
        db_path=tmp_path / f"a_tcp_{seed}.db",
        llm=FakeLLM(),
        actors=[],
        transport=t_a,
    )
    b = await AgentInstance.build(
        peer_id=f"B_tcp_{seed}",
        db_path=tmp_path / f"b_tcp_{seed}.db",
        llm=FakeLLM(),
        actors=[],
        transport=t_b,
    )
    return a, b, t_a, t_b


async def test_capstone_closed_loop_over_real_tcp(tmp_path: Path, seed: int) -> None:
    a, b, t_a, t_b = await _build_tcp_pair(tmp_path, seed)
    try:
        # Step 1-5: timeout -> guardrail -> reflect -> curate (on A).
        a.register_actor(FakeSymconActor(name="magic_home_controller", failures_until_ok=1))
        first = await a.run_task(
            Task(id=f"t1_tcp_{seed}", intent="trigger_scene", actor="magic_home_controller")
        )
        assert first.status is TaskStatus.BLOCKED_BY_GUARDRAIL

        rules_a = await a.memory.query_rules()
        assert len(rules_a) == 1
        learned = rules_a[0]

        # Step 6: gossip the learned rule to B over real TCP.
        assert await b.memory.query_rules() == [], "B should be empty before sync"
        await a.sync_once()

        # TCP delivery is async — wait until B's store has the rule (max 3s).
        async def b_has_rule() -> bool:
            return any(r.id == learned.id for r in await b.memory.query_rules())

        await _wait_for(b_has_rule, timeout=3.0)
        synced = (await b.memory.query_rules())[0]
        assert synced.id == learned.id
        assert synced.source_peer == f"A_tcp_{seed}"

        # Step 7: follow-up on both peers — the rule should drive a successful retry.
        a.register_actor(FakeSymconActor(name="magic_home_controller", failures_until_ok=1))
        b.register_actor(FakeSymconActor(name="magic_home_controller", failures_until_ok=1))
        followup_a = await a.run_task(
            Task(id=f"t2a_tcp_{seed}", intent="trigger_scene", actor="magic_home_controller")
        )
        followup_b = await b.run_task(
            Task(id=f"t2b_tcp_{seed}", intent="trigger_scene", actor="magic_home_controller")
        )
        assert followup_a.status is TaskStatus.OK
        assert followup_b.status is TaskStatus.OK
        assert followup_a.rule_applied == learned.id
        assert followup_b.rule_applied == learned.id
    finally:
        await a.close()
        await b.close()
        await t_a.stop()
        await t_b.stop()
