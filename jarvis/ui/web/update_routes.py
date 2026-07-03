"""In-app updater for a MANAGED install of Personal Jarvis.

An end user installs Personal Jarvis with the one-line installer, which clones
the public flagship repo into ``~/.personal-jarvis`` and runs the app from that
checkout. This module lets the running desktop app offer a one-click "Update
Now" button so the user never has to re-run the installer from a terminal:

* ``GET  /api/update/status`` — compares the running version against the latest
  published GitHub Release and reports whether an update is available (plus its
  release notes). It is **fail-open**: any network or parse error reports "no
  update" rather than erroring, so a flaky connection never breaks the UI.
* ``POST /api/update/apply``  — pulls the new code (the same ``git fetch`` +
  ``git reset --hard origin/main`` the installer uses), refreshes dependencies
  only if the lockfile changed, and reports ``restart_required``. It does NOT
  restart — the caller then hits the existing ``POST /api/settings/restart-app``
  which already owns the mission-guard + cross-platform relauncher.

Safety-critical guard (the single most important thing here):
``git reset --hard`` destroys uncommitted local changes. That is fine for an
end user's managed checkout but catastrophic in a maintainer's dev tree or any
manual clone. So the updater is active **only** on a *managed install*, proven
by BOTH:
  1. a marker file (``.jarvis-managed-install``) that the installer writes into
     the checkout root, and
  2. the checkout's ``origin`` remote resolving to the official public repo.
If either check fails, ``status`` reports ``managed: false`` (the button never
renders) and ``apply`` refuses with HTTP 403. This makes the dev tree and any
fork structurally immune to the self-update.

Cross-platform: git runs via ``asyncio`` subprocess with
``NO_WINDOW_CREATIONFLAGS`` (AP-1, no console flash under ``pythonw.exe``); the
dependency refresh calls the checkout's venv python; nothing here is Windows-
specific. On a headless VPS the pull works and the caller's restart step
degrades honestly (``restart-app`` returns 503).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/update", tags=["update"])

# The ONE official public repo this updater will ever pull from. The managed
# guard verifies the installed checkout's ``origin`` resolves here before any
# ``git reset --hard`` runs, so a dev checkout or a fork can never be self-reset.
_OFFICIAL_REPO_SLUG = "PersonalJarvis/PersonalJarvis"
_RELEASES_LATEST_API = (
    "https://api.github.com/repos/PersonalJarvis/PersonalJarvis/releases/latest"
)
# Written by install/installer.py into the checkout root. Its presence is one
# half of "this copy is safe to self-update".
_MARKER_NAME = ".jarvis-managed-install"

_NETWORK_TIMEOUT_S = 6.0
_STATUS_CACHE_TTL_S = 1800.0  # 30 min — don't hit GitHub on every poll.
_STATUS_RETRY_S = 120.0  # after a failed network check, retry sooner than the TTL.

# In-process cache of the last status result. The managed state is stable for a
# process lifetime; the network result is what the TTL protects.
_status_cache: dict[str, Any] | None = None
_status_cache_until: float = 0.0


# --------------------------------------------------------------------------- #
# Version helpers
# --------------------------------------------------------------------------- #
def _running_version() -> str:
    """The version of the CURRENTLY running process (in-memory module)."""
    try:
        import jarvis

        return str(jarvis.__version__)
    except (ImportError, AttributeError):
        return "unknown"


def _version_on_disk(root: Path) -> str | None:
    """Parse the version from the freshly-pulled files on disk.

    After ``apply`` the checkout is new but the imported ``jarvis`` module in
    memory is still the OLD version, so the post-update version must be read
    from disk, not from ``jarvis.__version__``.
    """
    for rel, pattern in (
        (Path("jarvis") / "__init__.py", r'__version__\s*=\s*"([^"]+)"'),
        (Path("pyproject.toml"), r'^version\s*=\s*"([^"]+)"'),
    ):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            return m.group(1)
    return None


def _naive_version_gt(a: str, b: str) -> bool:
    """Fallback dotted-int compare when ``packaging`` is unavailable."""

    def parts(v: str) -> list[int]:
        out: list[int] = []
        for chunk in v.split("."):
            m = re.match(r"\d+", chunk)
            out.append(int(m.group()) if m else 0)
        return out

    try:
        return parts(a) > parts(b)
    except Exception:  # noqa: BLE001
        return False


def _is_newer(latest: str, current: str) -> bool:
    """True iff ``latest`` is a strictly newer version than ``current``.

    Fail-closed on an unknown running version: if we can't tell what we're on,
    we do NOT offer an update (never blind-update).
    """
    if not latest or current in ("", "unknown"):
        return False
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except ImportError:
        return _naive_version_gt(latest, current)
    except Exception:  # noqa: BLE001 — InvalidVersion → not newer
        return False


# --------------------------------------------------------------------------- #
# Subprocess helpers (git + pip), NO_WINDOW_CREATIONFLAGS + clean teardown
# --------------------------------------------------------------------------- #
async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Guarantee a dead subprocess: terminate -> wait 50 ms -> kill -> wait."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.05)
            return
        except (TimeoutError, asyncio.CancelledError):
            pass
        proc.kill()
        await proc.wait()
    except (ProcessLookupError, OSError):
        pass


async def _run(
    cmd: list[str], *, cwd: Path, timeout_s: float
) -> tuple[int, str, str]:
    """Run ``cmd`` in ``cwd``. Returns ``(returncode, stdout, stderr)``.

    ``returncode == -1`` signals the process could not run at all (missing
    binary) or timed out — ``stderr`` then carries a human reason. Cleans up the
    child on timeout/cancel so no zombie is left behind.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (FileNotFoundError, OSError, NotImplementedError) as exc:
        return -1, "", f"could not run {cmd[0]}: {exc}"

    try:
        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except TimeoutError:
            await _terminate(proc)
            return -1, "", f"{cmd[0]} timed out after {timeout_s:.0f}s"
        except asyncio.CancelledError:
            await _terminate(proc)
            raise
    finally:
        if proc.returncode is None:
            await _terminate(proc)

    out = raw_out.decode(errors="replace").strip()
    err = raw_err.decode(errors="replace").strip()
    return proc.returncode if proc.returncode is not None else -1, out, err


async def _git(args: list[str], *, cwd: Path, timeout_s: float = 60.0) -> tuple[int, str, str]:
    return await _run(["git", *args], cwd=cwd, timeout_s=timeout_s)


async def _git_output(args: list[str], *, cwd: Path, timeout_s: float = 15.0) -> str | None:
    rc, out, _err = await _git(args, cwd=cwd, timeout_s=timeout_s)
    return out if rc == 0 else None


# --------------------------------------------------------------------------- #
# Managed-install guard
# --------------------------------------------------------------------------- #
def _repo_root() -> Path | None:
    """The checkout root, derived unambiguously from the running package."""
    try:
        import jarvis

        # .../repo/jarvis/__init__.py -> .../repo
        return Path(jarvis.__file__).resolve().parent.parent
    except Exception:  # noqa: BLE001 — never fatal
        return None


def _normalize_remote(url: str) -> str:
    """Reduce a git remote URL to a comparable ``.../owner/name`` tail.

    Handles https (``https://github.com/Owner/Name.git``), ssh
    (``git@github.com:Owner/Name.git``), and local file paths on either slash
    style — so the comparison is robust across platforms and remote forms.
    """
    tail = url.strip()
    if tail.endswith(".git"):
        tail = tail[:-4]
    tail = tail.replace("\\", "/").replace(":", "/").rstrip("/")
    return tail


def _remote_is_official(url: str) -> bool:
    """True only if ``url`` resolves to exactly the official ``owner/name``.

    Must MATCH the last two path segments, not merely contain the slug — so a
    look-alike fork (``.../PersonalJarvis/PersonalJarvisEvil``) is rejected.
    """
    norm = _normalize_remote(url).lower()
    slug = _OFFICIAL_REPO_SLUG.lower()
    return norm == slug or norm.endswith("/" + slug)


async def _resolve_managed_repo() -> Path | None:
    """Return the checkout root IFF this is a managed, self-updatable install.

    Requires BOTH the installer marker AND an ``origin`` that resolves to the
    official public repo. Any doubt returns ``None`` (fail-closed).
    """
    root = _repo_root()
    if root is None or not (root / _MARKER_NAME).exists():
        return None
    if not (root / ".git").exists():
        return None
    origin = await _git_output(["remote", "get-url", "origin"], cwd=root)
    if origin is None or not _remote_is_official(origin):
        return None
    return root


# --------------------------------------------------------------------------- #
# GitHub release check (fail-open)
# --------------------------------------------------------------------------- #
async def _fetch_latest_release() -> dict[str, Any] | None:
    """GET the latest GitHub Release. Fail-open: any error returns ``None``."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_NETWORK_TIMEOUT_S) as client:
            resp = await client.get(
                _RELEASES_LATEST_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "PersonalJarvis-Updater",
                },
            )
        if resp.status_code != 200:
            log.debug("update check: releases/latest HTTP %s", resp.status_code)
            return None
        data = resp.json()
        tag = str(data.get("tag_name") or "").strip().lstrip("vV")
        if not tag:
            return None
        return {
            "version": tag,
            "notes": (data.get("body") or "").strip(),
            "published_at": data.get("published_at"),
            "release_url": data.get("html_url"),
        }
    except Exception as exc:  # noqa: BLE001 — fail-open on any network/parse error
        log.debug("update check: latest-release fetch failed: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Dependency refresh (best-effort)
# --------------------------------------------------------------------------- #
def _venv_python(root: Path) -> Path:
    """The python inside the checkout's venv, falling back to the running one."""
    if sys.platform == "win32":
        cand = root / ".venv" / "Scripts" / "python.exe"
    else:
        cand = root / ".venv" / "bin" / "python"
    return cand if cand.exists() else Path(sys.executable)


def _hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


async def _refresh_dependencies(root: Path) -> tuple[bool, str]:
    """Best-effort ``pip install -r requirements.txt`` on the venv python.

    Only called when the lockfile actually changed. Never raises; a failure is
    reported so the UI can warn honestly (the pull still succeeded).
    """
    req = root / "requirements.txt"
    if not req.exists():
        return True, ""
    py = _venv_python(root)
    rc, out, err = await _run(
        [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)],
        cwd=root,
        timeout_s=600.0,
    )
    if rc != 0:
        return False, (err or out or "pip failed")[-400:]
    return True, ""


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/status")
async def update_status(force: bool = False) -> dict[str, object]:
    """Report whether a newer published version is available.

    ``force=true`` bypasses the in-process cache (a manual "check now").
    """
    global _status_cache, _status_cache_until
    now = time.monotonic()
    if not force and _status_cache is not None and now < _status_cache_until:
        return _status_cache

    current = _running_version()
    root = await _resolve_managed_repo()

    if root is None:
        result: dict[str, object] = {
            "managed": False,
            "current": current,
            "latest": None,
            "update_available": False,
            "notes": None,
            "published_at": None,
        }
        _status_cache, _status_cache_until = result, now + _STATUS_CACHE_TTL_S
        return result

    latest = await _fetch_latest_release()
    if latest is None:
        # Fail-open: we are managed but couldn't reach GitHub. Offer no update,
        # and retry sooner than the full TTL.
        result = {
            "managed": True,
            "current": current,
            "latest": None,
            "update_available": False,
            "notes": None,
            "published_at": None,
            "check_failed": True,
        }
        _status_cache, _status_cache_until = result, now + _STATUS_RETRY_S
        return result

    available = _is_newer(str(latest["version"]), current)
    result = {
        "managed": True,
        "current": current,
        "latest": latest["version"],
        "update_available": available,
        "notes": latest["notes"] if available else None,
        "published_at": latest["published_at"],
        "release_url": latest.get("release_url"),
    }
    _status_cache, _status_cache_until = result, now + _STATUS_CACHE_TTL_S
    return result


@router.post("/apply")
async def update_apply() -> dict[str, object]:
    """Pull the latest code for a managed install. Does NOT restart.

    Returns ``{ok, restart_required, version, deps_refreshed, deps_warning}``.
    The caller applies the update by then calling
    ``POST /api/settings/restart-app``.
    """
    root = await _resolve_managed_repo()
    if root is None:
        raise HTTPException(
            status_code=403,
            detail="not a managed install — in-app update is disabled here",
        )

    # Snapshot the lockfile so we can tell whether deps need a refresh.
    req_before = _hash_file(root / "requirements.txt")

    rc, _out, err = await _git(
        ["fetch", "--depth", "1", "origin", "main"], cwd=root, timeout_s=120.0
    )
    if rc != 0:
        raise HTTPException(
            status_code=502, detail=f"git fetch failed: {err[:300] or 'unknown error'}"
        )
    rc, _out, err = await _git(["reset", "--hard", "origin/main"], cwd=root, timeout_s=60.0)
    if rc != 0:
        raise HTTPException(
            status_code=500, detail=f"git reset failed: {err[:300] or 'unknown error'}"
        )

    # The new code refreshes the UI (prebuilt dist/ ships with the pull) and the
    # Python source (editable install loads it on restart). Refresh deps only if
    # the lockfile actually moved.
    deps_refreshed = False
    deps_warning: str | None = None
    req_after = _hash_file(root / "requirements.txt")
    if req_before != req_after:
        deps_refreshed = True
        ok, msg = await _refresh_dependencies(root)
        if not ok:
            deps_warning = msg

    new_version = _version_on_disk(root) or _running_version()
    return {
        "ok": True,
        "restart_required": True,
        "version": new_version,
        "deps_refreshed": deps_refreshed,
        "deps_warning": deps_warning,
    }
