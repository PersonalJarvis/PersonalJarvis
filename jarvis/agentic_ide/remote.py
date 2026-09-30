"""Moving IDE work between this computer and a connected one.

Three things travel when a pane moves to a VPS ("offload") or comes back:

1. **The code.** A git folder travels as a *snapshot commit* — the working
   tree exactly as it is, uncommitted edits and new files included — built
   with a throw-away index so the user's own index, branch and stash are never
   touched, and shipped as a git bundle over SFTP. On the server it lands
   checked out on the same branch with the edits back as uncommitted changes.
   Only objects the server does not have yet are sent after the first time.
   A folder without git travels as a tarball (heavy build folders skipped).
   Either way, a file that looks like a secret (``.env``, private keys) or
   is over :data:`MAX_FILE_BYTES` stays here unless it is already committed
   — the server may be rented or shared, and a key the user forgot to
   ignore must not land there by accident.
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
import fnmatch
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
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from jarvis.computers.remote_terminal import SshPtyPool
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

REMOTE_ROOT = "jarvis-workspaces"
SYNC_DIR = ".jarvis-sync"
MAX_TARBALL_BYTES = 300 * 1024 * 1024
#: A new (uncommitted) file bigger than this stays on this machine.
MAX_FILE_BYTES = 50 * 1024 * 1024
_SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".cache", ".mypy_cache", ".next"}
#: File names that hold credentials. Matched on the base name, case-insensitive.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.tfvars",
    "*credentials*.json",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    ".netrc",
    ".pypirc",
    ".npmrc",
    "login data",
    "cookies",
)
#: Templates that document a secret's shape without holding one.
SECRET_TEMPLATES = (".env.example", ".env.sample", ".env.template", ".env.dist")
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
    #: New files that were NOT sent (secrets, oversized), relative paths.
    left_behind: tuple[str, ...] = field(default=())


def is_secret_name(path: str) -> bool:
    """True for a file whose name says it holds credentials."""
    name = PurePosixPath(path.replace("\\", "/")).name.casefold()
    if name in SECRET_TEMPLATES:
        return False
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_PATTERNS)


def left_behind_note(paths: tuple[str, ...] | list[str]) -> str:
    """One sentence naming the files that stayed on this machine."""
    if not paths:
        return ""
    shown = ", ".join(sorted(paths)[:5])
    more = f" and {len(paths) - 5} more" if len(paths) > 5 else ""
    return f"Not copied (secrets or over 50 MB): {shown}{more}."


# ---------------------------------------------------------------------------
# local git, never touching the user's index
# ---------------------------------------------------------------------------


def _git_bytes(
    cwd: Path, *args: str, env: dict[str, str] | None = None, stdin: bytes | None = None
) -> bytes:
    """Run git and return its stdout exactly as written (a patch, NUL lists)."""
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
    return result.stdout


def _git(
    cwd: Path, *args: str, env: dict[str, str] | None = None, stdin: bytes | None = None
) -> str:
    return _git_bytes(cwd, *args, env=env, stdin=stdin).decode("utf-8", "replace").strip()


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


def _stage_working_tree(top: Path, head: str | None, index: Path) -> tuple[str, list[str]]:
    """Stage the working tree into the private ``index``; return (tree, left behind).

    Seeded from a copy of the user's own index, so ``git add -A`` only has to
    look at what changed (a fresh index re-hashes every file — seconds on a
    large repo). New files that look like secrets or are too large are taken
    back out again; a file already committed keeps its committed version.
    """
    env = {"GIT_INDEX_FILE": str(index)}
    own_index = Path(_git(top, "rev-parse", "--git-path", "index"))
    if not own_index.is_absolute():
        own_index = top / own_index
    if own_index.is_file():
        index.write_bytes(own_index.read_bytes())
        # A file the user marked "assume unchanged" would keep its old content
        # in the copy: ``add -A`` does not look at it. Clear that mark in the
        # private copy only. (Skip-worktree marks stay: a sparse checkout's
        # absent files are not deletions.)
        tagged = _git_bytes(top, "ls-files", "-v", "-z", env=env)
        assumed = [
            entry[2:] for entry in tagged.split(b"\0") if entry[:1].islower() and entry[1:2] == b" "
        ]
        if assumed:
            _git_bytes(
                top,
                "update-index",
                "--no-assume-unchanged",
                "-z",
                "--stdin",
                env=env,
                stdin=b"\0".join(assumed) + b"\0",
            )
    elif head:
        _git(top, "read-tree", head, env=env)
    _git(top, "add", "-A", env=env)
    if head:
        listed = _git_bytes(
            # --no-renames: with diff.renames=copies a new .env copied from a
            # committed template would show as a copy and slip past the filter.
            top,
            "diff",
            "--cached",
            "--no-renames",
            "--name-only",
            "-z",
            "--diff-filter=A",
            head,
            env=env,
        )
    else:
        listed = _git_bytes(top, "ls-files", "-z", env=env)
    added = [path for path in listed.decode("utf-8", "surrogateescape").split("\0") if path]
    held: list[str] = []
    for path in added:
        if is_secret_name(path):
            held.append(path)
            continue
        try:
            if (top / path).stat().st_size > MAX_FILE_BYTES:
                held.append(path)
        except OSError:  # vanished since git listed it: nothing to send or hold
            continue
    if held:
        _git_bytes(
            top,
            "update-index",
            "--force-remove",
            "-z",
            "--stdin",
            env=env,
            stdin="\0".join(held).encode("utf-8", "surrogateescape") + b"\0",
        )
    return _git(top, "write-tree", env=env), held


def snapshot_commit(top: Path, message: str) -> tuple[str, str, list[str]]:
    """Commit the working tree as it is (tracked + untracked, .gitignore honoured).

    Returns ``(commit, tree, left_behind)``. Uses a private index file, so the
    user's index, branch and stash are untouched; the commit is reachable only
    through the ref the caller gives it.
    """
    head = _head(top)
    with tempfile.TemporaryDirectory(prefix="jarvis-snap-") as tmp:
        tree, held = _stage_working_tree(top, head, Path(tmp) / "index")
    parents = ["-p", head] if head else []
    commit = _git(top, "commit-tree", tree, *parents, "-m", message, env=_IDENTITY_ENV)
    return commit, tree, held


def working_tree_id(top: Path) -> str:
    """The tree id the working tree would commit to right now (same filter)."""
    head = _head(top)
    with tempfile.TemporaryDirectory(prefix="jarvis-tree-") as tmp:
        tree, _held = _stage_working_tree(top, head, Path(tmp) / "index")
    return tree


def _relative_posix(folder: Path, top: Path) -> str:
    relative = os.path.relpath(os.path.realpath(folder), os.path.realpath(top))
    return PurePosixPath(*Path(relative).parts).as_posix()


def joined_folder(base_remote: str, base_local: Path, local: Path, top: Path) -> str:
    """``local``'s folder inside a copy of repo ``top`` that was made for ``base_local``.

    The copy holds the whole repo; ``base_remote`` is where ``base_local`` sits
    in it. Walking up by ``base_local``'s depth gives the copy's root, and
    ``local``'s own path below the repo top leads back down.
    """
    root = PurePosixPath(base_remote)
    base_rel = _relative_posix(base_local, top)
    if base_rel not in ("", "."):
        for _part in PurePosixPath(base_rel).parts:
            root = root.parent
    relative = _relative_posix(local, top)
    return str(root / relative) if relative not in ("", ".") else str(root)


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


async def preflight(pool: SshPtyPool, commands: Mapping[str, str], where: str) -> None:
    """Refuse early, in one sentence, when the server lacks a command panes need.

    ``commands`` maps what the user reads ("Claude Code") to the command that
    must be on the server's PATH ("claude"). One round trip for all of them,
    before any folder is copied — a missing CLI used to show up only as a dead
    pane after a long upload.
    """
    if not commands:
        return
    host = await pool.host()
    if host.windows:
        if not host.bash:
            raise MoveError(
                f"{where} runs Windows and needs Git for Windows first. "
                "Open Computers, then Prepare."
            )
        # Panes there run without tmux (``remote_terminal``); the rest is checked
        # the same way, in Git Bash.
        commands = {label: cmd for label, cmd in commands.items() if cmd != "tmux"}
        if not commands:
            return
    checks = " ".join(
        f"command -v {shlex.quote(command)} >/dev/null 2>&1 || printf '%s\\n' "
        f"{shlex.quote('missing:' + label)};"
        for label, command in commands.items()
    )
    _code, out, _err = await pool.run(checks, timeout_s=30)
    missing = [
        line.strip()[len("missing:") :]
        for line in out.splitlines()
        if line.strip().startswith("missing:")
    ]
    if missing:
        names = " and ".join(missing) if len(missing) < 3 else ", ".join(missing)
        verb = "is" if len(missing) == 1 else "are"
        raise MoveError(f"{names} {verb} not installed on {where}. Open Computers, then Prepare.")


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
    remote = await pool.sftp_path(remote)
    session = await pool.connection()
    async with session.conn.start_sftp_client() as sftp:
        await sftp.makedirs(str(PurePosixPath(remote).parent), exist_ok=True)
        await sftp.put(str(local), remote)


async def _download(pool: SshPtyPool, remote: str, local: Path) -> None:
    remote = await pool.sftp_path(remote)
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
        held = await _push_tarball(pool, local_folder, remote)
        return Placement(remote_folder=remote, offload_snapshot=None, left_behind=tuple(held))

    remote_top = remote_folder_for(home, top)
    relative = await asyncio.to_thread(_relative_posix, local_folder, top)
    remote_folder = (
        str(PurePosixPath(remote_top) / relative) if relative not in ("", ".") else remote_top
    )
    transfer = uuid.uuid4().hex[:12]
    ref = f"refs/jarvis/offload/{transfer}"
    snap, _tree, held = await asyncio.to_thread(snapshot_commit, top, "jarvis: offload snapshot")
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
    return Placement(remote_folder=remote_folder, offload_snapshot=snap, left_behind=tuple(held))


def _tar_plan(folder: Path) -> tuple[int, list[str]]:
    """Walk ``folder`` as the tarball will: (bytes to send, files held back)."""
    total = 0
    held: list[str] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            path = Path(root) / name
            relative = path.relative_to(folder).as_posix()
            try:
                size = path.stat().st_size
            except OSError:  # vanished or unreadable mid-walk: skip that one file
                continue
            if is_secret_name(name) or size > MAX_FILE_BYTES:
                held.append(relative)
                continue
            total += size
    return total, held


def _tar_filter(held: set[str]) -> object:
    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = PurePosixPath(info.name).parts
        if any(part in _SKIP_DIRS for part in parts):
            return None
        relative = PurePosixPath(*parts).as_posix() if parts else ""
        if info.isfile() and relative in held:
            return None
        return info

    return keep


async def _push_tarball(pool: SshPtyPool, folder: Path, remote: str) -> list[str]:
    """Copy a folder without git; returns the files that stayed here."""
    size, held = await asyncio.to_thread(_tar_plan, folder)
    if size > MAX_TARBALL_BYTES:
        # Measured before anything is packed: building a 5 GB archive only to
        # refuse it cost minutes and the disk space for the archive.
        raise MoveError("This folder is too large to copy without git (over 300 MB).")
    with tempfile.TemporaryDirectory(prefix="jarvis-tar-") as tmp:
        archive = Path(tmp) / "folder.tar.gz"
        keep = _tar_filter(set(held))

        def build() -> None:
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(str(folder), arcname=".", filter=keep)  # type: ignore[arg-type]

        await asyncio.to_thread(build)
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
    return held


# ---------------------------------------------------------------------------
# code: down
# ---------------------------------------------------------------------------


_PACK = r"""
set -e
cd {dest}
export GIT_INDEX_FILE="$(mktemp)"; rm -f "$GIT_INDEX_FILE"  # a name only: git makes the file
# A folder sent before its first commit has no HEAD here either.
if git rev-parse -q --verify HEAD >/dev/null
then git read-tree HEAD; set -- -p HEAD
else set --
fi
git add -A; t=$(git write-tree)
c=$(git -c user.name=Jarvis -c user.email=jarvis@localhost commit-tree "$t" "$@" \
    -m "jarvis: work from the server")
rm -f "$GIT_INDEX_FILE"; unset GIT_INDEX_FILE
git update-ref {ref} "$c"
mkdir -p {sync}
git bundle create {bundle} {ref} {exclude} >/dev/null 2>&1 \
  || git bundle create {bundle} {ref} >/dev/null
echo "$c"
"""


def _free_branch(top: Path, prefix: str) -> str:
    """``<prefix>/<time>``, with a counter when that name is taken already."""
    base = f"{prefix}/{time.strftime('%Y%m%d-%H%M%S')}"
    candidate, counter = base, 1
    while True:
        try:
            _git(top, "rev-parse", "-q", "--verify", f"refs/heads/{candidate}")
        except MoveError:  # no such branch: the answer this probe looks for
            return candidate
        counter += 1
        candidate = f"{base}-{counter}"


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
    branch = await asyncio.to_thread(_free_branch, top, f"jarvis/{_slug(computer_name)}")
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
    # Bytes as git wrote them: decoding and stripping cut the last context
    # line of a hunk that ends on a blank line, and mangled non-UTF-8 text.
    patch = await asyncio.to_thread(
        _git_bytes, top, "diff", "--binary", offload_snapshot, remote_snap
    )
    if patch.strip():
        try:
            await asyncio.to_thread(_git, top, "apply", "--whitespace=nowarn", stdin=patch)
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
