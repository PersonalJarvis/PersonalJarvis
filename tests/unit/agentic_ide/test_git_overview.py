"""The IDE Git tab's data: branches, merged-into, pull requests and CI."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.agentic_ide import git_overview, github_link
from jarvis.agentic_ide.git_overview import ci_from_rollup, parse_github

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    git_overview.clear_cache()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8"
    ).stdout.strip()


def _commit(root: Path, name: str) -> None:
    (root / name).write_text(name, encoding="utf-8")
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "a.txt")
    # feature/done: merged into main with a merge commit.
    _git(root, "switch", "-q", "-c", "feature/done")
    _commit(root, "b.txt")
    _git(root, "switch", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", "merge done", "feature/done")
    # feature/wip: work main does not have yet.
    _git(root, "switch", "-q", "-c", "feature/wip")
    _commit(root, "c.txt")
    # feature/fresh: just created from main, no work of its own.
    _git(root, "branch", "feature/fresh", "main")
    return root


def _rollup(*nodes: dict, state: str = "", total: int | None = None) -> dict:
    return {
        "state": state,
        "contexts": {
            "totalCount": total if total is not None else len(nodes),
            "nodes": list(nodes),
        },
    }


def _run(name: str, status: str, conclusion: str | None = None, url: str = "") -> dict:
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "detailsUrl": url + "/job",
        "checkSuite": {"workflowRun": {"url": url}},
    }


# ------------------------------------------------------------------ CI


def test_ci_failure_wins_and_links_the_failed_run() -> None:
    ci = ci_from_rollup(
        _rollup(
            _run("lint", "COMPLETED", "SUCCESS", "https://x/runs/1"),
            _run("tests", "IN_PROGRESS", None, "https://x/runs/2"),
            _run("build", "COMPLETED", "FAILURE", "https://x/runs/3"),
        ),
        "abcdef0123456789",
    )
    assert (ci.state, ci.passed, ci.running, ci.failed, ci.total) == ("failure", 1, 1, 1, 3)
    assert ci.url == "https://x/runs/3"
    assert ci.names == ["build"]
    assert ci.commit == "abcdef012345"


def test_ci_running_beats_queued_and_queued_is_pending() -> None:
    running = ci_from_rollup(
        _rollup(_run("a", "QUEUED"), _run("b", "IN_PROGRESS", url="https://x/runs/9"))
    )
    assert running.state == "running"
    assert running.url == "https://x/runs/9"
    assert ci_from_rollup(_rollup(_run("a", "QUEUED"), _run("b", "WAITING"))).state == "pending"


def test_ci_success_counts_skipped_and_status_contexts() -> None:
    ci = ci_from_rollup(
        _rollup(
            _run("a", "COMPLETED", "SUCCESS"),
            _run("b", "COMPLETED", "SKIPPED"),
            {"__typename": "StatusContext", "context": "ext", "state": "SUCCESS", "targetUrl": "u"},
        )
    )
    assert (ci.state, ci.passed, ci.total) == ("success", 3, 3)


def test_ci_trusts_the_rollup_beyond_the_first_page() -> None:
    ci = ci_from_rollup(_rollup(_run("a", "COMPLETED", "SUCCESS"), state="FAILURE", total=55))
    assert ci.state == "failure"
    assert ci.total == 55


def test_ci_without_checks_is_none() -> None:
    assert ci_from_rollup(None).state == "none"
    assert ci_from_rollup(_rollup()).state == "none"


# ------------------------------------------------------------------ GitHub parse


def _pr(number: int, head: str, **extra: object) -> dict:
    node = {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/o/r/pull/{number}",
        "state": "OPEN",
        "isDraft": False,
        "isCrossRepository": False,
        "headRefName": head,
        "headRefOid": "",
        "baseRefName": "main",
        "mergedAt": None,
        "updatedAt": "2026-09-29T00:00:00Z",
        "isInMergeQueue": False,
        "mergeQueueEntry": None,
        "commits": {"nodes": []},
    }
    node.update(extra)
    return node


def _payload(prs: list[dict], refs: list[dict] | None = None) -> dict:
    return {
        "data": {
            "repository": {
                "url": "https://github.com/o/r",
                "pullRequests": {"nodes": prs},
                "refs": {"nodes": refs or []},
            }
        }
    }


def test_pull_request_states_cover_the_five_github_states() -> None:
    snap = parse_github(
        _payload(
            [
                _pr(1, "a", isDraft=True),
                _pr(2, "b"),
                _pr(3, "c", isInMergeQueue=True, mergeQueueEntry={"position": 2}),
                _pr(4, "d", state="MERGED", mergedAt="2026-09-28T00:00:00Z"),
                _pr(5, "e", state="CLOSED"),
                _pr(6, "fork", isCrossRepository=True),
            ]
        )
    )
    assert [(pr.number, pr.state) for pr in snap.prs] == [
        (1, "draft"),
        (2, "open"),
        (3, "queued"),
        (4, "merged"),
        (5, "closed"),
    ]
    assert snap.prs[2].queue_position == 2
    assert snap.busy  # a queued PR is worth re-reading sooner


def test_unreadable_payload_is_unavailable() -> None:
    assert parse_github({"data": {"repository": None}}).ok is False


def test_a_github_failure_becomes_a_coded_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(token: str, query: str, variables: dict | None = None) -> dict:
        raise github_link.GitHubError("GitHub could not be reached.", code="unreachable")

    monkeypatch.setattr(github_link, "graphql", boom)
    snap = git_overview._fetch_github("o/r", "t")
    assert (snap.ok, snap.code, snap.reason) == (
        False,
        "unreachable",
        "GitHub could not be reached.",
    )


# ------------------------------------------------------------------ local git


@needs_git
def test_overview_marks_current_default_and_merged_branches(repo: Path) -> None:
    info = git_overview.overview(repo, github=False)
    assert info.available
    assert (info.branch, info.default_branch) == ("feature/wip", "main")
    by_name = {row.name: row for row in info.branches}
    assert by_name["feature/wip"].current
    assert [m.target for m in by_name["feature/done"].merged_into] == ["main"]
    assert by_name["feature/done"].merged_into[0].via == "git"
    # Work main lacks, and a branch with no work of its own: neither is "merged".
    assert by_name["feature/wip"].merged_into == []
    assert by_name["feature/fresh"].merged_into == []
    assert by_name["main"].merged_into == []


@needs_git
def test_overview_of_a_plain_folder_is_unavailable(tmp_path: Path) -> None:
    info = git_overview.overview(tmp_path, github=False)
    assert not info.available
    assert "not a git repository" in info.reason


@needs_git
def test_overview_joins_pull_requests_ci_and_squash_merges(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wip_head = _git(repo, "rev-parse", "feature/wip")
    fresh_head = _git(repo, "rev-parse", "feature/fresh")
    calls: list[str] = []

    def fake_fetch(repo_name: str, token: str) -> git_overview._GitHubSnapshot:
        calls.append(repo_name)
        snap = parse_github(
            _payload(
                [
                    _pr(
                        7,
                        "feature/wip",
                        headRefOid=wip_head,
                        commits={
                            "nodes": [
                                {
                                    "commit": {
                                        "oid": wip_head,
                                        "statusCheckRollup": _rollup(
                                            _run("tests", "IN_PROGRESS", url="https://x/runs/7")
                                        ),
                                    }
                                }
                            ]
                        },
                    ),
                    # A squash-merged PR whose branch has not moved since.
                    _pr(8, "feature/fresh", state="MERGED", headRefOid=fresh_head),
                    _pr(9, "only-remote", state="CLOSED"),
                ],
                refs=[
                    {"name": "main", "target": {"oid": "0" * 40, "statusCheckRollup": None}},
                    {"name": "only-remote", "target": {"oid": "1" * 40, "statusCheckRollup": None}},
                ],
            ),
            fetched_at=git_overview.time.time(),
        )
        return snap

    monkeypatch.setattr(git_overview, "_fetch_github", fake_fetch)
    monkeypatch.setattr(github_link, "credential", lambda: github_link.Credential("t", "app"))
    monkeypatch.setattr(github_link, "bound_repository", lambda folder: "o/r")
    info = git_overview.overview(repo)
    assert calls == ["o/r"]
    assert (info.github.repo, info.github.source) == ("o/r", "app")
    by_name = {row.name: row for row in info.branches}

    wip = by_name["feature/wip"]
    assert [(pr.number, pr.state) for pr in wip.pull_requests] == [(7, "open")]
    assert (wip.ci.state, wip.ci.url, wip.ci_stale) == ("running", "https://x/runs/7", False)

    fresh = by_name["feature/fresh"]
    assert [(m.target, m.via, m.number) for m in fresh.merged_into] == [("main", "pull_request", 8)]

    assert by_name["main"].on_github
    assert [row.name for row in info.remote_branches] == ["only-remote"]
    assert info.remote_branches[0].pull_requests[0].state == "closed"
    assert info.github.available and info.github.repo_url == "https://github.com/o/r"

    # A second read inside the TTL reuses the answer instead of calling GitHub again.
    git_overview.overview(repo)
    assert len(calls) == 1


# ------------------------------------------------------------------ the one-time pick


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "data" / "github_repos.json"
    monkeypatch.setattr(github_link, "_store_path", lambda: target)
    return target


@needs_git
def test_without_a_connection_the_tab_offers_to_connect(
    repo: Path, monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    monkeypatch.setattr(github_link, "credential", lambda: None)
    info = git_overview.overview(repo)
    assert info.available and info.branches  # local git still works
    assert (info.github.available, info.github.code) == (False, "not_connected")


@needs_git
def test_an_unpicked_folder_asks_once_and_suggests_its_remote(
    repo: Path, monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    monkeypatch.setattr(github_link, "credential", lambda: github_link.Credential("t", "gh"))
    _git(repo, "remote", "add", "origin", "git@github.com:Octo-Org/my.app.git")
    info = git_overview.overview(repo)
    assert info.github.code == "needs_repo"
    assert info.github.suggested_repo == "Octo-Org/my.app"

    github_link.bind_repository(repo, "Octo-Org/my.app")
    # A worktree of the same repository shares the answer.
    tree = repo.parent / "tree"
    _git(repo, "worktree", "add", "-q", str(tree), "feature/fresh")
    assert github_link.bound_repository(tree) == "Octo-Org/my.app"
    assert github_link.bound_repository(repo / ".") == "Octo-Org/my.app"

    github_link.bind_repository(repo, "")
    assert github_link.bound_repository(repo) == ""


def test_a_malformed_choice_is_refused(tmp_path: Path, store: Path) -> None:
    with pytest.raises(ValueError):
        github_link.bind_repository(tmp_path, "not a repo")
    store.parent.mkdir(parents=True)
    store.write_text("{broken", encoding="utf-8")
    assert github_link.bound_repository(tmp_path) == ""  # unreadable file = no choice


def test_remote_parsing_accepts_https_and_ssh(tmp_path: Path) -> None:
    for url in (
        "https://github.com/o/r.git",
        "https://github.com/o/r",
        "git@github.com:o/r.git",
        "ssh://git@github.com/o/r.git",
    ):
        match = github_link._REMOTE_RE.search(url)
        assert match is not None and f"{match['owner']}/{match['name']}" == "o/r", url
    assert github_link._REMOTE_RE.search("https://gitlab.com/o/r.git") is None
