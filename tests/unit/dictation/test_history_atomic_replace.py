"""History writes retain their atomic snapshot through short external file locks."""

from __future__ import annotations

import errno

import pytest

from jarvis.dictation import stats
from jarvis.dictation.history import DictationHistory
from tests.fakes.fake_atomic_replace import FailingReplace


@pytest.mark.parametrize("operation", ["add", "update", "delete", "clear"])
def test_history_retries_transient_replace_without_repeating_the_mutation(
    tmp_path, monkeypatch, operation,
):
    store = DictationHistory(tmp_path / "history.json")
    seed = store.add(raw_text="seed", text="seed")
    assert seed is not None
    failing = FailingReplace(store.path, failures=2)
    sleeps = []
    monkeypatch.setattr(stats.os, "replace", failing)
    monkeypatch.setattr(stats.time, "sleep", sleeps.append)

    if operation == "add":
        added = store.add(raw_text="new", text="new")
        assert added is not None
        assert [entry.text for entry in store.list_all()] == ["new", "seed"]
    elif operation == "update":
        updated = store.update(seed.id, text="restored")
        assert updated is not None
        assert [entry.text for entry in store.list_all()] == ["restored"]
    elif operation == "delete":
        assert store.delete(seed.id)
        assert store.list_all() == []
    else:
        assert store.clear()
        assert store.list_all() == []

    assert len(failing.sources) == 3
    assert len(set(failing.sources)) == 1  # Retry the same completed tempfile.
    assert 0 < sum(sleeps) <= 1
    assert not list(tmp_path.glob(".dictation_history_*.tmp"))


@pytest.mark.parametrize("permission_error", [True, False])
def test_history_write_failure_is_bounded_and_preserves_the_previous_file(
    tmp_path, monkeypatch, caplog, permission_error,
):
    store = DictationHistory(tmp_path / "history.json")
    seed = store.add(raw_text="seed", text="seed")
    assert seed is not None
    original = store.path.read_bytes()
    error = PermissionError(13, "Locked") if permission_error else OSError(errno.ENOSPC, "Full")
    failing = FailingReplace(store.path, failures=100, error=error)
    sleeps = []
    monkeypatch.setattr(stats.os, "replace", failing)
    monkeypatch.setattr(stats.time, "sleep", sleeps.append)

    assert store.update(seed.id, text="must not replace the saved version") is None
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".dictation_history_*.tmp"))
    assert "could not update a dictation history entry" in caplog.text
    if permission_error:
        assert 2 <= len(failing.sources) <= 10
        assert 0 < sum(sleeps) <= 1
    else:
        assert len(failing.sources) == 1
        assert sleeps == []
