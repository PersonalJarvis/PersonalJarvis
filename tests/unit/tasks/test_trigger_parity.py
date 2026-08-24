"""Parity test for the task TRIGGER vocabulary across Python ↔ TypeScript.

BUG-008 class: a vocabulary that spans Python (the Pydantic discriminator),
the TS trigger union (what the create dialog emits) and the Automations model
(what the list view displays) drifts silently — the dialog emits a trigger the
backend rejects, or the list view mislabels a trigger it never learned.

Source of truth: ``TRIGGER_TYPES`` in ``jarvis/tasks/schema.py``. Every TS layer
must spell exactly the same four values. (When-Then added ``on_event`` to the TS
side; this test would have failed on the missing value before that change.)
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.tasks.schema import PAUSABLE_TRIGGER_TYPES, TRIGGER_TYPES

REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_SPEC_TS = REPO_ROOT / "jarvis/ui/web/frontend/src/views/tasks/taskSpec.ts"
MODEL_TS = REPO_ROOT / "jarvis/ui/web/frontend/src/views/automations/automationsModel.ts"


def _expected() -> set[str]:
    return set(TRIGGER_TYPES)


def test_pausable_triggers_are_known_triggers() -> None:
    assert set(PAUSABLE_TRIGGER_TYPES) < _expected()


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


def test_automations_model_triggertype_matches_python() -> None:
    text = MODEL_TS.read_text(encoding="utf-8")
    block = re.search(r"export type TriggerType =([\s\S]+?);", text)
    assert block is not None, "could not find TriggerType in automationsModel.ts"
    found = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert found == _expected(), (
        f"automationsModel.ts TriggerType drift: extra={found - _expected()}, "
        f"missing={_expected() - found}"
    )
