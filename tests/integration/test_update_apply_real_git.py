"""Integration test: the updater's REAL git operations against a local repo.

No network, no mocks — a real bare "upstream" and a real "installed" clone prove
that (a) the managed guard passes only with the marker + an official-slug origin
using real ``git remote get-url``, and (b) the fetch/reset sequence actually
moves the installed checkout to the new upstream tip. This exercises the one
destructive operation (``git reset --hard``) end to end.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

import jarvis.ui.web.update_routes as u

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git binary required"
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _make_upstream_and_install(tmp_path: Path) -> tuple[Path, Path, Path]:
    # The upstream path deliberately contains the official slug so the real
    # origin-URL guard passes against a purely local remote.
    upstream = tmp_path / "PersonalJarvis" / "PersonalJarvis.git"
    upstream.parent.mkdir(parents=True)
    _git(["init", "--bare", "-b", "main", str(upstream)], tmp_path)

    seed = tmp_path / "seed"
    _git(["clone", str(upstream), str(seed)], tmp_path)
    _git(["config", "user.email", "t@example.com"], seed)
    _git(["config", "user.name", "Tester"], seed)
    (seed / "jarvis").mkdir()
    init_py = seed / "jarvis" / "__init__.py"
    init_py.write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (seed / "requirements.txt").write_text("pkg==1\n", encoding="utf-8")
    _git(["add", "-A"], seed)
    _git(["commit", "-m", "A"], seed)
    _git(["push", "origin", "main"], seed)

    install = tmp_path / "install"
    _git(["clone", str(upstream), str(install)], tmp_path)
    (install / ".jarvis-managed-install").write_text("{}\n", encoding="utf-8")
    return upstream, seed, install


def test_managed_guard_passes_with_real_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream, _seed, install = _make_upstream_and_install(tmp_path)
    monkeypatch.setattr(u, "_repo_root", lambda: install)
    resolved = asyncio.run(u._resolve_managed_repo())
    assert resolved == install

    # Remove the marker → guard must fail-closed even though origin is official.
    (install / ".jarvis-managed-install").unlink()
    assert asyncio.run(u._resolve_managed_repo()) is None


def test_real_apply_moves_head_to_new_tip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _upstream, seed, install = _make_upstream_and_install(tmp_path)

    # Publish a new upstream commit B (bumped version).
    (seed / "jarvis" / "__init__.py").write_text('__version__ = "9.9.10"\n', encoding="utf-8")
    _git(["add", "-A"], seed)
    _git(["commit", "-m", "B"], seed)
    _git(["push", "origin", "main"], seed)

    # Before: the installed checkout is still on A.
    assert '9.9.9' in (install / "jarvis" / "__init__.py").read_text(encoding="utf-8")

    monkeypatch.setattr(u, "_repo_root", lambda: install)
    result = asyncio.run(u.update_apply())

    assert result["ok"] is True
    assert result["restart_required"] is True
    assert result["version"] == "9.9.10"
    # The real reset --hard moved the working tree to B.
    assert '9.9.10' in (install / "jarvis" / "__init__.py").read_text(encoding="utf-8")
