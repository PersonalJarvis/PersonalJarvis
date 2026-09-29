"""Where a project folder can be opened besides the IDE itself.

The sidebar's project menu offers "Open on GitHub" only when the folder really
has a hosted remote, so the menu never shows an action that cannot work. This
module answers that question: the web address of the folder's ``origin``
remote, or None.

Only ``http(s)`` addresses ever come out. A remote written as SSH
(``git@github.com:owner/repo.git``) is rewritten to its web page, and embedded
credentials (``https://token@host/...``) are stripped, so neither a secret
nor an arbitrary scheme can reach the browser.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_GIT_TIMEOUT_S = 3.0
_SCP_LIKE = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w.-]+):(?P<path>[^/].*)$")


def web_url(remote: str) -> str | None:
    """The browser address of a git remote, or None when it has none."""
    remote = remote.strip()
    if not remote:
        return None
    if "://" not in remote:
        match = _SCP_LIKE.match(remote)
        if match is None:
            # A local path remote (``../other.git``) has no web page.
            return None
        host, path = match.group("host"), match.group("path")
    else:
        parts = urlsplit(remote)
        if parts.scheme not in {"http", "https", "ssh", "git"} or not parts.hostname:
            return None
        host, path = parts.hostname, parts.path.lstrip("/")
    path = path.removesuffix("/").removesuffix(".git")
    if not path:
        return None
    return urlunsplit(("https", host, f"/{path}", "", ""))


def host_label(url: str) -> str:
    """The name the menu uses for a remote's host: GitHub, GitLab, …"""
    host = (urlsplit(url).hostname or "").lower()
    for needle, label in (("github", "GitHub"), ("gitlab", "GitLab"), ("bitbucket", "Bitbucket")):
        if needle in host:
            return label
    return host or "the web"


def remote_web_url(folder: str | Path) -> str | None:
    """The web page of ``folder``'s ``origin`` remote, or None.

    None covers every "no" alike: git not installed, not a repository, no
    origin, a local-path remote, a slow network drive that hits the timeout.
    """
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [git, "-C", str(folder), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("Project links: no remote for {}: {}", folder, exc)
        return None
    if result.returncode != 0:
        return None
    return web_url(result.stdout)
