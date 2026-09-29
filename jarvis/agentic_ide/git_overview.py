"""Branches, pull requests and CI of a workspace's repository, in one answer.

The IDE's Git tab lists a project's branches and, for each one, three
separate facts that are easy to mix up:

* **Merged** — local git knows a branch is contained in a target branch
  (``git for-each-ref --merged``), and GitHub knows a pull request was merged
  (which also covers squash merges, where git ancestry says nothing).
* **Pull request** — the state of the branch's pull request on GitHub: draft,
  open, in the merge queue, merged, or closed without a merge.
* **CI** — the checks on the branch's newest pushed commit: pending, running,
  passed or failed. Green checks never mean "merged"; the two are reported
  apart on purpose.

Local git is read on every call (a handful of fast plumbing commands). GitHub
is read for the repository the person picked for this folder ONCE
(:mod:`jarvis.agentic_ide.github_link`), with their existing GitHub
connection, in ONE GraphQL query per repository, cached per repository: N open
panes polling one project cost one network call per minute — shorter while a
check is still running, because that is exactly when the user is waiting.

Degrades, never raises: no git, not a repository, GitHub not connected, no
repository picked yet — each comes back as ``available=False`` or
``github.available=False`` with a ``code`` the tab turns into the one next
step (connect GitHub, pick the repository) and one plain sentence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from jarvis.agentic_ide import github_link
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_GIT_TIMEOUT_S = 8.0
#: Most local branches listed; the newest by commit date win.
MAX_BRANCHES = 60
#: Most GitHub-only branches listed beside the local ones.
MAX_REMOTE_ONLY = 30
#: Most target branches a "merged into" check runs against (one git call each).
MAX_MERGE_TARGETS = 4
#: Pull requests kept per branch (the most relevant first).
MAX_PRS_PER_BRANCH = 3
#: Failed or running check names kept for a tooltip.
MAX_CHECK_NAMES = 6

#: How long one GitHub answer is reused while nothing is in flight ...
GITHUB_TTL_S = 60.0
#: ... and while a check runs or a pull request waits in the merge queue.
GITHUB_BUSY_TTL_S = 20.0
#: A forced refresh still reuses an answer this young.
GITHUB_MIN_REFRESH_S = 5.0

_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    url
    pullRequests(first: 40, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title url state isDraft isCrossRepository
        headRefName headRefOid baseRefName mergedAt updatedAt
        isInMergeQueue
        mergeQueueEntry { position }
        commits(last: 1) { nodes { commit { oid statusCheckRollup { ...Rollup } } } }
      }
    }
    refs(refPrefix: "refs/heads/", first: 60,
         orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) {
      nodes {
        name
        target { ... on Commit { oid committedDate statusCheckRollup { ...Rollup } } }
      }
    }
  }
}
fragment Rollup on StatusCheckRollup {
  state
  contexts(first: 40) {
    totalCount
    nodes {
      __typename
      ... on CheckRun {
        name status conclusion detailsUrl
        checkSuite { workflowRun { url } }
      }
      ... on StatusContext { context state targetUrl }
    }
  }
}
"""

# CheckRun.status values that have not started yet.
_QUEUED = {"QUEUED", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}
# CheckRun.conclusion values that count as a pass.
_PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}


# ------------------------------------------------------------------ model


@dataclass(slots=True)
class CiStatus:
    #: ``none`` | ``pending`` | ``running`` | ``success`` | ``failure``
    state: str = "none"
    #: Where a click goes: the failing / running workflow run, or the checks page.
    url: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    running: int = 0
    pending: int = 0
    #: The commit the checks ran on (12 chars).
    commit: str = ""
    #: Names of the failed checks (or the running ones while nothing failed).
    names: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PullRequest:
    number: int
    title: str
    url: str
    #: ``draft`` | ``open`` | ``queued`` | ``merged`` | ``closed``
    state: str
    base: str
    head: str
    head_oid: str = ""
    merged_at: str = ""
    updated_at: str = ""
    #: 1-based place in the merge queue while ``state == "queued"``.
    queue_position: int | None = None
    ci: CiStatus = field(default_factory=CiStatus)


@dataclass(slots=True)
class MergedInto:
    target: str
    #: ``git`` (ancestry) or ``pull_request`` (GitHub merged its PR).
    via: str
    number: int | None = None
    url: str = ""


@dataclass(slots=True)
class BranchRow:
    name: str
    current: bool = False
    #: Only on GitHub, no local branch of that name.
    remote_only: bool = False
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    head: str = ""
    committed_at: int = 0
    #: The checkout this branch is open in, when it is another worktree.
    worktree: str = ""
    #: The branch exists on GitHub (from the GitHub answer; False when unknown).
    on_github: bool = False
    merged_into: list[MergedInto] = field(default_factory=list)
    pull_requests: list[PullRequest] = field(default_factory=list)
    #: CI of the branch's newest pushed commit (its open PR's, when it has one).
    ci: CiStatus = field(default_factory=CiStatus)
    #: The local branch has commits that CI has not seen yet.
    ci_stale: bool = False


@dataclass(slots=True)
class GitHubState:
    available: bool = False
    reason: str = ""
    #: What the tab offers next: ``not_connected`` (connect GitHub),
    #: ``needs_repo`` (pick this folder's repository), another failure code, or "".
    code: str = ""
    #: The ``owner/name`` picked for this folder.
    repo: str = ""
    #: What the folder's git remote points at — the picker's recommendation.
    suggested_repo: str = ""
    #: ``app`` or ``gh``: which GitHub credential is in use.
    source: str = ""
    repo_url: str = ""
    #: Epoch seconds of the GitHub answer in use.
    fetched_at: float = 0.0


@dataclass(slots=True)
class RepoOverview:
    available: bool
    reason: str = ""
    root: str = ""
    branch: str = ""
    detached: bool = False
    head: str = ""
    default_branch: str = ""
    branches: list[BranchRow] = field(default_factory=list)
    remote_branches: list[BranchRow] = field(default_factory=list)
    truncated: bool = False
    github: GitHubState = field(default_factory=GitHubState)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ plumbing


def _run(args: list[str], cwd: Path, *, timeout: float = _GIT_TIMEOUT_S, program: str = "git"):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    command = ["git", "-c", "core.quotePath=false", *args] if program == "git" else [program, *args]
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except FileNotFoundError:  # git/gh not installed: callers report it as unavailable
        return None
    except subprocess.TimeoutExpired:
        logger.info("Git overview: {} did not answer in time in {}", program, cwd)
        return None


def _out(args: list[str], cwd: Path) -> str | None:
    result = _run(args, cwd)
    if result is None or result.returncode != 0:
        return None
    return result.stdout


# ------------------------------------------------------------------ CI


def ci_from_rollup(rollup: dict[str, Any] | None, commit: str = "") -> CiStatus:
    """Fold a GitHub ``statusCheckRollup`` into one CI verdict with counts."""
    ci = CiStatus(commit=commit[:12])
    if not rollup:
        return ci
    contexts = rollup.get("contexts") or {}
    failed_names: list[str] = []
    running_names: list[str] = []
    failed_url = running_url = pending_url = first_url = ""
    for node in contexts.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if node.get("__typename") == "StatusContext":
            name = str(node.get("context") or "")
            url = str(node.get("targetUrl") or "")
            state = str(node.get("state") or "").upper()
            kind = (
                "success"
                if state == "SUCCESS"
                else "failure"
                if state in {"FAILURE", "ERROR"}
                else "pending"
            )
        else:
            name = str(node.get("name") or "")
            run = ((node.get("checkSuite") or {}).get("workflowRun") or {}).get("url")
            url = str(run or node.get("detailsUrl") or "")
            status = str(node.get("status") or "").upper()
            conclusion = str(node.get("conclusion") or "").upper()
            if status == "COMPLETED":
                kind = "success" if conclusion in _PASSED else "failure"
            elif status == "IN_PROGRESS":
                kind = "running"
            else:
                kind = "pending" if status in _QUEUED or not status else "running"
        ci.total += 1
        first_url = first_url or url
        if kind == "success":
            ci.passed += 1
        elif kind == "failure":
            ci.failed += 1
            failed_names.append(name)
            failed_url = failed_url or url
        elif kind == "running":
            ci.running += 1
            running_names.append(name)
            running_url = running_url or url
        else:
            ci.pending += 1
            pending_url = pending_url or url
    rollup_state = str(rollup.get("state") or "").upper()
    if ci.failed or rollup_state in {"FAILURE", "ERROR"}:
        ci.state = "failure"
    elif ci.running:
        ci.state = "running"
    elif ci.pending or rollup_state in {"PENDING", "EXPECTED"}:
        ci.state = "pending"
    elif ci.total or rollup_state == "SUCCESS":
        ci.state = "success"
    # The page shows at most 40 checks; the rollup's own count is the truth.
    total = contexts.get("totalCount")
    if isinstance(total, int) and total > ci.total:
        ci.total = total
    ci.names = (failed_names or running_names)[:MAX_CHECK_NAMES]
    ci.url = failed_url or running_url or pending_url or first_url
    return ci


def _pr_state(node: dict[str, Any]) -> str:
    state = str(node.get("state") or "").upper()
    if state == "MERGED":
        return "merged"
    if state == "CLOSED":
        return "closed"
    if node.get("isInMergeQueue") or node.get("mergeQueueEntry"):
        return "queued"
    return "draft" if node.get("isDraft") else "open"


# Most relevant first: what is still moving, then what landed, then the rest.
_PR_RANK = {"queued": 0, "open": 1, "draft": 2, "merged": 3, "closed": 4}


@dataclass(slots=True)
class _GitHubSnapshot:
    ok: bool
    reason: str = ""
    code: str = ""
    repo_url: str = ""
    fetched_at: float = 0.0
    prs: list[PullRequest] = field(default_factory=list)
    #: Branch name → (tip oid, CI of that tip).
    refs: dict[str, tuple[str, CiStatus]] = field(default_factory=dict)

    @property
    def busy(self) -> bool:
        states = [pr.ci.state for pr in self.prs] + [ci.state for _, ci in self.refs.values()]
        return any(state in {"pending", "running"} for state in states) or any(
            pr.state == "queued" for pr in self.prs
        )


def parse_github(payload: dict[str, Any], fetched_at: float = 0.0) -> _GitHubSnapshot:
    """The parts of the GraphQL answer the tab shows."""
    repo = ((payload or {}).get("data") or {}).get("repository")
    if not isinstance(repo, dict):
        return _GitHubSnapshot(ok=False, reason="GitHub did not return this repository.")
    snap = _GitHubSnapshot(ok=True, repo_url=str(repo.get("url") or ""), fetched_at=fetched_at)
    for node in (repo.get("pullRequests") or {}).get("nodes") or []:
        if not isinstance(node, dict) or node.get("isCrossRepository"):
            continue
        commits = (node.get("commits") or {}).get("nodes") or []
        commit = (commits[0] or {}).get("commit") if commits else None
        commit = commit if isinstance(commit, dict) else {}
        entry = node.get("mergeQueueEntry") or {}
        snap.prs.append(
            PullRequest(
                number=int(node.get("number") or 0),
                title=str(node.get("title") or ""),
                url=str(node.get("url") or ""),
                state=_pr_state(node),
                base=str(node.get("baseRefName") or ""),
                head=str(node.get("headRefName") or ""),
                head_oid=str(node.get("headRefOid") or "")[:12],
                merged_at=str(node.get("mergedAt") or ""),
                updated_at=str(node.get("updatedAt") or ""),
                queue_position=entry.get("position") if isinstance(entry, dict) else None,
                ci=ci_from_rollup(commit.get("statusCheckRollup"), str(commit.get("oid") or "")),
            )
        )
    for node in (repo.get("refs") or {}).get("nodes") or []:
        if not isinstance(node, dict) or not node.get("name"):
            continue
        target = node.get("target") or {}
        oid = str(target.get("oid") or "")
        snap.refs[str(node["name"])] = (
            oid[:12],
            ci_from_rollup(target.get("statusCheckRollup"), oid),
        )
    return snap


def _fetch_github(repo: str, token: str) -> _GitHubSnapshot:
    now = time.time()
    owner, _, name = repo.partition("/")
    try:
        payload = github_link.graphql(token, _QUERY, {"owner": owner, "name": name})
    except github_link.GitHubError as exc:  # shown in the tab as github.reason/code
        return _GitHubSnapshot(ok=False, reason=str(exc), code=exc.code, fetched_at=now)
    return parse_github(payload, fetched_at=now)


class _GitHubCache:
    """One GitHub answer per repository, shared by every pane and window."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._entries: dict[str, _GitHubSnapshot] = {}

    def _fresh(self, snap: _GitHubSnapshot | None, now: float, force: bool) -> bool:
        if snap is None:
            return False
        age = now - snap.fetched_at
        if force:
            return age < GITHUB_MIN_REFRESH_S
        return age < (GITHUB_BUSY_TTL_S if snap.busy else GITHUB_TTL_S)

    def _load(self, key: str, token: str, lock: threading.Lock, force: bool) -> _GitHubSnapshot:
        # One fetch per repository at a time; everyone else waits for its answer.
        with lock:
            snap = self._entries.get(key)
            if self._fresh(snap, time.time(), force):
                assert snap is not None
                return snap
            snap = _fetch_github(key, token)
            self._entries[key] = snap
            return snap

    def get(self, key: str, token: str, *, force: bool = False) -> _GitHubSnapshot:
        """``key`` is the repository (``owner/name``)."""
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
            snap = self._entries.get(key)
        if snap is None or force:
            return self._load(key, token, lock, force)
        if not self._fresh(snap, time.time(), False) and not lock.locked():
            # Stale-while-revalidate: a poll never waits seconds for GitHub;
            # it gets the last answer now and the new one on its next tick.
            threading.Thread(
                target=self._load,
                args=(key, token, lock, False),
                name="git-overview-github",
                daemon=True,
            ).start()
        return snap

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()


_CACHE = _GitHubCache()


def clear_cache() -> None:
    _CACHE.clear()


# ------------------------------------------------------------------ local git


def _default_branch(root: Path, names: set[str], current: str) -> str:
    head = _out(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root)
    if head and head.strip():
        return head.strip().split("/", 1)[-1]
    for candidate in ("main", "master", "trunk", "develop"):
        if candidate in names:
            return candidate
    return current


def _local_branches(root: Path, current: str) -> list[BranchRow]:
    text = _out(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)%09%(objectname)%09%(upstream:short)%09"
            "%(upstream:track,nobracket)%09%(committerdate:unix)%09%(worktreepath)",
            "refs/heads",
        ],
        root,
    )
    rows: list[BranchRow] = []
    for line in (text or "").splitlines():
        name, oid, upstream, track, stamp, tree = ([*line.split("\t"), "", "", "", "", ""])[:6]
        if not name:
            continue
        row = BranchRow(
            name=name,
            current=name == current,
            upstream=upstream,
            head=oid[:12],
            committed_at=int(stamp) if stamp.isdigit() else 0,
        )
        for part in track.split(","):
            word, _, count = part.strip().partition(" ")
            if word == "ahead" and count.isdigit():
                row.ahead = int(count)
            elif word == "behind" and count.isdigit():
                row.behind = int(count)
        if tree and not row.current:
            row.worktree = str(Path(tree))
        rows.append(row)
    return rows


def _resolve_target(root: Path, target: str, local: set[str]) -> str | None:
    """The ref a "merged into ``target``" check reads: the local branch, else origin's."""
    if target in local:
        return f"refs/heads/{target}"
    ref = f"refs/remotes/origin/{target}"
    return ref if _out(["rev-parse", "--verify", "--quiet", ref], root) is not None else None


def _merged_by_git(root: Path, targets: list[str], local: set[str]) -> dict[str, list[str]]:
    """Branch name → targets whose history already contains all of its commits.

    A branch whose tip IS the target's tip (just created from it, or fast-
    forwarded) holds no work of its own and is not reported as merged.
    """
    merged: dict[str, list[str]] = {}
    for target in targets:
        ref = _resolve_target(root, target, local)
        if ref is None:
            continue
        tip = (_out(["rev-parse", ref], root) or "").strip()
        text = _out(
            [
                "for-each-ref",
                f"--merged={ref}",
                "--format=%(refname:short)%09%(objectname)",
                "refs/heads",
            ],
            root,
        )
        for line in (text or "").splitlines():
            name, _, oid = line.partition("\t")
            if name and name != target and oid.strip() != tip:
                merged.setdefault(name, []).append(target)
    return merged


# ------------------------------------------------------------------ assembly


def _remote_name(row: BranchRow) -> str:
    """The branch's name on GitHub: its upstream on origin, else its own name."""
    if row.upstream.startswith("origin/"):
        return row.upstream[len("origin/") :]
    return row.name


def _attach_github(row: BranchRow, remote: str, snap: _GitHubSnapshot) -> None:
    prs = [pr for pr in snap.prs if pr.head == remote]
    prs.sort(key=lambda pr: (_PR_RANK.get(pr.state, 9), -pr.number))
    row.pull_requests = prs[:MAX_PRS_PER_BRANCH]
    ref = snap.refs.get(remote)
    row.on_github = ref is not None
    live = next((pr for pr in prs if pr.state in {"queued", "open", "draft"}), None)
    if live is not None and live.ci.state != "none":
        row.ci = live.ci
    elif ref is not None:
        row.ci = ref[1]
    if row.ci.url == "" and row.ci.state != "none" and live is not None:
        row.ci.url = f"{live.url}/checks"
    if row.ci.state != "none" and row.head and row.ci.commit:
        row.ci_stale = row.ci.commit != row.head[: len(row.ci.commit)]
    # A merged pull request counts when the branch has not moved past it since.
    for pr in prs:
        if pr.state != "merged" or any(m.target == pr.base for m in row.merged_into):
            continue
        if row.remote_only or not pr.head_oid or row.head.startswith(pr.head_oid[:7]):
            row.merged_into.append(
                MergedInto(target=pr.base, via="pull_request", number=pr.number, url=pr.url)
            )


def _github_for(root: Path, info: RepoOverview, refresh: bool) -> _GitHubSnapshot | None:
    """GitHub's answer for the repository picked for ``root``, or None with
    ``info.github`` saying what is missing (a connection, or the choice)."""
    state = info.github
    state.repo = github_link.bound_repository(root)
    cred = github_link.credential()
    if cred is None:
        state.code = "not_connected"
        state.reason = "GitHub is not connected yet."
        return None
    state.source = cred.source
    if not state.repo:
        state.code = "needs_repo"
        state.reason = "Pick which GitHub repository this folder is."
        state.suggested_repo = github_link.remote_repository(root)
        return None
    snap = _CACHE.get(state.repo, cred.token, force=refresh)
    state.available = snap.ok
    state.reason = snap.reason
    state.code = snap.code
    state.repo_url = snap.repo_url or f"https://github.com/{state.repo}"
    state.fetched_at = snap.fetched_at
    return snap if snap.ok else None


def overview(folder: str | Path, *, refresh: bool = False, github: bool = True) -> RepoOverview:
    """Branches of the repository ``folder`` is in, with merge, PR and CI state."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        return RepoOverview(available=False, reason="The workspace folder is missing.")
    if shutil.which("git") is None:
        return RepoOverview(available=False, reason="Git is not installed.")
    top = _out(["rev-parse", "--show-toplevel"], path)
    if not top or not top.strip():
        return RepoOverview(available=False, reason="This folder is not a git repository.")
    root = Path(top.strip())
    info = RepoOverview(available=True, root=str(root))

    head = (_out(["symbolic-ref", "--quiet", "--short", "HEAD"], root) or "").strip()
    info.branch = head
    info.detached = not head
    info.head = (_out(["rev-parse", "--short=12", "HEAD"], root) or "").strip()

    rows = _local_branches(root, head)
    local = {row.name for row in rows}
    info.default_branch = _default_branch(root, local, head)

    snap: _GitHubSnapshot | None = None
    if github:
        snap = _github_for(root, info, refresh)

    # Current and default branch always make the list, however old they are.
    keep = {head, info.default_branch}
    pinned = [row for row in rows if row.name in keep]
    rest = [row for row in rows if row.name not in keep]
    info.truncated = len(rows) > MAX_BRANCHES
    rows = pinned + rest[: max(0, MAX_BRANCHES - len(pinned))]

    targets = [info.default_branch] if info.default_branch else []
    for pr in snap.prs if snap and snap.ok else []:
        if pr.base and pr.base not in targets and pr.base in local:
            targets.append(pr.base)
    merged = _merged_by_git(root, targets[:MAX_MERGE_TARGETS], local)
    for row in rows:
        row.merged_into = [MergedInto(target=t, via="git") for t in merged.get(row.name, [])]
        if snap is not None and snap.ok:
            _attach_github(row, _remote_name(row), snap)
    info.branches = rows

    if snap is not None and snap.ok:
        known = {_remote_name(row) for row in rows} | local
        for name, (oid, _ci) in snap.refs.items():
            if name in known:
                continue
            row = BranchRow(name=name, remote_only=True, head=oid)
            _attach_github(row, name, snap)
            info.remote_branches.append(row)
            if len(info.remote_branches) >= MAX_REMOTE_ONLY:
                break
    return info
