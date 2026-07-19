"""Cross-layer parity guard for the Jarvis-Agent Board status vocabulary.

Agent nodes are in-memory dataclasses serialized directly by the REST snapshot,
so the persistence and Pydantic layers collapse here. This guard keeps the
Python producer, TypeScript consumer, and visible board labels synchronized.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from jarvis.agents.registry import NodeStatus

_REPO = Path(__file__).resolve().parents[3]
_STORE = (
    _REPO
    / "jarvis"
    / "ui"
    / "web"
    / "frontend"
    / "src"
    / "store"
    / "jarvisAgents.ts"
)
_BOARD = (
    _REPO
    / "jarvis"
    / "ui"
    / "web"
    / "frontend"
    / "src"
    / "views"
    / "sub-agents"
    / "DepartureBoard.tsx"
)


def _ts_node_statuses() -> set[str]:
    text = _STORE.read_text(encoding="utf-8")
    match = re.search(r"export type NodeStatus\s*=([^;]+);", text)
    assert match is not None, "TypeScript NodeStatus union is missing"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _board_status_labels() -> set[str]:
    text = _BOARD.read_text(encoding="utf-8")
    match = re.search(
        r"const STATUS_LABEL: Record<[^>]+> = \{(.*?)\n\};",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "DepartureBoard STATUS_LABEL map is missing"
    return set(re.findall(r"^\s{2}([a-z_]+):", match.group(1), flags=re.MULTILINE))


def test_node_status_python_typescript_and_ui_parity() -> None:
    python_statuses = set(get_args(NodeStatus))
    assert python_statuses == _ts_node_statuses()
    assert python_statuses == _board_status_labels()


def test_node_status_expected_vocabulary() -> None:
    assert set(get_args(NodeStatus)) == {
        "running",
        "completed",
        "failed",
        "cancelled",
    }
