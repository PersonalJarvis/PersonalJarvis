"""WorkLedger — one read-only list of everything Jarvis is working on.

Missions, scheduled tasks, society quests and workflows each keep their own
store and state machine. The ledger reads them through the read methods those
stores already expose and folds every record into a :class:`WorkItem` with one
shared status vocabulary, so "what is running, what waits, what needs me" has
one answer.

The ledger never writes: no store, no state, no schedule. It starts nothing,
calls no model and builds no service — a source that is not running in this
process (e.g. the society runtime before first use) is reported as
``unavailable`` instead of being started. One failing source never hides the
others; it is reported as ``error`` and logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

WorkSource = Literal["mission", "task", "quest", "workflow"]
WORK_SOURCES: tuple[str, ...] = ("mission", "task", "quest", "workflow")

WorkStatus = Literal[
    "queued",  # created, not started yet
    "scheduled",  # waits for its time or trigger
    "running",
    "waiting",  # held back for a reason other than capacity (e.g. a busy agent)
    "waiting_capacity",  # parked until its subscription has capacity (missions)
    "paused",  # switched off by the person
    "done",
    "failed",
    "cancelled",
    "skipped",  # its slot passed without running (e.g. app was closed)
]
WORK_STATUSES: tuple[str, ...] = (
    "queued",
    "scheduled",
    "running",
    "waiting",
    "waiting_capacity",
    "paused",
    "done",
    "failed",
    "cancelled",
    "skipped",
)
#: Statuses that never change again.
FINISHED_STATUSES: frozenset[str] = frozenset({"done", "failed", "cancelled", "skipped"})

# Source state -> ledger status. Every state of every source is listed; the
# parity test fails when a source grows a state that is missing here.
MISSION_STATUS: Mapping[str, str] = {
    "PENDING": "queued",
    "RUNNING": "running",
    "CRITIQUING": "running",
    "LOOPING": "running",
    "WAITING_CAPACITY": "waiting_capacity",
    "APPROVED": "done",
    "FAILED": "failed",
    "TIMED_OUT": "failed",
    "CANCELLED": "cancelled",
}
TASK_STATUS: Mapping[str, str] = {
    "pending": "queued",
    "scheduled": "scheduled",
    "paused": "paused",
    "running": "running",
    "completed": "done",
    "failed": "failed",
    "cancelled": "cancelled",
    "interrupted": "failed",
}
QUEST_STATUS: Mapping[str, str] = {
    "open": "queued",
    "assigned": "queued",
    "running": "running",
    "done": "done",
    "failed": "failed",
    "cancelled": "cancelled",
}
WORKFLOW_RUN_STATUS: Mapping[str, str] = {
    "pending": "queued",
    "running": "running",
    "completed": "done",
    "failed": "failed",
    "cancelled": "cancelled",
    "missed": "skipped",
}

_TITLE_MAX = 160


@dataclass(frozen=True, slots=True)
class WorkItem:
    """One unit of work, whatever system owns it."""

    source: str
    id: str
    title: str
    status: str
    #: The owning system's own state, unchanged (e.g. ``WAITING_CAPACITY``).
    source_state: str
    needs_attention: bool = False
    attention_reason: str = ""
    created_ms: int | None = None
    updated_ms: int | None = None
    due_ms: int | None = None
    owner: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "source_state": self.source_state,
            "needs_attention": self.needs_attention,
            "attention_reason": self.attention_reason,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "due_ms": self.due_ms,
            "owner": self.owner,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SourceReport:
    """Whether a source could be read: ``ok``, ``unavailable`` or ``error``."""

    source: str
    state: str
    count: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "state": self.state,
            "count": self.count,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class WorkSnapshot:
    items: tuple[WorkItem, ...]
    sources: tuple[SourceReport, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = dict.fromkeys(WORK_STATUSES, 0)
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return {
            "items": [item.to_dict() for item in self.items],
            "counts": counts,
            "needs_attention": sum(1 for item in self.items if item.needs_attention),
            "sources": [report.to_dict() for report in self.sources],
        }


Getter = Callable[[], Any]


def _title(text: Any, fallback: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return fallback
    if len(cleaned) <= _TITLE_MAX:
        return cleaned
    return cleaned[: _TITLE_MAX - 1].rstrip() + "…"


def _ms_from_ns(value: Any) -> int | None:
    try:
        return int(value) // 1_000_000 if value is not None else None
    except (TypeError, ValueError):  # a malformed timestamp shows as unknown, not as an error
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):  # a malformed timestamp shows as unknown, not as an error
        return None


def _status(mapping: Mapping[str, str], state: str, source: str) -> str:
    status = mapping.get(state)
    if status is None:
        # A state this ledger does not know yet: show it, never drop it.
        log.warning("work ledger: unknown %s state %r", source, state)
        return "queued"
    return status


# ---------------------------------------------------------------- adapters


def mission_items(rows: Iterable[Mapping[str, Any]]) -> list[WorkItem]:
    from jarvis.missions.stream_evidence import clean_request_body

    items: list[WorkItem] = []
    for row in rows:
        state = str(row.get("state") or "")
        waiting = state == "WAITING_CAPACITY"
        items.append(
            WorkItem(
                source="mission",
                id=str(row.get("id") or ""),
                title=_title(clean_request_body(str(row.get("prompt") or "")), "Mission"),
                status=_status(MISSION_STATUS, state, "mission"),
                source_state=state,
                # A parked mission waits for the person: resume on its own once
                # capacity returns, or an explicit paid-run decision.
                needs_attention=waiting,
                attention_reason="capacity_decision" if waiting else "",
                created_ms=_int_or_none(row.get("created_ms")),
                updated_ms=_int_or_none(row.get("updated_ms")),
            )
        )
    return items


def task_items(rows: Iterable[Mapping[str, Any]]) -> list[WorkItem]:
    items: list[WorkItem] = []
    for row in rows:
        state = str(row.get("state") or "")
        items.append(
            WorkItem(
                source="task",
                id=str(row.get("id") or ""),
                title=_title(row.get("title"), "Task"),
                status=_status(TASK_STATUS, state, "task"),
                source_state=state,
                created_ms=_ms_from_ns(row.get("created_at_ns")),
                updated_ms=_ms_from_ns(row.get("finished_at_ns") or row.get("started_at_ns")),
                due_ms=_ms_from_ns(row.get("due_at_ns")) if state == "scheduled" else None,
                owner=str(row.get("trigger_type") or ""),
                detail=_title(row.get("last_error"), ""),
            )
        )
    return items


def quest_items(records: Iterable[Any]) -> list[WorkItem]:
    items: list[WorkItem] = []
    for quest in records:
        data = quest.to_dict() if hasattr(quest, "to_dict") else dict(quest)
        state = str(data.get("state") or "")
        result = data.get("result") or {}
        status = _status(QUEST_STATUS, state, "quest")
        detail = ""
        if status == "queued" and result.get("status") == "waiting":
            # The taker vetoed "not now" (busy, limit): the quest knocks again.
            status = "waiting"
            detail = str(result.get("reason") or "")
        items.append(
            WorkItem(
                source="quest",
                id=str(data.get("quest_id") or ""),
                title=_title(data.get("title") or data.get("text"), "Quest"),
                status=status,
                source_state=state,
                created_ms=_int_or_none(data.get("created_ms")),
                updated_ms=_int_or_none(data.get("updated_ms")),
                owner=str(data.get("agent_id") or ""),
                detail=detail,
            )
        )
    return items


def workflow_items(
    definitions: Iterable[Mapping[str, Any]], runs: Iterable[Mapping[str, Any]]
) -> list[WorkItem]:
    names = {str(d.get("id")): str(d.get("name") or "") for d in definitions}
    items: list[WorkItem] = []
    for run in runs:
        state = str(run.get("state") or "")
        workflow_id = str(run.get("workflow_id") or "")
        items.append(
            WorkItem(
                source="workflow",
                id=str(run.get("id") or ""),
                title=_title(names.get(workflow_id), "Workflow"),
                status=_status(WORKFLOW_RUN_STATUS, state, "workflow"),
                source_state=state,
                created_ms=_ms_from_ns(run.get("started_at_ns")),
                updated_ms=_ms_from_ns(run.get("finished_at_ns") or run.get("started_at_ns")),
                owner=str(run.get("trigger") or ""),
                detail=_title(run.get("error"), ""),
            )
        )
    for definition in definitions:
        due = definition.get("next_run_at_ns")
        if not definition.get("enabled") or due is None:
            continue
        items.append(
            WorkItem(
                source="workflow",
                id=str(definition.get("id") or ""),
                title=_title(definition.get("name"), "Workflow"),
                status="scheduled",
                source_state="enabled",
                created_ms=_ms_from_ns(definition.get("created_at_ns")),
                due_ms=_ms_from_ns(due),
                owner="cron",
            )
        )
    return items


# ---------------------------------------------------------------- ledger


class WorkLedger:
    """Reads the live stores through getters (they may come and go at runtime)."""

    def __init__(
        self,
        *,
        missions: Getter | None = None,
        tasks: Getter | None = None,
        quests: Getter | None = None,
        workflows: Getter | None = None,
    ) -> None:
        self._readers: dict[str, Callable[[int], Awaitable[list[WorkItem] | None]]] = {
            "mission": self._reader(missions, self._read_missions),
            "task": self._reader(tasks, self._read_tasks),
            "quest": self._reader(quests, self._read_quests),
            "workflow": self._reader(workflows, self._read_workflows),
        }

    @staticmethod
    def _reader(
        getter: Getter | None, read: Callable[[Any, int], Awaitable[list[WorkItem]]]
    ) -> Callable[[int], Awaitable[list[WorkItem] | None]]:
        async def _run(limit: int) -> list[WorkItem] | None:
            source = getter() if getter is not None else None
            if source is None:
                return None
            return await read(source, limit)

        return _run

    async def snapshot(
        self,
        *,
        sources: Iterable[str] | None = None,
        include_finished: bool = True,
        limit: int = 100,
    ) -> WorkSnapshot:
        """Read every requested source concurrently; never raises for one source."""
        wanted = [s for s in (sources or WORK_SOURCES) if s in self._readers]
        limit = max(1, min(int(limit), 1000))
        results = await asyncio.gather(
            *(self._readers[name](limit) for name in wanted), return_exceptions=True
        )
        items: list[WorkItem] = []
        reports: list[SourceReport] = []
        for name, result in zip(wanted, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                log.warning("work ledger: reading %s failed", name, exc_info=result)
                reports.append(SourceReport(name, "error", message=type(result).__name__))
                continue
            if result is None:
                reports.append(SourceReport(name, "unavailable"))
                continue
            kept = [i for i in result if include_finished or i.status not in FINISHED_STATUSES]
            reports.append(SourceReport(name, "ok", count=len(kept)))
            items.extend(kept)
        items.sort(key=_order)
        return WorkSnapshot(tuple(items), tuple(reports))

    # Each reader uses only a read method the owning store already exposes.

    @staticmethod
    async def _read_missions(manager: Any, limit: int) -> list[WorkItem]:
        store = getattr(manager, "store", manager)
        return mission_items(await store.list_missions(limit=limit))

    @staticmethod
    async def _read_tasks(store: Any, limit: int) -> list[WorkItem]:
        return task_items(await store.list(limit=limit))

    @staticmethod
    async def _read_quests(runtime: Any, limit: int) -> list[WorkItem]:
        quests = getattr(runtime, "quests", runtime)
        return quest_items(await quests.list(limit=limit))

    @staticmethod
    async def _read_workflows(store: Any, limit: int) -> list[WorkItem]:
        definitions = await store.list_workflows()
        runs = await store.list_runs(limit=limit)
        return workflow_items(definitions, runs)


_ORDER = {
    status: index
    for index, status in enumerate(
        (
            "waiting_capacity",
            "running",
            "waiting",
            "queued",
            "scheduled",
            "paused",
            "failed",
            "done",
            "cancelled",
            "skipped",
        )
    )
}


def _order(item: WorkItem) -> tuple[int, int, int]:
    """Needs-attention first, then by status, then most recent first."""
    recent = item.updated_ms or item.created_ms or 0
    return (0 if item.needs_attention else 1, _ORDER.get(item.status, 99), -recent)


__all__ = [
    "FINISHED_STATUSES",
    "MISSION_STATUS",
    "QUEST_STATUS",
    "TASK_STATUS",
    "WORKFLOW_RUN_STATUS",
    "WORK_SOURCES",
    "WORK_STATUSES",
    "SourceReport",
    "WorkItem",
    "WorkLedger",
    "WorkSnapshot",
    "WorkSource",
    "WorkStatus",
    "mission_items",
    "quest_items",
    "task_items",
    "workflow_items",
]
