"""Latency instrumentation for the voice hot path (Wave 0 — omni-latency suite).

Design constraints (CLAUDE.md AP-9 / AP-18):
  * ``perf_counter`` marks are free; emission is fire-and-forget on the EventBus
    so the hot path never ``await``s telemetry.
  * A disabled tracker is a near-zero no-op (guarded before any allocation).
  * Telemetry must NEVER break the hot path — every emit path swallows errors.
  * VPS-safe: stdlib only, no GPU / audio / Windows dependency.

``LatencyPhase`` and ``LatencySpan`` live in ``jarvis.core.events`` (the wire
format) and are re-exported here so this module is the single import surface for
hot-path latency instrumentation.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING
from uuid import UUID

from jarvis.core.events import LatencyPhase, LatencySpan

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

__all__ = [
    "CURRENT_TRACKER",
    "LatencyPhase",
    "LatencySpan",
    "LatencyTracker",
    "mark_phase",
]

_SOURCE = "telemetry.latency"
logger = logging.getLogger(__name__)


CURRENT_TRACKER: contextvars.ContextVar[LatencyTracker | None] = (
    contextvars.ContextVar("jarvis.telemetry.latency.tracker", default=None)
)


def mark_phase(phase: str, *, detail: str = "") -> None:
    """Mark on the current ContextVar-bound tracker, if any."""
    tracker = CURRENT_TRACKER.get()
    if tracker is None:
        return
    tracker.mark(phase, detail=detail)


class LatencyTracker:
    """Records perf_counter milestones for one voice turn and emits LatencySpans."""

    __slots__ = (
        "_bus",
        "_trace_id",
        "_enabled",
        "_anchor_ns",
        "_stages",
        "_errors",
    )

    def __init__(
        self,
        bus: EventBus | None,
        trace_id: UUID,
        *,
        enabled: bool = True,
    ) -> None:
        self._bus = bus
        self._trace_id = trace_id
        self._enabled = bool(enabled) and bus is not None
        self._anchor_ns = time.perf_counter_ns()
        self._stages: dict[str, float] = {}
        self._errors: list[str] = []

    @property
    def trace_id(self) -> UUID:
        return self._trace_id

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def anchor_ns(self) -> int:
        return self._anchor_ns

    def stages_snapshot(self) -> dict[str, float]:
        return dict(self._stages)

    def record_error(self, message: str) -> None:
        if len(self._errors) >= 16:
            return
        self._errors.append(message[:240])

    def errors_snapshot(self) -> tuple[str, ...]:
        return tuple(self._errors)

    def mark(self, phase: str, *, detail: str = "") -> None:
        """Emit a span covering anchor -> now (cumulative turn latency)."""
        now = time.perf_counter_ns()
        offset_ms = (now - self._anchor_ns) / 1_000_000
        self._stages.setdefault(phase, offset_ms)
        if not self._enabled:
            return
        self._emit(phase, self._anchor_ns, now, detail)

    @contextlib.contextmanager
    def span(self, phase: str, *, detail: str = "") -> Iterator[None]:
        """Measure the enclosed (synchronous) block as one span."""
        if not self._enabled:
            yield
            return
        start = time.perf_counter_ns()
        try:
            yield
        finally:
            end = time.perf_counter_ns()
            self._emit(phase, start, end, detail)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _emit(self, phase: str, start_ns: int, end_ns: int, detail: str) -> None:
        try:
            span = LatencySpan(
                trace_id=self._trace_id,
                source_layer=_SOURCE,
                phase=phase,
                duration_ms=(end_ns - start_ns) / 1_000_000,
                t_start_ns=start_ns,
                t_end_ns=end_ns,
                detail=detail,
            )
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No event loop on this call path — skip rather than block.
                return
            # Fire-and-forget; mirrors pipeline.py's intentional create_task use.
            loop.create_task(self._publish(span))  # noqa: RUF006
        except Exception:  # noqa: BLE001 — telemetry never breaks the hot path
            logger.debug("latency span emit failed", exc_info=True)

    async def _publish(self, span: LatencySpan) -> None:
        bus = self._bus
        if bus is None:
            return
        try:
            await bus.publish(span)
        except Exception:  # noqa: BLE001 — a broken subscriber must not bubble up
            logger.debug("latency span publish failed", exc_info=True)
