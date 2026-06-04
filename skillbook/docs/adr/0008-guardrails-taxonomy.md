# ADR-0008: Guardrails taxonomy — three-axis AgentDoG + LATS rollback

**Status:** Accepted
**Date:** 2026-05-26

## Context

AgentDoG (Diagnostic Guardrail Framework) per the survey classifies operational risk along three orthogonal axes — Source, Failure Mode, Consequence — and emits structured evidence that the Reflector's gap function can consume directly. LATS (Language Agent Tree Search) wraps this with MCTS-style alternative path exploration and a circuit breaker that rolls back to the last safe point on failure.

## Decision

- Encode the three AgentDoG axes as **Python enums** under `guardrails.diagnostics`:

  ```python
  class Source(StrEnum):
      ACTOR_INVOCATION = "actor_invocation"
      SENSOR_INTERPRETATION = "sensor_interpretation"
      RULE_APPLICATION = "rule_application"
      PLANNING_STEP = "planning_step"

  class FailureMode(StrEnum):
      TIMEOUT = "timeout"
      HALLUCINATED_RESPONSE = "hallucinated_response"
      INCONSISTENT_STATE = "inconsistent_state"
      POLICY_VIOLATION = "policy_violation"

  class Consequence(StrEnum):
      NONE = "none"
      RECOVERABLE = "recoverable"
      PHYSICAL_HARM = "physical_harm"
      SECURITY_BREACH = "security_breach"
  ```

  All three follow the five-layer enum pattern from the parent project (Python `StrEnum` ↔ SQLite TEXT ↔ pydantic `Literal` ↔ wire JSON). Cross-module string drift will be caught by a parity test.

- A `Diagnostic` pydantic model carries `(source, failure_mode, consequence, evidence: str, suggested_rule: dict | None)`.

- **LATS** is implemented as a simple in-memory **branching planner**: before calling an actor, generate up to K (default 3) alternative invocation parameter sets; score each via the skillbook rule cache (rules with `priority` boost matching branches); execute in score order; on failure, **roll back to the planner state before that branch and try the next**. The "MCTS" framing in the survey is more elaborate than this capstone needs — see ADR-0009.

- **Circuit-breaker** logic: if a single actor times out more than `MAX_ATTEMPTS_BEFORE_OPEN` (default 2) in the LATS branch, the breaker opens for that actor for the remainder of the task; the planner is forced to pick a non-affected actor or abort with a `FailureMode.TIMEOUT` diagnostic.

## Consequences

- The exact strings emitted by AgentDoG flow unchanged into Reflector gap-function evidence and ultimately into skillbook rule triggers — no string-drift bugs.
- Adding a fourth `Source` or `FailureMode` is one-line: extend the enum, parity test fails if a downstream consumer doesn't accept it, fix and re-run.

## Alternatives considered

- **Tag strings ad hoc**: would invite the multi-layer enum drift bug (parent BUG-008, four recurrences).
- **Class hierarchy of `Diagnostic` subclasses**: more verbose than enum, and pattern-matching on enum is cleaner in callers.
