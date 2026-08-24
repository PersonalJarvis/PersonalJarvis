"""Parity test for the task STATE vocabulary across the five layers.

``paused`` (2026-08-24) joined the vocabulary that lives in Python
(``TaskState``/``TASK_STATES``), SQL (the ``state`` CHECK in ``schema.sql``
and the migration rebuild target), TypeScript (the ``TaskState`` union + the
label/dot maps of the Automations views) and i18n (``tasks_view.state.*`` in
every locale). A value missing from any one layer is a silent defect: a CHECK
violation on pause, a blank badge, or an untranslated key.

Source of truth: ``TASK_STATES`` in ``jarvis/tasks/schema.py``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis.tasks import store as store_mod
from jarvis.tasks.schema import TASK_STATES, TERMINAL_STATES, TaskState

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND = REPO_ROOT / "jarvis/ui/web/frontend/src"
SCHEMA_SQL = REPO_ROOT / "jarvis/tasks/schema.sql"
MODEL_TS = FRONTEND / "views/automations/automationsModel.ts"
SHARED_TSX = FRONTEND / "views/automations/shared.tsx"
LOCALES = FRONTEND / "i18n/locales"


def _expected() -> set[str]:
    return set(TASK_STATES)


def _states_in_check(sql: str) -> set[str]:
    block = re.search(r"state\s+TEXT NOT NULL CHECK\(state IN \(([^)]+)\)\)", sql)
    assert block is not None, "could not find the state CHECK"
    return set(re.findall(r"'([a-z_]+)'", block.group(1)))


def test_literal_and_tuple_agree() -> None:
    assert set(TaskState.__args__) == _expected()  # type: ignore[attr-defined]
    assert "paused" in _expected()
    assert set(TERMINAL_STATES) < _expected()
    assert "paused" not in TERMINAL_STATES


def test_schema_sql_check_matches_python() -> None:
    found = _states_in_check(SCHEMA_SQL.read_text(encoding="utf-8"))
    assert found == _expected(), f"schema.sql drift: {found ^ _expected()}"


def test_migration_rebuild_target_matches_python() -> None:
    found = _states_in_check(store_mod._TASKS_REBUILD_SQL)
    assert found == _expected(), f"_TASKS_REBUILD_SQL drift: {found ^ _expected()}"


def test_ts_union_matches_python() -> None:
    text = MODEL_TS.read_text(encoding="utf-8")
    block = re.search(r"export type TaskState =([\s\S]+?);", text)
    assert block is not None, "could not find TaskState union in automationsModel.ts"
    found = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert found == _expected(), f"automationsModel.ts TaskState drift: {found ^ _expected()}"


def test_ts_label_map_covers_every_state() -> None:
    text = SHARED_TSX.read_text(encoding="utf-8")
    found = set(re.findall(r'(\w+):\s*t\("tasks_view\.state\.(\w+)"\)', text))
    keys = {k for k, _ in found}
    assert keys == _expected(), f"shared.tsx label map drift: {keys ^ _expected()}"
    assert all(k == v for k, v in found), "label map key/i18n key mismatch"


@pytest.mark.parametrize("locale", ["de", "en", "es"])
def test_i18n_has_every_state(locale: str) -> None:
    data = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    states = data["tasks_view"]["state"]
    assert set(states) == _expected(), f"{locale}.json drift: {set(states) ^ _expected()}"
    assert all(isinstance(v, str) and v for v in states.values())
