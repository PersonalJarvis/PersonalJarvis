"""LATSEngine: pre-emptive actor-call wrapper that produces structured rollbacks."""

from __future__ import annotations

import asyncio

import pytest

from skillbook.guardrails.diagnostics import (
    AgentDoG,
    Consequence,
    FailureMode,
    Source,
)
from skillbook.guardrails.lats import (
    CircuitBreaker,
    LATSEngine,
    StepOutcome,
    StepStatus,
)


def test_circuit_breaker_opens_after_max_attempts() -> None:
    cb = CircuitBreaker(max_attempts=2)
    assert cb.is_open("a") is False
    cb.record_failure("a")
    assert cb.is_open("a") is False
    cb.record_failure("a")
    assert cb.is_open("a") is True


def test_circuit_breaker_resets_on_success() -> None:
    cb = CircuitBreaker(max_attempts=2)
    cb.record_failure("a")
    cb.record_success("a")
    cb.record_failure("a")
    assert cb.is_open("a") is False


async def test_execute_step_returns_ok_on_success() -> None:
    engine = LATSEngine(dog=AgentDoG())

    async def call(params: dict) -> dict:
        return {"value": 42}

    outcome = await engine.execute_step(
        task_id="task_1", step_idx=0, actor="ok_actor", params={}, call=call
    )
    assert outcome.status is StepStatus.OK
    assert outcome.result == {"value": 42}
    assert outcome.diagnostic is None


async def test_execute_step_rolls_back_on_timeout() -> None:
    engine = LATSEngine(dog=AgentDoG())

    async def call(params: dict) -> dict:
        raise TimeoutError("boom")

    outcome = await engine.execute_step(
        task_id="task_2", step_idx=0, actor="magic_home_controller", params={}, call=call
    )
    assert outcome.status is StepStatus.BLOCKED_BY_GUARDRAIL
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.source is Source.ACTOR_INVOCATION
    assert outcome.diagnostic.failure_mode is FailureMode.TIMEOUT
    assert outcome.diagnostic.consequence is Consequence.RECOVERABLE
    assert outcome.diagnostic.suggested_rule is not None
    assert outcome.diagnostic.suggested_rule["trigger"]["actor"] == "magic_home_controller"


async def test_execute_step_opens_breaker_after_repeated_failures() -> None:
    engine = LATSEngine(dog=AgentDoG(), breaker=CircuitBreaker(max_attempts=2))

    async def fail(params: dict) -> dict:
        raise TimeoutError("...")

    await engine.execute_step("t3", 0, "x", {}, fail)
    await engine.execute_step("t3", 1, "x", {}, fail)
    assert engine.breaker.is_open("x") is True

    # Subsequent calls should short-circuit without invoking call()
    invoked = False

    async def succeed(params: dict) -> dict:
        nonlocal invoked
        invoked = True
        return {"value": 1}

    outcome = await engine.execute_step("t3", 2, "x", {}, succeed)
    assert outcome.status is StepStatus.BLOCKED_BY_GUARDRAIL
    assert invoked is False
    assert outcome.diagnostic is not None
    assert "breaker" in outcome.diagnostic.evidence.lower()


async def test_execute_step_short_circuit_diagnostic_carries_suggested_rule() -> None:
    """Even when the breaker short-circuits, the diagnostic should not regress to
    silent failure — Reflector must still see a structured signal (AD-OE6)."""
    engine = LATSEngine(dog=AgentDoG(), breaker=CircuitBreaker(max_attempts=1))

    async def fail(params: dict) -> dict:
        raise TimeoutError("...")

    await engine.execute_step("t4", 0, "y", {}, fail)
    outcome = await engine.execute_step("t4", 1, "y", {}, fail)
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.suggested_rule is not None
