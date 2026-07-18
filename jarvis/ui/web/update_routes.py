"""In-app updater for a MANAGED install of Personal Jarvis.

An end user installs Personal Jarvis with the one-line installer, which clones
the public flagship repo into ``~/.personal-jarvis`` and runs the app from that
checkout. This module lets the running desktop app offer a one-click "Update
Now" button so the user never has to re-run the installer from a terminal:

* ``GET  /api/update/status`` — compares the running version against the latest
  published GitHub Release and reports whether an update is available (plus its
  release notes). It is **fail-open**: any network or parse error reports "no
  update" rather than erroring, so a flaky connection never breaks the UI.
* ``POST /api/update/apply``  — fetches and pins the exact target revision, then
  writes a pending-update manifest without changing the running checkout. The
  caller hits ``POST /api/settings/restart-app``; after the old process exits,
  the detached relauncher applies the revision and re-runs the full installer
  before it starts the new app.

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
``NO_WINDOW_CREATIONFLAGS`` (AP-1, no console flash under ``pythonw.exe``).
Dependency and desktop files are changed only by the detached relauncher after
the live process has released imported modules. On a headless VPS the fetch
works and the caller's restart step degrades honestly (``restart-app`` returns
503).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/update", tags=["update"])

# The ONE official public repo this updater will ever pull from. The managed
# guard verifies the installed checkout's ``origin`` resolves here before any
# ``git reset --hard`` runs, so a dev checkout or a fork can never be self-reset.
_OFFICIAL_REPO_SLUG = "PersonalJarvis/PersonalJarvis"
_RELEASES_LATEST_API = "https://api.github.com/repos/PersonalJarvis/PersonalJarvis/releases/latest"
# Written by install/installer.py into the checkout root. Its presence is one
# half of "this copy is safe to self-update".
_MARKER_NAME = ".jarvis-managed-install"
_PENDING_UPDATE_NAME = ".jarvis-update-pending.json"
_UPDATE_RESULT_NAME = ".jarvis-update-result.json"

_NETWORK_TIMEOUT_S = 6.0
_STATUS_CACHE_TTL_S = 1800.0  # 30 min — don't hit GitHub on every poll.
_STATUS_RETRY_S = 120.0  # after a failed network check, retry sooner than the TTL.
_RELEASE_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

# In-process cache of the last status result. The managed state is stable for a
# process lifetime; the network result is what the TTL protects.
_status_cache: dict[str, Any] | None = None
_status_cache_until: float = 0.0

# Last release metadata that was successfully fetched from GitHub, kept for the
# apply path. The unauthenticated releases API is rate-limited PER IP (60/h) —
# on carrier-grade NAT / DS-Lite connections that budget is shared with other
# households, so the refetch inside ``apply`` can 403 minutes after ``status``
# succeeded. Falling back to the last good answer keeps the one-click update
# working instead of failing with an opaque 502.
_last_good_release: dict[str, Any] | None = None

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


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
    except Exception:  # noqa: BLE001 - malformed versions are never newer
        return False


def _versions_equal(left: str, right: str) -> bool:
    """Compare release versions and fail closed on malformed metadata."""
    if not left or not right:
        return False
    try:
        from packaging.version import Version

        return Version(left) == Version(right)
    except ImportError:
        dotted = re.compile(r"^\d+(?:\.\d+){2,3}$")
        return bool(dotted.fullmatch(left) and left == right)
    except Exception:  # noqa: BLE001 - malformed versions never compare equal
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


async def _run(cmd: list[str], *, cwd: Path, timeout_s: float) -> tuple[int, str, str]:
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
            raw_out, raw_err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
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
    """GET the latest GitHub Release. Fail-open: any error returns ``None``.

    A successful answer is also remembered in ``_last_good_release`` so the
    apply path can survive a transient API failure (rate limit, blip) that
    happens between the status check and the button click.
    """
    global _last_good_release
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
        release_tag = str(data.get("tag_name") or "").strip()
        if not _RELEASE_TAG_RE.fullmatch(release_tag):
            return None
        release = {
            "version": release_tag.lstrip("vV"),
            "tag": release_tag,
            "notes": (data.get("body") or "").strip(),
            "published_at": data.get("published_at"),
            "release_url": data.get("html_url"),
        }
        _last_good_release = release
        return release
    except Exception as exc:  # noqa: BLE001 — fail-open on any network/parse error
        log.debug("update check: latest-release fetch failed: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Managed install profile + deferred transaction manifest
# --------------------------------------------------------------------------- #
InstallProfile = Literal["full", "headless"]


def _managed_install_profile(root: Path) -> InstallProfile:
    """Resolve the installer profile, including pre-profile marker fallback.

    New installers persist the decision in the managed marker. Older markers
    predate that field, so desktop sessions retain the advertised ``[full]``
    profile while a display-less Linux host keeps the torch-free base floor.
    """

    try:
        payload = json.loads((root / _MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        payload = {}
    if isinstance(payload, dict):
        profile = payload.get("profile")
        if profile in {"full", "headless"}:
            return profile
        desktop = payload.get("desktop")
        if isinstance(desktop, bool):
            return "full" if desktop else "headless"

    if sys.platform in {"win32", "darwin"}:
        return "full"
    if sys.platform.startswith("linux") and (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return "full"
    return "headless"


def _write_pending_update(
    root: Path,
    *,
    previous_revision: str,
    target_revision: str,
    profile: InstallProfile,
) -> None:
    """Atomically stage the post-exit update transaction for the relauncher."""

    path = root / _PENDING_UPDATE_NAME
    temp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "schema": 1,
        "previous_revision": previous_revision,
        "target_revision": target_revision,
        "profile": profile,
        "created_at": int(time.time()),
    }
    temp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)
    try:
        (root / _UPDATE_RESULT_NAME).unlink(missing_ok=True)
    except OSError:
        pass


def _read_pending_manifest(root: Path) -> dict[str, Any] | None:
    """Read + validate a staged-but-not-yet-installed update transaction.

    Mirrors the relauncher's strict validation (same file, same schema) without
    importing its private helper. Any doubt returns ``None``.
    """
    try:
        payload = json.loads((root / _PENDING_UPDATE_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        return None
    target = payload.get("target_revision")
    previous = payload.get("previous_revision")
    profile = payload.get("profile")
    if not isinstance(target, str) or not _REVISION_RE.fullmatch(target):
        return None
    if not isinstance(previous, str) or not _REVISION_RE.fullmatch(previous):
        return None
    if profile not in {"full", "headless"}:
        return None
    return payload


def _read_update_result(root: Path) -> dict[str, Any] | None:
    """The relauncher's verdict on the LAST finalized update, if any.

    ``ok: false`` means the target install failed after the restart and the
    checkout was reset back — without surfacing this, a rolled-back update is
    indistinguishable from "the button silently did nothing".
    """
    try:
        payload = json.loads((root / _UPDATE_RESULT_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        return None
    return {
        "ok": payload["ok"],
        "rolled_back": bool(payload.get("rolled_back", False)),
        "completed_at": payload.get("completed_at"),
    }


async def _pending_update_overlay(root: Path) -> dict[str, Any]:
    """Live (never cached) status fields: staged transaction + last verdict."""
    overlay: dict[str, Any] = {"pending_update": None, "last_result": None}
    manifest = _read_pending_manifest(root)
    if manifest is not None:
        overlay["pending_update"] = {
            "version": await _version_at_revision(root, str(manifest["target_revision"])),
            "target_revision": manifest["target_revision"],
        }
    overlay["last_result"] = _read_update_result(root)
    return overlay


async def _version_at_revision(root: Path, revision: str) -> str | None:
    """Read a target version without checking that target out over the live app."""

    for rel, pattern in (
        ("jarvis/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
        ("pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
    ):
        raw = await _git_output(["show", f"{revision}:{rel}"], cwd=root)
        if raw is None:
            continue
        match = re.search(pattern, raw, re.MULTILINE)
        if match:
            return match.group(1)
    return None


async def _staged_update_response(root: Path) -> dict[str, object] | None:
    """Re-offer an already-staged transaction when GitHub is unreachable.

    Only accepted when the staged target is still strictly newer than the
    running version (the manifest was written by a fully validated apply, so
    the revision itself is trusted). Returns ``None`` when nothing usable is
    staged — the caller then reports the network failure honestly.
    """
    manifest = _read_pending_manifest(root)
    if manifest is None:
        return None
    target = str(manifest["target_revision"])
    version = await _version_at_revision(root, target)
    if version is None or not _is_newer(version, _running_version()):
        return None
    profile = manifest["profile"]
    return {
        "ok": True,
        "prepared": True,
        "restart_required": True,
        "version": version,
        "release_tag": f"v{version}",
        "install_profile": profile,
        "deps_refreshed": False,
        "deps_pending": True,
        "deps_warning": None,
        "ui_bundle_pending": True,
        "desktop_integration_ok": None,
        "desktop_integration_pending": profile == "full",
        "desktop_integration_warning": None,
    }


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
        # The network part is cached; the staged-transaction fields are cheap
        # local reads and must always be live (an apply invalidates the cache,
        # but a relauncher result appears while the cache is warm).
        if _status_cache.get("managed"):
            root = await _resolve_managed_repo()
            if root is not None:
                return {**_status_cache, **(await _pending_update_overlay(root))}
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
        return {**result, **(await _pending_update_overlay(root))}

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
    return {**result, **(await _pending_update_overlay(root))}


@router.post("/apply", openapi_extra={"x-jarvis-dangerous": True})
async def update_apply() -> dict[str, object]:
    """Prepare the latest code for a managed install. Does NOT restart.

    The live checkout remains untouched. The caller then invokes
    ``POST /api/settings/restart-app``; its detached relauncher applies the
    pinned revision and completes installation after this process exits.
    """
    global _status_cache, _status_cache_until
    root = await _resolve_managed_repo()
    if root is None:
        raise HTTPException(
            status_code=403,
            detail="not a managed install — in-app update is disabled here",
        )

    latest = await _fetch_latest_release()
    if latest is None and _last_good_release is not None:
        # The status check knew the target minutes ago; a transient API failure
        # (shared-IP rate limit, blip) must not brick the one-click update. The
        # tag fetch + version-at-revision equality check below still verify the
        # actual bytes, so a stale answer can never install the wrong thing.
        log.info("update apply: live release check failed — using last good answer")
        latest = _last_good_release
    if latest is None:
        staged = await _staged_update_response(root)
        if staged is not None:
            # GitHub is unreachable but a validated transaction is already on
            # disk from an earlier click — restarting can finish it offline.
            return staged
        raise HTTPException(
            status_code=502,
            detail=(
                "could not reach GitHub to resolve the latest published release "
                "(offline or rate-limited) — try again in a few minutes"
            ),
        )
    release_version = str(latest.get("version") or "")
    release_tag = str(latest.get("tag") or "")
    if not _RELEASE_TAG_RE.fullmatch(release_tag):
        raise HTTPException(status_code=502, detail="latest release tag is invalid")
    if not _is_newer(release_version, _running_version()):
        raise HTTPException(status_code=409, detail="no newer published release exists")

    previous_revision = await _git_output(["rev-parse", "HEAD"], cwd=root)
    if not previous_revision:
        raise HTTPException(
            status_code=500,
            detail="could not identify the currently installed revision",
        )

    # Fetch the published tag, never the moving main branch. The update button
    # promises a specific GitHub Release; applying an unreleased main commit
    # would make the displayed version and installed bytes disagree.
    rc, _out, err = await _git(
        ["fetch", "--depth", "1", "origin", f"refs/tags/{release_tag}"],
        cwd=root,
        timeout_s=120.0,
    )
    if rc != 0:
        raise HTTPException(
            status_code=502, detail=f"git fetch failed: {err[:300] or 'unknown error'}"
        )

    target_revision = await _git_output(["rev-parse", "FETCH_HEAD^{commit}"], cwd=root)
    if not target_revision:
        raise HTTPException(
            status_code=500,
            detail="could not identify the fetched update revision",
        )

    new_version = await _version_at_revision(root, target_revision)
    if new_version is None or not _versions_equal(new_version, release_version):
        raise HTTPException(
            status_code=502,
            detail="published tag version does not match its release metadata",
        )

    profile = _managed_install_profile(root)
    try:
        _write_pending_update(
            root,
            previous_revision=previous_revision,
            target_revision=target_revision,
            profile=profile,
        )
    except OSError as exc:
        log.warning("Could not stage the pending update manifest: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="could not stage the update transaction",
        ) from exc

    # The next status poll must see the staged transaction immediately.
    _status_cache, _status_cache_until = None, 0.0

    return {
        "ok": True,
        "prepared": True,
        "restart_required": True,
        "version": new_version,
        "release_tag": release_tag,
        "install_profile": profile,
        "deps_refreshed": False,
        "deps_pending": True,
        "deps_warning": None,
        "ui_bundle_pending": True,
        "desktop_integration_ok": None,
        "desktop_integration_pending": profile == "full",
        "desktop_integration_warning": None,
    }
