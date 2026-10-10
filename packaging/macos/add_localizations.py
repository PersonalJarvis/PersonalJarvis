"""Write the translated ``InfoPlist.strings`` into a built ``Personal Jarvis.app``.

PyInstaller's ``BUNDLE`` writes the base (English) ``Info.plist`` and, through
``jarvis.spec``, the ``CFBundleLocalizations`` key. The per-language strings that
macOS shows in its permission dialogs live in ``Contents/Resources/<lang>.lproj/
InfoPlist.strings`` and cannot come from the spec, so ``packaging/macos/build.sh``
runs this script on the finished ``.app`` BEFORE it signs it: the files are part of
the code seal, and a file added afterwards would invalidate the signature.

The text comes from the single table in ``jarvis/core/macos_privacy_strings.py``,
loaded by path exactly like ``jarvis.spec`` does (the package is not necessarily
importable on the build machine). The managed source-install app writes the same
files itself (``jarvis.setup.macos_app_bundle``), so both bundles show the same
sentences.

    python packaging/macos/add_localizations.py --app "dist/Personal Jarvis.app"

Exit codes: 0 = written, 1 = the app bundle is not usable (no ``Contents`` folder).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLE_PATH = REPO_ROOT / "jarvis" / "core" / "macos_privacy_strings.py"


def _load_table(path: Path = TABLE_PATH) -> ModuleType:
    """Load the usage-string table by path (it imports nothing from ``jarvis``)."""
    loader_spec = importlib.util.spec_from_file_location("_jarvis_macos_privacy_strings", path)
    if loader_spec is None or loader_spec.loader is None:
        raise RuntimeError(f"cannot load the macOS usage strings from {path}")
    module = importlib.util.module_from_spec(loader_spec)
    loader_spec.loader.exec_module(module)
    return module


def add_localizations(app: Path) -> list[Path]:
    """Write the ``.lproj`` files into ``app``'s ``Contents/Resources``; return them."""
    contents = app / "Contents"
    if not (contents / "Info.plist").is_file():
        raise FileNotFoundError(f"{app} has no Contents/Info.plist - is it a built app bundle?")
    return _load_table().write_localizations(contents / "Resources")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True, type=Path, help="the built Personal Jarvis.app")
    args = parser.parse_args(argv)
    try:
        written = add_localizations(args.app)
    except (OSError, RuntimeError) as exc:
        print(f"add_localizations: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"add_localizations: wrote {path.relative_to(args.app)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
