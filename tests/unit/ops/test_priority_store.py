"""OpsPriorityStore: marks only, its own file, validated before anything is written."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from jarvis.ops import priority as priority_module
from jarvis.ops.priority import (
    PRIORITY_LEVELS,
    OpsPriorityStore,
    PriorityError,
    PriorityMark,
)


@pytest.fixture
def store(tmp_path: Path) -> OpsPriorityStore:
    return OpsPriorityStore(tmp_path / "ops" / "ops.sqlite")


async def test_put_get_all_and_delete(store: OpsPriorityStore) -> None:
    stored = await store.put(PriorityMark("mission", "m1", "high", "2026-10-07", "board deck"))
    assert stored.updated_ms > 0
    assert await store.get("mission", "m1") == stored
    await store.put(PriorityMark("task", "t1", "low"))
    assert {(m.source, m.item_id) for m in await store.all()} == {("mission", "m1"), ("task", "t1")}

    replaced = await store.put(PriorityMark("mission", "m1", "urgent"))
    assert (replaced.priority, replaced.focus_date, replaced.note) == ("urgent", None, "")
    assert await store.delete("mission", "m1") is True
    assert await store.delete("mission", "m1") is False
    assert await store.get("mission", "m1") is None


async def test_an_empty_mark_removes_the_row(store: OpsPriorityStore) -> None:
    await store.put(PriorityMark("task", "t1", "high"))
    await store.put(PriorityMark("task", "t1"))
    assert await store.all() == []


@pytest.mark.parametrize(
    "mark",
    [
        PriorityMark("calendar", "x", "high"),  # not a WorkLedger source
        PriorityMark("task", "", "high"),
        PriorityMark("task", "x" * 201, "high"),
        PriorityMark("task", "x", "critical"),
        PriorityMark("task", "x", None, "07.10.2026"),
        PriorityMark("task", "x", None, None, "n" * 501),
    ],
)
async def test_invalid_marks_are_refused_and_nothing_is_written(
    store: OpsPriorityStore, mark: PriorityMark
) -> None:
    with pytest.raises(PriorityError):
        await store.put(mark)
    assert await store.all() == []


async def test_the_store_only_holds_marks(store: OpsPriorityStore, tmp_path: Path) -> None:
    await store.put(PriorityMark("task", "t1", "high"))
    con = sqlite3.connect(tmp_path / "ops" / "ops.sqlite")
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {r[1] for r in con.execute("PRAGMA table_info(ops_priority)")}
    finally:
        con.close()
    assert tables == {"ops_priority"}
    # No title, state or schedule: the item itself lives in its own system.
    assert columns == {"source", "item_id", "priority", "focus_date", "note", "updated_ms"}


def test_sql_levels_match_the_python_levels() -> None:
    match = re.search(r"priority IN \(([^)]*)\)", priority_module._SCHEMA)
    assert match is not None
    sql_levels = {v.strip().strip("'") for v in match.group(1).split(",")}
    assert sql_levels == set(PRIORITY_LEVELS)


async def test_focus_lapses_by_date(store: OpsPriorityStore) -> None:
    from datetime import date

    mark = await store.put(PriorityMark("task", "t1", None, "2026-10-07"))
    assert mark.focus_on(date(2026, 10, 7)) is True
    assert mark.focus_on(date(2026, 10, 8)) is False
    assert mark.to_dict(date(2026, 10, 8))["focus_today"] is False
