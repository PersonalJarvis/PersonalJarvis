"""User guidance for desktop installs without a native window backend."""

from __future__ import annotations


def default_window_fallback_remedy(*, platform: str, frozen: bool) -> str:
    """Return the recovery guidance that applies to this install type."""
    if platform.startswith("linux") and frozen:
        return (
            "  - This packaged Linux build has no native window backend; installing "
            "host GTK packages will not add one.\n"
            "    The browser UI at the address above is the supported interface for this "
            "build.\n"
        )
    return (
        "  - Or install the system GTK 3 + WebKit2GTK packages pywebview "
        "needs and restart\n"
        "    (Debian/Ubuntu, for example: "
        "'sudo apt install python3-gi gir1.2-webkit2-4.1').\n"
    )
