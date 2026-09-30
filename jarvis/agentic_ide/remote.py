"""Moving IDE work between this computer and a connected one.

Three things travel when a pane moves to a VPS ("offload") or comes back:

1. **The code.** A git folder travels as a *snapshot commit* — the working
   tree exactly as it is, uncommitted edits and new files included — built
   with a throw-away index so the user's own index, branch and stash are never
   touched, and shipped as a git bundle over SFTP. On the server it lands
   checked out on the same branch with the edits back as uncommitted changes.
   Only objects the server does not have yet are sent after the first time.
   A folder without git travels as a tarball (heavy build folders skipped).
2. **The conversation.** Claude Code and Codex keep a transcript per
   conversation id; that file is copied into the matching place on the other
   side, so the agent continues with ``--resume`` instead of starting blind.
   Other agents start fresh there (said so in the log).
3. **Nothing else.** No credentials move here; logging a CLI in on the server
   is its own explicit step (``jarvis.computers.toolbox``).

Coming back reverses 1 and 2. The server's work returns as a local branch
``jarvis/<computer>/<time>``; when this folder has not changed since the
offload, the same changes are also applied to the working tree, so the user
simply finds the work where it was.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jarvis.computers.remote_terminal import SshPtyPool
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

REMOTE_ROOT = "jarvis-workspaces"
SYNC_DIR = ".jarvis-sync"
MAX_TARBALL_BYTES = 300 * 1024 * 1024
_SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".cache", ".mypy_cache", ".next"}
_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "Jarvis",
    "GIT_AUTHOR_EMAIL": "jarvis@localhost",
    "GIT_COMMITTER_NAME": "Jarvis",
    "GIT_COMMITTER_EMAIL": "jarvis@localhost",
}


class MoveError(RuntimeError):
    """A move failed; the message is written for the user."""


@dataclass(frozen=True)
class Placement:
    """Where a pane's code lives on the server, and what it left behind here."""

    remote_folder: str
    #: The snapshot commit the server was given (git folders only).
    offload_snapshot: str | None


# ---------------------------------------------------------------------------
# local git, never touching the user's index
# ---------------------------------------------------------------------------


def _git(
    cwd: Path, *args: str, env: dict[str, str] | None = None, stdin: bytes | None = None
) -> str:
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(  # noqa: S603 — fixed git argv, no shell
        ["git", *args],  # noqa: S607
        cwd=str(cwd),
        env=merged,
        input=stdin,
        capture_output=True,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise MoveError(f"git {args[0]} failed: {detail[-1] if detail else result.returncode}")
    return result.stdout.decode("utf-8", "replace").strip()


def git_toplevel(folder: Path) -> Path | None:
    try:
        return Path(_git(folder, "rev-parse", "--show-toplevel"))
    except (MoveError, OSError):
        # Not a git checkout (or git missing): None is the documented answer.
        return None


def _head(top: Path) -> str | None:
    try:
        return _git(top, "rev-parse", "-q", "--verify", "HEAD^{commit}")
    except MoveError:
        # An unborn repo has no HEAD yet; None is the documented answer.
        return None


def _branch(top: Path) -> str | None:
    try:
        name = _git(top, "symbolic-ref", "-q", "--short", "HEAD")
    except MoveError:
        # Detached HEAD has no branch name; None is the documented answer.
        return None
    return name or None


def snapshot_commit(top: Path, message: str) -> tuple[str, str]:
    """Commit the working tree as it is (tracked + untracked, .gitignore honoured).

    Returns ``(commit, tree)``. Uses a private index file, so the user's index,
    branch and stash are untouched; the commit is reachable only through the
    ref the caller gives it.
    """
    head = _head(top)
    with tempfile.TemporaryDirectory(prefix="jarvis-snap-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), **_IDENTITY_ENV}
        if head:
            _git(top, "read-tree", head, env=env)
        _git(top, "add", "-A", env=env)
        tree = _git(top, "write-tree", env=env)
    parents = ["-p", head] if head else []
    commit = _git(top, "commit-tree", tree, *parents, "-m", message, env=_IDENTITY_ENV)
    return commit, tree


def working_tree_id(top: Path) -> str:
    """The tree id the working tree would commit to right now."""
    head = _head(top)
    with tempfile.TemporaryDirectory(prefix="jarvis-tree-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if head:
            _git(top, "read-tree", head, env=env)
        _git(top, "add", "-A", env=env)
        return _git(top, "write-tree", env=env)


def _relative_posix(folder: Path, top: Path) -> str:
    relative = os.path.relpath(os.path.realpath(folder), os.path.realpath(top))
    return PurePosixPath(*Path(relative).parts).as_posix()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32] or "workspace"


def remote_folder_for(home: str, local_folder: Path) -> str:
    """Stable per local folder: ``~/jarvis-workspaces/<name>-<hash>``."""
    digest = hashlib.sha1(str(local_folder.resolve()).encode("utf-8")).hexdigest()[:6]  # noqa: S324
    return str(PurePosixPath(home) / REMOTE_ROOT / f"{_slug(local_folder.name)}-{digest}")


# ---------------------------------------------------------------------------
# code: up
# ---------------------------------------------------------------------------


_APPLY = r"""
set -e
DEST={dest}; BUNDLE={bundle}; REF={ref}; BASE={base}; BRANCH={branch}; SNAP={snap}
mkdir -p "$DEST" && cd "$DEST"
[ -d .git ] || git init -q
if git rev-parse -q --verify HEAD >/dev/null && [ -n "$(git status --porcelain)" ]; then
  export GIT_INDEX_FILE="$(mktemp)"; git read-tree HEAD; git add -A; t=$(git write-tree)
  c=$(git -c user.name=Jarvis -c user.email=jarvis@localhost commit-tree "$t" -p HEAD \
      -m "jarvis: server state before sync")
  git update-ref "refs/jarvis/backup/$(date +%s)" "$c"
  rm -f "$GIT_INDEX_FILE"; unset GIT_INDEX_FILE
fi
git fetch -q "$BUNDLE" "$REF:$REF"
if [ -n "$BASE" ]; then
  if [ -n "$BRANCH" ]; then git checkout -q -f -B "$BRANCH" "$BASE"
  else git checkout -q -f --detach "$BASE"; fi
fi
git read-tree -u --reset "$SNAP"
if [ -n "$BASE" ]; then git reset -q; fi
rm -f "$BUNDLE"
echo ok
"""


async def _remote_known(pool: SshPtyPool, remote_folder: str) -> list[str]:
    code, out, _err = await pool.run(
        f"cd {shlex.quote(remote_folder)} 2>/dev/null && git for-each-ref --format='%(objectname)' "
        "refs/heads refs/jarvis 2>/dev/null",
        timeout_s=30,
    )
    return [
        line.strip() for line in out.splitlines() if re.fullmatch(r"[0-9a-f]{40}", line.strip())
    ]


async def _upload(pool: SshPtyPool, local: Path, remote: str) -> None:
    session = await pool.connection()
    async with session.conn.start_sftp_client() as sftp:
        await sftp.makedirs(str(PurePosixPath(remote).parent), exist_ok=True)
        await sftp.put(str(local), remote)


async def _download(pool: SshPtyPool, remote: str, local: Path) -> None:
    session = await pool.connection()
    async with session.conn.start_sftp_client() as sftp:
        await sftp.get(remote, str(local))


async def push_code(pool: SshPtyPool, local_folder: Path) -> Placement:
    """Put ``local_folder`` (as it is right now) onto the server."""
    home = await pool.home()
    code, _out, _err = await pool.run("command -v git >/dev/null")
    if code != 0:
        raise MoveError("This computer needs git first. Open Computers, then Prepare.")
    top = await asyncio.to_thread(git_toplevel, local_folder)
    if top is None:
        remote = remote_folder_for(home, local_folder)
        await _push_tarball(pool, local_folder, remote)
        return Placement(remote_folder=remote, offload_snapshot=None)

    remote_top = remote_folder_for(home, top)
    relative = await asyncio.to_thread(_relative_posix, local_folder, top)
    remote_folder = (
        str(PurePosixPath(remote_top) / relative) if relative not in ("", ".") else remote_top
    )
    transfer = uuid.uuid4().hex[:12]
    ref = f"refs/jarvis/offload/{transfer}"
    snap, _tree = await asyncio.to_thread(snapshot_commit, top, "jarvis: offload snapshot")
    head = await asyncio.to_thread(_head, top)
    branch = await asyncio.to_thread(_branch, top)
    known = await _remote_known(pool, remote_top)
    with tempfile.TemporaryDirectory(prefix="jarvis-bundle-") as tmp:
        bundle = Path(tmp) / "offload.bundle"
        await asyncio.to_thread(_git, top, "update-ref", ref, snap)
        present = []
        for sha in known:
            try:
                await asyncio.to_thread(_git, top, "cat-file", "-e", f"{sha}^{{commit}}")
                present.append(f"^{sha}")
            except MoveError:
                # The target lacks this commit, so it cannot serve as a bundle base.
                continue
        try:
            await asyncio.to_thread(_git, top, "bundle", "create", str(bundle), ref, *present)
        except MoveError:
            # Nothing new to send is reported as an error by git; send it whole.
            await asyncio.to_thread(_git, top, "bundle", "create", str(bundle), ref)
        remote_bundle = f"{home}/{SYNC_DIR}/{transfer}.bundle"
        await _upload(pool, bundle, remote_bundle)
    script = _APPLY.format(
        dest=shlex.quote(remote_top),
        bundle=shlex.quote(remote_bundle),
        ref=shlex.quote(ref),
        base=shlex.quote(head or ""),
        branch=shlex.quote(branch or ""),
        snap=shlex.quote(snap),
    )
    code, out, err = await pool.run(script, timeout_s=600)
    if code != 0 or "ok" not in out:
        detail = (err or out).strip().splitlines()
        raise MoveError(
            "The code could not be set up on the server" + (f": {detail[-1]}" if detail else ".")
        )
    return Placement(remote_folder=remote_folder, offload_snapshot=snap)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = PurePosixPath(info.name).parts
    if any(part in _SKIP_DIRS for part in parts):
        return None
    return info


async def _push_tarball(pool: SshPtyPool, folder: Path, remote: str) -> None:
    with tempfile.TemporaryDirectory(prefix="jarvis-tar-") as tmp:
        archive = Path(tmp) / "folder.tar.gz"

        def build() -> int:
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(str(folder), arcname=".", filter=_tar_filter)
            return archive.stat().st_size

        size = await asyncio.to_thread(build)
        if size > MAX_TARBALL_BYTES:
            raise MoveError("This folder is too large to copy without git (over 300 MB).")
        home = await pool.home()
        archive_name = f"{uuid.uuid4().hex[:12]}.tar.gz"
        sync_dir = f"{home}/{SYNC_DIR}"
        await _upload(pool, archive, f"{sync_dir}/{archive_name}")
    # Relative archive name: some tars read "C:/..." as a remote host.
    code, _out, err = await pool.run(
        f"mkdir -p {shlex.quote(remote)} && cd {shlex.quote(sync_dir)} && "
        f"tar -xzf {shlex.quote(archive_name)} -C {shlex.quote(remote)} && "
        f"rm -f {shlex.quote(archive_name)}",
        timeout_s=600,
    )
    if code != 0:
        raise MoveError(f"The folder could not be unpacked on the server: {err.strip()[:200]}")


# ---------------------------------------------------------------------------
# code: down
# ---------------------------------------------------------------------------


_PACK = r"""
set -e
cd {dest}
export GIT_INDEX_FILE="$(mktemp)"; git read-tree HEAD; git add -A; t=$(git write-tree)
c=$(git -c user.name=Jarvis -c user.email=jarvis@localhost commit-tree "$t" -p HEAD \
    -m "jarvis: work from the server")
rm -f "$GIT_INDEX_FILE"; unset GIT_INDEX_FILE
git update-ref {ref} "$c"
mkdir -p {sync}
git bundle create {bundle} {ref} {exclude} >/dev/null 2>&1 \
  || git bundle create {bundle} {ref} >/dev/null
echo "$c"
"""


@dataclass(frozen=True)
class Return:
    branch: str | None
    applied: bool
    message: str


async def pull_code(
    pool: SshPtyPool,
    local_folder: Path,
    remote_folder: str,
    offload_snapshot: str | None,
    computer_name: str,
) -> Return:
    """Bring the server's version of the folder back."""
    top = await asyncio.to_thread(git_toplevel, local_folder)
    if top is None or offload_snapshot is None:
        return Return(
            branch=None,
            applied=False,
            message="This folder has no git history, so the server's copy stays on the server.",
        )
    home = await pool.home()
    code, remote_top, _ = await pool.run(
        f"cd {shlex.quote(remote_folder)} && git rev-parse --show-toplevel", timeout_s=30
    )
    remote_top = remote_top.strip()
    if code != 0 or not remote_top:
        raise MoveError("The server's copy of this folder is gone.")
    transfer = uuid.uuid4().hex[:12]
    ref = f"refs/jarvis/return/{transfer}"
    remote_bundle = f"{home}/{SYNC_DIR}/{transfer}.bundle"
    script = _PACK.format(
        dest=shlex.quote(remote_top),
        ref=shlex.quote(ref),
        sync=shlex.quote(f"{home}/{SYNC_DIR}"),
        bundle=shlex.quote(remote_bundle),
        exclude=shlex.quote(f"^{offload_snapshot}"),
    )
    code, out, err = await pool.run(script, timeout_s=600)
    lines = [line for line in out.split() if re.fullmatch(r"[0-9a-f]{40}", line)]
    if code != 0 or not lines:
        raise MoveError(f"The server's work could not be packed: {(err or out).strip()[-200:]}")
    remote_snap = lines[-1]
    with tempfile.TemporaryDirectory(prefix="jarvis-return-") as tmp:
        bundle = Path(tmp) / "return.bundle"
        await _download(pool, remote_bundle, bundle)
        await pool.run(f"rm -f {shlex.quote(remote_bundle)}", timeout_s=30)
        await asyncio.to_thread(_git, top, "fetch", "-q", str(bundle), f"{ref}:{ref}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    branch = f"jarvis/{_slug(computer_name)}/{stamp}"
    await asyncio.to_thread(_git, top, "branch", branch, remote_snap)
    offload_tree = await asyncio.to_thread(_git, top, "rev-parse", f"{offload_snapshot}^{{tree}}")
    current_tree = await asyncio.to_thread(working_tree_id, top)
    if current_tree != offload_tree:
        return Return(
            branch=branch,
            applied=False,
            message=(
                f"This folder changed while the work was away, so nothing was overwritten. "
                f"The server's work is on the branch {branch}."
            ),
        )
    patch = await asyncio.to_thread(_git, top, "diff", "--binary", offload_snapshot, remote_snap)
    if patch:
        try:
            await asyncio.to_thread(
                _git, top, "apply", "--whitespace=nowarn", stdin=(patch + "\n").encode("utf-8")
            )
        except MoveError as exc:
            # The failure is returned to the caller, which shows it to the user.
            return Return(branch=branch, applied=False, message=f"{exc} The work is on {branch}.")
    return Return(
        branch=branch,
        applied=True,
        message="The server's changes are back in this folder (also kept on " + branch + ").",
    )


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------


def claude_project_dir(cwd: str) -> str:
    """Claude Code's folder name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _claude_home(override: Path | None) -> Path:
    if override is not None:
        return override
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def _codex_home(override: Path | None) -> Path:
    if override is not None:
        return override
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def find_local_transcript(
    agent: str, conversation_id: str, home: Path | None
) -> tuple[Path, str] | None:
    """The local transcript file and its path relative to the CLI home."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", conversation_id or ""):
        return None
    if agent == "claude":
        root = _claude_home(home)
        match = next((root / "projects").glob(f"*/{conversation_id}.jsonl"), None)
        return (match, "") if match else None
    if agent == "codex":
        root = _codex_home(home)
        match = next((root / "sessions").glob(f"*/*/*/rollout-*{conversation_id}*.jsonl"), None)
        if match:
            return match, match.relative_to(root).as_posix()
    return None


async def push_conversation(
    pool: SshPtyPool, agent: str, conversation_id: str | None, remote_cwd: str, home: Path | None
) -> bool:
    """Copy a conversation to the server so ``--resume`` finds it there."""
    if not conversation_id:
        return False
    found = await asyncio.to_thread(find_local_transcript, agent, conversation_id, home)
    if found is None:
        log.info("agentic-ide: no local %s transcript for %s to move", agent, conversation_id)
        return False
    path, relative = found
    remote_home = await pool.home()
    if agent == "claude":
        project = claude_project_dir(remote_cwd)
        target = f"{remote_home}/.claude/projects/{project}/{conversation_id}.jsonl"
    else:
        target = f"{remote_home}/.codex/{relative}"
    await _upload(pool, path, target)
    return True


async def pull_conversation(
    pool: SshPtyPool,
    agent: str,
    conversation_id: str | None,
    local_cwd: Path,
    home: Path | None,
) -> bool:
    """Copy the server's transcript back so the local ``--resume`` continues it."""
    if not conversation_id or not re.fullmatch(r"[A-Za-z0-9._-]+", conversation_id):
        return False
    remote_home = await pool.home()
    if agent == "claude":
        code, out, _ = await pool.run(
            f"ls {shlex.quote(remote_home)}/.claude/projects/*/{conversation_id}.jsonl "
            "2>/dev/null | head -n 1"
        )
        remote = out.strip()
        if code != 0 or not remote:
            return False
        target = (
            _claude_home(home)
            / "projects"
            / claude_project_dir(str(local_cwd))
            / f"{conversation_id}.jsonl"
        )
    elif agent == "codex":
        code, out, _ = await pool.run(
            f"cd {shlex.quote(remote_home)}/.codex 2>/dev/null && "
            f"ls sessions/*/*/*/rollout-*{conversation_id}*.jsonl 2>/dev/null | head -n 1"
        )
        relative = out.strip()
        if code != 0 or not relative:
            return False
        remote = f"{remote_home}/.codex/{relative}"
        target = _codex_home(home) / relative
    else:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    await _download(pool, remote, target)
    return True
