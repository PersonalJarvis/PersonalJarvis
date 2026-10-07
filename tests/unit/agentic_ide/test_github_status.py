"""Session badges must describe the exact GitHub branch and pushed commit."""

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import github_link
from jarvis.agentic_ide import github_status as status

HEAD = "a" * 40
OTHER = "b" * 40
TARGET = status.Checkout("owner/repo", "feature/test", HEAD)


def rollup(conclusion="SUCCESS", state="SUCCESS", phase="COMPLETED"):
    return {
        "state": state,
        "contexts": {
            "totalCount": 1,
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "tests",
                    "status": phase,
                    "conclusion": conclusion,
                    "detailsUrl": "https://github.com/owner/repo/actions/runs/1",
                }
            ],
        },
    }


def pr(number=1, state="OPEN", head=HEAD, **extra):
    return {
        "number": number,
        "url": f"https://github.com/owner/repo/pull/{number}",
        "state": state,
        "headRefName": TARGET.branch,
        "headRefOid": head,
        "headRepository": {"nameWithOwner": TARGET.repo},
        "commits": {"nodes": [{"commit": {"oid": head, "statusCheckRollup": rollup()}}]},
        **extra,
    }


def payload(*prs, head=HEAD, checks=None):
    return {
        "data": {
            "repository": {
                "ref": {"target": {"oid": head, "statusCheckRollup": checks}} if head else None,
                "pullRequests": {"nodes": list(prs)},
            }
        }
    }


@pytest.mark.parametrize(
    "remote, expected",
    [
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("ssh://git@github.com/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("https://notgithub.com/owner/repo.git", ""),
        ("https://github.com.evil.invalid/owner/repo", ""),
        ("https://gitlab.com/owner/repo", ""),
    ],
)
def test_remote_host_is_exact(remote, expected):
    assert status._repository(remote) == expected


@pytest.mark.parametrize(
    "state, extra, expected",
    [
        ("OPEN", {}, "open"),
        ("OPEN", {"isDraft": True}, "draft"),
        ("OPEN", {"isInMergeQueue": True}, "queued"),
        ("MERGED", {}, "merged"),
        ("CLOSED", {}, "closed"),
    ],
)
def test_pr_states_survive_branch_deletion(state, extra, expected):
    value = status.parse_status(TARGET, payload(pr(state=state, **extra), head=""), 123)
    assert value.published and value.state == expected
    assert value.ci.state == "success"


def test_local_only_has_no_badge_and_green_checks_do_not_mean_merged():
    assert not status.parse_status(TARGET, payload(head=""), 1).published
    value = status.parse_status(TARGET, payload(checks=rollup()), 1)
    assert value.published and value.state == "branch"
    assert value.ci.state == "success"


def test_reused_branch_does_not_inherit_an_old_merge():
    value = status.parse_status(TARGET, payload(pr(state="MERGED", head=OTHER)), 1)
    assert value.state == "branch" and value.number is None
    # Remote moved too, while the local checkout still has the merged commit.
    value = status.parse_status(TARGET, payload(pr(state="MERGED"), head=OTHER), 1)
    assert value.state == "branch"


def test_other_forks_same_branch_name_are_not_this_session():
    value = status.parse_status(
        TARGET, payload(pr(headRepository={"nameWithOwner": "other/repo"}), head=""), 1
    )
    assert not value.published


def test_fork_pr_in_upstream_is_supported():
    data = payload()
    data["data"]["repository"]["ref"]["associatedPullRequests"] = {
        "nodes": [pr(url="https://github.com/upstream/repo/pull/1")]
    }
    assert status.parse_status(TARGET, data, 1).url == "https://github.com/upstream/repo/pull/1"


def test_new_pushed_tip_wins_over_old_pr_ci():
    value = status.parse_status(
        TARGET, payload(pr(head=OTHER), checks=rollup("FAILURE", "FAILURE")), 1
    )
    assert value.ci.state == "failure" and value.ci.commit == HEAD[:12]


def test_unpushed_local_commits_are_explicit():
    value = status.parse_status(TARGET, payload(pr(head=OTHER), head=OTHER), 1)
    assert value.ci_stale and value.ci.commit == OTHER[:12]


def test_remote_merge_remains_true_when_local_work_has_advanced():
    value = status.parse_status(TARGET, payload(pr(state="MERGED", head=OTHER), head=OTHER), 1)
    assert value.state == "merged" and value.ci.commit == OTHER[:12]


def test_historical_pr_before_this_session_created_the_branch_is_ignored():
    target = status.Checkout(TARGET.repo, TARGET.branch, HEAD, created_at=1_800_000_000)
    value = status.parse_status(
        target, payload(pr(state="MERGED", createdAt="2020-01-01T00:00:00Z")), 1
    )
    assert value.state == "branch" and value.number is None


def test_deleted_fork_branch_uses_its_remembered_pull_request():
    data = payload(head="")
    data["data"]["nodes"] = [pr(state="MERGED", url="https://github.com/upstream/repo/pull/1")]
    value = status.parse_status(TARGET, data, 1)
    assert value.published and value.state == "merged"


def test_folder_only_requests_are_not_evidence_of_session_ownership():
    assert status.statuses({"t1": "workspace-on-main"}) == {"t1": None}


@pytest.mark.parametrize(
    "conclusion, phase, rollup_state, expected",
    [
        ("SUCCESS", "COMPLETED", "SUCCESS", "success"),
        ("FAILURE", "COMPLETED", "FAILURE", "failure"),
        ("TIMED_OUT", "COMPLETED", "FAILURE", "timed_out"),
        ("CANCELLED", "COMPLETED", "FAILURE", "cancelled"),
        ("SKIPPED", "COMPLETED", "SUCCESS", "skipped"),
        ("NEUTRAL", "COMPLETED", "SUCCESS", "neutral"),
        (None, "IN_PROGRESS", "PENDING", "running"),
        (None, "QUEUED", "PENDING", "pending"),
    ],
)
def test_ci_states(conclusion, phase, rollup_state, expected):
    value = status.parse_status(TARGET, payload(checks=rollup(conclusion, rollup_state, phase)), 1)
    assert value.ci.state == expected


def test_incomplete_check_page_uses_github_aggregate():
    checks = rollup("SUCCESS", "FAILURE")
    checks["contexts"]["totalCount"] = 120
    assert status.parse_status(TARGET, payload(checks=checks), 1).ci.state == "failure"


def test_checkout_reads_worktree_head_and_custom_upstream(monkeypatch, tmp_path):
    def git(folder, *args):
        assert folder == tmp_path
        if args[0] == "symbolic-ref":
            return "local-name"
        if args[0] == "for-each-ref":
            return f"{HEAD}\tcompany\trefs/heads/feature/test\trefs/heads/local-name"
        if args[0] == "remote":
            assert args[-1] == "company"
            return "git@github.com:owner/repo.git"
        return ""

    monkeypatch.setattr(status, "_git", git)
    assert status.checkout(tmp_path, "local-name") == TARGET


def test_detached_head_never_gets_branch_status(monkeypatch, tmp_path):
    monkeypatch.setattr(status, "_git", lambda *_: "")
    assert status.checkout(tmp_path) is None


def test_multiple_panes_share_requests_and_errors_are_unknown(monkeypatch):
    status._slot.cache_clear()
    reads, requests = [], []

    def local(folder, branch, created_at):
        reads.append(folder)
        return TARGET

    def graphql(*args):
        requests.append(args)
        if len(requests) > 1:
            raise github_link.GitHubError("GitHub could not be reached.")
        return payload(pr(state="MERGED"))

    monkeypatch.setattr(status, "checkout", local)
    monkeypatch.setattr(
        status,
        "owned_branch",
        lambda record: SimpleNamespace(folder="worktree", branch=TARGET.branch, created_at=0),
    )
    monkeypatch.setattr(
        github_link, "credential", lambda: github_link.Credential("test-token", "gh")
    )
    monkeypatch.setattr(github_link, "graphql", graphql)
    records = [status.PaneBranchRecord(pane, pane, "worktree") for pane in ("t1", "t2")]
    panes = status.statuses(records)
    assert len(reads) == len(requests) == 1
    assert panes["t1"] == panes["t2"]
    cached = status._snapshot(TARGET, "test-token")
    cached.fetched_at = 0
    failed = status.statuses(records[:1])["t1"]
    assert failed["published"] and not failed["available"]


def test_route_uses_pane_cwd_and_excludes_remote_computers(monkeypatch):
    from fastapi import HTTPException

    from jarvis.ui.web import agentic_ide_git_routes as routes

    calls = []
    pane = SimpleNamespace(
        name="t1",
        computer_id="",
        cwd=lambda _: "fork-folder",
        history_id="history",
        agent="codex",
        account="",
        resume=None,
        branch="owned",
    )
    remote = SimpleNamespace(name="t2", computer_id="server")
    session = SimpleNamespace(folder="workspace", terminals=[pane, remote])
    monkeypatch.setattr(
        routes,
        "get_registry",
        lambda: SimpleNamespace(get=lambda key: session if key == "w" else None),
    )
    monkeypatch.setattr(status, "statuses", lambda folders: calls.append(folders) or {})
    assert routes.session_github_status("w") == {"panes": {}}
    assert calls[0][0].folder == "fork-folder"
    assert calls[0][0].created_branch == "owned"
    with pytest.raises(HTTPException) as error:
        routes.session_github_status("missing")
    assert error.value.status_code == 404
