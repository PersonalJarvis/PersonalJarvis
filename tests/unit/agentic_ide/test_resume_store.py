"""The snapshot that lets a workspace outlive the browser and the process.

Half of these tests are about damage: this file survives crashes, app upgrades
and the occasional hand edit, and every unreadable form of it must mean "there
is nothing to resume" rather than an exception on a screen the user is waiting
on. The other half is about honesty — the offer has to say which panes really
come back before anyone clicks it.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide.agent_sessions import ResumeHandle

# The store itself is redirected to a throwaway file by the package conftest,
# so nothing here can reach the developer's real data directory.


def _snapshot(folder: str) -> resume_store.Snapshot:
    return resume_store.Snapshot(
        session_id="ide_test",
        folder=folder,
        saved_at=100.0,
        terminals=[
            resume_store.SnapshotTerminal(
                key="mika",
                name="Mika",
                agent="claude",
                column=0,
                slot=0,
                resume=ResumeHandle(
                    kind="claude_session", id="u-1", captured_at=1.0
                ),
                prompts_sent=3,
            ),
            resume_store.SnapshotTerminal(
                key="nova", name="Nova", agent="codex", column=1, slot=0
            ),
        ],
    )


# --------------------------------------------------------------- round trip
def test_a_saved_snapshot_comes_back_intact(tmp_path: Path) -> None:
    resume_store.save(_snapshot(str(tmp_path)))
    loaded = resume_store.load()
    assert loaded is not None
    assert [t.name for t in loaded.terminals] == ["Mika", "Nova"]
    assert [(t.column, t.slot) for t in loaded.terminals] == [(0, 0), (1, 0)]
    assert [t.agent for t in loaded.terminals] == ["claude", "codex"]
    assert loaded.terminals[0].resume is not None
    assert loaded.terminals[0].resume.id == "u-1"
    assert loaded.terminals[1].resume is None


def test_nothing_saved_means_nothing_to_resume() -> None:
    assert resume_store.load() is None


# ------------------------------------------------------------------ damage
def test_a_truncated_file_degrades_to_nothing(tmp_path: Path) -> None:
    resume_store.save(_snapshot(str(tmp_path)))
    resume_store._store_path().write_text('{"version": 1, "term', encoding="utf-8")
    assert resume_store.load() is None


def test_a_snapshot_from_a_future_version_is_ignored(tmp_path: Path) -> None:
    """Half-reading a newer build's file would reopen a broken workspace."""
    resume_store.save(_snapshot(str(tmp_path)))
    path = resume_store._store_path()
    path.write_text(
        path.read_text(encoding="utf-8").replace('"version": 1', '"version": 99'),
        encoding="utf-8",
    )
    assert resume_store.load() is None


def test_a_pane_without_a_name_is_dropped_not_fatal(tmp_path: Path) -> None:
    """One damaged entry must not cost the whole workspace."""
    resume_store.save(_snapshot(str(tmp_path)))
    path = resume_store._store_path()
    path.write_text(
        path.read_text(encoding="utf-8").replace('"name": "Nova"', '"name": ""'),
        encoding="utf-8",
    )
    loaded = resume_store.load()
    assert loaded is not None and [t.name for t in loaded.terminals] == ["Mika"]


def test_a_snapshot_without_terminals_is_not_an_offer(tmp_path: Path) -> None:
    empty = resume_store.Snapshot(
        session_id="ide_x", folder=str(tmp_path), saved_at=1.0, terminals=[]
    )
    resume_store.save(empty)
    assert resume_store.load() is None


def test_saving_an_empty_workspace_withdraws_the_old_offer(tmp_path: Path) -> None:
    """Closing the last pane must not leave yesterday's workspace on offer."""
    resume_store.save(_snapshot(str(tmp_path)))
    resume_store.save(
        resume_store.Snapshot(
            session_id="ide_x", folder=str(tmp_path), saved_at=2.0, terminals=[]
        )
    )
    assert resume_store.load() is None


def test_clear_removes_the_offer(tmp_path: Path) -> None:
    resume_store.save(_snapshot(str(tmp_path)))
    assert resume_store.clear() is True
    assert resume_store.load() is None
    assert resume_store.clear() is False


def test_a_failed_write_leaves_the_previous_snapshot_readable(
    tmp_path: Path,
) -> None:
    """A bad write must not turn a good offer into a stub."""
    resume_store.save(_snapshot(str(tmp_path)))
    target = resume_store._store_path()
    before = target.read_text(encoding="utf-8")
    # A directory sitting exactly where the temp file wants to go.
    target.with_suffix(".json.tmp").mkdir(parents=True, exist_ok=True)
    resume_store.save(
        resume_store.Snapshot(
            session_id="ide_other",
            folder=str(tmp_path),
            saved_at=999.0,
            terminals=[
                resume_store.SnapshotTerminal(key="x", name="X", agent="claude")
            ],
        )
    )
    assert target.read_text(encoding="utf-8") == before


# ------------------------------------------------------------------- offer
def test_the_offer_reports_what_will_actually_come_back(tmp_path: Path) -> None:
    view = resume_store.offer(_snapshot(str(tmp_path)), installed={"claude"})
    assert view["available"] is True
    assert view["folder_exists"] is True
    assert view["folder_name"] == tmp_path.name
    panes = {p["name"]: p for p in view["terminals"]}
    # Mika has a handle and its CLI is here -> the conversation comes back.
    assert panes["Mika"]["resumable"] is True
    assert panes["Mika"]["available"] is True
    # Nova never got a handle -> the pane returns, the conversation does not.
    assert panes["Nova"]["resumable"] is False
    # ...and Codex is not installed here, which the user must see beforehand.
    assert panes["Nova"]["available"] is False
    assert view["resumable_count"] == 1


def test_the_offer_keeps_the_grid_coordinates(tmp_path: Path) -> None:
    view = resume_store.offer(_snapshot(str(tmp_path)), installed={"claude", "codex"})
    assert [(p["column"], p["slot"]) for p in view["terminals"]] == [(0, 0), (1, 0)]


def test_a_vanished_folder_is_reported_not_raised(tmp_path: Path) -> None:
    view = resume_store.offer(
        _snapshot(str(tmp_path / "deleted")), installed={"claude", "codex"}
    )
    assert view["available"] is False
    assert view["folder_exists"] is False


def test_a_machine_without_any_coding_cli_is_told_so(tmp_path: Path) -> None:
    """A fresh install elsewhere: never a crash, never a false promise."""
    view = resume_store.offer(_snapshot(str(tmp_path)), installed=set())
    assert view["available"] is False
    assert all(p["available"] is False for p in view["terminals"])
    assert view["resumable_count"] == 0


def test_no_snapshot_yields_an_empty_offer() -> None:
    view = resume_store.offer(None, installed={"claude"})
    assert view["available"] is False
    assert view["terminals"] == []
