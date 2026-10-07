"""Parity test for the task TRIGGER vocabulary across Python ↔ TypeScript.

BUG-008 class: a vocabulary that spans Python (the Pydantic discriminator) and
the TS task model the agent card reads routines with drifts silently — the card
mislabels a trigger it never learned.

Source of truth: ``TRIGGER_TYPES`` in ``jarvis/tasks/schema.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.tasks.schema import PAUSABLE_TRIGGER_TYPES, TRIGGER_TYPES

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_TS = REPO_ROOT / "jarvis/ui/web/frontend/src/lib/tasksApi.ts"


def _expected() -> set[str]:
    return set(TRIGGER_TYPES)


def test_pausable_triggers_are_known_triggers() -> None:
    assert set(PAUSABLE_TRIGGER_TYPES) < _expected()


def test_tasks_api_triggertype_matches_python() -> None:
    text = MODEL_TS.read_text(encoding="utf-8")
    block = re.search(r"export type TriggerType =([\s\S]+?);", text)
    assert block is not None, "could not find TriggerType in tasksApi.ts"
    found = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert found == _expected(), (
        f"tasksApi.ts TriggerType drift: extra={found - _expected()}, "
        f"missing={_expected() - found}"
    )
