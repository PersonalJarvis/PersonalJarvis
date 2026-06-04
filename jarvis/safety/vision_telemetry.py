"""VisionTelemetryCollector — akkumuliert VisionInjected-Events.

Bus-Subscriber fuer die Permanent-Vision-Pipeline. Haelt drei Buckets:

- ``bytes_total``: Summe der Bytes aller injizierten Screen-Observations.
- ``injects_total``: Zahl der Injects (pro trace_id maximal einmal gezaehlt).
- ``avg_capture_age_ms``: Running-Average des Alters zum Inject-Zeitpunkt.

De-Duplication ueber ``trace_id`` verhindert Doppelzaehlung bei Retries.
Reine Telemetrie — keine Rate-Limits, keine Budgets. Rate-/Budget-Logik
lebt separat in ``jarvis.control`` (Protocol ``CostMeter``) und ist nicht
Teil dieses Moduls.

Bekannte Einschraenkung: ``_seen_trace_ids`` waechst unbegrenzt. In
MVP-Laufzeiten (Sessions bis ~Stunden) kein Problem; ein spaeterer Fix
koennte ein LRU-Set sein.
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
