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

It also asserts the privacy-permission declarations of the bundle:

* every ``NS...UsageDescription`` string of the single table in
  ``jarvis/core/macos_privacy_strings.py`` is in ``Info.plist`` with exactly that
  text, and the keys that table lists as removed (camera, speech recognition,
  system administration) are not;
* the bundle is localised for German, Spanish and European Portuguese: ``Info.plist`` declares
  ``CFBundleLocalizations`` and ``CFBundleDevelopmentRegion``, and
  ``Contents/Resources/<lang>.lproj/InfoPlist.strings`` exists for each language
  with exactly the text of that table (``packaging/macos/add_localizations.py``
  writes them before signing; a file missing here means the build step did not run);
* on a SIGNED build (a certificate identity, i.e. the Developer ID path of
  ``packaging/macos/build.sh``), ``codesign -d --entitlements :-`` must report
  every entitlement of ``packaging/macos/entitlements.plist`` and none of the
  removed ones. An unsigned or ad-hoc build embeds no entitlements, so that
  half is SKIPPED with a message that says so; a skip is not a pass. It also
  needs ``codesign``, which exists on macOS only. The behaviour of a hardened
  signed build on a real Mac is unverified: this reads declarations, it does not
  run the app.

Usage:
    python scripts/ci/check_frozen_macos_app.py --app "dist/Personal Jarvis.app"

Exit codes: 0 = fine (skipped parts are printed as NOTE lines), 1 = the app would
ship broken (problems listed), 2 = could not check (missing file, unreadable
archive, no PyInstaller, a signature codesign cannot read).
"""

from __future__ import annotations

import argparse
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.core import macos_privacy_strings  # noqa: E402
from jarvis.core.branding import MACOS_DMG_BUNDLE_ID  # noqa: E402
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402

ENTITLEMENTS_FILE = ROOT / "packaging" / "macos" / "entitlements.plist"

# Entitlements that must never be embedded: Jarvis has no caller for them, so
# they would grant the right to ask for something nothing asks for.
REMOVED_ENTITLEMENTS: tuple[str, ...] = ("com.apple.security.device.camera",)

# ``codesign`` is invoked as ``codesign <args>``; the seam returns the finished
# process with BYTES in stdout and stderr (the plist may carry any encoding).
CodesignRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[bytes]"]

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
    problems += check_usage_strings(info)
    problems += check_localization_keys(info)
    return problems


# What a missing string costs, per key. The microphone one is the documented
# process-ending case; the others are third-party reports (silent refusal, no
# prompt) and therefore worded as "may".
_MISSING_STRING_CONSEQUENCE = {
    "NSMicrophoneUsageDescription": "macOS ends the process the moment it asks for the microphone",
}
_DEFAULT_MISSING_STRING_CONSEQUENCE = (
    "macOS may refuse the request without ever showing a permission prompt"
)


def check_usage_strings(info: Mapping[str, object]) -> list[str]:
    """What is wrong with the bundle's usage-description strings (empty = fine).

    The reference is ``jarvis/core/macos_privacy_strings.py``, the one table the
    spec and the managed bundle both load: a missing, blank or different string
    means this bundle was not built from it, and a key from the removed list
    claims access the app never uses.
    """
    problems: list[str] = []
    expected = macos_privacy_strings.usage_descriptions()
    for key in macos_privacy_strings.REQUIRED_USAGE_KEYS:
        value = info.get(key)
        if not isinstance(value, str) or not value.strip():
            consequence = _MISSING_STRING_CONSEQUENCE.get(key, _DEFAULT_MISSING_STRING_CONSEQUENCE)
            problems.append(f"{key} is missing: {consequence}")
        elif value != expected[key]:
            problems.append(
                f"{key} differs from jarvis/core/macos_privacy_strings.py: the bundle was "
                "not built from the single usage-string table (jarvis.spec must load it)"
            )
    for key in macos_privacy_strings.REMOVED_USAGE_KEYS:
        if key in info:
            problems.append(
                f"{key} is in Info.plist but Jarvis has no caller for that permission: "
                "remove it from jarvis.spec (least privilege)"
            )
    return problems


def check_localization_keys(info: Mapping[str, object]) -> list[str]:
    """What is wrong with the localisation declarations of ``Info.plist`` (empty = fine)."""
    expected = macos_privacy_strings.localization_plist_keys()
    problems: list[str] = []
    for key, value in expected.items():
        if info.get(key) != value:
            problems.append(
                f"{key} is {info.get(key)!r}, expected {value!r}: the bundle declares no "
                "German/Spanish localisation (jarvis.spec must spread "
                "localization_plist_keys() into its Info.plist)"
            )
    return problems


def check_localization_files(resources_dir: Path) -> list[str]:
    """Which ``<lang>.lproj/InfoPlist.strings`` are missing or differ from the table."""
    problems: list[str] = []
    for language in macos_privacy_strings.LOCALIZED_LANGUAGES:
        target = resources_dir / f"{language}.lproj" / "InfoPlist.strings"
        if not target.is_file():
            problems.append(
                f"{language}.lproj/InfoPlist.strings is missing from Contents/Resources: "
                "macOS would show the English permission text only (build.sh runs "
                "packaging/macos/add_localizations.py before signing)"
            )
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{language}.lproj/InfoPlist.strings is not valid UTF-8")
            continue
        if text != macos_privacy_strings.info_plist_strings_text(language):
            problems.append(
                f"{language}.lproj/InfoPlist.strings differs from "
                "jarvis/core/macos_privacy_strings.py: it was not written from the "
                "single usage-string table"
            )
    return problems


# --- code signature and entitlements ------------------------------------------


def _run_codesign(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """The real ``codesign``; raises ``FileNotFoundError`` where it does not exist."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["codesign", *args],
        capture_output=True,
        timeout=120,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def _text(blob: bytes) -> str:
    return blob.decode("utf-8", errors="replace")


def signing_kind(app: Path, run: CodesignRunner) -> str:
    """``"identity"`` (signed with a certificate), ``"adhoc"`` or ``"unsigned"``.

    Reads ``codesign -dvv`` (its report goes to stderr). Raises ``RuntimeError``
    for output it cannot classify, because guessing here would either skip a
    check that should run or demand entitlements an ad-hoc build never has.
    """
    result = run(["-dvv", str(app)])
    report = _text(result.stderr) + "\n" + _text(result.stdout)
    if "not signed at all" in report:
        return "unsigned"
    if re.search(r"^Signature=adhoc\s*$", report, re.MULTILINE):
        return "adhoc"
    if re.search(r"^Authority=\S", report, re.MULTILINE):
        return "identity"
    first = next((line for line in report.splitlines() if line.strip()), "<no output>")
    raise RuntimeError(
        f"codesign -dvv gave no signature kind I recognise (exit {result.returncode}): {first}"
    )


_PLIST_XML = re.compile(rb"<\?xml.*?</plist>", re.DOTALL)


def read_entitlements(app: Path, run: CodesignRunner) -> dict[str, object]:
    """The entitlements embedded in the signed ``app`` (empty = none embedded).

    ``codesign -d --entitlements :-`` writes the entitlements as an XML plist
    on stdout; older and newer macOS differ in what else surrounds it (an
    ``Executable=`` line on stderr, sometimes a blob header), so the plist is cut
    out of the output instead of parsing the whole stream.
    """
    result = run(["-d", "--entitlements", ":-", str(app)])
    for stream in (result.stdout, result.stderr):
        match = _PLIST_XML.search(stream)
        if match:
            parsed = plistlib.loads(match.group(0))
            if not isinstance(parsed, dict):
                raise RuntimeError("codesign reported entitlements that are not a dictionary")
            return parsed
    return {}


def check_entitlements(found: Mapping[str, object], expected: Mapping[str, object]) -> list[str]:
    """What an embedded entitlement set lacks, or carries that it must not."""
    problems = [
        f"the signed app lacks entitlement {key} (packaging/macos/entitlements.plist asks for it)"
        for key, value in expected.items()
        if found.get(key) != value
    ]
    problems += [
        f"the signed app carries entitlement {key}, which Jarvis has no caller for "
        "(least privilege)"
        for key in REMOVED_ENTITLEMENTS
        if key in found
    ]
    return problems


def check_signature(
    app: Path,
    *,
    run: CodesignRunner | None = None,
    notes: list[str] | None = None,
    entitlements_file: Path | None = None,
) -> list[str]:
    """Entitlement problems of a SIGNED build; a skip is recorded in ``notes``."""
    notes = notes if notes is not None else []
    runner = run if run is not None else _run_codesign
    try:
        kind = signing_kind(app, runner)
    except FileNotFoundError:
        notes.append(
            "entitlements NOT checked: codesign is not available on this host "
            "(it ships with macOS; run this on the Mac or macOS runner that built the app)"
        )
        return []
    if kind != "identity":
        state = "ad-hoc signed" if kind == "adhoc" else "unsigned"
        notes.append(
            f"entitlements NOT checked: the app is {state}, and build.sh embeds "
            "entitlements only with a Developer ID identity (a skip, not a pass)"
        )
        return []
    expected = plistlib.loads((entitlements_file or ENTITLEMENTS_FILE).read_bytes())
    return check_entitlements(read_entitlements(app, runner), expected)


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
    run_codesign: CodesignRunner | None = None,
    notes: list[str] | None = None,
) -> list[str]:
    """All problems of the built ``app`` bundle; raises if the bundle cannot be read.

    ``notes`` collects what was SKIPPED (no ``codesign`` on this host, or an
    unsigned/ad-hoc build that embeds no entitlements) so the caller can print it.
    """
    read = read_modules if read_modules is not None else frozen_module_names
    contents = app / "Contents"
    with (contents / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    macos_dir = contents / "MacOS"
    problems = check_info_plist(info, macos_dir)
    problems += check_localization_files(contents / "Resources")
    executable = info.get("CFBundleExecutable")
    if isinstance(executable, str) and (macos_dir / executable).is_file():
        problems += check_frozen_modules(read(macos_dir / executable))
    problems += check_signature(app, run=run_codesign, notes=notes)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True, type=Path, help="the built Personal Jarvis.app")
    args = parser.parse_args(argv)
    notes: list[str] = []
    try:
        problems = check_app(args.app, notes=notes)
    except Exception as exc:  # noqa: BLE001 - any unreadable bundle or archive is "cannot check"
        print(f"check_frozen_macos_app: cannot check {args.app}: {type(exc).__name__}: {exc}")
        return 2
    for note in notes:
        print(f"check_frozen_macos_app: NOTE - {note}")
    if problems:
        print(f"check_frozen_macos_app: {args.app} would ship broken:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"check_frozen_macos_app: OK - {args.app} can use macOS permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
