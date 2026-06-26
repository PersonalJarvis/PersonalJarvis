"""Parity test for the task ACTION vocabulary across Python ↔ TypeScript.

The create dialog must never emit an action ``kind`` the Pydantic
``TaskAction`` discriminator rejects (that is an instant HTTP 422 on save).
The TS side is a SUBSET of Python by design — the UI does not surface every
action kind (e.g. ``tool_call`` has no dialog) — so the contract is
containment, not equality: every kind the frontend builds must be a known
Python kind.

Source of truth: ``ACTION_KINDS`` in ``jarvis/tasks/schema.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.tasks.schema import ACTION_KINDS

REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_SPEC_TS = REPO_ROOT / "jarvis/ui/web/frontend/src/views/tasks/taskSpec.ts"


def test_ts_action_kinds_are_all_known_to_python() -> None:
    text = TASK_SPEC_TS.read_text(encoding="utf-8")
    found = set(re.findall(r'kind:\s*"([^"]+)"', text))
    assert found, "no action kinds found in taskSpec.ts"
    unknown = found - set(ACTION_KINDS)
    assert unknown == set(), (
        f"taskSpec.ts emits action kinds the Python schema rejects: {unknown}"
    )


def test_whenthen_uses_harness_dispatch_for_computer_use() -> None:
    """The When-Then Computer-Use path must dispatch via the harness action —
    a regression here would silently drop CU rules."""
    text = TASK_SPEC_TS.read_text(encoding="utf-8")
    assert "harness_dispatch" in text
    assert "harness_dispatch" in ACTION_KINDS
