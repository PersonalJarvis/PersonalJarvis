"""Handler-Protocol: jeder Job-Type implementiert ``async def execute(spec, input)``."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class HandlerResult:
    """Was ein Handler zurueckgibt.

    Der Runner persistiert das zu einer Run-Zeile. ``output`` ist der
    Primaer-Payload (stdout / response-body / LLM-text). ``metrics`` ist
    strukturierte Side-Info (http-status, tokens, cost, duration).
    """
    success: bool
    output: str
    exit_code: int = 0
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class JobHandler(Protocol):
    """Strukturelles Protocol — jeder Handler ist runtime-checkable."""

    async def execute(
        self,
        spec: Any,
        input_data: dict[str, Any],
    ) -> HandlerResult:
        """Fuehrt einen Job aus. ``spec`` ist das jeweilige JobSpec-Modell,
        ``input_data`` kommt aus manual-trigger / webhook-body / cron-default.
        """
        ...
