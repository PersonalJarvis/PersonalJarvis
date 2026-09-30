"""Which pane's agent wrote which changed file, from the agents' own records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import change_authors
from jarvis.agentic_ide.change_authors import PaneRecord


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    change_authors._cache.clear()


def _call(call_id: str, name: str, args: Any, ts: int) -> dict[str, Any]:
    return {
        "kind": "tool_call",
        "ts_ms": ts,
        "payload": {"call_id": call_id, "name": name, "input": args},
    }


def _result(call_id: str, *, error: bool = False) -> dict[str, Any]:
    return {"kind": "tool_result", "ts_ms": 0, "payload": {"call_id": call_id, "is_error": error}}


def _pane(name: str, folder: Path, agent: str = "claude") -> PaneRecord:
    return PaneRecord(
        pane=name,
        history_id=f"h-{name}",
        agent=agent,
        display_name="Claude Code" if agent == "claude" else "Codex",
        session_id=f"s-{name}",
        home=None,
        folder=str(folder),
    )


def test_written_paths_counts_only_successful_writing_tools() -> None:
    events = [
        _call("1", "Edit", {"file_path": "a.py"}, 10),
        _result("1"),
        _call("2", "Read", {"file_path": "b.py"}, 20),
        _call("3", "Bash", {"command": "sed -i s/x/y/ c.py"}, 30),
        _call("4", "Write", {"file_path": "d.py"}, 40),
        _result("4", error=True),
        _call("5", "Edit", {"file_path": "a.py"}, 50),
    ]
    assert change_authors.written_paths(events) == {"a.py": 50}


def test_written_paths_reads_apply_patch_bodies() -> None:
    patch = (
        "*** Begin Patch\n*** Update File: src/app.py\n@@\n-x\n+y\n"
        "*** Add File: docs/new.md\n+hi\n*** End Patch"
    )
    events = [_call("1", "apply_patch", {"input": patch}, 7)]
    assert change_authors.written_paths(events) == {"src/app.py": 7, "docs/new.md": 7}


def test_change_authors_maps_panes_to_workspace_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    records = {
        "T1": [_call("1", "Edit", {"file_path": str(root / "src" / "a.py")}, 100)],
        "T2": [
            _call("1", "apply_patch", {"input": "*** Update File: src/a.py\n"}, 200),
            _call("2", "Write", {"file_path": str(tmp_path / "elsewhere.py")}, 300),
        ],
    }
    panes = [_pane("T1", root), _pane("T2", root, agent="codex")]

    by_path = change_authors.change_authors(
        root, panes, reader=lambda record: records[record.pane], now=1.0
    )

    assert list(by_path) == ["src/a.py"]
    assert [(a.pane, a.last_edit_ms) for a in by_path["src/a.py"]] == [("T2", 200), ("T1", 100)]


def test_untracked_folder_gathers_the_authors_inside_it(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    records = {
        "T1": [_call("1", "Write", {"file_path": "out/a.txt"}, 5)],
        "T2": [_call("1", "Write", {"file_path": "out/deep/b.txt"}, 9)],
    }
    by_path = change_authors.change_authors(
        root,
        [_pane("T1", root), _pane("T2", root)],
        reader=lambda record: records[record.pane],
        now=2.0,
    )

    folder = change_authors.authors_for("out", True, by_path)
    assert [a.pane for a in folder] == ["T2", "T1"]
    assert change_authors.authors_for("out", False, by_path) == []


def test_unreadable_record_yields_no_authors(tmp_path: Path) -> None:
    def broken(_record: PaneRecord) -> list[dict[str, Any]]:
        raise OSError("gone")

    assert change_authors.change_authors(tmp_path, [_pane("T9", tmp_path)], reader=broken) == {}
