"""VisionTelemetryCollector — accumulates VisionInjected events.

Bus subscriber for the permanent-vision pipeline. Holds three buckets:

- ``bytes_total``: sum of the bytes of all injected screen observations.
- ``injects_total``: number of injects (counted at most once per trace_id).
- ``avg_capture_age_ms``: running average of the age at inject time.

De-duplication via ``trace_id`` prevents double-counting on retries.
Pure telemetry — no rate limits, no budgets. Rate/budget logic
lives separately in ``jarvis.control`` (protocol ``CostMeter``) and is not
part of this module.

Known limitation: ``_seen_trace_ids`` grows unbounded. Not a problem for
MVP-scale runtimes (sessions up to ~hours); a later fix could
switch to an LRU set.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from jarvis.core.events import VisionInjected

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

log = logging.getLogger(__name__)


@dataclass
class VisionTelemetryCollector:
    bytes_total: int = 0
    injects_total: int = 0
    sum_capture_age_ms: int = 0
    _seen_trace_ids: set[UUID] = field(default_factory=set)

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(VisionInjected, self._on_vision_injected)

    async def _on_vision_injected(self, event: VisionInjected) -> None:
        if event.trace_id in self._seen_trace_ids:
            return
        self._seen_trace_ids.add(event.trace_id)
        self.bytes_total += event.bytes_size
        self.injects_total += 1
        self.sum_capture_age_ms += event.capture_age_ms

    @property
    def avg_capture_age_ms(self) -> float:
        if self.injects_total == 0:
            return 0.0
        return self.sum_capture_age_ms / self.injects_total

    def snapshot(self) -> dict[str, int | float]:
        return {
            "vision.bytes_total": self.bytes_total,
            "vision.injects_total": self.injects_total,
            "vision.avg_capture_age_ms": self.avg_capture_age_ms,
        }
