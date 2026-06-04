"""LATS engine: per-actor circuit breaker + UCB1 tree search over candidates.

Two execution surfaces:

- :meth:`LATSEngine.execute_step` — single-candidate wrapper used by the
  Generator's retry loop. Wraps the actor call in a try/except, records
  failures on the per-actor :class:`CircuitBreaker`, returns a structured
  ``StepOutcome`` with a Diagnostic on failure.
- :meth:`LATSEngine.search_and_execute` — real Monte Carlo Tree Search over
  a sequence of candidate parameter sets. Uses the UCB1 / select / expand /
  backpropagate primitives from :mod:`skillbook.guardrails.mcts`; stops
  early on the first successful candidate; returns the best ``StepOutcome``
  observed across iterations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable, Sequence

from .diagnostics import (
    AgentDoG,
    Consequence,
    Diagnostic,
    FailureMode,
    Source,
)
from .mcts import PlanNode, backpropagate, best_child, expand, select


class StepStatus(StrEnum):
    OK = "ok"
    BLOCKED_BY_GUARDRAIL = "blocked_by_guardrail"


@dataclass(slots=True)
class StepOutcome:
    status: StepStatus
    actor: str
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    diagnostic: Diagnostic | None = None


class CircuitBreaker:
    def __init__(self, *, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        self._failures: dict[str, int] = {}

    def is_open(self, actor: str) -> bool:
        return self._failures.get(actor, 0) >= self.max_attempts

    def record_failure(self, actor: str) -> None:
        self._failures[actor] = self._failures.get(actor, 0) + 1

    def record_success(self, actor: str) -> None:
        if actor in self._failures:
            del self._failures[actor]

    def reset(self, actor: str | None = None) -> None:
        if actor is None:
            self._failures.clear()
        else:
            self._failures.pop(actor, None)


@dataclass(slots=True)
class LATSEngine:
    """Wraps actor invocations with AgentDoG diagnostics + CircuitBreaker rollback."""

    dog: AgentDoG = field(default_factory=AgentDoG)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    async def execute_step(
        self,
        task_id: str,
        step_idx: int,
        actor: str,
        params: dict[str, Any],
        call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> StepOutcome:
        if self.breaker.is_open(actor):
            diag = Diagnostic(
                source=Source.PLANNING_STEP,
                failure_mode=FailureMode.POLICY_VIOLATION,
                consequence=Consequence.RECOVERABLE,
                evidence=(
                    f"Circuit breaker is open for actor {actor!r}; refusing further "
                    f"invocations in this task."
                ),
                suggested_rule={
                    "trigger": {"actor": actor},
                    "strategy": {"kind": "use_alternative_actor"},
                },
            )
            return StepOutcome(
                status=StepStatus.BLOCKED_BY_GUARDRAIL,
                actor=actor,
                params=params,
                diagnostic=diag,
            )

        try:
            result = await call(params)
        except Exception as exc:  # noqa: BLE001
            self.breaker.record_failure(actor)
            diag = self.dog.diagnose(
                source=Source.ACTOR_INVOCATION,
                actor=actor,
                exception=exc,
            )
            return StepOutcome(
                status=StepStatus.BLOCKED_BY_GUARDRAIL,
                actor=actor,
                params=params,
                diagnostic=diag,
            )

        self.breaker.record_success(actor)
        return StepOutcome(
            status=StepStatus.OK,
            actor=actor,
            params=params,
            result=result,
        )

    async def search_and_execute(
        self,
        *,
        task_id: str,
        actor: str,
        candidate_params: Sequence[dict[str, Any]],
        call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        iterations: int = 8,
    ) -> StepOutcome:
        """Run UCB1-driven MCTS over candidate parameter sets."""
        if not candidate_params:
            return StepOutcome(
                status=StepStatus.BLOCKED_BY_GUARDRAIL,
                actor=actor,
                params={},
                diagnostic=Diagnostic(
                    source=Source.PLANNING_STEP,
                    failure_mode=FailureMode.POLICY_VIOLATION,
                    consequence=Consequence.RECOVERABLE,
                    evidence="search_and_execute called with no candidates",
                ),
            )

        root = PlanNode(state={"is_root": True})
        root.untried_actions = list(range(len(candidate_params)))
        last_failure: StepOutcome | None = None

        for _ in range(iterations):
            leaf = select(root)
            if leaf.untried_actions:
                leaf = expand(leaf, action_to_state=lambda idx: {"idx": idx})
            if leaf is root:
                break

            idx = leaf.state["idx"]
            params = candidate_params[idx]

            try:
                result = await call(params)
            except Exception as exc:  # noqa: BLE001
                diag = self.dog.diagnose(
                    source=Source.ACTOR_INVOCATION,
                    actor=actor,
                    exception=exc,
                )
                backpropagate(leaf, 0.0)
                last_failure = StepOutcome(
                    status=StepStatus.BLOCKED_BY_GUARDRAIL,
                    actor=actor,
                    params=dict(params),
                    diagnostic=diag,
                )
                continue

            backpropagate(leaf, 1.0)
            return StepOutcome(
                status=StepStatus.OK,
                actor=actor,
                params=dict(params),
                result=result,
            )

        if last_failure is not None:
            return last_failure
        return StepOutcome(
            status=StepStatus.BLOCKED_BY_GUARDRAIL,
            actor=actor,
            params={},
            diagnostic=Diagnostic(
                source=Source.PLANNING_STEP,
                failure_mode=FailureMode.POLICY_VIOLATION,
                consequence=Consequence.RECOVERABLE,
                evidence=f"MCTS exhausted {iterations} iterations without success",
            ),
        )

    @staticmethod
    def mcts_winner(root: PlanNode) -> PlanNode | None:
        return best_child(root)
