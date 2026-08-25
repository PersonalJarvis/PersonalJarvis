"""Write the AppImage's freedesktop Desktop Entry.

The AppImage spec requires exactly one ``.desktop`` file in the AppDir root
(appimagetool refuses to build without it), and the desktop environments that
integrate an AppImage - AppImageLauncher, appimaged, GNOME's "Add to
favourites" - read the application's name, icon and categories from it.

The encoding is NOT hand-rolled here. ``jarvis/core/desktop_entry.py`` is the
one place in this project that knows the two nested escaping layers a
``.desktop`` file carries, and a desktop environment silently discards an entry
it cannot parse - so a hand-written ``Exec=`` line fails invisibly. This script
is a thin renderer on top of that module, which keeps the AppImage entry and
the entry the source install writes byte-compatible in the parts that matter.

Usage:

    python packaging/linux/make_desktop_entry.py OUTPUT.desktop [--exec NAME]

``--exec`` is the executable name inside the AppDir (default ``Jarvis``).
Desktop-integration tools rewrite it to the absolute AppImage path when they
install the entry into ``~/.local/share/applications``; the plain name is what
the AppImage specification asks for.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis import __version__  # noqa: E402  - needs REPO_ROOT on sys.path first
from jarvis.core.branding import (  # noqa: E402
    LINUX_APP_NAME,
    LINUX_WM_CLASS,
    PRODUCT_SLUG,
)
from jarvis.core.desktop_entry import escape_value, exec_value  # noqa: E402

#: Same one-line summary the source install's menu entry uses.
COMMENT = "Voice-driven meta-orchestrator"

#: Freedesktop main category. "Utility" is what the source install already
#: registers; changing it would move the app in the menu between install kinds.
CATEGORIES = "Utility;"

#: The app search matches Name, and only some shells also match Comment. The
#: product name is two words, so somebody typing the half they remember needs
#: these keywords to find it at all.
KEYWORDS = "jarvis;assistant;voice;agent;automation;"


def render(exec_name: str = "Jarvis", icon_name: str = PRODUCT_SLUG) -> str:
    """Return the full Desktop Entry text for the AppImage."""

    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={escape_value(LINUX_APP_NAME)}\n"
        f"Comment={escape_value(COMMENT)}\n"
        # No %f/%u field code on purpose: the executable takes CLI subcommands
        # ("Jarvis serve"), not file or URL arguments, and a field code would
        # hand it a path it would then reject.
        f"Exec={exec_value(exec_name)}\n"
        f"Icon={escape_value(icon_name)}\n"
        "Terminal=false\n"
        f"Categories={CATEGORIES}\n"
        f"Keywords={KEYWORDS}\n"
        # Binds the running window to this entry, so a desktop that does get a
        # native window shows the Jarvis icon in the dock rather than a generic
        # one. Matches jarvis.ui.icon_utils.pin_linux_wm_class().
        f"StartupWMClass={escape_value(LINUX_WM_CLASS)}\n"
        f"X-AppImage-Version={escape_value(__version__)}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="Path of the .desktop file to write")
    parser.add_argument(
        "--exec",
        dest="exec_name",
        default="Jarvis",
        help="Executable name inside the AppDir (default: Jarvis)",
    )
    parser.add_argument(
        "--icon",
        dest="icon_name",
        default=PRODUCT_SLUG,
        help=f"Icon name without extension (default: {PRODUCT_SLUG})",
    )
    args = parser.parse_args(argv)

    text = render(exec_name=args.exec_name, icon_name=args.icon_name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
