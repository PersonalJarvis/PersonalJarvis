"""Fake CostMeter for tests.

Can be wired up with `over_task_after_usd=0.5` so the meter returns
true from `over_task_budget()` once it exceeds 0.50 USD —
perfect for the budget-exceed integration test.
"""
from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from jarvis.core.protocols import CostRecord


class FakeCostMeter:
    name: str = "fake-cost-meter"

    def __init__(
        self,
        *,
        over_task_after_usd: float = float("inf"),
        over_daily_after_usd: float = float("inf"),
    ) -> None:
        self._over_task_after = over_task_after_usd
        self._over_daily_after = over_daily_after_usd
        self._totals_per_trace: dict[UUID, float] = defaultdict(float)
        self._total_today: float = 0.0
        self.records: list[CostRecord] = []
        self.started: list[tuple[UUID, str, str]] = []
        self.closed: list[UUID] = []

    def start(self, trace_id: UUID, provider: str, model: str) -> None:
        self.started.append((trace_id, provider, model))

    def add(self, record: CostRecord) -> None:
        self.records.append(record)
        self._totals_per_trace[record.trace_id] += record.usd
        self._total_today += record.usd

    def total_for(self, trace_id: UUID) -> float:
        return self._totals_per_trace[trace_id]

    def total_today(self) -> float:
        return self._total_today

    def over_task_budget(self, trace_id: UUID) -> bool:
        return self._totals_per_trace[trace_id] > self._over_task_after

    def over_daily_budget(self) -> bool:
        return self._total_today > self._over_daily_after

    def close(self, trace_id: UUID) -> None:
        self.closed.append(trace_id)
