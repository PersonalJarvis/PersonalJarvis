"""Read-only GitHub status for branches actually created by each coding session.

Unlike the repository overview, this queries the exact remote branch, including
its historical PR when GitHub has deleted the branch after merging. No local
ancestry, terminal output or CI success is treated as proof of a GitHub merge.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from loguru import logger

from jarvis.agentic_ide import git_overview, github_link
from jarvis.agentic_ide.github_checks import CheckStatus, checks
from jarvis.agentic_ide.session_branches import PaneBranchRecord, owned_branch

_QUERY = """
query($owner: String!, $name: String!, $branch: String!, $ref: String!, $prIds: [ID!]!) {
  nodes(ids: $prIds) { ... on PullRequest { ...PR } }
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: $ref) {
      target { ... on Commit { oid statusCheckRollup { ...Rollup } } }
      associatedPullRequests(first: 20, orderBy: {field: UPDATED_AT, direction: DESC}) {
        nodes { ...PR }
      }
    }
    pullRequests(headRefName: $branch, first: 20,
                 orderBy: {field: UPDATED_AT, direction: DESC}) { nodes { ...PR } }
    openPullRequests: pullRequests(headRefName: $branch, states: [OPEN], first: 20,
                 orderBy: {field: UPDATED_AT, direction: DESC}) { nodes { ...PR } }
  }
}
fragment PR on PullRequest {
  id number url title state isDraft isInMergeQueue headRefName headRefOid createdAt
  locked mergeable mergeStateStatus reviewDecision
  headRepository { nameWithOwner }
  commits(last: 1) { nodes { commit { oid statusCheckRollup { ...Rollup } } } }
}
fragment Rollup on StatusCheckRollup {
  state contexts(first: 100) {
    totalCount checkRunCountsByState { state count } statusContextCountsByState { state count }
    nodes {
    __typename
    ... on CheckRun { name status conclusion detailsUrl checkSuite { workflowRun { url } } }
    ... on StatusContext { context state targetUrl }
  } }
}
"""


@dataclass(frozen=True, slots=True)
class Checkout:
    repo: str
    branch: str
    head: str
    created_at: float = 0


def _git(folder: Path, *args: str) -> str:
    return (git_overview._out(list(args), folder) or "").strip()


def _repository(url: str) -> str:
    """Accept GitHub hosts exactly, never a hostname containing github.com."""
    match = re.fullmatch(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?/?", url)
    if match:
        repo = match[1]
    else:
        try:
            parsed = urlsplit(url)
        except ValueError:
            logger.debug("Session GitHub status: ignoring a malformed remote URL")
            return ""
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            return ""
        repo = parsed.path.strip("/").removesuffix(".git")
    return repo if github_link.REPO_RE.fullmatch(repo) else ""


def checkout(folder: str | Path, branch: str = "", created_at: float = 0) -> Checkout | None:
    """Resolve the owned branch's remote; never follow a shared checkout's HEAD."""
    path = Path(folder)
    if not path.is_dir():
        return None
    if not branch:
        return None
    fields = _git(
        path,
        "for-each-ref",
        "--count=1",
        "--format=%(objectname)%09%(upstream:remotename)%09%(upstream:remoteref)%09%(refname)",
        f"refs/heads/{branch}",
    ).split("\t")
    head, upstream, ref, full_ref = (fields + ["", "", ""])[:4]
    if full_ref != f"refs/heads/{branch}":
        head = upstream = ref = ""
    # A configured push remote takes precedence in triangular fork workflows.
    push = _git(path, "config", "--get", f"branch.{branch}.pushRemote")
    push = push or _git(path, "config", "--get", "remote.pushDefault")
    remote = push or upstream or "origin"
    if remote == ".":
        return None
    url = _git(path, "remote", "get-url", remote)
    repo = _repository(url)
    if not repo:
        return None
    remote_branch = (
        ref.removeprefix("refs/heads/") if ref and (not push or push == upstream) else branch
    )
    return Checkout(repo, remote_branch, head, created_at)


@dataclass(slots=True)
class BranchStatus:
    repo: str
    branch: str
    url: str
    published: bool = False
    owned: bool = True
    available: bool = True
    reason: str = ""
    fetched_at: float = 0
    # branch | draft | open | queued | merged | closed
    state: str = "branch"
    number: int | None = None
    ci: CheckStatus = field(default_factory=CheckStatus)
    ci_stale: bool = False
    merge_status: str = ""
    review: str = ""
    locked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Snapshot:
    lock: threading.Lock = field(default_factory=threading.Lock)
    payload: dict[str, Any] = field(default_factory=dict)
    fetched_at: float = 0
    reason: str = ""
    pr_ids: list[str] = field(default_factory=list)


@lru_cache(maxsize=256)
def _slot(repo: str, branch: str, credential_id: str) -> _Snapshot:
    # Only a credential fingerprint is retained in the cache key, never a token.
    return _Snapshot()


def _snapshot(target: Checkout, token: str) -> _Snapshot:
    cached = _slot(target.repo, target.branch, hashlib.sha256(token.encode()).hexdigest())
    with cached.lock:
        if time.time() - cached.fetched_at < 20:
            return cached
        owner, name = target.repo.split("/", 1)
        try:
            payload = github_link.graphql(
                token,
                _QUERY,
                {
                    "owner": owner,
                    "name": name,
                    "branch": target.branch,
                    "ref": f"refs/heads/{target.branch}",
                    "prIds": cached.pr_ids,
                },
            )
            if not isinstance((payload.get("data") or {}).get("repository"), dict):
                raise github_link.GitHubError("GitHub did not return this repository.")
            cached.payload = payload
            candidates = _pull_requests(payload)
            matching = [pr for pr in candidates if pr.get("id") and _same_branch(pr, target)]
            matching.sort(key=lambda pr: (pr.get("state") != "OPEN", -int(pr.get("number") or 0)))
            cached.pr_ids = [str(matching[0]["id"])] if matching else cached.pr_ids
            cached.reason = ""
        except github_link.GitHubError as exc:
            # Retain only identity evidence on failure. The UI suppresses all
            # previous success/merge colours while available is false.
            cached.reason = str(exc)
        cached.fetched_at = time.time()
        return cached


def _pull_requests(payload: dict[str, Any]) -> list[dict[str, Any]]:
    repo = (payload.get("data") or {}).get("repository") or {}
    ref = repo.get("ref") or {}
    prs = list((ref.get("associatedPullRequests") or {}).get("nodes") or [])
    prs += (repo.get("pullRequests") or {}).get("nodes") or []
    prs += (repo.get("openPullRequests") or {}).get("nodes") or []
    prs += (payload.get("data") or {}).get("nodes") or []
    return [pr for pr in prs if isinstance(pr, dict)]


def _same_branch(pr: dict[str, Any], target: Checkout) -> bool:
    from datetime import datetime

    owner = (pr.get("headRepository") or {}).get("nameWithOwner", "")
    if owner.casefold() != target.repo.casefold() or pr.get("headRefName") != target.branch:
        return False
    if target.created_at and pr.get("createdAt"):
        try:
            created = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            logger.debug("Session GitHub status: malformed pull request creation time")
            return False
        if created < target.created_at - 5:
            return False
    return True


def parse_status(target: Checkout, payload: dict[str, Any], fetched_at: float) -> BranchStatus:
    status = BranchStatus(
        repo=target.repo,
        branch=target.branch,
        url=f"https://github.com/{target.repo}/tree/{quote(target.branch, safe='')}",
        fetched_at=fetched_at,
    )
    repo = (payload.get("data") or {}).get("repository") or {}
    ref = repo.get("ref") or {}
    tip = ref.get("target") or {}
    oid = str(tip.get("oid") or "")
    status.published = bool(oid)
    prs = _pull_requests(payload)
    relevant = []
    for pr in prs:
        if not _same_branch(pr, target):
            continue
        live = pr.get("state") == "OPEN"
        # A reused branch must not inherit a historical merge/close verdict.
        if live or (not oid or pr.get("headRefOid") == oid):
            relevant.append(pr)
    relevant.sort(key=lambda pr: (pr.get("state") != "OPEN", -int(pr.get("number") or 0)))
    pr = relevant[0] if relevant else None
    rollup = tip.get("statusCheckRollup")
    if pr:
        status.published = True  # Survives GitHub's delete-branch-after-merge.
        status.state = git_overview._pr_state(pr)
        status.number = int(pr["number"])
        status.url = str(pr["url"])
        status.merge_status = str(pr.get("mergeStateStatus") or "UNKNOWN").lower()
        status.review = str(pr.get("reviewDecision") or "").lower()
        status.locked = bool(pr.get("locked"))
        commits = (pr.get("commits") or {}).get("nodes") or []
        commit = (commits[-1].get("commit") or {}) if commits else {}
        # PR checks are usable only for the current remote tip (or a deleted
        # branch). A stale PR must never override a newer pushed commit's CI.
        if not oid or commit.get("oid") == oid:
            oid = str(commit.get("oid") or oid)
            rollup = commit.get("statusCheckRollup") or rollup
    status.ci = checks(rollup, oid)
    status.ci_stale = bool(oid and target.head != oid)
    if status.ci.state != "none" and not status.ci.url:
        status.ci.url = (
            f"{status.url}/checks"
            if pr
            else f"https://github.com/{target.repo}/commit/{oid}/checks"
        )
    return status


def statuses(records: list[PaneBranchRecord] | dict[str, str]) -> dict[str, dict[str, Any] | None]:
    """One result per pane with ownership evidence; shared GitHub reads."""
    result: dict[str, dict[str, Any] | None] = {}
    # Old route versions supplied only folders: those carry no ownership proof.
    if isinstance(records, dict):
        return {pane: None for pane in records}
    targets: dict[tuple[str, str, float], Checkout | None] = {}
    credential: github_link.Credential | None = None
    credential_read = False
    for record in records:
        pane = record.pane
        owned = owned_branch(record)
        if owned is None:
            result[pane] = None
            continue
        key = (owned.folder, owned.branch, owned.created_at)
        if key not in targets:
            targets[key] = checkout(*key)
        target = targets[key]
        if target is None:
            result[pane] = None
            continue
        if not credential_read:
            credential = github_link.credential()
            credential_read = True
        if credential is None:
            status = parse_status(target, {}, 0)
            status.available = False
            status.reason = "GitHub is not connected."
        else:
            snapshot = _snapshot(target, credential.token)
            status = parse_status(target, snapshot.payload, snapshot.fetched_at)
            status.available = not snapshot.reason
            status.reason = snapshot.reason
        result[pane] = status.to_dict()
    return result
