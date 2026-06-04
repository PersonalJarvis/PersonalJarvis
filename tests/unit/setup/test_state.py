"""Unit tests for ``jarvis.setup.state`` (Phase B9.7 / Sub-Agent 6).

Pure-Python tests that never touch the real ``data/setup_state.json``;
every read and write happens under pytest's ``tmp_path``. Each test
covers a single property of the tiny JSON-backed first-run flag store:
missing file, corrupt JSON, happy-path round trip, directory
creation, key preservation, timestamp predicate, and atomic-write
hygiene (no leftover tempfiles).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jarvis.setup.state import (
    has_seen_obsidian_setup,
    load_setup_state,
    mark_obsidian_seen,
)


# ---------------------------------------------------------------------------
# (a) load_setup_state: missing file → {}
# ---------------------------------------------------------------------------
def test_load_setup_state_missing_file(tmp_path: Path) -> None:
    """Path that does not exist returns an empty dict, no exception."""
    target = tmp_path / "nope" / "setup_state.json"
    assert not target.exists()
    assert load_setup_state(target) == {}


# ---------------------------------------------------------------------------
# (b) load_setup_state: corrupt JSON → {}
# ---------------------------------------------------------------------------
def test_load_setup_state_corrupt_json(tmp_path: Path) -> None:
    """Malformed JSON content is treated as an empty state silently."""
    target = tmp_path / "setup_state.json"
    target.write_text("{not valid", encoding="utf-8")
    # Must not raise.
    assert load_setup_state(target) == {}


# ---------------------------------------------------------------------------
# (c) load_setup_state: valid JSON → matching dict
# ---------------------------------------------------------------------------
def test_load_setup_state_happy_path(tmp_path: Path) -> None:
    """A well-formed JSON object round-trips byte-for-byte into a dict."""
    target = tmp_path / "setup_state.json"
    payload = {"obsidian_setup_seen_at": "2026-05-14T10:30:00+00:00", "other": 42}
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert load_setup_state(target) == payload


# ---------------------------------------------------------------------------
# (d) mark_obsidian_seen: creates parent directory + file
# ---------------------------------------------------------------------------
def test_mark_obsidian_seen_creates_file_and_dir(tmp_path: Path) -> None:
    """Parent directory is created on demand; file persists the timestamp key."""
    target = tmp_path / "data" / "setup_state.json"
    # Neither the file nor its parent exists yet.
    assert not target.parent.exists()

    mark_obsidian_seen(target)

    assert target.exists()
    state = json.loads(target.read_text(encoding="utf-8"))
    assert "obsidian_setup_seen_at" in state
    assert isinstance(state["obsidian_setup_seen_at"], str)
    assert state["obsidian_setup_seen_at"]  # non-empty


# ---------------------------------------------------------------------------
# (e) mark_obsidian_seen: preserves other keys
# ---------------------------------------------------------------------------
def test_mark_obsidian_seen_preserves_other_keys(tmp_path: Path) -> None:
    """A pre-existing top-level key survives the timestamp update."""
    target = tmp_path / "setup_state.json"
    target.write_text(json.dumps({"other": 42}), encoding="utf-8")

    mark_obsidian_seen(target)

    state = json.loads(target.read_text(encoding="utf-8"))
    assert state["other"] == 42
    assert "obsidian_setup_seen_at" in state


# ---------------------------------------------------------------------------
# (f) has_seen_obsidian_setup: False before mark, True after
# ---------------------------------------------------------------------------
def test_has_seen_obsidian_setup(tmp_path: Path) -> None:
    """Predicate flips from False to True across a single mark_* call."""
    target = tmp_path / "setup_state.json"
    assert has_seen_obsidian_setup(target) is False

    mark_obsidian_seen(target)

    assert has_seen_obsidian_setup(target) is True


# ---------------------------------------------------------------------------
# (g) mark_obsidian_seen: idempotent timestamp refresh on repeated calls
# ---------------------------------------------------------------------------
def test_mark_is_idempotent_on_timestamp_update(tmp_path: Path) -> None:
    """Two consecutive marks both succeed; the second updates the timestamp."""
    target = tmp_path / "setup_state.json"

    mark_obsidian_seen(target)
    first_ts = json.loads(target.read_text(encoding="utf-8"))["obsidian_setup_seen_at"]

    # Small sleep so the ISO timestamp can change at microsecond precision.
    time.sleep(0.01)

    mark_obsidian_seen(target)
    second_ts = json.loads(target.read_text(encoding="utf-8"))["obsidian_setup_seen_at"]

    # Both are valid; the second is at least as recent as the first. We
    # do not require strict inequality because ISO timestamps on some
    # filesystems can collapse to the same microsecond — but the call
    # MUST succeed twice (idempotent) and leave a non-empty value.
    assert first_ts
    assert second_ts
    assert second_ts >= first_ts


# ---------------------------------------------------------------------------
# (h) mark_obsidian_seen: no tempfile leftovers
# ---------------------------------------------------------------------------
def test_mark_uses_atomic_write_no_tempfile_leftover(tmp_path: Path) -> None:
    """After a successful mark, no .tmp-* siblings remain in the parent dir."""
    target = tmp_path / "setup_state.json"

    mark_obsidian_seen(target)

    siblings = [p.name for p in tmp_path.iterdir()]
    assert target.name in siblings
    leftovers = [n for n in siblings if ".tmp-" in n]
    assert leftovers == [], f"tempfile leftovers: {leftovers}"
