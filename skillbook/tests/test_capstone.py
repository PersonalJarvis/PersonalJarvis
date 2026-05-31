"""Capstone: full ACE closed-loop scenario from the goal.

  1. Inject an IP-Symcon actor timeout on instance A.
  2. Guardrails block it via LATS, producing a structured Diagnostic.
  3. The agent's gap function fires (BLOCKED_BY_GUARDRAIL outcome).
  4. RecursiveReflector analyses the trace in a subprocess REPL sandbox.
  5. Curator stores the proposed correction as a skillbook delta on A.
  6. P2P sync replicates the delta to instance B.
  7. A follow-up task runs cleanly on both A and B because the learned rule
     drives a retry-with-delay strategy.

Runs under ``pytest skillbook/ -x -q --seeds=5``: each seed re-runs the full
loop with a different task-id prefix to defeat any hidden non-determinism in
trace storage, sandbox subprocess startup, or sync ordering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skillbook.ace_core.models import Task, TaskStatus
from skillbook.agent import AgentInstance
from tests.fakes.transport import InProcessTransport
from tests.fakes.symcon import FakeSymconActor
from tests.fakes.llm import FakeLLM


pytestmark = pytest.mark.capstone


async def _build_pair(tmp_path: Path, seed: int) -> tuple[AgentInstance, AgentInstance]:
    t_a, t_b = InProcessTransport.pair()
    a = await AgentInstance.build(
        peer_id=f"A_{seed}",
        db_path=tmp_path / f"a_{seed}.db",
        llm=FakeLLM(),
        actors=[],
        transport=t_a,
    )
    b = await AgentInstance.build(
        peer_id=f"B_{seed}",
        db_path=tmp_path / f"b_{seed}.db",
        llm=FakeLLM(),
        actors=[],
        transport=t_b,
    )
    return a, b


async def test_capstone_closed_loop(tmp_path: Path, seed: int) -> None:
    a, b = await _build_pair(tmp_path, seed)
    try:
        # Step 1-3: inject a timeout on instance A.
        timeout_actor_a = FakeSymconActor(name="magic_home_controller", failures_until_ok=1)
        a.register_actor(timeout_actor_a)

        first = await a.run_task(
            Task(id=f"t1_{seed}", intent="trigger_scene", actor="magic_home_controller")
        )

        # Guardrail-blocked first attempt; A has no prior rule, so retry budget is 1.
        assert first.status is TaskStatus.BLOCKED_BY_GUARDRAIL
        assert any(d.suggested_rule is not None for d in first.diagnostics), (
            "AgentDoG should have proposed a corrective rule"
        )

        # Step 4-5: Reflector ran inside run_task; Curator stored the rule on A.
        rules_a = await a.memory.query_rules()
        assert len(rules_a) == 1, f"expected 1 learned rule, found {len(rules_a)}"
        learned = rules_a[0]
        assert learned.trigger == {"actor": "magic_home_controller"}
        assert learned.strategy["kind"] == "retry_with_delay"
        assert int(learned.strategy.get("max_retries", 0)) >= 1
        assert learned.source_peer == f"A_{seed}"

        # Step 6: P2P sync propagates the delta to instance B.
        rules_b_before = await b.memory.query_rules()
        assert rules_b_before == [], "B should be empty before sync"

        await a.sync_once()

        rules_b_after = await b.memory.query_rules()
        assert len(rules_b_after) == 1
        synced = rules_b_after[0]
        assert synced.id == learned.id
        assert synced.trigger == {"actor": "magic_home_controller"}
        assert synced.strategy["kind"] == "retry_with_delay"
        assert synced.source_peer == f"A_{seed}", "source_peer attribution must survive sync"

        # Step 7: Follow-up task runs cleanly on BOTH peers because the rule
        # drives the retry strategy. Register fresh flaky actors on both sides;
        # each will time out on call 1 and succeed on call 2.
        a.register_actor(FakeSymconActor(name="magic_home_controller", failures_until_ok=1))
        b.register_actor(FakeSymconActor(name="magic_home_controller", failures_until_ok=1))

        followup_a = await a.run_task(
            Task(id=f"t2a_{seed}", intent="trigger_scene", actor="magic_home_controller")
        )
        followup_b = await b.run_task(
            Task(id=f"t2b_{seed}", intent="trigger_scene", actor="magic_home_controller")
        )

        assert followup_a.status is TaskStatus.OK, (
            f"A follow-up failed: diagnostics={followup_a.diagnostics}"
        )
        assert followup_b.status is TaskStatus.OK, (
            f"B follow-up failed: diagnostics={followup_b.diagnostics}"
        )
        assert followup_a.rule_applied == learned.id
        assert followup_b.rule_applied == learned.id

        # No new rules should have been added on the follow-up (Curator dedupes).
        assert len(await a.memory.query_rules()) == 1
        assert len(await b.memory.query_rules()) == 1
    finally:
        await a.close()
        await b.close()


async def test_capstone_idempotent_sync(tmp_path: Path, seed: int) -> None:
    """Repeated sync cycles after the loop must not duplicate or lose the rule."""
    a, b = await _build_pair(tmp_path, seed)
    try:
        a.register_actor(FakeSymconActor(name="x", failures_until_ok=1))
        await a.run_task(Task(id=f"idem_{seed}", intent="x", actor="x"))

        for _ in range(3):
            await a.sync_once()
            await b.sync_once()

        rules_a = await a.memory.query_rules(include_tombstones=True)
        rules_b = await b.memory.query_rules(include_tombstones=True)
        assert len(rules_a) == 1
        assert len(rules_b) == 1
        assert rules_a[0].id == rules_b[0].id
    finally:
        await a.close()
        await b.close()
