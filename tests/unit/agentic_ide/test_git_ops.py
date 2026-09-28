"""Git for the Agentic IDE: inspect, prepare, finish — against a real ``git``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.agentic_ide import folders, git_ops
from jarvis.agentic_ide.git_ops import GitError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A reproducible identity and no user/system config leaking into the test.
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8"
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git_ops.init_repository(root)
    (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "feat: app")
    return root


def test_inspect_plain_folder_is_not_a_repo(tmp_path: Path) -> None:
    info = git_ops.inspect(tmp_path)
    assert info.git_available
    assert not info.is_repo
    assert info.suggested_branch.startswith(git_ops.BRANCH_PREFIX)


def test_init_creates_main_with_an_empty_first_commit(tmp_path: Path) -> None:
    (tmp_path / "secret.env").write_text("TOKEN=x\n", encoding="utf-8")
    result = git_ops.init_repository(tmp_path)
    assert result.branch == "main" and result.created
    info = git_ops.inspect(tmp_path)
    assert info.branch == "main" and not info.unborn
    # The user's files are never committed on their behalf.
    assert info.untracked == 1
    with pytest.raises(GitError) as exc:
        git_ops.init_repository(tmp_path)
    assert exc.value.code == "exists"


def test_inspect_counts_changes_and_line_stats(repo: Path) -> None:
    (repo / "app.py").write_text("print('hi')\nprint('more')\n", encoding="utf-8")
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    info = git_ops.inspect(repo)
    assert info.branch == "main"
    assert info.unstaged == 1 and info.untracked == 1 and info.dirty
    assert info.insertions == 1
    assert {change.path for change in info.changes} == {"app.py", "new.txt"}
    assert info.default_branch == "main"
    assert [branch.name for branch in info.branches] == ["main"]
    assert info.to_dict()["dirty"] is True


def test_new_branch_in_place_and_reuse(repo: Path) -> None:
    made = git_ops.prepare(repo, "new_branch", branch="feature/login")
    assert made.created and made.folder == str(repo)
    assert git_ops.inspect(repo).branch == "feature/login"
    git_ops.prepare(repo, "switch_branch", branch="main")
    again = git_ops.prepare(repo, "new_branch", branch="feature/login")
    assert not again.created
    assert git_ops.inspect(repo).branch == "feature/login"


def test_invalid_branch_name_is_refused(repo: Path) -> None:
    with pytest.raises(GitError) as exc:
        git_ops.prepare(repo, "new_branch", branch="bad..name")
    assert exc.value.code == "invalid"


def test_new_worktree_lands_under_dot_worktrees_and_is_excluded(repo: Path) -> None:
    made = git_ops.prepare(repo, "new_worktree", branch="agent/brave-river-0001")
    target = repo / ".worktrees" / "agent-brave-river-0001"
    assert Path(made.folder) == target and made.created
    assert (target / "app.py").is_file()
    main = git_ops.inspect(repo)
    # The worktree folder never shows up as untracked in the main checkout.
    assert not main.dirty
    assert any(tree.branch == "agent/brave-river-0001" for tree in main.worktrees)
    linked = git_ops.inspect(target)
    assert linked.is_worktree and linked.branch == "agent/brave-river-0001"
    assert Path(linked.main_root) == Path(_git(repo, "rev-parse", "--show-toplevel"))
    # The sidebar's cheap branch reader understands a worktree's `.git` file.
    assert folders._git_branch(target) == "agent/brave-river-0001"
    assert git_ops.same_repository(target, repo)


def test_new_worktree_for_a_checked_out_branch_opens_that_checkout(repo: Path) -> None:
    first = git_ops.prepare(repo, "new_worktree", branch="agent/one")
    second = git_ops.prepare(repo, "new_worktree", branch="agent/one")
    assert second.folder == first.folder and not second.created


def test_worktree_keeps_the_sub_folder(repo: Path) -> None:
    (repo / "web").mkdir()
    (repo / "web" / "index.ts").write_text("export {}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat: web")
    made = git_ops.prepare(repo / "web", "new_worktree", branch="agent/web")
    assert Path(made.folder) == repo / ".worktrees" / "agent-web" / "web"


def test_unborn_repository_cannot_branch(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    with pytest.raises(GitError) as exc:
        git_ops.prepare(tmp_path, "new_worktree", branch="agent/x")
    assert exc.value.code == "unborn"


def test_commit_all_and_nothing_to_commit(repo: Path) -> None:
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    sha = git_ops.commit_all(repo, "feat: add new")
    assert sha and not git_ops.inspect(repo).dirty
    with pytest.raises(GitError) as exc:
        git_ops.commit_all(repo, "feat: again")
    assert exc.value.code == "clean"


def test_push_without_remote_says_so(repo: Path) -> None:
    with pytest.raises(GitError) as exc:
        git_ops.push(repo)
    assert exc.value.code == "no_remote"


def test_push_sets_upstream_then_tracks_ahead(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    assert git_ops.push(repo) == "origin/main"
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    git_ops.commit_all(repo, "feat: b")
    info = git_ops.inspect(repo)
    assert info.upstream == "origin/main" and info.ahead == 1 and info.behind == 0


def test_merge_back_from_worktree(repo: Path) -> None:
    made = git_ops.prepare(repo, "new_worktree", branch="agent/feature")
    tree = Path(made.folder)
    (tree / "feature.txt").write_text("done\n", encoding="utf-8")
    git_ops.commit_all(tree, "feat: feature")
    git_ops.merge_back(tree)
    assert (repo / "feature.txt").is_file()


def test_merge_back_refuses_uncommitted_work(repo: Path) -> None:
    tree = Path(git_ops.prepare(repo, "new_worktree", branch="agent/dirty").folder)
    (tree / "app.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(GitError) as exc:
        git_ops.merge_back(tree)
    assert exc.value.code == "dirty"


def test_merge_conflict_is_aborted(repo: Path) -> None:
    tree = Path(git_ops.prepare(repo, "new_worktree", branch="agent/clash").folder)
    (tree / "app.py").write_text("theirs\n", encoding="utf-8")
    git_ops.commit_all(tree, "feat: theirs")
    (repo / "app.py").write_text("ours\n", encoding="utf-8")
    git_ops.commit_all(repo, "feat: ours")
    with pytest.raises(GitError) as exc:
        git_ops.merge_back(tree)
    assert exc.value.code == "conflict"
    # Nothing half-merged is left behind.
    assert not git_ops.inspect(repo).dirty
    assert (repo / "app.py").read_text(encoding="utf-8") == "ours\n"


def test_remove_worktree_asks_again_when_dirty(repo: Path) -> None:
    tree = Path(git_ops.prepare(repo, "new_worktree", branch="agent/tmp").folder)
    (tree / "scratch.txt").write_text("wip\n", encoding="utf-8")
    with pytest.raises(GitError) as exc:
        git_ops.remove_worktree(repo, tree)
    assert exc.value.code == "dirty"
    summary = git_ops.remove_worktree(repo, tree, force=True, delete_branch=True)
    assert not tree.exists()
    # Unmerged work is never thrown away with the branch: `-d`, not `-D`.
    assert "Deleted branch" in summary
    assert "agent/tmp" not in _git(repo, "branch")


def test_remove_refuses_main_checkout(repo: Path) -> None:
    with pytest.raises(GitError) as exc:
        git_ops.remove_worktree(repo, repo)
    assert exc.value.code == "invalid"


def test_open_worktree_by_branch(repo: Path) -> None:
    made = git_ops.prepare(repo, "new_worktree", branch="agent/open-me")
    opened = git_ops.prepare(repo, "open_worktree", branch="agent/open-me")
    assert opened.folder == made.folder


def test_worktree_in_use() -> None:
    base = Path("/work/repo/.worktrees/agent-x")
    assert git_ops.worktree_in_use(base, [str(base / "web")])
    assert not git_ops.worktree_in_use(base, [str(Path("/work/repo"))])


def test_pull_request_without_gh(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(git_ops, "gh_available", lambda: False)
    with pytest.raises(GitError) as exc:
        git_ops.create_pull_request(repo)
    assert exc.value.code == "missing"


def test_no_git_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(git_ops, "git_available", lambda: False)
    info = git_ops.inspect(tmp_path)
    assert not info.git_available and not info.is_repo


async def test_workspace_may_open_in_a_worktree_of_its_project(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    from jarvis.agentic_ide import library, session
    from tests.fakes.fake_pty_manager import FakePtyManager

    monkeypatch.setattr(session, "agent_argv", lambda agent: (f"/usr/bin/{agent}",))
    registry = session.Registry(pty_manager=FakePtyManager())
    project = library.ensure_project(repo, name="Project")
    tree = git_ops.prepare(repo, "new_worktree", branch="agent/tab").folder
    opened = await registry.start(tree, [{"agent": "claude"}], project_id=project.id)
    assert opened.folder == tree
    assert opened.project_id == project.id


def test_routes_report_git_errors_with_a_code(repo: Path) -> None:
    from fastapi import HTTPException

    from jarvis.ui.web import agentic_ide_git_routes as routes

    made = routes.prepare_checkout(
        routes.PrepareRequest(folder=str(repo), mode="new_worktree", branch="agent/api")
    )
    assert made["branch"] == "agent/api" and made["created"]
    info = routes.inspect_folder(folder=made["folder"])
    assert info["is_worktree"] and info["branch"] == "agent/api"
    with pytest.raises(HTTPException) as exc:
        routes.push_branch(routes.FolderRequest(folder=str(repo)))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "no_remote"
