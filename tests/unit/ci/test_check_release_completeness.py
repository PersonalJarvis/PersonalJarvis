"""Fail-closed tests for the release-completeness gate's pure helpers.

The gate exists so a release can never SILENTLY ship without local work
(CLAUDE.md section 2 + docs/device-parity-debugging.md). These tests pin the
decision logic that does not need a live git repo or network: dirty-path
parsing (renames included), the volatile-telemetry allowlist, version-parity
reading, and the published-release tag match.
"""

from __future__ import annotations

from pathlib import Path

from scripts.ci import check_release_completeness as gate


def test_parse_dirty_paths_keeps_rename_targets_and_untracked():
    porcelain = (
        " M jarvis/platform/permissions.py\n"
        "?? test_output.txt\n"
        'RM "old name.tsx" -> jarvis/ui/new_name.tsx\n'
    )
    assert gate.parse_dirty_paths(porcelain) == [
        "jarvis/platform/permissions.py",
        "test_output.txt",
        "jarvis/ui/new_name.tsx",
    ]


def test_allowlist_removes_only_the_volatile_telemetry_stamp():
    paths = ["desktop-ttu-latest.json", "jarvis/core/config.py"]
    assert gate.filter_allowlisted(paths, gate.DIRTY_ALLOWLIST) == ["jarvis/core/config.py"]
    # Windows-style separators must not sneak a path past the allowlist match.
    assert gate.filter_allowlisted(["desktop-ttu-latest.json"], gate.DIRTY_ALLOWLIST) == []


def test_read_versions_reports_drift_between_the_two_sources(tmp_path: Path):
    (tmp_path / "jarvis").mkdir()
    (tmp_path / "jarvis" / "__init__.py").write_text('__version__ = "1.2.0"\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n', encoding="utf-8")

    assert gate.read_versions(tmp_path) == ("1.2.0", "1.1.0")
    ok, message = gate.check_version_parity(tmp_path)
    assert not ok
    assert "drift" in message


def test_read_versions_fails_closed_on_missing_files(tmp_path: Path):
    assert gate.read_versions(tmp_path) == (None, None)
    ok, _ = gate.check_version_parity(tmp_path)
    assert not ok


def test_dirty_check_keeps_the_first_entry_intact(monkeypatch, tmp_path: Path):
    # Regression: stripping git's stdout ate the leading status space of the
    # FIRST porcelain line and shifted its path parse by one character
    # ("AGENTS.md" surfaced as "GENTS.md"). The check must consume the raw,
    # unstripped porcelain exactly as _run_git now returns it.
    porcelain = " M AGENTS.md\n?? notes.txt"
    monkeypatch.setattr(gate, "_run_git", lambda args, *, cwd: (0, porcelain))

    ok, message = gate.check_dirty_tree(tmp_path, ack_dirty=False)
    assert not ok
    assert "AGENTS.md" in message
    assert "GENTS.md" not in message.replace("AGENTS.md", "")


def test_release_matches_accepts_v_prefix_and_rejects_other_versions():
    assert gate.release_matches("v1.1.0", "1.1.0")
    assert gate.release_matches("1.1.0", "1.1.0")
    assert not gate.release_matches("v1.2.0", "1.1.0")
    # No published release at all must never satisfy the gate.
    assert not gate.release_matches("", "1.1.0")
    assert not gate.release_matches("v1.1.0", "")
