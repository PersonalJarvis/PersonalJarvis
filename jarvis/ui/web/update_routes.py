"""In-app updater — one "Update Now" button over two very different installs.

Personal Jarvis reaches a machine in one of two supported ways, and each one
updates itself differently:

* **frozen** — the visitor downloaded a native installer from the website
  (``Setup.exe`` / ``.dmg`` / ``.AppImage``). There is no git checkout here. The
  update is the NEXT installer asset on the GitHub Release: downloaded, proven
  against that same release's ``installers-SHA256SUMS.txt``, then handed to the
  platform's own upgrade mechanism (``jarvis/core/installer_update.py``).
  ``jarvis.core.frozen.is_frozen()`` is the ONLY probe that selects this path,
  so a dev tree and a managed checkout are structurally unaffected by it.
* **managed** — the one-line installer's git checkout under
  ``~/.personal-jarvis``. Updates are a ``git fetch`` of the published tag plus
  a reinstall; everything below describes that path.

Anything else (a maintainer checkout, a fork, a plain ``pip install``) is
``dev``: ``status`` reports ``managed: false``, the button never renders, and
``apply`` refuses.

The managed path
----------------
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
import platform as platform_module
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from jarvis.core.branding import (
    MANAGED_INSTALL_MARKER,
    OFFICIAL_RELEASES_LATEST_API_URL,
    OFFICIAL_REPO_SLUG,
    UPDATER_USER_AGENT,
)
from jarvis.core.frozen import is_frozen
from jarvis.core.installer_update import (
    CHECKSUMS_ASSET_NAME,
    InstallerUpdateError,
    apply_installer,
    download_and_verify,
    installer_asset_name,
    select_asset,
)
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/update", tags=["update"])

# The ONE official public repo this updater will ever pull from. The managed
# guard verifies the installed checkout's ``origin`` resolves here before any
# ``git reset --hard`` runs, so a dev checkout or a fork can never be self-reset.
_OFFICIAL_REPO_SLUG = OFFICIAL_REPO_SLUG
_RELEASES_LATEST_API = OFFICIAL_RELEASES_LATEST_API_URL
# Written by install/installer.py into the checkout root. Its presence is one
# half of "this copy is safe to self-update".
_MARKER_NAME = MANAGED_INSTALL_MARKER
_PENDING_UPDATE_NAME = ".jarvis-update-pending.json"
_UPDATE_RESULT_NAME = ".jarvis-update-result.json"

_NETWORK_TIMEOUT_S = 6.0
_STATUS_CACHE_TTL_S = 1800.0  # 30 min — don't hit GitHub on every poll.
_STATUS_RETRY_S = 120.0  # after a failed network check, retry sooner than the TTL.
_RELEASE_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

# In-process cache of the last status result. The managed state is stable for a
# process lifetime; the network result is what the TTL protects. The resolved
# managed root is cached alongside so a cache hit (every focus-triggered poll)
# costs only two tiny local file reads — not a git subprocess.
_status_cache: dict[str, Any] | None = None
_status_cache_until: float = 0.0
_status_cache_root: Path | None = None

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
        # A source checkout without __version__ is normal; the caller falls
        # back to reading the version files.
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
            # Not "file absent" alone — an unreadable version file makes the
            # installed version look older than it is, which shows up as an
            # update that keeps re-offering itself. Worth a line before moving on.
            log.debug("[update] version file %s unreadable; trying the next", rel)
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
    except Exception:  # noqa: BLE001 - unparseable version is never "newer"
        # Fail closed: an update is offered only when we can actually prove the
        # remote version is higher. Never blind-update on a parse failure.
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
        # `packaging` is not in the torch-free base install on every host; the
        # dotted-int compare below covers the SemVer tags releases actually use.
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
        # Same as _is_newer: no `packaging` on a minimal install, so fall back
        # to an exact match on a strict dotted shape.
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
            # It ignored SIGTERM within the grace period; escalate to kill below.
            pass
        proc.kill()
        await proc.wait()
    except (ProcessLookupError, OSError):
        # The child is already gone — which is exactly what this function is
        # for. Nothing to report.
        pass


# git writes its progress counters to stderr and separates the redraws with a
# carriage return, so "one update" is a CR- or LF-delimited segment, not a line.
_PROGRESS_SEGMENT_RE = re.compile(rb"[\r\n]")


async def _pump(stream: asyncio.StreamReader, on_line: Callable[[str], None] | None) -> bytes:
    """Read ``stream`` to EOF, handing each finished segment to ``on_line``.

    Returns everything that was read, so the caller still gets the complete
    output it would have gotten from ``communicate()``.
    """
    chunks: list[bytes] = []
    pending = b""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if on_line is None:
            continue
        segments = _PROGRESS_SEGMENT_RE.split(pending + chunk)
        pending = segments.pop()
        for segment in segments:
            text = segment.decode(errors="replace").strip()
            if text:
                on_line(text)
    return b"".join(chunks)


async def _run(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_s: float,
    on_stderr_line: Callable[[str], None] | None = None,
) -> tuple[int, str, str]:
    """Run ``cmd`` in ``cwd``. Returns ``(returncode, stdout, stderr)``.

    ``returncode == -1`` signals the process could not run at all (missing
    binary) or timed out — ``stderr`` then carries a human reason. Cleans up the
    child on timeout/cancel so no zombie is left behind.

    ``on_stderr_line`` taps stderr as it arrives instead of only at the end,
    which is the only way to read git's progress counters while the fetch is
    still running. It must not raise; :func:`_git_progress_tap` wraps the one
    caller that uses it.
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
        # Not swallowed: the reason travels back to the caller in stderr and
        # ends up in the update status the user sees.
        return -1, "", f"could not run {cmd[0]}: {exc}"

    async def collect() -> tuple[bytes, bytes]:
        # Both pipes must be drained concurrently: a child that fills one while
        # we read the other deadlocks, which is exactly what a chatty
        # ``git fetch --progress`` would do.
        assert proc.stdout is not None and proc.stderr is not None
        raw_out, raw_err = await asyncio.gather(
            _pump(proc.stdout, None), _pump(proc.stderr, on_stderr_line)
        )
        await proc.wait()
        return raw_out, raw_err

    try:
        try:
            raw_out, raw_err = await asyncio.wait_for(collect(), timeout=timeout_s)
        except TimeoutError:
            # Same contract: the timeout is reported through stderr, and the
            # child is reaped first so no zombie survives the request.
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


async def _git(
    args: list[str],
    *,
    cwd: Path,
    timeout_s: float = 60.0,
    on_stderr_line: Callable[[str], None] | None = None,
) -> tuple[int, str, str]:
    return await _run(["git", *args], cwd=cwd, timeout_s=timeout_s, on_stderr_line=on_stderr_line)


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
    except Exception:  # noqa: BLE001 - an unlocatable checkout is not updatable
        # Returning None makes the managed-install guard fail closed: no root,
        # no update. Silent on purpose — this runs on every status poll, and a
        # namespace package without __file__ is a legitimate deployment shape.
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
                    "User-Agent": UPDATER_USER_AGENT,
                },
            )
        if resp.status_code != 200:
            log.debug("update check: releases/latest HTTP %s", resp.status_code)
            return None
        data = resp.json()
        release_tag = str(data.get("tag_name") or "").strip()
        if not _RELEASE_TAG_RE.fullmatch(release_tag):
            return None
        raw_assets = data.get("assets")
        release = {
            "version": release_tag.lstrip("vV"),
            "tag": release_tag,
            "notes": (data.get("body") or "").strip(),
            "published_at": data.get("published_at"),
            "release_url": data.get("html_url"),
            # Only the frozen path reads this; the managed path resolves its
            # target from the git tag and ignores the attachments entirely.
            "assets": raw_assets if isinstance(raw_assets, list) else [],
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
        # A source checkout has no install marker; the defaults below apply.
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
        # A result file we failed to clear is read again on the next boot and
        # reports the PREVIOUS run's outcome as if it were this one's.
        log.warning(
            "[update] could not clear %s; a stale result may be reported next boot",
            _UPDATE_RESULT_NAME,
        )


def _read_pending_manifest(root: Path) -> dict[str, Any] | None:
    """Read + validate a staged-but-not-yet-installed update transaction.

    Mirrors the relauncher's strict validation (same file, same schema) without
    importing its private helper. Any doubt returns ``None``.
    """
    try:
        payload = json.loads((root / _PENDING_UPDATE_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        # No pending update is the normal case, and a half-written marker is
        # indistinguishable from it here; either way there is nothing to resume.
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
        # Absent on every boot that did not just apply an update — the common
        # path, not a failure.
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        return None
    return {
        "ok": payload["ok"],
        "rolled_back": bool(payload.get("rolled_back", False)),
        "completed_at": payload.get("completed_at"),
    }


async def _fresh_staged_manifest(root: Path) -> tuple[dict[str, Any], str] | None:
    """The staged manifest + its target version, IFF still worth finishing.

    "Worth finishing" means the target is strictly newer than the RUNNING
    version — the same fail-closed rule the apply route enforces. A manifest
    left behind by a crashed finalize (the checkout already reset to the
    target) or by an already-installed update must never be re-offered, in
    the status overlay any more than in the apply fallback.
    """
    manifest = _read_pending_manifest(root)
    if manifest is None:
        return None
    version = await _version_at_revision(root, str(manifest["target_revision"]))
    if version is None or not _is_newer(version, _running_version()):
        return None
    return manifest, version


async def _pending_update_overlay(root: Path) -> dict[str, Any]:
    """Live (never cached) status fields: staged transaction + last verdict."""
    overlay: dict[str, Any] = {"pending_update": None, "last_result": None}
    staged = await _fresh_staged_manifest(root)
    if staged is not None:
        manifest, version = staged
        overlay["pending_update"] = {
            "version": version,
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
    staged = await _fresh_staged_manifest(root)
    if staged is None:
        return None
    manifest, version = staged
    profile = manifest["profile"]
    return {
        "ok": True,
        "prepared": True,
        "restart_required": True,
        "kind": INSTALL_KIND_MANAGED,
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
# Frozen install (native installer) — download, verify, hand over
# --------------------------------------------------------------------------- #
INSTALL_KIND_FROZEN = "frozen"
INSTALL_KIND_MANAGED = "managed"
INSTALL_KIND_DEV = "dev"


# --------------------------------------------------------------------------- #
# Live progress of the one in-flight update
# --------------------------------------------------------------------------- #
# ``apply`` is a single long request (a few hundred MB on a frozen install), so
# the UI cannot learn anything from its return value until it is over. This
# tracker is what ``GET /api/update/progress`` publishes while that request is
# still running, turning "Updating…" into "Updating 70%".
#
# The percentages are a PHASE MODEL, never a timer. Each phase owns a fixed
# window, and inside a window the number moves only on evidence we actually
# have: bytes off the socket on a frozen install, git's own object counters on
# a managed one. A phase with no measurable inside (writing the transaction
# manifest) simply lands on its window's end when it completes. That makes a
# jump possible, but never a lie — which is the right trade for a bar the user
# is deciding "is this stuck?" from.
PHASE_IDLE = "idle"
PHASE_RESOLVING = "resolving"
PHASE_DOWNLOADING = "downloading"
PHASE_VERIFYING = "verifying"
PHASE_INSTALLING = "installing"
PHASE_READY = "ready"
PHASE_FAILED = "failed"

# ``{install kind: {phase: (window start %, window end %)}}``. The two kinds do
# very different work, so they weight the phases differently: a frozen install
# is dominated by the download, a managed one by the git fetch.
_PHASE_SPANS: dict[str, dict[str, tuple[int, int]]] = {
    INSTALL_KIND_FROZEN: {
        PHASE_RESOLVING: (0, 6),
        PHASE_DOWNLOADING: (6, 86),
        PHASE_VERIFYING: (86, 94),
        PHASE_INSTALLING: (94, 100),
    },
    INSTALL_KIND_MANAGED: {
        PHASE_RESOLVING: (0, 10),
        PHASE_DOWNLOADING: (10, 78),
        PHASE_VERIFYING: (78, 92),
        PHASE_INSTALLING: (92, 100),
    },
}


class _UpdateProgress:
    """Mutable snapshot of the update currently being applied.

    One instance per process: only one update may run at a time (the apply
    route rejects a second with 409), so a single slot is the whole model.
    Every method is total — a tracker that raised would take the update down
    with it, and progress is cosmetic.
    """

    def __init__(self) -> None:
        self.kind: str = INSTALL_KIND_DEV
        self.phase: str = PHASE_IDLE
        self.percent: int = 0
        self.detail: str | None = None
        self.version: str | None = None
        self.error: str | None = None
        self.restart_required: bool = False
        self.started_at: float | None = None
        self.updated_at: float | None = None

    # -- lifecycle ---------------------------------------------------------- #
    def begin(self, kind: str, *, version: str | None = None) -> None:
        """Start a run. The ONLY point at which the percentage may go down."""
        self.kind = kind
        self.phase = PHASE_RESOLVING
        self.percent = 0
        self.detail = None
        self.version = version
        self.error = None
        self.restart_required = False
        self.started_at = self.updated_at = time.time()

    def enter(self, phase: str, *, detail: str | None = None) -> None:
        """Move to ``phase``, landing on the start of its window."""
        self.advance(phase, 0.0, detail=detail)

    def advance(self, phase: str, fraction: float, *, detail: str | None = None) -> None:
        """Report being ``fraction`` (0..1) through ``phase``.

        Monotonic on purpose: git reports "Receiving objects" and "Resolving
        deltas" as two separate 0→100 % counters, and a bar that rewinds reads
        as a restart to anyone watching it.
        """
        low, high = _PHASE_SPANS.get(self.kind, {}).get(phase, (self.percent, self.percent))
        clamped = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
        self.phase = phase
        self.percent = max(self.percent, int(low + (high - low) * clamped))
        if detail is not None:
            self.detail = detail
        self.updated_at = time.time()

    def finish(self, *, version: str | None, restart_required: bool) -> None:
        """The server's work is done; what remains is the restart."""
        self.phase = PHASE_READY
        self.percent = 100
        self.version = version or self.version
        self.restart_required = restart_required
        self.error = None
        self.updated_at = time.time()

    def fail(self, message: str) -> None:
        """Stop, keeping the percentage where it died so the UI can say where."""
        self.phase = PHASE_FAILED
        self.error = message[:300]
        self.updated_at = time.time()

    # -- readers ------------------------------------------------------------ #
    def snapshot(self) -> dict[str, object]:
        """The shape ``GET /api/update/progress`` returns."""
        return {
            "active": self.phase not in (PHASE_IDLE, PHASE_READY, PHASE_FAILED),
            "phase": self.phase,
            "percent": self.percent,
            "detail": self.detail,
            "version": self.version,
            "kind": self.kind,
            "error": self.error,
            "restart_required": self.restart_required,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }


_progress = _UpdateProgress()


def _human_bytes(count: int) -> str:
    """``134217728`` -> ``"128.0 MB"``.

    Short on purpose: this string sits inside a top-bar pill, where fitting
    matters far more than precision.
    """
    step = 1024.0
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < step or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} GB"


# Serialises applies. A second click while the first download runs would fight
# over the same temp directory and scramble the percentage; the route answers
# 409 instead of queueing, because the user is already getting what they asked
# for.
_apply_lock = asyncio.Lock()

# git's own progress lines on stderr: "Receiving objects:  43% (430/1000)".
# Receiving is where the bytes are, so it owns most of the fetch window and
# delta resolution finishes it off.
_GIT_PROGRESS_RE = re.compile(r"(Receiving objects|Resolving deltas):\s+(\d+)%")
_GIT_PROGRESS_WEIGHTS = {"Receiving objects": (0.0, 0.85), "Resolving deltas": (0.85, 1.0)}


def _on_git_progress(line: str) -> None:
    """Translate one git stderr line into a fetch-window fraction."""
    match = _GIT_PROGRESS_RE.search(line)
    if match is None:
        return
    low, high = _GIT_PROGRESS_WEIGHTS[match.group(1)]
    share = int(match.group(2)) / 100.0
    _progress.advance(PHASE_DOWNLOADING, low + (high - low) * share)


def _frozen_asset_name() -> str | None:
    """The installer asset this machine installs, or ``None`` if there is none."""
    return installer_asset_name(sys.platform, platform_module.machine())


def _frozen_release_assets(
    release: dict[str, Any], asset_name: str
) -> tuple[Any | None, Any | None]:
    """``(installer asset, checksum manifest asset)`` from a release payload."""
    assets = release.get("assets") or []
    if not isinstance(assets, list):
        return None, None
    return select_asset(assets, asset_name), select_asset(assets, CHECKSUMS_ASSET_NAME)


async def _frozen_status(current: str) -> dict[str, object]:
    """Status for a natively installed app. Fail-OPEN, exactly like the managed one.

    ``managed`` stays the field the UI gates the button on — it means "this
    install can update itself", which a frozen install can. ``kind`` says HOW.

    An update is offered only when the release actually carries BOTH this
    platform's installer and the checksum manifest, because without either one
    ``apply`` would have to refuse: offering a button that cannot work is worse
    than staying quiet.
    """
    result: dict[str, object] = {
        "managed": True,
        "kind": INSTALL_KIND_FROZEN,
        "current": current,
        "latest": None,
        "update_available": False,
        "notes": None,
        "published_at": None,
        "asset": None,
        # Frozen installs have no staged git transaction and no relauncher
        # verdict; the fields stay present so the UI reads one shape.
        "pending_update": None,
        "last_result": None,
    }

    asset_name = _frozen_asset_name()
    if asset_name is None:
        # No installer is published for this OS/CPU pair (a Windows ARM64 box,
        # a Linux aarch64 box). Never offer what cannot be installed.
        log.info(
            "[update] no installer asset exists for %s/%s",
            sys.platform,
            platform_module.machine(),
        )
        result["managed"] = False
        result["unsupported_platform"] = True
        return result

    latest = await _fetch_latest_release()
    if latest is None:
        result["check_failed"] = True
        return result

    version = str(latest.get("version") or "")
    asset, checksums = _frozen_release_assets(latest, asset_name)
    result["latest"] = version or None
    result["published_at"] = latest.get("published_at")
    result["release_url"] = latest.get("release_url")

    if asset is None or checksums is None:
        # A release without installers is a code-only release (or one whose
        # installer job failed). Report honestly instead of half-offering.
        log.info(
            "[update] release %s carries no %s / %s — not offering a frozen update",
            version or "?",
            asset_name,
            CHECKSUMS_ASSET_NAME,
        )
        return result

    result["asset"] = asset.as_dict()
    if _is_newer(version, current):
        result["update_available"] = True
        result["notes"] = latest.get("notes")
    return result


async def _apply_frozen() -> dict[str, object]:
    """Download, verify and install the native installer for this machine.

    Fail-CLOSED at every step: an unresolvable release, a missing asset, a
    missing or mismatching SHA-256 all raise before anything is executed. The
    running app keeps working on the old version in every failure case.
    """
    _progress.begin(INSTALL_KIND_FROZEN)
    current = _running_version()
    asset_name = _frozen_asset_name()
    if asset_name is None:
        raise HTTPException(
            status_code=501,
            detail=(
                f"no Personal Jarvis installer is published for "
                f"{sys.platform}/{platform_module.machine()}"
            ),
        )

    latest = await _fetch_latest_release()
    if latest is None and _last_good_release is not None:
        # Same reasoning as the managed path: the unauthenticated releases API
        # is rate-limited per IP, so a blip between status and click must not
        # brick the button. The SHA-256 check below still proves the bytes.
        log.info("[update] live release check failed — using the last good answer")
        latest = _last_good_release
    if latest is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "could not reach GitHub to resolve the latest published release "
                "(offline or rate-limited) — try again in a few minutes"
            ),
        )

    release_tag = str(latest.get("tag") or "")
    release_version = str(latest.get("version") or "")
    if not _RELEASE_TAG_RE.fullmatch(release_tag):
        raise HTTPException(status_code=502, detail="latest release tag is invalid")
    if not _is_newer(release_version, current):
        raise HTTPException(status_code=409, detail="no newer published release exists")

    asset, checksums = _frozen_release_assets(latest, asset_name)
    if asset is None:
        raise HTTPException(
            status_code=502,
            detail=f"release {release_tag} does not contain {asset_name}",
        )
    if checksums is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"release {release_tag} has no {CHECKSUMS_ASSET_NAME} — refusing to "
                "install an installer that cannot be verified"
            ),
        )

    _progress.version = release_version

    def on_bytes(written: int, total: int | None) -> None:
        """One download tick. Without a total there is no honest fraction."""
        if not total:
            _progress.advance(PHASE_DOWNLOADING, 0.0, detail=_human_bytes(written))
            return
        _progress.advance(
            PHASE_DOWNLOADING,
            written / total,
            detail=f"{_human_bytes(written)} / {_human_bytes(total)}",
        )

    workdir = Path(tempfile.mkdtemp(prefix="jarvis-update-"))
    try:
        _progress.enter(PHASE_DOWNLOADING, detail=asset.name)
        installer = await download_and_verify(
            asset, checksums, dest_dir=workdir, on_progress=on_bytes
        )
        # download_and_verify hashes the file after the last byte lands, so by
        # the time it returns the verify phase is already over — the window is
        # closed here rather than announced, to keep the bar truthful.
        _progress.advance(PHASE_VERIFYING, 1.0, detail=None)
        _progress.enter(PHASE_INSTALLING)
        # hdiutil, a directory swap and a detached spawn all block; keep the
        # event loop (and therefore the UI this answer travels back over) free.
        handover = await asyncio.to_thread(apply_installer, installer)
    except InstallerUpdateError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        _progress.fail(str(exc))
        log.warning("[update] frozen update refused: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — the user must see WHY it stopped
        shutil.rmtree(workdir, ignore_errors=True)
        _progress.fail(f"the update could not be installed: {exc}")
        log.exception("[update] frozen update failed unexpectedly")
        raise HTTPException(
            status_code=500, detail=f"the update could not be installed: {exc}"
        ) from exc

    # The native installer restarts the app itself, so this run is complete.
    _progress.finish(version=release_version, restart_required=False)

    # The download is deliberately NOT deleted: on Windows the installer that
    # replaces this app is running from it right now. The OS reclaims the temp
    # directory; deleting it here would kill the update mid-flight.
    log.info("[update] %s installed from %s (%s)", release_tag, asset.name, workdir)

    global _status_cache, _status_cache_until, _status_cache_root
    _status_cache, _status_cache_until, _status_cache_root = None, 0.0, None

    return {
        "ok": True,
        "prepared": True,
        # The handover restarts the app itself (Inno's /RESTARTAPPLICATIONS,
        # `open` on macOS, re-exec on Linux), so no caller-driven restart is
        # required. The field is honest about that; a caller that restarts
        # anyway is harmless because the single-instance lock still holds.
        "restart_required": False,
        "kind": INSTALL_KIND_FROZEN,
        "version": release_version,
        "release_tag": release_tag,
        "install_profile": "frozen",
        "asset": asset.as_dict(),
        "handover": handover,
        "deps_refreshed": False,
        "deps_pending": False,
        "deps_warning": None,
        "ui_bundle_pending": False,
        "desktop_integration_ok": None,
        # The native installer owns Start Menu / Applications / .desktop
        # registration, so there is nothing for the app to write.
        "desktop_integration_pending": False,
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
    global _status_cache, _status_cache_until, _status_cache_root
    now = time.monotonic()
    if not force and _status_cache is not None and now < _status_cache_until:
        # The network part (and the resolved managed root, one git subprocess)
        # is cached; the staged-transaction fields are cheap local file reads
        # and must always be live (an apply invalidates the cache, but a
        # relauncher result appears while the cache is warm). A frozen install
        # has no such local state, so its cached answer is complete.
        if _status_cache.get("managed") and _status_cache_root is not None:
            return {
                **_status_cache,
                **(await _pending_update_overlay(_status_cache_root)),
            }
        return _status_cache

    current = _running_version()

    if is_frozen():
        # A native install has no checkout to resolve and must never enter the
        # git path below (there is no `origin`, no marker, and no `git`).
        frozen_result = await _frozen_status(current)
        _status_cache_root = None
        _status_cache = frozen_result
        _status_cache_until = now + (
            _STATUS_RETRY_S if frozen_result.get("check_failed") else _STATUS_CACHE_TTL_S
        )
        return frozen_result

    root = await _resolve_managed_repo()
    _status_cache_root = root

    if root is None:
        result: dict[str, object] = {
            "managed": False,
            "kind": INSTALL_KIND_DEV,
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
            "kind": INSTALL_KIND_MANAGED,
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
        "kind": INSTALL_KIND_MANAGED,
        "current": current,
        "latest": latest["version"],
        "update_available": available,
        "notes": latest["notes"] if available else None,
        "published_at": latest["published_at"],
        "release_url": latest.get("release_url"),
    }
    _status_cache, _status_cache_until = result, now + _STATUS_CACHE_TTL_S
    return {**result, **(await _pending_update_overlay(root))}


@router.get("/progress")
async def update_progress() -> dict[str, object]:
    """The live percentage of the update currently being applied.

    ``POST /api/update/apply`` is one long request — several hundred megabytes
    on a frozen install — so its return value tells the UI nothing until it is
    over. This is the side channel the button polls meanwhile, which is what
    turns "Updating…" into "Updating 70%".

    Always answers, whether or not an update is running: ``phase: "idle"`` is
    the resting state, ``"ready"`` means the server's work is done and only the
    restart remains, and ``"failed"`` carries the reason in ``error``. Reading
    it costs nothing but a dict copy, so a 300 ms poll is fine.
    """
    return _progress.snapshot()


@router.post("/apply", openapi_extra={"x-jarvis-dangerous": True})
async def update_apply() -> dict[str, object]:
    """Prepare the latest version and report progress while doing it.

    Dispatches on the install kind: a FROZEN install downloads and hands over
    the platform installer (``_apply_frozen``), a MANAGED checkout stages a git
    transaction the relauncher finishes (``_apply_managed``).

    Two things wrap both branches. The lock rejects a second concurrent apply
    with 409 rather than queueing it — two downloads would fight over the temp
    directory and scramble the shared progress state, and the user is already
    getting what they clicked for. The handler also guarantees that ANY failure
    lands in the progress tracker: a run that ends without ``fail`` leaves the
    button reading "Updating 42%" forever.
    """
    if _apply_lock.locked():
        raise HTTPException(status_code=409, detail="an update is already being applied")
    async with _apply_lock:
        try:
            if is_frozen():
                return await _apply_frozen()
            return await _apply_managed()
        except HTTPException as exc:
            _progress.fail(str(exc.detail))
            raise
        except Exception as exc:  # noqa: BLE001 — re-raised; this only records it
            _progress.fail(f"{type(exc).__name__}: {exc}")
            raise


async def _apply_managed() -> dict[str, object]:
    """Stage the latest published tag for a managed checkout. Does NOT restart.

    The live checkout remains untouched. The caller then invokes
    ``POST /api/settings/restart-app``; its detached relauncher applies the
    pinned revision and completes installation after this process exits.
    """
    global _status_cache, _status_cache_until, _status_cache_root
    _progress.begin(INSTALL_KIND_MANAGED)

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
            # Nothing was downloaded, so the run is complete at once.
            _progress.finish(version=str(staged.get("version") or ""), restart_required=True)
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

    _progress.version = release_version
    _progress.advance(PHASE_RESOLVING, 1.0)

    previous_revision = await _git_output(["rev-parse", "HEAD"], cwd=root)
    if not previous_revision:
        raise HTTPException(
            status_code=500,
            detail="could not identify the currently installed revision",
        )

    # Fetch the published tag, never the moving main branch. The update button
    # promises a specific GitHub Release; applying an unreleased main commit
    # would make the displayed version and installed bytes disagree.
    #
    # ``--progress`` forces git to emit its object counters even though stderr
    # is a pipe rather than a terminal; without it the download phase would
    # have no measurable inside at all.
    _progress.enter(PHASE_DOWNLOADING, detail=release_tag)
    rc, _out, err = await _git(
        ["fetch", "--progress", "--depth", "1", "origin", f"refs/tags/{release_tag}"],
        cwd=root,
        timeout_s=120.0,
        on_stderr_line=_on_git_progress,
    )
    if rc != 0:
        raise HTTPException(
            status_code=502, detail=f"git fetch failed: {err[:300] or 'unknown error'}"
        )

    _progress.enter(PHASE_VERIFYING)
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

    _progress.advance(PHASE_VERIFYING, 1.0)
    _progress.enter(PHASE_INSTALLING)
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
    _status_cache, _status_cache_until, _status_cache_root = None, 0.0, None
    # Everything this process can do is done; the relauncher does the rest,
    # which is why the caller must now restart.
    _progress.finish(version=new_version, restart_required=True)

    return {
        "ok": True,
        "prepared": True,
        "restart_required": True,
        "kind": INSTALL_KIND_MANAGED,
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
