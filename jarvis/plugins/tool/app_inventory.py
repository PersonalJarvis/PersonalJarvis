"""Installed-application inventory — the sweep behind "where could context live?".

``resolve_app_launch_target`` (app_resolver.py) answers "is THIS app here?" one
name at a time. Context acquisition (C13, docs/plans/autonomous-missions.md)
needs the opposite direction: "what IS here?", so an errand can notice that
WhatsApp or Spotify exists before deciding where to look. This module walks the
same roots the resolver already trusts — Start-Menu ``.lnk`` trees on Windows,
``.app`` bundles on macOS, ``.desktop`` entries on Linux.

Constraints, in order of importance:
- NEVER on the boot critical path (anti-pattern register): callers reach this
  lazily from an errand's gather phase, and results are cached for a day
  because installed software changes rarely.
- Headless-safe: a server with none of these roots yields an empty tuple,
  quietly. An empty inventory is a useful fact, not an error.
- Never raises: a broken registry walk costs the inventory, not the errand.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Final

from jarvis.platform import detect_platform

# Same package, shared on purpose: the sweep must trust exactly the roots the
# per-name resolver trusts, or the two would drift apart.
from .app_resolver import _LINUX_DESKTOP_ENTRY_DIRS, _start_menu_roots

log = logging.getLogger(__name__)

#: Installed software changes rarely; a stale day costs nothing because the
#: per-name resolver re-verifies before any launch.
CACHE_TTL_SECONDS: Final[float] = 24 * 3600

#: Cap on the returned inventory. Far beyond what a prompt should ever carry —
#: the renderer trims further — this only guards against a pathological tree.
_MAX_APPS: Final[int] = 200

#: Start-Menu folders are full of "Uninstall Foo" companions that name no app.
_NOISE_PREFIXES: Final[tuple[str, ...]] = ("uninstall", "readme", "help ", "website")

_cache: list[tuple[float, tuple[str, ...]]] = []


def installed_app_names(*, force_refresh: bool = False) -> tuple[str, ...]:
    """Names of applications installed on this machine, cached, sorted, capped."""
    now = time.monotonic()
    if _cache and not force_refresh and now - _cache[0][0] < CACHE_TTL_SECONDS:
        return _cache[0][1]
    names = _sweep()
    _cache.clear()
    _cache.append((now, names))
    return names


def reset_cache() -> None:
    """Drop the cached sweep. Tests only."""
    _cache.clear()


def _sweep() -> tuple[str, ...]:
    plat = detect_platform()
    try:
        if plat == "win32":
            raw = _sweep_windows()
        elif plat == "darwin":
            raw = _sweep_darwin()
        else:
            raw = _sweep_linux()
    except Exception:  # noqa: BLE001 — a broken walk costs the inventory, never a run
        log.debug("app inventory sweep failed", exc_info=True)
        return ()
    cleaned = {
        name.strip()
        for name in raw
        if name.strip() and not name.strip().lower().startswith(_NOISE_PREFIXES)
    }
    return tuple(sorted(cleaned, key=str.lower)[:_MAX_APPS])


def _sweep_windows(roots: list[str] | None = None) -> list[str]:
    """Stems of every Start-Menu ``.lnk`` — the tree installers actually fill.

    The same reasoning as ``_resolve_via_start_menu``: per-user Electron
    installs (Discord, Slack, WhatsApp) appear ONLY here, in neither App Paths
    nor ``PATH``.
    """
    names: list[str] = []
    for root in _start_menu_roots() if roots is None else roots:
        try:
            for _dirpath, _dirnames, filenames in os.walk(root):
                names.extend(fn[:-4] for fn in filenames if fn.lower().endswith(".lnk"))
        except OSError:
            continue
    return names


_DARWIN_APP_ROOTS: Final[tuple[str, ...]] = (
    "/Applications",
    "~/Applications",
    "/System/Applications",
)


def _sweep_darwin(roots: tuple[str, ...] | None = None) -> list[str]:
    """Top-level ``.app`` bundle names. Deliberately shallow: nested bundles
    are helpers, not applications a user would name."""
    names: list[str] = []
    for root in _DARWIN_APP_ROOTS if roots is None else roots:
        base = Path(root).expanduser()
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        names.extend(e.stem for e in entries if e.name.endswith(".app"))
    return names


def _sweep_linux(roots: tuple[str, ...] | None = None) -> list[str]:
    """Stems of ``.desktop`` entries, with reverse-DNS ids shortened to their
    last segment (``org.mozilla.firefox`` reads as ``firefox``)."""
    names: list[str] = []
    for root in _LINUX_DESKTOP_ENTRY_DIRS if roots is None else roots:
        base = Path(root).expanduser()
        try:
            entries = list(base.glob("*.desktop"))
        except OSError:
            continue
        for entry in entries:
            stem = entry.stem
            names.append(stem.rsplit(".", 1)[-1] if stem.count(".") >= 2 else stem)
    return names


__all__ = ["CACHE_TTL_SECONDS", "installed_app_names", "reset_cache"]
