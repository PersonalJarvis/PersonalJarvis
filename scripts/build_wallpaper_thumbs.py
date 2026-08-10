"""Pre-derive the grid thumbnails for the desktop-wallpaper library.

The server derives a missing thumbnail on demand, so this script is an
optimisation, not a prerequisite: it moves five hundred resizes off the first
visit to the Wallpaper section, where they would otherwise trickle in as the
grid scrolls.

    python scripts/build_wallpaper_thumbs.py

Re-running is cheap — thumbnails newer than their source are left alone.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.ui.web.wallpapers import WallpaperLibrary  # noqa: E402


def main() -> int:
    library = WallpaperLibrary()
    items = list(library.items().values())
    if not items:
        print(f"No wallpaper library found at {library.root} — nothing to do.")
        return 0

    print(f"Deriving thumbnails for {len(items)} wallpapers…")
    derived = 0
    # Pillow releases the GIL around encode/decode, so threads genuinely help
    # here; the work is IO plus C-level image code, not Python bytecode.
    with ThreadPoolExecutor() as pool:
        for item, thumb in zip(items, pool.map(library.thumbnail, items), strict=True):
            if thumb != item.path:
                derived += 1
            else:
                print(f"  ! fell back to the full image for {item.id}")
    print(f"Done: {derived}/{len(items)} thumbnails available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
