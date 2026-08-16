"""Escape the MSIX file-system redirection a Microsoft-Store Python imposes.

Why this module exists — the forensic, because the symptom lies:

A Python installed from the Microsoft Store runs every child interpreter inside
the store package's MSIX container, and a venv built from it inherits that
identity.  Windows then silently redirects the container's writes to
``%APPDATA%`` and ``%LOCALAPPDATA%`` into a private per-package tree under
``%LOCALAPPDATA%\\Packages\\<family>\\LocalCache``.  The per-user Start Menu
lives in ``%APPDATA%``, so the launcher we write "into the Start Menu" is
diverted into that container and the shell never sees it: the app is absent
from Windows Search and from the Start menu, while the Desktop launcher — the
Desktop is NOT a redirected location — keeps working.  That is exactly the
2026-08-16 report: "only the Desktop icon starts it".

The redirection is invisible from the inside.  Reads are redirected the same
way, so ``lnk.is_file()`` answers ``True`` for the trapped copy: every layer we
had reported success while the shell saw nothing, and an earlier session
concluded the machine's app index was broken.  It was not — the file simply was
never there.

The escape has two halves, both required:

* **Publish** through a helper process that has no package identity, which is
  the only way to write into the real tree.  ``powershell.exe`` from System32
  qualifies: Windows does not hand package identity to it (verified live).
* **Drop the shadow** afterwards, because MSIX resolves reads container-first
  and falls through to the real path only when the container copy is gone.
  Removing it is what makes our own later ``is_file()`` checks honest, so the
  idempotence and staleness logic upstream reasons about what the shell sees.

Everything here is a no-op off Windows and on an unpackaged interpreter (the
regular python.org install, where nothing is redirected), and best-effort
throughout: shell registration must never raise and never block boot.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from loguru import logger

# GetCurrentPackageFamilyName's way of saying "this process has no package
# identity" — i.e. a normal, unpackaged interpreter with no redirection at all.
_APPMODEL_ERROR_NO_PACKAGE = 15700
# ...and its way of saying "you have identity, the name needs this much room".
_ERROR_INSUFFICIENT_BUFFER = 122

# Resolved once: the answer cannot change within a process, and the probe sits
# on the boot path (AP-26 — nothing heavy on the critical path).
_FAMILY_PROBED = False
_FAMILY: str | None = None


def package_family_name() -> str | None:
    """The MSIX package family hosting this process, or ``None`` when unpackaged.

    ``GetCurrentPackageFamilyName`` is the documented identity probe and the
    only trustworthy one: the interpreter path is not a reliable signal, since a
    venv built from a Store Python reports its own ``Scripts\\python.exe`` in
    ``sys.executable`` while still running inside the container.

    Cached, Windows-only, never raises.  ``kernel32`` gained this export in
    Windows 8, so an older or stripped system simply reports "unpackaged".
    """
    global _FAMILY_PROBED, _FAMILY

    if _FAMILY_PROBED:
        return _FAMILY
    _FAMILY_PROBED = True
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        probe = kernel32.GetCurrentPackageFamilyName
        probe.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        probe.restype = wintypes.LONG

        length = wintypes.UINT(0)
        rc = probe(ctypes.byref(length), None)
        if rc == _APPMODEL_ERROR_NO_PACKAGE:
            return None
        if rc != _ERROR_INSUFFICIENT_BUFFER:
            logger.debug("package identity probe returned {}; assuming unpackaged", rc)
            return None
        buffer = ctypes.create_unicode_buffer(length.value)
        if probe(ctypes.byref(length), buffer) != 0:
            return None
        _FAMILY = buffer.value or None
        if _FAMILY:
            logger.debug("running inside MSIX package family {}", _FAMILY)
        return _FAMILY
    except Exception as exc:  # noqa: BLE001 — an unprobeable host is an unpackaged one
        logger.debug("package identity could not be probed: {}", exc)
        return None


def _is_relative_to(path: Path, root: Path) -> bool:
    """``Path.is_relative_to`` with Windows' case-insensitive comparison."""
    try:
        candidate = os.path.normcase(os.path.abspath(path))
        base = os.path.normcase(os.path.abspath(root))
    except (OSError, ValueError):
        # A path this malformed cannot be under a redirected root either, and
        # the honest answer to "is it?" is no. Logging here would fire on every
        # shortcut check for a profile with an odd env var, for no new fact.
        return False
    return candidate == base or candidate.startswith(base + os.sep)


def shadow_path_for(
    path: Path,
    *,
    family: str | None,
    local_appdata: str | Path | None,
    roaming_appdata: str | Path | None,
) -> Path | None:
    """Where a packaged process's write to ``path`` really lands.

    The two redirected roots are documented for the Desktop Bridge:
    ``%LOCALAPPDATA%`` maps into ``LocalCache\\Local`` and roaming ``%APPDATA%``
    into ``LocalCache\\Roaming``.  ``%LOCALAPPDATA%\\Packages`` is deliberately
    NOT redirected — it holds the containers themselves — which is what lets us
    address a shadow copy directly instead of only through the redirection.

    Returns ``None`` when nothing is redirected: no package identity, or a
    target outside both roots (the Desktop, Program Files, the install tree).

    Pure and platform-neutral so the arithmetic that decides whether the shell
    can see a launcher stays testable on every CI runner; the environment
    lookup lives in :func:`container_shadow_path`.
    """
    if not family or not local_appdata:
        return None
    local = Path(local_appdata)
    # The containers live UNDER %LOCALAPPDATA%, so this exception has to be
    # checked before the roots below or every shadow path would itself resolve
    # to a shadow — addressing a trapped file directly (to publish or clear it)
    # would then aim one level deeper on each call.
    if _is_relative_to(path, local / "Packages"):
        return None
    cache = local / "Packages" / family / "LocalCache"
    pairs: list[tuple[Path, Path]] = [(local, cache / "Local")]
    if roaming_appdata:
        pairs.append((Path(roaming_appdata), cache / "Roaming"))

    for real_root, container_root in pairs:
        # A path already inside a container is the shadow, not a source for one.
        if _is_relative_to(path, container_root):
            return None
        if _is_relative_to(path, real_root):
            try:
                return container_root / path.relative_to(real_root)
            except ValueError:  # differing case defeated relative_to
                tail = os.path.abspath(path)[len(os.path.abspath(real_root)) :]
                return container_root / tail.lstrip(os.sep)
    return None


def container_shadow_path(path: Path) -> Path | None:
    """Where a write to ``path`` really lands, or ``None`` when it lands there.

    ``None`` covers every ordinary install — off Windows, on an unpackaged
    interpreter, or for a target outside the redirected roots.  Callers treat
    ``None`` as "nothing to do".
    """
    if sys.platform != "win32":
        return None
    return shadow_path_for(
        path,
        family=package_family_name(),
        local_appdata=os.environ.get("LOCALAPPDATA"),
        roaming_appdata=os.environ.get("APPDATA"),
    )


def _powershell() -> str:
    """The Windows PowerShell binary, addressed absolutely where we can.

    An absolute path keeps the helper off ``PATH``, which a user profile can
    point anywhere — this process is about to hand it a file to copy.
    """
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if candidate.is_file():
            return str(candidate)
    return "powershell.exe"


# Each script ends by proving its own outcome from OUTSIDE the container, so the
# exit code answers "does the shell see what we intended?" rather than "did the
# command not throw".  Paths travel in the environment, never on the command
# line: no quoting rules to get wrong for a path holding a space, a quote or a
# dollar sign, and no way for a path to become script.
_PUBLISH_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "$src = $env:JARVIS_MSIX_SRC; $dst = $env:JARVIS_MSIX_DST; "
    "New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) "
    "| Out-Null; "
    "Copy-Item -LiteralPath $src -Destination $dst -Force; "
    "if (Test-Path -LiteralPath $dst) { exit 0 } else { exit 1 }"
)

_REMOVE_SCRIPT = (
    "$dst = $env:JARVIS_MSIX_DST; "
    "Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue; "
    "if (Test-Path -LiteralPath $dst) { exit 1 } else { exit 0 }"
)


def _run_helper(script: str, **paths: Path) -> bool:
    """Run one PowerShell script outside the container; ``True`` on exit code 0."""
    try:
        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

        env = dict(os.environ)
        for name, value in paths.items():
            env[f"JARVIS_MSIX_{name.upper()}"] = str(value)
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell, System32 binary
            [
                _powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            env=env,
            timeout=60,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            logger.debug(
                "MSIX helper failed (rc={}) for {}: {}",
                result.returncode,
                paths,
                (result.stderr or "").strip()[:400],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocks boot
        logger.debug("MSIX helper could not run for {}: {}", paths, exc)
        return False


def publish_out_of_container(path: Path) -> bool:
    """Move a redirected write to where the shell can see it.

    Returns ``True`` when ``path`` needs no escaping (the common case: an
    unpackaged interpreter, or a target outside the redirected roots) or when
    the trapped copy was successfully published.  ``False`` means the file is
    still only inside the container — the caller should report the artifact as
    not installed rather than claim a success the shell cannot see.

    Safe to call on a path that was never written: an absent shadow is simply
    nothing to do.
    """
    shadow = container_shadow_path(path)
    if shadow is None:
        return True
    try:
        if not shadow.is_file():
            return True
    except OSError:
        # An unreadable container path means nothing is trapped there to
        # publish, so the caller's write already went where it should. Treated
        # as "nothing to do" rather than a failure the caller must report.
        return True
    if not _run_helper(_PUBLISH_SCRIPT, src=shadow, dst=path):
        return False
    # Drop the shadow so later reads fall through to the real file. Without
    # this, every subsequent is_file()/staleness check in this process keeps
    # answering from the container and the caller can never tell the two apart.
    try:
        shadow.unlink()
    except OSError as exc:
        logger.debug("container shadow could not be removed at {}: {}", shadow, exc)
    logger.debug("published out of the MSIX container: {}", path)
    return True


def reveal_real_path(path: Path) -> None:
    """Remove a stale container copy so reads of ``path`` see the real file.

    Called before the idempotence checks so they reason about what the shell
    sees, not about a copy trapped by an earlier run of an older build. Only
    ever deletes inside this process's own container.
    """
    shadow = container_shadow_path(path)
    if shadow is None:
        return
    try:
        if shadow.is_file():
            shadow.unlink()
            logger.debug("cleared stale MSIX container copy: {}", shadow)
    except OSError as exc:
        logger.debug("stale container copy could not be cleared at {}: {}", shadow, exc)


def remove_outside_container(path: Path) -> bool:
    """Delete ``path`` where the shell sees it, container copy included.

    Deletion is redirected exactly like writing, so an uninstall running under a
    Store Python removes only its own container copy: the launcher stays in the
    real Start Menu, the caller reports a clean uninstall, and the user is left
    with an entry that starts a deleted install. Reaching the real file needs
    the same unpackaged helper the install path uses.

    Returns ``True`` when the path is gone (or was never redirected, in which
    case the caller's own ``unlink`` is authoritative and this is a no-op).
    """
    shadow = container_shadow_path(path)
    if shadow is None:
        return True
    try:
        if shadow.is_file():
            shadow.unlink()
    except OSError as exc:
        logger.debug("container copy could not be removed at {}: {}", shadow, exc)
    return _run_helper(_REMOVE_SCRIPT, dst=path)


def redirects_writes(path: Path) -> bool:
    """Would a write to ``path`` be diverted into this process's container?"""
    return container_shadow_path(path) is not None


__all__ = [
    "container_shadow_path",
    "package_family_name",
    "publish_out_of_container",
    "redirects_writes",
    "remove_outside_container",
    "reveal_real_path",
    "shadow_path_for",
]
