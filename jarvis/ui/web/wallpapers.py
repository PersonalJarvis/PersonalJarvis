"""The desktop-wallpaper library: catalog, thumbnails, and full-size delivery.

The 500 generated wallpapers are *content*, not source. They live under
``data/jarvis-wallpaper-gallery`` — a git-ignored directory — and are served
from there instead of being copied into the frontend's ``public/`` folder:
147 MB of artwork in the repository would be paid for by every clone, every
build, and every packaged desktop app, including the ones whose owner never
opens the picker.

Two consequences shape this module:

* The library is *optional*. A checkout without it is normal, not broken, so
  the catalog endpoint answers ``available: false`` and the app keeps its
  bundled default wallpaper. Nothing here raises on a missing directory.
* The browse grid must never pull full-size art. Every wallpaper is 1920x1080
  and averages 300 KB; five hundred of those is a 147 MB scroll. Thumbnails are
  derived once, cached on disk, and are what the grid actually requests — the
  full image is downloaded only when a visitor previews or applies one.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from jarvis.core.config import DATA_DIR

#: Where the generated library lives, relative to the project data directory.
LIBRARY_DIRNAME = "jarvis-wallpaper-gallery"

#: Derived thumbnails are cached beside the originals. The leading dot keeps
#: them out of the manifest builder's ``images/`` scan.
THUMBS_DIRNAME = ".thumbs"

#: Long edge of a grid thumbnail. 480 px covers a 240 px tile on a 2x display,
#: which is the widest the grid goes before it adds a column instead.
THUMB_WIDTH = 480

#: Quality for the derived thumbnails. At tile size the difference from 90 is
#: invisible and the file is roughly a third of the size.
THUMB_QUALITY = 72

#: A wallpaper id as written by the manifest builder, e.g.
#: ``01-cinematic-photoreal-07``. Validating against this *and* against the
#: loaded catalog is what keeps a request from reaching outside the library.
_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-\d{2}$")

#: Immutable content: the files are generated once and never edited in place.
_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


@dataclass(frozen=True)
class Wallpaper:
    """One entry of the library, as the browse grid needs to know it."""

    id: str
    title: str
    style: str
    style_label: str
    theme: str
    path: Path


def library_root() -> Path:
    """Absolute path of the wallpaper library (may not exist)."""
    return DATA_DIR / LIBRARY_DIRNAME


def _humanize(slug: str) -> str:
    """Turn a directory slug into a display label.

    Used as the fallback when a manifest's ``style`` field is itself a slug
    rather than a written-out name — one of the twenty manifests is, and a
    filter chip reading "bright-photoreal" next to "Watercolor & Ink" looks
    like a bug to the person reading it.
    """
    words = slug.split("-", 1)[-1].split("-")
    return " ".join(word.capitalize() for word in words if word)


class WallpaperLibrary:
    """Loads the manifests once and answers lookups from memory.

    The catalog is read lazily on first request rather than at import time:
    boot should not pay for twenty JSON files that most sessions never touch.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or library_root()
        self._lock = threading.Lock()
        self._items: dict[str, Wallpaper] | None = None

    @property
    def root(self) -> Path:
        return self._root

    def _load(self) -> dict[str, Wallpaper]:
        manifests_dir = self._root / "manifests"
        images_dir = self._root / "images"
        if not manifests_dir.is_dir() or not images_dir.is_dir():
            return {}

        items: dict[str, Wallpaper] = {}
        for manifest in sorted(manifests_dir.glob("*.json")):
            try:
                entries = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("Skipping unreadable wallpaper manifest {}: {}", manifest, exc)
                continue
            if not isinstance(entries, list):
                continue
            style_slug = manifest.stem
            for entry in entries:
                item = self._parse(entry, style_slug)
                if item is not None and item.id not in items:
                    items[item.id] = item
        if items:
            logger.debug("Wallpaper library: {} entries from {}", len(items), self._root)
        return items

    def _parse(self, entry: Any, style_slug: str) -> Wallpaper | None:
        if not isinstance(entry, dict):
            return None
        item_id = str(entry.get("id", "")).strip()
        rel = str(entry.get("file", "")).strip()
        theme = str(entry.get("theme", "")).strip()
        if not _ID_PATTERN.match(item_id) or theme not in {"light", "dark"}:
            return None

        # The manifest's own path is untrusted input as far as this loader is
        # concerned — a hand-edited "file" must not be able to point the server
        # at an arbitrary file on disk.
        if not rel.startswith("images/") or ".." in rel or not rel.endswith(".webp"):
            return None
        path = (self._root / rel).resolve()
        try:
            path.relative_to((self._root / "images").resolve())
        except ValueError:
            return None
        if not path.is_file():
            return None

        style_label = str(entry.get("style", "")).strip()
        if not style_label or style_label == style_label.lower().replace(" ", "-"):
            style_label = _humanize(style_slug)
        return Wallpaper(
            id=item_id,
            title=str(entry.get("title", "")).strip() or item_id,
            style=style_slug,
            style_label=style_label,
            theme=theme,
            path=path,
        )

    def items(self) -> dict[str, Wallpaper]:
        with self._lock:
            if self._items is None:
                self._items = self._load()
            return self._items

    def get(self, item_id: str) -> Wallpaper | None:
        if not _ID_PATTERN.match(item_id):
            return None
        return self.items().get(item_id)

    def catalog(self) -> dict[str, Any]:
        """The payload the browse grid is built from — metadata only, no bytes."""
        items = list(self.items().values())
        styles: dict[str, dict[str, Any]] = {}
        for item in items:
            bucket = styles.setdefault(
                item.style,
                {"slug": item.style, "label": item.style_label, "count": 0},
            )
            bucket["count"] += 1
        return {
            "available": bool(items),
            "count": len(items),
            "styles": sorted(styles.values(), key=lambda style: str(style["slug"])),
            "items": [
                {
                    "id": item.id,
                    "title": item.title,
                    "style": item.style,
                    "styleLabel": item.style_label,
                    "theme": item.theme,
                }
                for item in items
            ],
        }

    def thumbnail(self, item: Wallpaper) -> Path:
        """Return a cached grid thumbnail, deriving it on first request.

        Falls back to the original file when Pillow is unavailable or the
        resize fails: a heavier grid is a far better outcome than an empty one.
        """
        target = self._root / THUMBS_DIRNAME / item.style / f"{item.id}.webp"
        try:
            if target.is_file() and target.stat().st_mtime >= item.path.stat().st_mtime:
                return target
        except OSError:
            return item.path

        try:
            from PIL import Image
        except ImportError:
            return item.path

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(item.path) as source:
                source.load()
                thumb = source.convert("RGB")
                height = max(1, round(thumb.height * THUMB_WIDTH / thumb.width))
                thumb = thumb.resize((THUMB_WIDTH, height), Image.Resampling.LANCZOS)
                # Write via a per-item temporary name: two browsers scrolling
                # the same row must not read a half-written file.
                staging = target.with_suffix(".webp.part")
                thumb.save(staging, "WEBP", quality=THUMB_QUALITY, method=4)
                staging.replace(target)
            return target
        except (OSError, ValueError) as exc:
            logger.warning("Could not derive wallpaper thumbnail for {}: {}", item.id, exc)
            return item.path


def register_wallpaper_routes(app: FastAPI, library: WallpaperLibrary | None = None) -> None:
    """Mount the wallpaper endpoints under ``/api``.

    Deliberately under ``/api`` rather than a static mount of its own: that
    prefix is already proxied by the Vite dev server, so the picker behaves
    identically in development and in the packaged app.
    """
    lib = library or WallpaperLibrary()

    @app.get("/api/wallpapers")
    async def list_wallpapers() -> dict[str, Any]:
        return lib.catalog()

    @app.get("/api/wallpapers/{item_id}/thumb", response_model=None)
    async def wallpaper_thumb(item_id: str) -> FileResponse | JSONResponse:
        item = lib.get(item_id)
        if item is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(
            str(lib.thumbnail(item)),
            media_type="image/webp",
            headers=_CACHE_HEADERS,
        )

    @app.get("/api/wallpapers/{item_id}/full", response_model=None)
    async def wallpaper_full(item_id: str) -> FileResponse | JSONResponse:
        item = lib.get(item_id)
        if item is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(
            str(item.path),
            media_type="image/webp",
            headers=_CACHE_HEADERS,
        )
