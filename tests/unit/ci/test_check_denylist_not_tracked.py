"""Tests for the CI distribution-denylist gate.

The gate lives in scripts/ci/ (not an importable package), so we add that
directory to sys.path before importing — mirroring how CI runs the scripts
directly with `python scripts/ci/<name>.py`.

The behaviour worth pinning is the split the gate exists for: it must BLOCK a
withheld path the moment a change adds it, while staying silent about the
withheld files already tracked. A gate that also failed on the backlog would
block every commit and be disabled within the hour — which is exactly how the
register lost its teeth the first time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_CI = Path(__file__).resolve().parents[3] / "scripts" / "ci"
sys.path.insert(0, str(_SCRIPTS_CI))

import check_denylist_not_tracked as gate  # noqa: E402

DENYLIST = """\
# --- Internal scratch scripts (underscore-prefixed convention) --------------
scripts/_*.py

# Captures of the maintainer's LIVE vault: relocation planning, finances,
# named third parties.
video/qa-wiki/**
assets/screenshots/view-wiki.png
"""


@pytest.fixture
def engine():
    eng = gate._load_engine()
    if eng is None:
        pytest.skip("privacy-gate module not present in this tree")
    return eng


class TestMatching:
    def test_flags_a_withheld_path(self, engine):
        hits = gate._violations(["scripts/_scratch.py"], engine, DENYLIST)
        assert [h[0] for h in hits] == ["scripts/_scratch.py"]
        assert hits[0][1] == "scripts/_*.py"

    def test_subtree_glob_matches_at_any_depth(self, engine):
        paths = ["video/qa-wiki/map.png", "video/qa-wiki/frames/t01.jpg"]
        assert [h[0] for h in gate._violations(paths, engine, DENYLIST)] == paths

    def test_exact_path_entry(self, engine):
        assert gate._violations(["assets/screenshots/view-wiki.png"], engine, DENYLIST)

    def test_ordinary_paths_are_clean(self, engine):
        clean = [
            "scripts/preflight.ps1",
            "scripts/ci/check_denylist_not_tracked.py",  # no leading underscore
            "video/src/intro/scenes/Integrations.tsx",  # video/, but not qa-wiki
            "assets/screenshots/app-home.png",
            "jarvis/core/bus.py",
        ]
        assert gate._violations(clean, engine, DENYLIST) == []

    def test_each_path_reported_once(self, engine):
        """A path matching several entries yields one hit, not one per entry."""
        overlapping = DENYLIST + "\nscripts/**\n"
        hits = gate._violations(["scripts/_scratch.py"], engine, overlapping)
        assert len(hits) == 1


class TestRationale:
    def test_surfaces_the_comment_block_above_the_entry(self):
        why = gate._rationale_for("scripts/_*.py", DENYLIST)
        assert "Internal scratch scripts" in why

    def test_joins_a_multi_line_comment_block(self):
        why = gate._rationale_for("video/qa-wiki/**", DENYLIST)
        assert "LIVE vault" in why and "named third parties" in why

    def test_entry_without_a_preceding_comment(self):
        assert gate._rationale_for("assets/screenshots/view-wiki.png", DENYLIST) == ""

    def test_unknown_entry_does_not_raise(self):
        assert gate._rationale_for("nope/**", DENYLIST) == ""


class TestModes:
    def test_staged_mode_blocks_on_an_added_withheld_path(self, engine, monkeypatch, capsys):
        monkeypatch.setattr(gate, "_load_engine", lambda: engine)
        monkeypatch.setattr(gate, "_added_paths", lambda: ["scripts/_scratch.py"])
        monkeypatch.setattr(gate, "_tracked_paths", lambda: [])
        monkeypatch.setattr(
            Path, "read_text", lambda self, **kw: DENYLIST, raising=False
        )
        assert gate.main(["--staged"]) == 1
        out = capsys.readouterr().out
        assert "scripts/_scratch.py" in out
        assert "Internal scratch scripts" in out  # the reason, not just the path

    def test_staged_mode_ignores_the_tracked_backlog(self, engine, monkeypatch):
        """The whole point: a dirty tree must not make every commit fail."""
        monkeypatch.setattr(gate, "_load_engine", lambda: engine)
        monkeypatch.setattr(gate, "_added_paths", lambda: ["jarvis/core/bus.py"])
        monkeypatch.setattr(gate, "_tracked_paths", lambda: ["scripts/_old_scratch.py"])
        monkeypatch.setattr(
            Path, "read_text", lambda self, **kw: DENYLIST, raising=False
        )
        assert gate.main(["--staged"]) == 0

    def test_report_mode_lists_the_backlog_without_failing(self, engine, monkeypatch, capsys):
        monkeypatch.setattr(gate, "_load_engine", lambda: engine)
        monkeypatch.setattr(gate, "_tracked_paths", lambda: ["scripts/_old_scratch.py"])
        monkeypatch.setattr(
            Path, "read_text", lambda self, **kw: DENYLIST, raising=False
        )
        assert gate.main(["--report"]) == 0
        assert "scripts/_*.py" in capsys.readouterr().out

    def test_check_mode_fails_on_the_same_backlog(self, engine, monkeypatch):
        monkeypatch.setattr(gate, "_load_engine", lambda: engine)
        monkeypatch.setattr(gate, "_tracked_paths", lambda: ["scripts/_old_scratch.py"])
        monkeypatch.setattr(
            Path, "read_text", lambda self, **kw: DENYLIST, raising=False
        )
        assert gate.main(["--check"]) == 1

    def test_skips_cleanly_when_the_register_is_absent(self, monkeypatch, capsys):
        """A fork without the personal privacy gate must not be blocked by it."""
        monkeypatch.setattr(gate, "_load_engine", lambda: None)
        assert gate.main(["--staged"]) == 0
        assert "SKIP" in capsys.readouterr().out
