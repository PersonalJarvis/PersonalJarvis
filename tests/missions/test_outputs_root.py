"""Portable mission-output root selection tests."""

from __future__ import annotations

from jarvis.missions.isolation.worktree import (
    resolve_outputs_root,
    resolve_readable_outputs_roots,
)


def test_headless_data_dir_keeps_outputs_in_writable_volume(
    tmp_path, monkeypatch
) -> None:
    data_dir = tmp_path / "data-volume"
    monkeypatch.delenv("JARVIS_ISOLATION_ROOT", raising=False)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_dir))

    result = resolve_outputs_root(tmp_path / "read-only-app")

    assert result == data_dir.resolve() / "jarvis-agent-outputs"


def test_explicit_isolation_root_wins_over_data_dir(tmp_path, monkeypatch) -> None:
    explicit = tmp_path / "explicit-outputs"
    monkeypatch.setenv("JARVIS_ISOLATION_ROOT", str(explicit))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data-volume"))

    assert resolve_outputs_root(tmp_path / "repo") == explicit.resolve()


def test_checkout_defaults_to_repo_parent_when_no_override(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    monkeypatch.delenv("JARVIS_ISOLATION_ROOT", raising=False)
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)

    assert resolve_outputs_root(repo) == tmp_path / "jarvis-agent-outputs"


def test_read_roots_include_retained_legacy_history_when_both_exist(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    canonical = tmp_path / "jarvis-agent-outputs"
    legacy = tmp_path / "sub-agents-outputs"
    canonical.mkdir()
    legacy.mkdir()
    monkeypatch.delenv("JARVIS_ISOLATION_ROOT", raising=False)
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)

    assert resolve_readable_outputs_roots(repo) == (canonical, legacy)


def test_explicit_read_root_never_scans_a_legacy_sibling(tmp_path, monkeypatch) -> None:
    explicit = tmp_path / "jarvis-agent-outputs"
    (tmp_path / "sub-agents-outputs").mkdir()
    monkeypatch.setenv("JARVIS_ISOLATION_ROOT", str(explicit))
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)

    assert resolve_readable_outputs_roots(tmp_path / "repo") == (explicit.resolve(),)
