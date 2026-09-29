"""Which GitHub repository a workspace folder belongs to, and how to reach GitHub.

The IDE's Git tab asks the person ONCE per folder: "which of your GitHub
repositories is this?" — picked from a list of their own repositories. The
answer is remembered per folder (per repository checkout, so every worktree of
one repository shares it) in a small JSON file under the per-user data
directory, never in the repo and never in ``jarvis.toml``.

GitHub itself is reached over HTTPS with the credential the person already
has, in this order:

1. The app's own GitHub connection (Plugins → GitHub, one browser approval).
2. The GitHub CLI's login (``gh auth token``), for people who use ``gh``.

Nothing here ever asks for a pasted token. Error bodies from GitHub are logged
at debug level and never returned: every failure maps to one fixed sentence.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jarvis.core.http_pool import SyncHttpClientPool
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

GRAPHQL_URL = "https://api.github.com/graphql"
#: ``owner/name`` as GitHub allows it.
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
#: How long the repository list is reused (it changes rarely).
REPOS_TTL_S = 300.0
#: Most repositories listed (three pages of 100).
MAX_REPOS = 300

_POOL = SyncHttpClientPool(timeout_s=20.0)


class GitHubError(RuntimeError):
    """A GitHub call that failed, worded for the person; ``code`` for the UI."""

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ credential


@dataclass(slots=True)
class Credential:
    token: str
    #: ``app`` (the Plugins → GitHub connection) or ``gh`` (the GitHub CLI).
    source: str


def _app_token() -> str | None:
    try:
        from jarvis.marketplace.token_store import TokenStore

        tokens = TokenStore().load("github")
    except Exception as exc:  # keyring unavailable (headless box): try gh next
        logger.debug("GitHub link: app connection unreadable: {}", type(exc).__name__)
        return None
    if tokens is None or tokens.needs_reauth or not tokens.access:
        return None
    return tokens.access


def _gh_token() -> str | None:
    if shutil.which("gh") is None:
        return None
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1")
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            env=env,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("GitHub link: gh auth token failed: {}", type(exc).__name__)
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def credential() -> Credential | None:
    """The person's GitHub credential, or None when GitHub is not connected."""
    token = _app_token()
    if token:
        return Credential(token=token, source="app")
    token = _gh_token()
    if token:
        return Credential(token=token, source="gh")
    return None


# ------------------------------------------------------------------ GraphQL


def graphql(token: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """One GraphQL call; raises :class:`GitHubError` with a fixed sentence."""
    try:
        response = _POOL.client().post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "PersonalJarvis",
            },
        )
    except Exception as exc:  # network down, DNS, TLS: one sentence for all
        logger.debug("GitHub link: request failed: {}", type(exc).__name__)
        raise GitHubError("GitHub could not be reached.", code="unreachable") from exc
    if response.status_code == 401:
        raise GitHubError(
            "The GitHub connection has expired. Connect GitHub again.", code="not_connected"
        )
    if response.status_code in (403, 429):
        raise GitHubError(
            "GitHub's rate limit is reached; it resets within the hour.", code="rate_limited"
        )
    if response.status_code >= 400:
        logger.debug("GitHub link: HTTP {}", response.status_code)
        raise GitHubError("GitHub could not be reached.", code="unreachable")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubError("GitHub sent an answer that could not be read.") from exc
    if not isinstance(payload, dict):
        raise GitHubError("GitHub sent an answer that could not be read.")
    errors = payload.get("errors")
    if errors and not payload.get("data"):
        logger.debug("GitHub link: GraphQL errors: {}", str(errors)[:300])
        text = str(errors).lower()
        if "could not resolve to a repository" in text:
            raise GitHubError(
                "GitHub does not know this repository, or this account cannot see it.",
                code="repo_missing",
            )
        raise GitHubError("GitHub could not answer this request.")
    return payload


# ------------------------------------------------------------------ repositories

_REPOS_QUERY = """
query($after: String) {
  viewer {
    login
    repositories(first: 100, after: $after,
                 ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER],
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { nameWithOwner description isPrivate isFork url pushedAt }
    }
  }
}
"""


@dataclass(slots=True)
class RepoChoice:
    name: str
    description: str = ""
    private: bool = False
    fork: bool = False
    url: str = ""
    pushed_at: str = ""


_repos_lock = threading.Lock()
_repos_cache: dict[str, tuple[float, str, list[RepoChoice]]] = {}


def list_repositories(cred: Credential, *, refresh: bool = False) -> tuple[str, list[RepoChoice]]:
    """(login, the person's repositories newest-push first), cached for minutes."""
    key = f"{cred.source}:{hash(cred.token)}"
    with _repos_lock:
        cached = _repos_cache.get(key)
        if cached and not refresh and time.time() - cached[0] < REPOS_TTL_S:
            return cached[1], list(cached[2])
    login = ""
    repos: list[RepoChoice] = []
    after: str | None = None
    while len(repos) < MAX_REPOS:
        data = graphql(cred.token, _REPOS_QUERY, {"after": after}).get("data") or {}
        viewer = data.get("viewer") or {}
        login = str(viewer.get("login") or login)
        page = viewer.get("repositories") or {}
        for node in page.get("nodes") or []:
            if isinstance(node, dict) and node.get("nameWithOwner"):
                repos.append(
                    RepoChoice(
                        name=str(node["nameWithOwner"]),
                        description=str(node.get("description") or "")[:200],
                        private=bool(node.get("isPrivate")),
                        fork=bool(node.get("isFork")),
                        url=str(node.get("url") or ""),
                        pushed_at=str(node.get("pushedAt") or ""),
                    )
                )
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
    with _repos_lock:
        _repos_cache[key] = (time.time(), login, repos)
    return login, list(repos)


# ------------------------------------------------------------------ folder → repo


def _store_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agentic_ide" / "github_repos.json"


_store_lock = threading.Lock()


def folder_key(folder: str | Path) -> str:
    """The key a folder's choice is stored under: its main checkout when it is
    a git repository (so every worktree shares one answer), else the folder."""
    path = Path(folder).expanduser()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        common = result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):  # no git: the folder itself is the key
        common = ""
    base = Path(common).parent if common and Path(common).name == ".git" else path
    try:
        base = base.resolve()
    except OSError:  # unresolvable (network drive gone): keep the path as given
        pass
    return os.path.normcase(str(base))


def _load() -> dict[str, str]:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
    except FileNotFoundError:  # nothing picked yet on this machine
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("GitHub link: repository choices unreadable, starting empty: {}", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and REPO_RE.match(v)}


def bound_repository(folder: str | Path) -> str:
    """The ``owner/name`` chosen for this folder, or ``""``."""
    with _store_lock:
        return _load().get(folder_key(folder), "")


def bind_repository(folder: str | Path, repo: str) -> str:
    """Remember ``repo`` for this folder (``""`` forgets it). Returns the key."""
    repo = repo.strip()
    if repo and not REPO_RE.match(repo):
        raise ValueError("A repository is written as owner/name.")
    key = folder_key(folder)
    with _store_lock:
        choices = _load()
        if repo:
            choices[key] = repo
        else:
            choices.pop(key, None)
        target = _store_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(choices, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
    return key


_REMOTE_RE = re.compile(
    r"github\.com[:/]+(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)


def remote_repository(folder: str | Path) -> str:
    """The GitHub repository this folder's git remote points at, or ``""``.

    Only a suggestion for the picker — ``origin`` first, then any other remote.
    """
    path = Path(folder).expanduser()
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            cwd=str(path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):  # no git: nothing to suggest
        return ""
    found: list[tuple[int, str]] = []
    for line in result.stdout.splitlines() if result.returncode == 0 else []:
        parts = line.split()
        if len(parts) < 2:
            continue
        match = _REMOTE_RE.search(parts[1])
        if match:
            found.append((0 if parts[0] == "origin" else 1, f"{match['owner']}/{match['name']}"))
    found.sort(key=lambda item: item[0])
    return found[0][1] if found else ""


def repo_dicts(repos: list[RepoChoice]) -> list[dict[str, Any]]:
    return [asdict(repo) for repo in repos]
