"""A project's hosted remote, as the sidebar's "Open on GitHub" needs it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis.agentic_ide import project_links


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:owner/repo.git", "https://github.com/owner/repo"),
        ("https://github.com/owner/repo.git\n", "https://github.com/owner/repo"),
        ("https://x-token:secret@github.com/owner/repo", "https://github.com/owner/repo"),
        ("ssh://git@gitlab.com/group/sub/repo.git", "https://gitlab.com/group/sub/repo"),
        ("../local/mirror.git", None),
        ("file:///srv/repo.git", None),
        ("", None),
    ],
)
def test_remote_becomes_a_safe_web_address(remote: str, expected: str | None) -> None:
    assert project_links.web_url(remote) == expected


def test_host_label_names_the_forge() -> None:
    assert project_links.host_label("https://github.com/a/b") == "GitHub"
    assert project_links.host_label("https://gitlab.example.org/a/b") == "GitLab"
    assert project_links.host_label("https://code.example.org/a/b") == "code.example.org"


def test_a_folder_without_git_has_no_remote(tmp_path: Path) -> None:
    assert project_links.remote_web_url(tmp_path) is None


def test_reads_the_origin_of_a_real_repository(tmp_path: Path) -> None:
    git = pytest.importorskip("shutil").which("git")
    if git is None:
        pytest.skip("git is not installed")
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [git, "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:me/app.git"],
        check=True,
    )
    assert project_links.remote_web_url(tmp_path) == "https://github.com/me/app"
