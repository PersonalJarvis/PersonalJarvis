"""Generator: consults skillbook, applies retry-with-delay strategy, drives LATSEngine.

The generator translates a Task into one or more actor invocations, respects
skillbook rules that modify retry/delay behavior, records a trace step per
attempt for the Reflector, and (FORENSICS gap #5 closer) writes knowledge-
graph entities + relations per interaction so ``skb_entities`` and
``skb_relations`` stop being permanently empty.

KG schema per task run:
  - Entity ``actor:<name>`` (kind=actor) — upserted once per actor.
  - Entity ``task:<task_id>`` (kind=task) — created per task with intent attr.
  - Relation per attempt: ``task -> actor`` with kind ``invoked`` or
    ``invoked_failed``, attrs carrying step_idx + status + failure_mode.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol, runtime_checkable

from skillbook.guardrails.lats import LATSEngine, StepStatus
from skillbook.memory_layer.models import Entity, Relation, Rule, TraceStep
from skillbook.memory_layer.store import MemoryStore

from .models import Task, TaskResult, TaskStatus


@runtime_checkable
class Actor(Protocol):
    name: str
    async def call(self, params: dict) -> dict: ...


@dataclass(slots=True)
class Generator:
    memory: MemoryStore
    engine: LATSEngine
    actors: dict[str, Actor] = field(default_factory=dict)
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep

    def register_actor(self, actor: Actor) -> None:
        self.actors[actor.name] = actor

    async def run_task(self, task: Task) -> TaskResult:
        rules = await self.memory.query_rules(actor=task.actor)
        rule = self._select_rule(rules)

        diagnostics_collected = []
        task_started_ns = time.time_ns()

        # KG: upsert actor + task entities at the start of the run.
        await self._record_actor_entity(task.actor, task_started_ns)
        await self._record_task_entity(task, task_started_ns)

        # Strategy: search_alternatives -> route through LATSEngine MCTS.
        if rule is not None and rule.strategy.get("kind") == "search_alternatives":
            return await self._run_with_mcts(task, rule, diagnostics_collected)

        max_attempts = 1
        delay_s = 0.0
        if rule is not None and rule.strategy.get("kind") == "retry_with_delay":
            max_attempts = 1 + int(rule.strategy.get("max_retries", 0))
            delay_s = float(rule.strategy.get("delay_s", 0))

        for attempt in range(max_attempts):
            outcome = await self.engine.execute_step(
                task_id=task.id,
                step_idx=attempt,
                actor=task.actor,
                params=task.params,
                call=self._actor_call(task.actor),
            )

            await self.memory.put_trace_step(
                TraceStep(
                    task_id=task.id,
                    step_idx=attempt,
                    actor=task.actor,
                    params=dict(task.params),
                    result=(
                        outcome.result
                        if outcome.result is not None
                        else (
                            outcome.diagnostic.model_dump(mode="json")
                            if outcome.diagnostic is not None
                            else {}
                        )
                    ),
                    status=outcome.status.value.upper(),
                    ts_ns=time.time_ns(),
                )
            )

            # KG: relation per attempt, kind reflects outcome.
            await self._record_invocation_relation(task, attempt, outcome)

            if outcome.status is StepStatus.OK:
                return TaskResult(
                    task_id=task.id,
                    status=TaskStatus.OK,
                    result=outcome.result,
                    diagnostics=diagnostics_collected,
                    rule_applied=(rule.id if rule is not None else None),
                )

            if outcome.diagnostic is not None:
                diagnostics_collected.append(outcome.diagnostic)

            if attempt < max_attempts - 1 and delay_s > 0:
                await self.sleep_fn(delay_s)
            elif attempt < max_attempts - 1:
                await self.sleep_fn(0)

        return TaskResult(
            task_id=task.id,
            status=TaskStatus.BLOCKED_BY_GUARDRAIL,
            result=None,
            diagnostics=diagnostics_collected,
            rule_applied=(rule.id if rule is not None else None),
        )

    async def _run_with_mcts(
        self,
        task: Task,
        rule: Rule,
        diagnostics_collected: list,
    ) -> TaskResult:
        """Strategy 'search_alternatives': drive LATSEngine.search_and_execute over
        a list of candidate parameter sets from the rule, then write a single
        trace step + KG relation for the winning (or last-failing) candidate."""
        candidates = list(rule.strategy.get("candidate_params", []))
        iterations = int(rule.strategy.get("iterations", 8))
        outcome = await self.engine.search_and_execute(
            task_id=task.id,
            actor=task.actor,
            candidate_params=candidates,
            call=self._actor_call(task.actor),
            iterations=iterations,
        )

        await self.memory.put_trace_step(
            TraceStep(
                task_id=task.id,
                step_idx=0,
                actor=task.actor,
                params=dict(outcome.params),
                result=(
                    outcome.result
                    if outcome.result is not None
                    else (
                        outcome.diagnostic.model_dump(mode="json")
                        if outcome.diagnostic is not None
                        else {}
                    )
                ),
                status=outcome.status.value.upper(),
                ts_ns=time.time_ns(),
            )
        )
        await self._record_invocation_relation(task, 0, outcome)

        if outcome.status is StepStatus.OK:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.OK,
                result=outcome.result,
                diagnostics=diagnostics_collected,
                rule_applied=rule.id,
            )
        if outcome.diagnostic is not None:
            diagnostics_collected.append(outcome.diagnostic)
        return TaskResult(
            task_id=task.id,
            status=TaskStatus.BLOCKED_BY_GUARDRAIL,
            result=None,
            diagnostics=diagnostics_collected,
            rule_applied=rule.id,
        )

    async def _record_actor_entity(self, actor: str, ts_ns: int) -> None:
        await self.memory.put_entity(
            Entity(
                id=f"actor:{actor}",
                kind="actor",
                attrs={"name": actor},
                valid_from_ns=ts_ns,
                valid_to_ns=None,
            )
        )

    async def _record_task_entity(self, task: Task, ts_ns: int) -> None:
        await self.memory.put_entity(
            Entity(
                id=f"task:{task.id}",
                kind="task",
                attrs={"intent": task.intent, "actor": task.actor},
                valid_from_ns=ts_ns,
                valid_to_ns=None,
            )
        )

    async def _record_invocation_relation(
        self,
        task: Task,
        attempt: int,
        outcome,
    ) -> None:
        kind = "invoked" if outcome.status is StepStatus.OK else "invoked_failed"
        attrs: dict = {"step_idx": attempt, "status": outcome.status.value.upper()}
        if outcome.diagnostic is not None:
            attrs["failure_mode"] = outcome.diagnostic.failure_mode.value
            attrs["evidence"] = outcome.diagnostic.evidence
        await self.memory.put_relation(
            Relation(
                id=f"rel_{uuid.uuid4().hex}",
                src_id=f"task:{task.id}",
                dst_id=f"actor:{task.actor}",
                kind=kind,
                attrs=attrs,
                valid_from_ns=time.time_ns(),
                valid_to_ns=None,
            )
        )

    @staticmethod
    def _select_rule(rules: list[Rule]) -> Rule | None:
        """Pick the most actionable rule: search_alternatives > retry_with_delay > first."""
        for r in rules:
            if r.strategy.get("kind") == "search_alternatives":
                return r
        for r in rules:
            if r.strategy.get("kind") == "retry_with_delay":
                return r
        return rules[0] if rules else None

    def _actor_call(self, actor_name: str) -> Callable[[dict], Awaitable[dict]]:
        actor = self.actors.get(actor_name)
        if actor is None:
            async def _missing(_: dict) -> dict:
                raise RuntimeError(f"Actor {actor_name!r} is not registered")
            return _missing

        async def _call(params: dict) -> dict:
            return await actor.call(params)

        return _call
