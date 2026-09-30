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
import types
from pathlib import Path

import pytest

from jarvis.agentic_ide import remote
from jarvis.computers import remote_os


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

    async def host(self) -> remote_os.RemoteHost:
        return remote_os.RemoteHost()

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


async def test_secrets_and_huge_new_files_stay_on_this_machine(
    project: Path, tmp_path: Path, copies: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote, "MAX_FILE_BYTES", 1000)
    (project / "deploy.pem").write_text("committed on purpose\n", encoding="utf-8")
    _git(project, "add", "deploy.pem")
    _git(project, "commit", "-q", "-m", "tracked key")
    (project / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
    (project / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
    (project / "keys").mkdir()
    (project / "keys" / "id_ed25519").write_text("-----BEGIN OPENSSH\n", encoding="utf-8")
    (project / "big.bin").write_bytes(b"0" * 2000)
    (project / "notes.md").write_text("fine\n", encoding="utf-8")
    server = tmp_path / "server-home"
    server.mkdir()

    placement = await remote.push_code(LocalPool(server), project)

    there = Path(placement.remote_folder)
    assert (there / "notes.md").is_file()
    assert (there / ".env.example").is_file(), "a template holds no secret"
    assert (there / "deploy.pem").is_file(), "what is committed already travels"
    for held in (".env", "keys/id_ed25519", "big.bin"):
        assert not (there / held).exists(), held
    assert set(placement.left_behind) == {".env", "keys/id_ed25519", "big.bin"}
    assert ".env" in remote.left_behind_note(placement.left_behind)


async def test_bring_back_still_applies_when_secrets_stayed_here(
    project: Path, tmp_path: Path, copies: None
) -> None:
    (project / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
    server = tmp_path / "server-home"
    server.mkdir()
    pool = LocalPool(server)
    placement = await remote.push_code(pool, project)
    (Path(placement.remote_folder) / "app.py").write_text("print('server')\n", encoding="utf-8")

    outcome = await remote.pull_code(
        pool, project, placement.remote_folder, placement.offload_snapshot, "vps"
    )

    assert outcome.applied, outcome.message
    assert (project / ".env").read_text(encoding="utf-8") == "API_KEY=secret\n"


async def test_bring_back_is_byte_exact_for_blank_line_hunks_and_other_encodings(
    project: Path, tmp_path: Path, copies: None
) -> None:
    (project / "text.txt").write_text("one\ntwo\n\n", encoding="utf-8")
    _git(project, "add", "text.txt")
    _git(project, "commit", "-q", "-m", "ends on a blank line")
    server = tmp_path / "server-home"
    server.mkdir()
    pool = LocalPool(server)
    placement = await remote.push_code(pool, project)
    there = Path(placement.remote_folder)
    (there / "text.txt").write_text("ONE\ntwo\n\n", encoding="utf-8")
    (there / "latin.txt").write_bytes("caf\xe9\n".encode("latin-1"))

    outcome = await remote.pull_code(
        pool, project, placement.remote_folder, placement.offload_snapshot, "vps"
    )

    assert outcome.applied, outcome.message
    assert (project / "text.txt").read_bytes().replace(b"\r\n", b"\n") == b"ONE\ntwo\n\n"
    assert (project / "latin.txt").read_bytes().replace(b"\r\n", b"\n") == b"caf\xe9\n"


def test_a_branch_name_taken_in_the_same_second_gets_a_counter(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote, "time", types.SimpleNamespace(strftime=lambda _f: "20260930-1200"))
    first = remote._free_branch(project, "jarvis/vps")
    _git(project, "branch", first)

    assert first == "jarvis/vps/20260930-1200"
    assert remote._free_branch(project, "jarvis/vps") == "jarvis/vps/20260930-1200-2"


async def test_preflight_names_every_missing_command(tmp_path: Path) -> None:
    pool = LocalPool(tmp_path)

    with pytest.raises(remote.MoveError, match="Nope CLI is not installed on vps"):
        await remote.preflight(pool, {"git": "git", "Nope CLI": "no-such-cli-4711"}, "vps")
    await remote.preflight(pool, {"git": "git"}, "vps")


async def test_folder_without_git_leaves_secrets_here(tmp_path: Path, copies: None) -> None:
    plain = tmp_path / "plain"
    (plain / "sub").mkdir(parents=True)
    (plain / "main.txt").write_text("hello\n", encoding="utf-8")
    (plain / ".env").write_text("TOKEN=x\n", encoding="utf-8")
    (plain / "sub" / "id_rsa").write_text("key\n", encoding="utf-8")
    server = tmp_path / "server-home"
    server.mkdir()

    placement = await remote.push_code(LocalPool(server), plain)

    there = Path(placement.remote_folder)
    assert (there / "main.txt").is_file()
    assert not (there / ".env").exists()
    assert not (there / "sub" / "id_rsa").exists()
    assert set(placement.left_behind) == {".env", "sub/id_rsa"}


async def test_a_committed_file_renamed_to_env_stays_here(
    project: Path, tmp_path: Path, copies: None
) -> None:
    (project / "sample.cfg").write_text("KEY=placeholder\n", encoding="utf-8")
    _git(project, "add", "sample.cfg")
    _git(project, "commit", "-q", "-m", "template")
    (project / "sample.cfg").rename(project / ".env")  # git would call this a rename
    server = tmp_path / "server-home"
    server.mkdir()

    placement = await remote.push_code(LocalPool(server), project)

    assert not (Path(placement.remote_folder) / ".env").exists()
    assert ".env" in placement.left_behind


async def test_an_assume_unchanged_edit_still_travels(
    project: Path, tmp_path: Path, copies: None
) -> None:
    _git(project, "update-index", "--assume-unchanged", "app.py")
    (project / "app.py").write_text("print('edited anyway')\n", encoding="utf-8")
    server = tmp_path / "server-home"
    server.mkdir()

    placement = await remote.push_code(LocalPool(server), project)

    there = Path(placement.remote_folder) / "app.py"
    assert there.read_text(encoding="utf-8") == "print('edited anyway')\n"
    assert "h app.py" in _git(project, "ls-files", "-v", "app.py"), "the user's own mark stays"


def test_joined_folder_maps_a_subfolder_into_the_same_copy(tmp_path: Path) -> None:
    top = tmp_path / "repo"
    (top / "pkg" / "sub").mkdir(parents=True)

    from_root = remote.joined_folder("/h/jw/repo-1", top, top / "pkg", top)
    from_sub = remote.joined_folder("/h/jw/repo-1/pkg/sub", top / "pkg" / "sub", top, top)

    assert from_root == "/h/jw/repo-1/pkg"
    assert from_sub == "/h/jw/repo-1"


async def test_a_repo_without_commits_comes_back_too(tmp_path: Path, copies: None) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q", "-b", "main")
    (fresh / "draft.py").write_text("print('draft')\n", encoding="utf-8")
    server = tmp_path / "server-home"
    server.mkdir()
    pool = LocalPool(server)
    placement = await remote.push_code(pool, fresh)
    (Path(placement.remote_folder) / "draft.py").write_text("print('finished')\n", encoding="utf-8")

    outcome = await remote.pull_code(
        pool, fresh, placement.remote_folder, placement.offload_snapshot, "vps"
    )

    assert outcome.applied, outcome.message
    assert (fresh / "draft.py").read_text(encoding="utf-8") == "print('finished')\n"
