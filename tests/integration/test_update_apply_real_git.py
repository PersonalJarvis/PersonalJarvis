"""Integration test: the updater's real git operations against a local repo.

A real bare upstream and installed clone prove that the managed guard requires
the marker plus official-slug origin, and that apply fetches the exact published
tag without mutating the running checkout. Only the release-API edge is mocked.
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


def _git_output(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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


def test_real_apply_fetches_and_pins_without_mutating_live_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream, seed, install = _make_upstream_and_install(tmp_path)

    # Publish a new upstream commit B (bumped version).
    (seed / "jarvis" / "__init__.py").write_text('__version__ = "9.9.10"\n', encoding="utf-8")
    _git(["add", "-A"], seed)
    _git(["commit", "-m", "B"], seed)
    _git(["tag", "v9.9.10"], seed)
    _git(["push", "origin", "main"], seed)
    _git(["push", "origin", "v9.9.10"], seed)

    # Before: the installed checkout is still on A.
    assert '9.9.9' in (install / "jarvis" / "__init__.py").read_text(encoding="utf-8")

    monkeypatch.setattr(u, "_repo_root", lambda: install)
    monkeypatch.setattr(u, "_running_version", lambda: "9.9.9")

    async def _latest() -> dict[str, object]:
        return {"version": "9.9.10", "tag": "v9.9.10"}

    monkeypatch.setattr(u, "_fetch_latest_release", _latest)
    result = asyncio.run(u.update_apply())

    assert result["ok"] is True
    assert result["prepared"] is True
    assert result["restart_required"] is True
    assert result["version"] == "9.9.10"
    # The running checkout stays on A until the old process has exited.
    assert '9.9.9' in (install / "jarvis" / "__init__.py").read_text(encoding="utf-8")
    pending = (install / u._PENDING_UPDATE_NAME).read_text(encoding="utf-8")
    assert _git_output(["rev-parse", "HEAD"], install) in pending
    assert _git_output(["rev-parse", "FETCH_HEAD^{commit}"], install) in pending
