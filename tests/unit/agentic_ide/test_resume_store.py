"""The snapshot that lets a workspace outlive the browser and the process.

Half of these tests are about damage: this file survives crashes, app upgrades
and the occasional hand edit, and every unreadable form of it must mean "there
is nothing to resume" rather than an exception on a screen the user is waiting
on. The other half is about honesty — the offer has to say which panes really
come back before anyone clicks it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide.agent_sessions import ResumeHandle

# The store itself is redirected to a throwaway file by the package conftest,
# so nothing here can reach the developer's real data directory.


def _snapshot(folder: str) -> resume_store.Snapshot:
    return _snapshot_of(_workspace(folder))


def _snapshot_of(*workspaces: resume_store.SnapshotWorkspace) -> resume_store.Snapshot:
    return resume_store.Snapshot(saved_at=100.0, workspaces=list(workspaces))


def _workspace(folder: str) -> resume_store.SnapshotWorkspace:
    return resume_store.SnapshotWorkspace(
        session_id="ide_test",
        folder=folder,
        name="Release review",
        terminals=[
            resume_store.SnapshotTerminal(
                key="alex",
                name="Alex",
                agent="claude",
                column=0,
                slot=0,
                resume=ResumeHandle(kind="claude_session", id="u-1", captured_at=1.0),
                prompts_sent=3,
                continuation_needed=True,
            ),
            resume_store.SnapshotTerminal(
                key="blake", name="Blake", agent="codex", column=1, slot=0
            ),
        ],
    )


# --------------------------------------------------------------- round trip
def test_a_saved_snapshot_comes_back_intact(tmp_path: Path) -> None:
    resume_store.save(_snapshot(str(tmp_path)))
    loaded = resume_store.load()
    assert loaded is not None
    assert [t.name for t in loaded.workspaces[0].terminals] == ["Alex", "Blake"]
    assert [(t.column, t.slot) for t in loaded.workspaces[0].terminals] == [(0, 0), (1, 0)]
    assert [t.agent for t in loaded.workspaces[0].terminals] == ["claude", "codex"]
    assert loaded.workspaces[0].terminals[0].resume is not None
    assert loaded.workspaces[0].terminals[0].resume.id == "u-1"
    assert loaded.workspaces[0].terminals[1].resume is None
    assert loaded.workspaces[0].terminals[0].continuation_needed is True
    assert loaded.workspaces[0].terminals[1].continuation_needed is False
    assert loaded.workspaces[0].name == "Release review"


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
        path.read_text(encoding="utf-8").replace('"version": 2', '"version": 99'),
        encoding="utf-8",
    )
    assert resume_store.load() is None


def test_a_snapshot_from_the_one_workspace_era_still_resumes(tmp_path: Path) -> None:
    """An upgrade must not cost the restore point on the very restart that needs it.

    Version 1 was written when only one workspace could be open, with its folder
    and panes at the top level. It is lifted into a single workspace rather than
    discarded.
    """
    import json

    legacy = {
        "version": 1,
        "session_id": "ide_old",
        "folder": str(tmp_path),
        "saved_at": 42.0,
        "terminals": [{"key": "alex", "name": "Alex", "agent": "claude", "column": 0, "slot": 0}],
    }
    target = resume_store._store_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = resume_store.load()
    assert loaded is not None
    assert len(loaded.workspaces) == 1
    assert loaded.workspaces[0].folder == str(tmp_path)
    assert [t.name for t in loaded.workspaces[0].terminals] == ["Alex"]
    assert loaded.workspaces[0].terminals[0].continuation_needed is False
    assert loaded.saved_at == 42.0


def test_every_open_workspace_is_remembered(tmp_path: Path) -> None:
    """Somebody with four folders open had four — one is not an answer."""
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    resume_store.save(_snapshot_of(_workspace(str(first)), _workspace(str(second))))

    loaded = resume_store.load()
    assert loaded is not None
    assert [w.folder for w in loaded.workspaces] == [str(first), str(second)]
    assert loaded.terminal_count == 4


def test_the_offer_reports_each_workspace_separately(tmp_path: Path) -> None:
    alive, gone = tmp_path / "alive", tmp_path / "deleted"
    alive.mkdir()
    view = resume_store.offer(
        _snapshot_of(_workspace(str(alive)), _workspace(str(gone))),
        installed={"claude", "codex"},
    )
    # One can come back, one cannot — and the whole offer is still usable.
    assert view["available"] is True
    assert view["workspace_count"] == 2
    assert view["terminal_count"] == 4
    assert view["workspaces"][0]["available"] is True
    assert view["workspaces"][1]["available"] is False
    assert view["workspaces"][1]["folder_exists"] is False


def _stamped(folder: str, when: float) -> resume_store.SnapshotWorkspace:
    space = _workspace(folder)
    space.saved_at = when
    return space


def test_the_last_session_is_what_the_newest_stamp_says(tmp_path: Path) -> None:
    """A remembered folder is not part of the session being offered back."""
    now, earlier = 1000.0, 900.0
    snapshot = resume_store.Snapshot(
        saved_at=now,
        workspaces=[
            _stamped(str(tmp_path / "a"), now),
            _stamped(str(tmp_path / "b"), now),
            _stamped(str(tmp_path / "old"), earlier),
        ],
    )
    assert [w.folder for w in snapshot.last_session()] == [
        str(tmp_path / "a"),
        str(tmp_path / "b"),
    ]


def test_a_file_without_stamps_counts_entirely_as_the_last_session(tmp_path: Path) -> None:
    """Older files cannot be split, so nothing is silently dropped from them."""
    snapshot = resume_store.Snapshot(
        saved_at=0.0,
        workspaces=[_workspace(str(tmp_path / "a")), _workspace(str(tmp_path / "b"))],
    )
    assert len(snapshot.last_session()) == 2


def test_the_offer_counts_only_what_resuming_will_reopen(tmp_path: Path) -> None:
    """The card must not promise an archive's worth of folders."""
    alive, old = tmp_path / "alive", tmp_path / "old"
    alive.mkdir()
    old.mkdir()
    view = resume_store.offer(
        resume_store.Snapshot(
            saved_at=1000.0,
            workspaces=[_stamped(str(alive), 1000.0), _stamped(str(old), 900.0)],
        ),
        installed={"claude", "codex"},
    )
    assert view["workspace_count"] == 1
    assert view["terminal_count"] == 2
    assert view["earlier_count"] == 1
    # Both are still listed — an old folder is worth seeing, just not resuming.
    assert [w["in_last_session"] for w in view["workspaces"]] == [True, False]
    assert [w["saved_at"] for w in view["workspaces"]] == [1000.0, 900.0]


def test_a_pane_without_a_name_is_dropped_not_fatal(tmp_path: Path) -> None:
    """One damaged entry must not cost the whole workspace."""
    resume_store.save(_snapshot(str(tmp_path)))
    path = resume_store._store_path()
    path.write_text(
        path.read_text(encoding="utf-8").replace('"name": "Blake"', '"name": ""'),
        encoding="utf-8",
    )
    loaded = resume_store.load()
    assert loaded is not None and [t.name for t in loaded.workspaces[0].terminals] == ["Alex"]


def test_a_snapshot_without_terminals_is_not_an_offer(tmp_path: Path) -> None:
    resume_store.save(resume_store.Snapshot(saved_at=1.0, workspaces=[]))
    assert resume_store.load() is None


def test_saving_an_empty_workspace_withdraws_the_old_offer(tmp_path: Path) -> None:
    """Closing the last pane must not leave yesterday's workspace on offer."""
    resume_store.save(_snapshot(str(tmp_path)))
    resume_store.save(resume_store.Snapshot(saved_at=2.0, workspaces=[]))
    assert resume_store.load() is None


def test_clear_removes_the_offer(tmp_path: Path) -> None:
    resume_store.save(_snapshot(str(tmp_path)))
    assert resume_store.clear() is True
    assert resume_store.load() is None
    assert resume_store.clear() is False


def test_a_failed_write_leaves_the_previous_snapshot_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad write must not turn a good offer into a stub."""
    resume_store.save(_snapshot(str(tmp_path)))
    target = resume_store._store_path()
    before = target.read_text(encoding="utf-8")

    def _fail(*_args: object) -> None:
        raise OSError("the disk went away mid-write")

    monkeypatch.setattr(resume_store.os, "replace", _fail)
    resume_store.save(
        _snapshot_of(
            resume_store.SnapshotWorkspace(
                session_id="ide_other",
                folder=str(tmp_path),
                terminals=[resume_store.SnapshotTerminal(key="x", name="X", agent="claude")],
            )
        )
    )
    assert target.read_text(encoding="utf-8") == before
    # ...and no debris is left lying around to confuse the next read.
    assert list(target.parent.glob("*.tmp-*")) == []


def test_two_threads_saving_at_once_do_not_lose_the_write(tmp_path: Path) -> None:
    """The collision that actually happens, and cost a conversation id.

    A pane connecting saves the workspace; a moment later the background lookup
    that found a Codex conversation saves it again, from another thread. With
    one shared temp filename the two clobbered each other — and on Windows the
    second rename failed outright, dropping exactly the id this feature exists
    to keep.
    """
    import threading

    start = threading.Barrier(6)
    errors: list[BaseException] = []

    def _save(index: int) -> None:
        try:
            start.wait(timeout=5)
            for _ in range(10):
                resume_store.save(
                    resume_store.Snapshot(
                        saved_at=float(index),
                        workspaces=[
                            resume_store.SnapshotWorkspace(
                                session_id=f"ide_{index}",
                                folder=str(tmp_path),
                                terminals=[
                                    resume_store.SnapshotTerminal(
                                        key=f"t{index}",
                                        name=f"T{index}",
                                        agent="claude",
                                    )
                                ],
                            )
                        ],
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - reported to the assertion
            errors.append(exc)

    threads = [threading.Thread(target=_save, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    # Whoever won, the file is a COMPLETE snapshot and not a torn one.
    loaded = resume_store.load()
    assert loaded is not None and len(loaded.workspaces[0].terminals) == 1
    assert list(resume_store._store_path().parent.glob("*.tmp-*")) == []


# ------------------------------------------------------------------- offer
def test_the_offer_reports_what_will_actually_come_back(
    tmp_path: Path, existing_conversation
) -> None:
    existing_conversation("u-1")
    view = resume_store.offer(_snapshot(str(tmp_path)), installed={"claude"})
    assert view["available"] is True
    space = view["workspaces"][0]
    assert space["folder_exists"] is True
    assert space["folder_name"] == tmp_path.name
    panes = {p["name"]: p for p in space["terminals"]}
    # Alex has a handle and its CLI is here -> the conversation comes back.
    assert panes["Alex"]["resumable"] is True
    assert panes["Alex"]["available"] is True
    # Blake never got a handle -> the pane returns, the conversation does not.
    assert panes["Blake"]["resumable"] is False
    # ...and Codex is not installed here, which the user must see beforehand.
    assert panes["Blake"]["available"] is False
    assert view["resumable_count"] == 1


def test_the_offer_keeps_the_grid_coordinates(tmp_path: Path) -> None:
    view = resume_store.offer(_snapshot(str(tmp_path)), installed={"claude", "codex"})
    panes = view["workspaces"][0]["terminals"]
    assert [(p["column"], p["slot"]) for p in panes] == [(0, 0), (1, 0)]


def test_a_vanished_folder_is_reported_not_raised(tmp_path: Path) -> None:
    view = resume_store.offer(_snapshot(str(tmp_path / "deleted")), installed={"claude", "codex"})
    assert view["available"] is False
    assert view["workspaces"][0]["folder_exists"] is False


def test_a_machine_without_any_coding_cli_is_told_so(tmp_path: Path) -> None:
    """A fresh install elsewhere: never a crash, never a false promise."""
    view = resume_store.offer(_snapshot(str(tmp_path)), installed=set())
    assert view["available"] is False
    panes = view["workspaces"][0]["terminals"]
    assert all(p["available"] is False for p in panes)
    assert view["resumable_count"] == 0


def test_no_snapshot_yields_an_empty_offer() -> None:
    view = resume_store.offer(None, installed={"claude"})
    assert view["available"] is False
    assert view["workspaces"] == []
