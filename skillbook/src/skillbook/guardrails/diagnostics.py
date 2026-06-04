"""AgentDoG diagnostic taxonomy and analyzer (ADR-0008).

Three orthogonal axes — Source, FailureMode, Consequence — encoded as StrEnum
so the wire format is JSON-stable across the Python/SQLite/Pydantic/sandbox
boundary. AgentDoG.diagnose() inspects an actor exception and produces a
Diagnostic that can flow directly into Reflector evidence and into a
suggested skillbook rule.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


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


class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    source: Source
    failure_mode: FailureMode
    consequence: Consequence
    evidence: str
    suggested_rule: dict[str, Any] | None = None


class AgentDoG:
    """Maps observed runtime failures to structured Diagnostics.

    The mapping is intentionally explicit (no LLM here) so that the same
    exception type always produces the same diagnostic — Reflectors and
    capstone assertions can depend on it.
    """

    def diagnose(
        self,
        *,
        source: Source,
        actor: str | None,
        exception: BaseException,
    ) -> Diagnostic:
        if source is Source.ACTOR_INVOCATION and actor is None:
            raise ValueError(
                "Source.ACTOR_INVOCATION requires an actor name for evidence"
            )

        if isinstance(exception, TimeoutError):
            actor_name = actor or "<unknown>"
            return Diagnostic(
                source=source,
                failure_mode=FailureMode.TIMEOUT,
                consequence=Consequence.RECOVERABLE,
                evidence=(
                    f"Actor {actor_name!r} did not respond within the deadline: "
                    f"{exception!s}"
                ),
                suggested_rule={
                    "trigger": {"actor": actor_name},
                    "strategy": {
                        "kind": "retry_with_delay",
                        "delay_s": 3,
                        "max_retries": 2,
                    },
                },
            )

        # Unknown exception class: report as inconsistent state, no automatic rule.
        actor_repr = repr(actor) if actor else "<unknown>"
        return Diagnostic(
            source=source,
            failure_mode=FailureMode.INCONSISTENT_STATE,
            consequence=Consequence.RECOVERABLE,
            evidence=f"Actor {actor_repr} raised {type(exception).__name__}: {exception!s}",
            suggested_rule=None,
        )
