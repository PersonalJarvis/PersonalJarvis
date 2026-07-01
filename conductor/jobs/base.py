"""Handler protocol: every job type implements ``async def execute(spec, input)``."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class HandlerResult:
    """What a handler returns.

    The runner persists this to a run row. ``output`` is the primary
    payload (stdout / response body / LLM text). ``metrics`` is
    structured side info (http status, tokens, cost, duration).
    """
    success: bool
    output: str
    exit_code: int = 0
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class JobHandler(Protocol):
    """Structural protocol — every handler is runtime-checkable."""

    async def execute(
        self,
        spec: Any,
        input_data: dict[str, Any],
    ) -> HandlerResult:
        """Runs a job. ``spec`` is the respective JobSpec model,
        ``input_data`` comes from a manual trigger / webhook body / cron default.
        """
        ...
