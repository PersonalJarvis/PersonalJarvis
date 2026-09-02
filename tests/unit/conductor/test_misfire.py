"""BUG-212 — the Conductor skips a stale slot instead of catching it up.

The Conductor is a standalone package and carries its own copy of the
misfire grace; the first test pins it to ``jarvis.core.misfire`` so the two
schedulers can never disagree about what "too late" means.
"""
from __future__ import annotations

import time
from typing import Any

from conductor.core.scheduler import MISFIRE_GRACE_NS, Scheduler
from jarvis.core.misfire import MISFIRE_GRACE_NS as JARVIS_GRACE_NS

_HOUR_NS = 3_600 * 1_000_000_000


def test_the_conductor_grace_matches_the_app_wide_policy() -> None:
    assert MISFIRE_GRACE_NS == JARVIS_GRACE_NS


class _Store:
    def __init__(self, next_run_at_ns: int) -> None:
        self.rows: list[dict[str, Any]] = [{
            "id": "job-1", "name": "Daily Standup", "enabled": 1,
            "schedule_type": "cron", "schedule_expr": "0 9 * * 1-5",
            "next_run_at_ns": next_run_at_ns,
        }]

    async def list_jobs(self) -> list[dict[str, Any]]:
        return list(self.rows)

    async def set_next_run(self, jid: str, ns: int | None) -> None:
        self.rows[0]["next_run_at_ns"] = ns


class _Runner:
    def __init__(self) -> None:
        self.triggered: list[str] = []

    async def trigger(self, jid: str, *, trigger: str) -> str:
        self.triggered.append(jid)
        return "run"


async def test_a_stale_slot_is_skipped_not_run() -> None:
    now = time.time_ns()
    store = _Store(now - 6 * _HOUR_NS)      # 09:00 seen at 15:00
    runner = _Runner()
    scheduler = Scheduler(store, runner)  # type: ignore[arg-type]

    await scheduler._tick()

    assert runner.triggered == []
    assert store.rows[0]["next_run_at_ns"] > now


async def test_a_slot_inside_the_grace_window_runs() -> None:
    now = time.time_ns()
    store = _Store(now - 5 * 60 * 1_000_000_000)
    runner = _Runner()
    scheduler = Scheduler(store, runner)  # type: ignore[arg-type]

    await scheduler._tick()

    assert runner.triggered == ["job-1"]
    assert store.rows[0]["next_run_at_ns"] > now
