"""Parity test for the task TRIGGER vocabulary across Python ↔ TypeScript.

BUG-008 class: a vocabulary that spans Python (the Pydantic discriminator),
the TS trigger union (what the create dialog emits) and the TasksView display
(icon + label maps) drifts silently — the dialog emits a trigger the backend
rejects, or the list view shows a blank icon for a trigger it never learned.

Source of truth: ``TRIGGER_TYPES`` in ``jarvis/tasks/schema.py``. Every TS layer
must spell exactly the same four values. (When-Then added ``on_event`` to the TS
side; this test would have failed on the missing value before that change.)
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.tasks.schema import TRIGGER_TYPES

REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_SPEC_TS = REPO_ROOT / "jarvis/ui/web/frontend/src/views/tasks/taskSpec.ts"
TASKS_VIEW_TSX = REPO_ROOT / "jarvis/ui/web/frontend/src/views/TasksView.tsx"


def _expected() -> set[str]:
    return set(TRIGGER_TYPES)


def test_taskspec_ts_trigger_union_matches_python() -> None:
    text = TASK_SPEC_TS.read_text(encoding="utf-8")
    # Capture up to the next top-level `export` — the union body contains inner
    # `;` (e.g. `{ type: "after_delay"; ... }`) so a non-greedy `;` stops short.
    block = re.search(r"export type TaskTrigger =([\s\S]+?)\nexport ", text)
    assert block is not None, "could not find TaskTrigger union in taskSpec.ts"
    found = set(re.findall(r'type:\s*"([^"]+)"', block.group(1)))
    assert found == _expected(), (
        f"taskSpec.ts TaskTrigger drift: extra={found - _expected()}, "
        f"missing={_expected() - found}"
    )


def test_tasksview_triggertype_matches_python() -> None:
    text = TASKS_VIEW_TSX.read_text(encoding="utf-8")
    block = re.search(r"type TriggerType =([\s\S]+?);", text)
    assert block is not None, "could not find TriggerType in TasksView.tsx"
    found = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert found == _expected(), (
        f"TasksView.tsx TriggerType drift: extra={found - _expected()}, "
        f"missing={_expected() - found}"
    )


def test_tasksview_icon_map_covers_every_trigger() -> None:
    text = TASKS_VIEW_TSX.read_text(encoding="utf-8")
    block = re.search(r"const TRIGGER_ICON[^{]+\{([\s\S]+?)\};", text)
    assert block is not None, "could not find TRIGGER_ICON map in TasksView.tsx"
    found = set(re.findall(r"(\w+)\s*:", block.group(1)))
    assert found == _expected(), (
        f"TasksView.tsx TRIGGER_ICON drift: extra={found - _expected()}, "
        f"missing={_expected() - found}"
    )


def test_tasksview_label_map_covers_every_trigger() -> None:
    text = TASKS_VIEW_TSX.read_text(encoding="utf-8")
    block = re.search(
        r"function makeTriggerLabels[\s\S]+?return\s*\{([\s\S]+?)\};", text
    )
    assert block is not None, "could not find makeTriggerLabels in TasksView.tsx"
    found = set(re.findall(r"(\w+)\s*:\s*t\(", block.group(1)))
    assert found == _expected(), (
        f"TasksView.tsx label map drift: extra={found - _expected()}, "
        f"missing={_expected() - found}"
    )
