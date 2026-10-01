#!/usr/bin/env python3
"""Prove a built macOS app can use its privacy permissions, before it ships.

Why this gate exists (BUG-222). The v2.5.0 ``.dmg`` shipped with two defects
that no headless smoke run can see, because the smoke run starts the executable
directly instead of the way a user does:

* The pyobjc ``AVFoundation`` module was not in the frozen archive. The
  permission port loads it by NAME (``SystemPermissionPort._load``), which
  PyInstaller's static analysis cannot follow, so the app read its microphone
  permission as "unavailable" for good and the voice gate never opened.
* ``Info.plist`` said ``LSBackgroundOnly``. PyInstaller sets it whenever the
  last executable of the COLLECT is a console one (the ``jarvis`` CLI is), and
  LaunchServices then runs the app as a faceless background process.

This script reads the finished ``.app`` itself: the plist, and the module table
of the frozen archive embedded in the main executable (the same data
``pyi-archive_viewer`` prints). It runs on any host that has PyInstaller, and
fails with the exact thing that is missing.

Usage:
    python scripts/ci/check_frozen_macos_app.py --app "dist/Personal Jarvis.app"

Exit codes: 0 = fine, 1 = the app would ship broken (problems listed),
2 = could not check (missing file, unreadable archive, no PyInstaller).
"""

from __future__ import annotations

import argparse
import plistlib
import sys
from collections.abc import Callable, Collection
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.core.branding import MACOS_DMG_BUNDLE_ID  # noqa: E402

# Modules the permission port and the macOS input/capture code reach by NAME or
# through the frameworks' own lazy loaders: all of them must be in the archive.
REQUIRED_FROZEN_MODULES: tuple[str, ...] = (
    "AVFoundation",
    "ApplicationServices",
    "AppKit",
    "Foundation",
    "Quartz",
    "objc",
)


def check_info_plist(info: dict, macos_dir: Path) -> list[str]:
    """What is wrong with the bundle's ``Info.plist`` (empty list = fine)."""
    problems: list[str] = []
    if info.get("CFBundleIdentifier") != MACOS_DMG_BUNDLE_ID:
        problems.append(
            f"CFBundleIdentifier is {info.get('CFBundleIdentifier')!r}, but the permission "
            f"port accepts {MACOS_DMG_BUNDLE_ID!r} for this app"
        )
    if info.get("LSBackgroundOnly"):
        problems.append(
            "LSBackgroundOnly is true: LaunchServices would run the app as a faceless "
            "background process (no Dock icon, no windows). Set it to False in jarvis.spec."
        )
    executable = info.get("CFBundleExecutable")
    if not isinstance(executable, str) or not (macos_dir / executable).is_file():
        problems.append(f"CFBundleExecutable {executable!r} is not in Contents/MacOS")
    if not str(info.get("NSMicrophoneUsageDescription") or "").strip():
        problems.append(
            "NSMicrophoneUsageDescription is missing: macOS ends the process the moment "
            "it asks for the microphone"
        )
    return problems


def check_frozen_modules(names: Collection[str]) -> list[str]:
    """Which required modules the frozen archive lacks (empty list = fine)."""
    present = set(names)
    return [
        f"the frozen archive has no {module!r} module (needs the [desktop-macos] extra "
        "on the build machine and a hidden import in jarvis.spec)"
        for module in REQUIRED_FROZEN_MODULES
        if module not in present
    ]


def frozen_module_names(executable: Path) -> set[str]:
    """Every module name in the PYZ archives embedded in a frozen executable."""
    from PyInstaller.archive.readers import CArchiveReader  # noqa: PLC0415

    archive = CArchiveReader(str(executable))
    names: set[str] = set()
    for entry_name, (*_, typecode) in archive.toc.items():
        if typecode == "z":
            names.update(archive.open_embedded_archive(entry_name).toc.keys())
    return names


def check_app(
    app: Path,
    *,
    read_modules: Callable[[Path], Collection[str]] | None = None,
) -> list[str]:
    """All problems of the built ``app`` bundle; raises if the bundle cannot be read."""
    read = read_modules if read_modules is not None else frozen_module_names
    contents = app / "Contents"
    with (contents / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    macos_dir = contents / "MacOS"
    problems = check_info_plist(info, macos_dir)
    executable = info.get("CFBundleExecutable")
    if isinstance(executable, str) and (macos_dir / executable).is_file():
        problems += check_frozen_modules(read(macos_dir / executable))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True, type=Path, help="the built Personal Jarvis.app")
    args = parser.parse_args(argv)
    try:
        problems = check_app(args.app)
    except Exception as exc:  # noqa: BLE001 - any unreadable bundle or archive is "cannot check"
        print(f"check_frozen_macos_app: cannot check {args.app}: {type(exc).__name__}: {exc}")
        return 2
    if problems:
        print(f"check_frozen_macos_app: {args.app} would ship broken:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"check_frozen_macos_app: OK - {args.app} can use macOS permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
