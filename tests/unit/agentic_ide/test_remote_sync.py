"""Moving a folder to a "server" and back, with real git and a real shell.

The server here is this machine: a pool double runs the remote commands in a
local POSIX shell inside a temp "home", and file transfer is a copy. That keeps
the part that can actually go wrong — the snapshot commit, the bundle, the
checkout on the other side, the way back — fully real, on every OS that has
git and a POSIX shell (Git Bash on Windows).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.agentic_ide import remote


def _posix_shell() -> str | None:
    if sys.platform == "win32":
        git = shutil.which("git")
        if git is None:
            return None
        roots = Path(git).resolve().parents
        for candidate in (
            *(root / "bin" / "bash.exe" for root in list(roots)[:3]),
            *(root / "usr" / "bin" / "bash.exe" for root in list(roots)[:3]),
        ):
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash") or shutil.which("sh")


SHELL = _posix_shell()
pytestmark = pytest.mark.skipif(
    SHELL is None or shutil.which("git") is None, reason="needs git and a POSIX shell"
)


class LocalPool:
    """Stands in for ``SshPtyPool``: the 'server' is a folder on this machine."""

    def __init__(self, home: Path) -> None:
        self._home = home

    async def home(self) -> str:
        return self._home.as_posix()

    async def run(self, command: str, *, timeout_s: float = 60.0) -> tuple[int, str, str]:
        result = await asyncio.to_thread(
            subprocess.run,
            [str(SHELL), "-c", command],
            capture_output=True,
            timeout=timeout_s,
            check=False,
            cwd=str(self._home),
        )
        return (
            result.returncode,
            result.stdout.decode("utf-8", "replace"),
            result.stderr.decode("utf-8", "replace"),
        )


@pytest.fixture
def copies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def upload(_pool: object, local: Path, remote_path: str) -> None:
        target = Path(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)

    async def download(_pool: object, remote_path: str, local: Path) -> None:
        shutil.copyfile(Path(remote_path), local)

    monkeypatch.setattr(remote, "_upload", upload)
    monkeypatch.setattr(remote, "_download", download)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={**__import__("os").environ, **remote._IDENTITY_ENV},
    ).stdout.strip()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "My App"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text("print('v1')\n", encoding="utf-8")
    (repo / ".gitignore").write_text("secret.env\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


async def test_offload_carries_uncommitted_and_new_files_but_not_ignored_ones(
    project: Path, tmp_path: Path, copies: None
) -> None:
    (project / "app.py").write_text("print('v2 draft')\n", encoding="utf-8")
    (project / "notes.md").write_text("new file\n", encoding="utf-8")
    (project / "secret.env").write_text("TOKEN=x\n", encoding="utf-8")
    index_before = (project / ".git" / "index").read_bytes()
    server = tmp_path / "server-home"
    server.mkdir()

    placement = await remote.push_code(LocalPool(server), project)

    there = Path(placement.remote_folder)
    assert (there / "app.py").read_text(encoding="utf-8") == "print('v2 draft')\n"
    assert (there / "notes.md").is_file()
    assert not (there / "secret.env").exists(), ".gitignore is honoured"
    assert _git(there, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "app.py" in _git(there, "status", "--porcelain"), "edits arrive uncommitted"
    assert (project / ".git" / "index").read_bytes() == index_before, "local index untouched"
    assert placement.offload_snapshot


async def test_bring_back_applies_the_servers_work_when_nothing_changed_here(
    project: Path, tmp_path: Path, copies: None
) -> None:
    server = tmp_path / "server-home"
    server.mkdir()
    pool = LocalPool(server)
    placement = await remote.push_code(pool, project)
    there = Path(placement.remote_folder)
    (there / "app.py").write_text("print('done on the vps')\n", encoding="utf-8")
    (there / "vps.txt").write_text("made there\n", encoding="utf-8")

    outcome = await remote.pull_code(
        pool, project, placement.remote_folder, placement.offload_snapshot, "My VPS"
    )

    assert outcome.applied, outcome.message
    assert (project / "app.py").read_text(encoding="utf-8") == "print('done on the vps')\n"
    assert (project / "vps.txt").is_file()
    assert outcome.branch and outcome.branch.startswith("jarvis/my-vps/")
    assert _git(project, "rev-parse", "--verify", outcome.branch)


async def test_bring_back_never_overwrites_local_changes(
    project: Path, tmp_path: Path, copies: None
) -> None:
    server = tmp_path / "server-home"
    server.mkdir()
    pool = LocalPool(server)
    placement = await remote.push_code(pool, project)
    (Path(placement.remote_folder) / "app.py").write_text("server\n", encoding="utf-8")
    (project / "app.py").write_text("local meanwhile\n", encoding="utf-8")

    outcome = await remote.pull_code(
        pool, project, placement.remote_folder, placement.offload_snapshot, "vps"
    )

    assert not outcome.applied
    assert (project / "app.py").read_text(encoding="utf-8") == "local meanwhile\n"
    assert outcome.branch and outcome.branch in outcome.message


async def test_second_offload_backs_up_the_servers_unsaved_state(
    project: Path, tmp_path: Path, copies: None
) -> None:
    server = tmp_path / "server-home"
    server.mkdir()
    pool = LocalPool(server)
    placement = await remote.push_code(pool, project)
    (Path(placement.remote_folder) / "app.py").write_text("unsaved on server\n", encoding="utf-8")

    await remote.push_code(pool, project)

    backups = _git(Path(placement.remote_folder), "for-each-ref", "refs/jarvis/backup")
    assert backups, "the server's own edits were kept before being replaced"


async def test_folder_without_git_travels_as_a_tarball(tmp_path: Path, copies: None) -> None:
    plain = tmp_path / "plain"
    (plain / "node_modules" / "x").mkdir(parents=True)
    (plain / "node_modules" / "x" / "big.js").write_text("x", encoding="utf-8")
    (plain / "main.txt").write_text("hello\n", encoding="utf-8")
    server = tmp_path / "server-home"
    server.mkdir()

    placement = await remote.push_code(LocalPool(server), plain)

    there = Path(placement.remote_folder)
    assert (there / "main.txt").read_text(encoding="utf-8") == "hello\n"
    assert not (there / "node_modules").exists()
    assert placement.offload_snapshot is None


def test_claude_project_dir_matches_the_cli_convention() -> None:
    assert remote.claude_project_dir("/root/jarvis-workspaces/app-1a2b3c") == (
        "-root-jarvis-workspaces-app-1a2b3c"
    )
