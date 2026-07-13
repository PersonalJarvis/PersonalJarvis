"""Per-task git worktree manager.

ADR-0009 §3 + Research-Doc §E (lines 213-220). Every Phase-6 worker subprocess
gets a fresh working tree as its `cwd` so that parallel edits do not cause
race conditions in the user's working tree.

Path layout (Research-Doc §E point 1):

    <repo_parent>/jarvis-agent-outputs/
      <YYYYMMDDTHHMMSS>__<mission-slug>__<short-uuid>/
        tasks/
          <NN>__<task-slug>/
            workspace/    <- the worker runs here with cwd

`workspace/` is the path created via `git worktree add -b agent/<task-slug>`.
The branch name follows `agent/<NN>-<task-slug>` (no slash in the suffix due
to Windows path lengths).

Constraint: path length ≤ 200 chars (`MAX_PATH=260` minus a safety margin
for files inside the worktree). `create()` raises `ValueError` on violation —
we do not let the worker fall into a path-length trap.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

logger = logging.getLogger(__name__)

# Hard cap for the worktree root path. Files inside (e.g.
# `workspace/jarvis/very/deep/path/file.py`) must fit underneath it ->
# 200 chars root + ~60 chars inner path = ~260 = MAX_PATH.
_MAX_WORKTREE_PATH_LEN = 200

# Slug sanitizer: allows [a-z0-9-], everything else -> '-', leading/trailing trim.
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Sidecar file (in the TASK dir, one level ABOVE ``workspace/`` so it never
# pollutes the worktree diff) that records the worktree's BASE commit SHA — the
# HEAD it was forked from at creation time (the ``main`` checkout for a full
# worktree, the ``lean-workspace-init`` empty commit for a lean one). The
# Kontrollierer diffs against this base instead of the live HEAD so a worker
# that ``git commit``s its deliverable is still captured: after a commit,
# ``git diff --cached HEAD`` is empty and ``git ls-files --others`` no longer
# lists the file (it is now tracked), so the committed deliverable would
# otherwise be invisible to archiving and silently lost when the worktree is
# pruned. Live forensic 2026-07-03, mission 019f26d0-bb07: a worker built a
# complete ``schokolade-99.html``, then (as coding agents habitually do)
# ``git add`` + ``git commit``-ed it; the file fell out of the untracked-only
# capture and the user received only a materialised ``.md`` summary — the HTML
# was gone. Diffing against the base makes committed files reappear.
BASE_SHA_SIDECAR: str = ".mission_base_sha"


def read_worktree_base_sha(workspace: Path) -> str | None:
    """Return the recorded base commit SHA for a worktree, or ``None``.

    Reads the :data:`BASE_SHA_SIDECAR` file written by
    :meth:`WorktreeManager._write_base_sha` at creation time. ``None`` when the
    sidecar is absent (a pre-fix / externally-created worktree) or unreadable —
    callers then fall back to their pre-existing ``HEAD``-relative behaviour, so
    this is purely additive and never breaks an older mission.
    """
    try:
        raw = (workspace.parent / BASE_SHA_SIDECAR).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    # A commit SHA is 40 hex chars (or the short form). Guard against a truncated
    # / corrupt sidecar so we never hand git a bogus revision.
    return raw if re.fullmatch(r"[0-9a-fA-F]{7,40}", raw) else None

# Worktree RUN-dir name shape: ``<YYYYMMDDTHHMMSS>__<slug>__<8-hex>`` (see
# ``create`` below). ONLY these transient scaffolding dirs are eligible for the
# leaked-worktree sweep. The persistent ``mission_<id>`` archive dirs that
# ``Kontrollierer._archive_task_artifacts`` writes live in the SAME outputs
# root but hold the user's deliverables (diff.patch + artifacts/files/) and are
# never git worktrees — sweeping them by age silently wipes every completed
# Jarvis-Agent's output (live 2026-05-29: 0/77 mission dirs still had files after a
# >6 h-old restart triggered the 6 h sweep). The 14-day retention of those
# archive dirs is owned by ``cleanup.startup_sweep`` instead.
_RUN_DIR_RE = re.compile(r"^\d{8}T\d{6}__.+__[0-9a-f]{8}$")

# Regenerable dependency / cache trees a worker may create (npm/pip/yarn/etc.).
# These are NEVER a deliverable and, on Windows, a fresh `npm install` can drop
# tens of thousands of files here. Walking them blows the 10 s cap on the
# per-iteration ``git add -A`` in ``Kontrollierer._capture_diff`` -> empty diff
# -> "no usable output" -> the worker's REAL build is discarded and rmtree'd
# (live mission 019ee416, 2026-06-20: a complete Remotion promo video lost this
# way). Excluding them via the lean repo's ``.git/info/exclude`` makes git skip
# the whole subtree (it never descends an ignored top-level dir) AND keeps the
# patterns out of the captured diff. Build-OUTPUT dirs (dist/, build/, out/)
# are DELIBERATELY not listed — a rendered video or a built site IS a
# legitimate deliverable and must still reach the Critic.
_DEPENDENCY_EXCLUDE_DIRS: tuple[str, ...] = (
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pnpm-store/",
    ".yarn/cache/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
)


def _slugify(value: str) -> str:
    """Lowercase, non-alphanum -> '-', trimmed."""
    out = _SLUG_RE.sub("-", value.lower()).strip("-")
    return out or "x"


def resolve_outputs_root(repo_root: Path) -> Path:
    """Return the mission outputs root directory.

    ``JARVIS_ISOLATION_ROOT`` is the explicit highest-priority override.
    ``JARVIS_DATA_DIR`` places outputs under the writable application data
    volume used by headless and non-root installs.

    Prefers ``<repo_root>.parent/jarvis-agent-outputs/`` (post-rename, 2026-06-29).
    Falls back to ``<repo_root>.parent/sub-agents-outputs/`` when that old directory
    exists AND the new one does NOT — this keeps existing missions readable without
    any migration step. Does NOT create the directory; callers are responsible.
    """
    explicit_root = os.environ.get("JARVIS_ISOLATION_ROOT", "").strip()
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()

    data_root = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if data_root:
        return Path(data_root).expanduser().resolve() / "jarvis-agent-outputs"

    parent = repo_root.resolve().parent
    new_dir = parent / "jarvis-agent-outputs"
    old_dir = parent / "sub-agents-outputs"
    if not new_dir.exists() and old_dir.exists():
        return old_dir  # back-compat: keep existing missions accessible
    return new_dir


class WorktreeManager:
    """Manages `git worktree add/remove` for Phase-6 worker tasks.

    Not thread-safe (runs in the orchestrator event loop). Subprocess calls
    are synchronous because git is local and returns in <100 ms — no asyncio
    needed for a once-per-task call.
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        outputs_root: Path | None = None,
    ) -> None:
        """
        Inputs:
            repo_root: path to the main repo (the one that contains `.git`).
            outputs_root: optional override for the mission outputs root.
                Default: ``resolve_outputs_root(repo_root)``.
        """
        self._repo_root = repo_root.resolve()
        self._outputs_root = (
            outputs_root.resolve()
            if outputs_root is not None
            else resolve_outputs_root(self._repo_root)
        )

    # --- Public API ---------------------------------------------------------

    def create(
        self,
        *,
        mission_slug: str,
        task_id: str,
        base_branch: str = "main",
        needs_repo: bool = True,
    ) -> Path:
        """Create a new workspace and return the `workspace/` path.

        Inputs:
            mission_slug: short mission identifier (will be slugified).
            task_id: task identifier (will be slugified, typically `01__refactor-router`).
            base_branch: branch from which the worktree branch is forked.
            needs_repo: when True (default) the workspace is a full `git
                worktree add` checkout of the repo at ``base_branch`` — the
                isolated, registered worktree every repo task has always
                received. When False the workspace is a LEAN repo: a fresh
                ``git init`` directory with one initial empty commit and NO
                files from the host repo. The lean path is for external-artefact
                tasks ("create an HTML file with today's news") that would
                otherwise burn minutes + millions of tokens exploring the
                codebase before a trivial write (live mission 019eb17d).

        Output:
            Path to `<run_dir>/tasks/<NN>__<task-slug>/workspace/`.

        Raises:
            ValueError: when the path exceeds 200 chars (path-length cap).
            subprocess.CalledProcessError: when `git worktree add` (full mode)
                or `git init`/the initial commit (lean mode) fails.

        Lean-vs-full diff parity (CRITICAL): both modes produce a real git repo
        with a resolvable HEAD, so ``Kontrollierer._capture_diff`` — which runs
        ``git add -A .`` then ``git diff HEAD`` against the workspace —
        surfaces the worker's written files identically in either mode. The
        lean repo's initial commit is the empty tree, so a freshly-written file
        shows up as a plain add, exactly like a new file in a full worktree.
        """
        mission_part = _slugify(mission_slug)
        task_part = _slugify(task_id)
        ts = time.strftime("%Y%m%dT%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]

        run_dir_name = f"{ts}__{mission_part}__{short_uuid}"
        task_dir_name = (
            task_part
            if task_part.startswith(tuple("0123456789"))
            else f"01__{task_part}"
        )

        workspace = (
            self._outputs_root / run_dir_name / "tasks" / task_dir_name / "workspace"
        )

        if len(str(workspace)) > _MAX_WORKTREE_PATH_LEN:
            raise ValueError(
                f"Worktree path is too long ({len(str(workspace))} > "
                f"{_MAX_WORKTREE_PATH_LEN}): {workspace}"
            )

        workspace.parent.mkdir(parents=True, exist_ok=True)

        if not needs_repo:
            return self._create_lean(workspace, branch_hint=task_part)

        branch_name = f"agent/{task_part}-{short_uuid}"

        cmd = [
            "git",
            "worktree",
            "add",
            "-b",
            branch_name,
            str(workspace),
            base_branch,
        ]
        logger.info("WorktreeManager.create: %s", " ".join(cmd))
        self._run_git(cmd)

        # Record the base commit (the forked-from HEAD) so the Kontrollierer can
        # diff against it and capture files the worker later ``git commit``s.
        self._write_base_sha(workspace)
        return workspace

    # Identity used for the lean repo's initial commit. A fresh ``git init``
    # repo inherits no ``user.name``/``user.email`` from the host repo's local
    # config, and the host machine may have none set globally — so we always
    # pass an explicit identity via ``-c`` to make the initial commit
    # deterministic and never prompt. This commit is throwaway scaffolding.
    _LEAN_COMMIT_NAME = "Personal Jarvis"
    _LEAN_COMMIT_EMAIL = "noreply@personal-jarvis.local"

    def _create_lean(self, workspace: Path, *, branch_hint: str) -> Path:
        """Create a lean (empty) git workspace with one initial empty commit.

        The directory is a standalone git repo — NOT a registered worktree of
        the host repo — so cleanup must route around ``git worktree remove``
        (see :meth:`remove`). Initialising on a fixed ``main`` branch keeps the
        HEAD name predictable; the empty initial commit gives ``HEAD`` a base so
        the diff-capture sequence (``git diff --cached HEAD``) works.
        """
        workspace.mkdir(parents=True, exist_ok=True)
        logger.info("WorktreeManager.create (lean): git init at %s", workspace)
        self._run_git_in(["git", "init", "-b", "main", str(workspace)], cwd=workspace)
        # One empty initial commit so HEAD exists. ``--allow-empty`` because the
        # tree is empty; the explicit ``-c user.*`` identity avoids depending on
        # any global git config on the host.
        self._run_git_in(
            [
                "git",
                "-c",
                f"user.name={self._LEAN_COMMIT_NAME}",
                "-c",
                f"user.email={self._LEAN_COMMIT_EMAIL}",
                "commit",
                "--allow-empty",
                "-m",
                "lean-workspace-init",
            ],
            cwd=workspace,
        )
        # Keep the per-iteration `git add -A` cheap when the worker installs
        # dependencies (npm/pip) into this lean workspace — see
        # `_DEPENDENCY_EXCLUDE_DIRS`. Lean only: a full worktree shares the host
        # repo's `.git/info/exclude` (which already ignores node_modules/) and
        # its `.git` is a FILE, so there is nothing local to write here.
        self._write_dependency_excludes(workspace)
        # Record the base commit (the lean repo's initial empty commit) so the
        # Kontrollierer can diff against it and capture files a worker commits.
        self._write_base_sha(workspace)
        return workspace

    def _write_base_sha(self, workspace: Path) -> None:
        """Persist the worktree's base commit SHA next to (not inside) the tree.

        Runs ``git rev-parse HEAD`` in the freshly-created ``workspace`` and
        writes the result to ``workspace.parent / BASE_SHA_SIDECAR`` — the task
        dir, which is OUTSIDE the worktree so the sidecar never appears in a
        ``git add -A`` / ``git diff`` capture. Best-effort: a failure only
        regresses to the pre-fix ``HEAD``-relative capture and must never block
        workspace creation.
        """
        try:
            r = self._run_git_in(["git", "rev-parse", "HEAD"], cwd=workspace)
            sha = (r.stdout or "").strip()
            if sha:
                (workspace.parent / BASE_SHA_SIDECAR).write_text(
                    sha, encoding="utf-8"
                )
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning(
                "could not record base SHA for %s: %s — committed deliverables "
                "may not be captured for this task", workspace, exc,
            )

    def _write_dependency_excludes(self, workspace: Path) -> None:
        """Append `_DEPENDENCY_EXCLUDE_DIRS` to the lean repo's
        ``.git/info/exclude``.

        Local to this throwaway lean repo (never touches the host repo). The
        patterns make git skip whole dependency subtrees during ``git add -A``,
        the live cause of the 10 s diff-capture timeout that discarded a
        finished Remotion build (mission 019ee416).

        Best-effort: a write failure (e.g. a full-mode workspace whose ``.git``
        is a file) is logged and swallowed — the mission still runs, it just
        pays the slower add. Never aborts workspace creation.
        """
        exclude_file = workspace / ".git" / "info" / "exclude"
        try:
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            with exclude_file.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n# Personal Jarvis: regenerable dependency/cache trees "
                    "(keeps `git add -A` cheap; never a deliverable)\n"
                )
                fh.write("\n".join(_DEPENDENCY_EXCLUDE_DIRS) + "\n")
        except OSError as exc:
            logger.warning(
                "could not write dependency excludes to %s: %s",
                exclude_file, exc,
            )

    # BUG-LIVE-05 (2026-05-14) — Retry delays for the Windows file-
    # handle race in `git worktree remove`. The OpenClaw subprocess
    # may still hold one of its trajectory / SQLite handles for a
    # few hundred milliseconds after the parent yields control back
    # to the orchestrator. A short, exponentially-spaced retry loop
    # turns the noisy 80% case into a clean removal without paying
    # the full rmtree fallback. If all three retries fail we still
    # fall through to rmtree so cleanup never blocks the mission.
    _REMOVE_RETRY_DELAYS_S: tuple[float, ...] = (0.05, 0.1, 0.2)

    def remove(self, path: Path, *, force: bool = False) -> None:
        """Remove a worktree via `git worktree remove`.

        Inputs:
            path: the `workspace/` path or its parent — passed to git as-is
                (git is liberal about this).
            force: sets `--force` (uncommitted changes are discarded).

        Raises:
            subprocess.CalledProcessError: when `git worktree remove` fails
                and `force=False` (in the `force` path, remove() falls back
                to a manual `rmtree`).

        BUG-LIVE-05: on Windows, `git worktree remove` frequently fails
        with `error: failed to delete '...': Permission denied` right
        after a worker subprocess exits — the OpenClaw process is gone
        but a few SQLite WAL / trajectory file handles are still being
        flushed by the OS. A short retry loop (50/100/200 ms) catches
        this transient state without paying the cost of the full
        `shutil.rmtree` fallback. The fallback path is preserved as a
        last resort so cleanup never blocks the mission loop.

        Lean-workspace mode: a lean workspace (created with
        ``create(..., needs_repo=False)``) is a STANDALONE ``git init`` repo,
        not a registered worktree of the host repo. ``git worktree remove``
        would fail on it (``is not a working tree``) AND, worse, could leave a
        stale registration if a path ever collided. So when the workspace's
        ``.git`` is a real directory (lean) rather than the worktree link FILE
        (full mode), we skip git entirely and remove the directory tree
        directly. Plain directory removal leaves nothing for the host repo's
        ``git worktree prune`` to clean up, because the lean repo was never
        registered there in the first place.
        """
        if self._is_lean_workspace(path):
            self._remove_lean(path)
            return

        cmd = ["git", "worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(path))
        logger.info("WorktreeManager.remove: %s", " ".join(cmd))

        last_exc: subprocess.CalledProcessError | None = None
        attempts = (None,) + self._REMOVE_RETRY_DELAYS_S  # 1 initial + 3 retries
        for idx, delay in enumerate(attempts):
            if delay is not None:
                time.sleep(delay)
                logger.debug(
                    "WorktreeManager.remove retry %d (after %.0fms): %s",
                    idx, delay * 1000.0, path,
                )
            try:
                self._run_git(cmd)
                return  # success
            except subprocess.CalledProcessError as exc:
                last_exc = exc
                continue

        # All retries failed. Decide based on `force`.
        if force:
            logger.warning(
                "git worktree remove failed after %d attempts "
                "(last exit=%s) — manual rmtree for %s",
                len(attempts),
                last_exc.returncode if last_exc else "?",
                path,
            )
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            return
        assert last_exc is not None
        raise last_exc

    def prune_orphans(self) -> None:
        """Call `git worktree prune` to remove stale entries.

        Stale entries arise when someone runs `rm -rf` on a worktree directory
        without calling `git worktree remove`. Does nothing when none are present.
        """
        cmd = ["git", "worktree", "prune"]
        logger.info("WorktreeManager.prune_orphans: %s", " ".join(cmd))
        self._run_git(cmd)

    # H6 (2026-05-17 audit): boot-time defense against worktree leaks.
    # On Windows, `git worktree remove` regularly fails when a worker
    # subprocess holds file handles open past parent process teardown.
    # BUG-LIVE-05's retry loop catches the 80 % case but force-quits,
    # process crashes, and parallel-session locks leave behind directories
    # under the mission outputs root. Audit-team-4 counted 60 such leaks on
    # disk. Sweep at bootstrap is the simplest defense: forget the missing
    # entries from git's internal state, then aggressively rmtree
    # anything older than `max_age_hours` that no live worktree claims.
    def prune_and_sweep_leaked(
        self, *, max_age_hours: float = 6.0,
    ) -> dict[str, int]:
        """Prune git's worktree state, then remove stale leaked dirs.

        Two-step safety:
          1. ``git worktree prune`` removes git's internal entry for any
             worktree whose directory disappeared (e.g. user `rm -rf`d it).
          2. Scan the mission outputs root for run-dirs older than
             ``max_age_hours``. If git still considers any path under
             that run-dir an active worktree we leave it alone (Windows
             permission errors from racing workers will be retried by
             ``remove()`` on the next mission). Otherwise the whole
             run-dir is ``shutil.rmtree(ignore_errors=True)``-ed.

        Conservatively defaults to 6 hours so a parallel
        long-running mission in another session is never wiped out --
        production missions complete in well under that window. Returns
        a small report dict for telemetry / logging.

        Best-effort: any failure is logged at WARNING and counted; the
        helper never raises, since a half-cleaned tree is still better
        than a boot that crashes on housekeeping.
        """
        report = {"pruned": 0, "swept_run_dirs": 0, "errors": 0}
        # A worktree can only be CREATED via git, so on a host with no git on
        # PATH none can exist and there is nothing to sweep. Skip cleanly with
        # one honest log line instead of letting ``git worktree prune`` raise
        # FileNotFoundError and dump a traceback at every headless boot (observed
        # live on a bare python:3.11-slim container, 2026-07-08). Missions that
        # genuinely need worktree isolation still fail with an actionable message
        # at create-time; this is the best-effort boot-time housekeeping path.
        if shutil.which("git") is None:
            logger.info(
                "WorktreeManager.prune_and_sweep_leaked: git not on PATH — "
                "worktree sweep skipped (no git means no worktrees can exist here)."
            )
            report["skipped_no_git"] = 1
            return report
        try:
            self.prune_orphans()
            report["pruned"] = 1
        # OSError (incl. FileNotFoundError if git vanishes mid-run) as well as a
        # non-zero git exit must never escape this best-effort housekeeping path.
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning("prune_and_sweep_leaked: prune failed: %s", exc)
            report["errors"] += 1

        if not self._outputs_root.is_dir():
            return report

        # Build the set of paths git currently considers a worktree.
        # `git worktree list --porcelain` emits "worktree <path>" lines.
        active_paths: set[Path] = set()
        try:
            out = self._run_git(["git", "worktree", "list", "--porcelain"])
            for line in out.stdout.splitlines():
                if line.startswith("worktree "):
                    p = Path(line[len("worktree "):].strip()).resolve()
                    active_paths.add(p)
                    # Layout: outputs_root / <run-dir> / tasks / <task-id> /
                    # workspace.  So workspace.parent.parent.parent IS the
                    # run-dir, and one level up is outputs_root. Record the
                    # run-dir so we never wipe a run-dir that still has a
                    # live worktree underneath it.
                    if p.parent.parent.parent.parent == self._outputs_root:
                        active_paths.add(p.parent.parent.parent)
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning(
                "prune_and_sweep_leaked: worktree list failed: %s", exc,
            )
            report["errors"] += 1
            return report

        cutoff = time.time() - max_age_hours * 3600.0
        for child in self._outputs_root.iterdir():
            if not child.is_dir():
                continue
            # Only sweep transient worktree RUN-dirs. A persistent
            # ``mission_<id>`` deliverable-archive dir (or any other
            # non-run-dir) must never be rmtree'd here — that is the bug that
            # left every completed Jarvis-Agent's Outputs view empty (2026-05-29).
            if not _RUN_DIR_RE.match(child.name):
                continue
            try:
                age = child.stat().st_mtime
            except OSError:
                continue
            if age >= cutoff:
                continue  # young enough -- could still be in flight
            resolved = child.resolve()
            if resolved in active_paths:
                continue  # git still claims it -- skip
            # Cross-check: is any active-worktree path INSIDE this run-dir?
            if any(
                str(ap).startswith(str(resolved) + ("\\" if "\\" in str(ap) else "/"))
                for ap in active_paths
            ):
                continue
            logger.info(
                "prune_and_sweep_leaked: removing stale run-dir %s "
                "(age_hours=%.1f, threshold=%.1f)",
                child, (time.time() - age) / 3600.0, max_age_hours,
            )
            try:
                # `onerror` clears the read-only bit git sets on its object
                # store — with plain ignore_errors=True those files survived,
                # so the same dirs were re-swept (and re-failed) on EVERY
                # boot: 10s of blocked startup per launch (2026-06-10).
                shutil.rmtree(child, onerror=self._on_rmtree_error)
                # The onerror handler swallows residual failures, which may
                # leave a broken tree on Windows when handles are held; treat
                # partial removal as still-stale rather than success.
                if child.exists():
                    logger.warning(
                        "prune_and_sweep_leaked: partial removal of %s "
                        "(handles still held by another process)", child,
                    )
                    report["errors"] += 1
                else:
                    report["swept_run_dirs"] += 1
            except OSError as exc:
                logger.warning(
                    "prune_and_sweep_leaked: rmtree %s failed: %s",
                    child, exc,
                )
                report["errors"] += 1

        return report

    # --- Internals ----------------------------------------------------------

    @staticmethod
    def _is_lean_workspace(path: Path) -> bool:
        """True when ``path`` is a lean (standalone ``git init``) workspace.

        A full ``git worktree add`` checkout links back to the host repo via a
        ``.git`` FILE (``gitdir: …``). A lean ``git init`` repo has a real
        ``.git`` DIRECTORY. That structural difference is the cheapest reliable
        discriminator and needs no extra bookkeeping. If ``.git`` is absent
        entirely (e.g. the directory was already partly torn down) we treat it
        as lean so cleanup falls through to a plain ``rmtree`` rather than a
        doomed ``git worktree remove``.
        """
        git_path = path / ".git"
        if git_path.is_dir():
            return True
        if git_path.is_file():
            return False
        # No `.git` at all → not a registered worktree; safe to rmtree.
        return True

    @staticmethod
    def _on_rmtree_error(
        func: Callable[..., object], target: str, _exc_info: object
    ) -> None:
        """``shutil.rmtree`` onerror handler that clears the read-only bit.

        On Windows git marks objects under ``.git`` (pack files, loose objects)
        read-only, and ``shutil.rmtree`` raises ``PermissionError`` trying to
        unlink them. Clear the bit and retry the failing operation once. Any
        residual failure is swallowed — the lean teardown is best-effort and
        must never block the mission loop.
        """
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    @classmethod
    def _remove_lean(cls, path: Path) -> None:
        """Tear down a lean workspace with a plain ``rmtree`` (no git call).

        Mirrors the force-path's tolerance: a half-held Windows handle should
        never block the mission loop, so failures are swallowed and a warning is
        logged if anything survives. The ``onerror`` handler clears the
        read-only bit git sets on its object store so the empty lean repo
        actually deletes on Windows.
        """
        logger.info("WorktreeManager.remove (lean rmtree): %s", path)
        if path.exists():
            shutil.rmtree(path, onerror=cls._on_rmtree_error)
            if path.exists():
                logger.warning(
                    "lean workspace partial removal (handles held): %s", path
                )

    def _run_git_in(
        self, cmd: list[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        """Like :meth:`_run_git` but runs with ``cwd`` set to a specific path.

        Used for lean-workspace creation, where ``git init``/``git commit`` must
        operate INSIDE the new lean repo, not in the host ``repo_root``. Shares
        the same ``-c core.longpaths=true`` injection, UTF-8 handling, no-window
        creation flag, and stderr logging as :meth:`_run_git`.
        """
        if cmd and cmd[0] == "git":
            patched = ["git", "-c", "core.longpaths=true", *cmd[1:]]
        else:
            patched = list(cmd)
        try:
            return subprocess.run(  # noqa: S603 — no shell=True, args controlled
                patched,
                cwd=str(cwd),
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except subprocess.CalledProcessError as exc:
            logger.error(
                "git command failed (exit=%s): %s\nstderr: %s\nstdout: %s",
                exc.returncode,
                " ".join(patched),
                (exc.stderr or "").strip(),
                (exc.stdout or "").strip(),
            )
            raise

    def _run_git(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Synchronous git call, cwd=repo_root, no shell=True.

        Injects ``-c core.longpaths=true`` right after ``git`` so worktree
        operations on Windows survive paths longer than MAX_PATH (260).
        Without this, repos containing files with deep nested paths cause
        ``git worktree add`` to fail with ``Filename too long`` while only
        surfacing exit code 128 to the caller — which previously made every
        OpenClaw mission report ``MissionFailed`` with no diagnostic.

        On ``CalledProcessError`` the stderr/stdout from git are logged
        before re-raising so the underlying reason (path-length, lock,
        existing branch, …) is visible in ``data/jarvis_desktop.log``.

        Raises ``FileNotFoundError``/``OSError`` unmodified when the ``git``
        executable itself is missing from PATH — the caller (currently
        ``WorktreeManager.create()`` -> the orchestrator's task loop) is
        responsible for turning that into an honest, actionable failure
        (AP-23 wave-2 finding 1); this helper only shapes and logs the
        command, it never swallows an exception.
        """
        if cmd and cmd[0] == "git":
            patched = ["git", "-c", "core.longpaths=true", *cmd[1:]]
        else:
            patched = list(cmd)
        try:
            return subprocess.run(  # noqa: S603 — no shell=True, args controlled
                patched,
                cwd=self._repo_root,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except subprocess.CalledProcessError as exc:
            logger.error(
                "git command failed (exit=%s): %s\nstderr: %s\nstdout: %s",
                exc.returncode,
                " ".join(patched),
                (exc.stderr or "").strip(),
                (exc.stdout or "").strip(),
            )
            raise
