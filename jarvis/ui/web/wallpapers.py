"""The desktop wallpapers: the generated library, plus the owner's own uploads.

Two stores live here, deliberately kept apart on disk:

* The generated library — five hundred pieces of *content* under
  ``data/jarvis-wallpaper-gallery`` (see below).
* Uploads — the pictures the owner brought themselves, under
  ``data/jarvis-wallpaper-uploads``. Regenerating or deleting the library must
  never touch somebody's own photograph, which is the one thing here that
  cannot be produced again.

The 500 generated wallpapers are *content*, not source. They live in a
git-ignored directory and are served from there instead of being copied into
the frontend's ``public/`` folder: 147 MB of artwork in the repository would be
paid for by every clone, every build, and every packaged desktop app, including
the ones whose owner never opens the picker.

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

import io
import json
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from jarvis.core.config import DATA_DIR

#: Where the generated library lives, relative to the project data directory.
LIBRARY_DIRNAME = "jarvis-wallpaper-gallery"

#: Where the owner's own wallpapers live. A sibling of the library, never a
#: child of it: the library is regenerable content, these are not.
UPLOADS_DIRNAME = "jarvis-wallpaper-uploads"

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

#: An upload id. Random rather than derived from the file name: two photographs
#: called ``IMG_0042.jpg`` are two wallpapers, and a name is not a key.
_UPLOAD_ID_PATTERN = re.compile(r"^u[0-9a-f]{16}$")

#: Ceiling for an accepted upload. Generous enough for a phone photograph or a
#: 4K screenshot, low enough that a mis-drop of a video file fails fast.
MAX_UPLOAD_BYTES = 40 * 1024 * 1024

#: Uploads are re-encoded to at most this width. A wallpaper never needs more
#: pixels than the largest desktop, and a 12-megapixel original would otherwise
#: be decoded on every repaint of the app's background.
UPLOAD_MAX_WIDTH = 3840

#: Quality of the re-encoded upload. Visually indistinguishable at wallpaper
#: distance, and a fraction of the size of the incoming PNG.
UPLOAD_QUALITY = 82

#: Mean luminance (0-255) at or above which an upload is treated as a light
#: picture. The midpoint is the honest guess; the owner can correct it.
_LIGHT_THEME_LUMA = 128

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
        """Return a cached grid thumbnail, deriving it on first request."""
        target = self._root / THUMBS_DIRNAME / item.style / f"{item.id}.webp"
        return _derive_thumbnail(item.path, target, item.id)


class UploadRejected(Exception):
    """An upload the store refuses, with a sentence meant for the owner."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class UploadedWallpaper:
    """One wallpaper the owner brought themselves."""

    id: str
    title: str
    theme: str
    created_at: float
    path: Path

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "theme": self.theme,
            "createdAt": self.created_at,
        }


def _clean_title(filename: str) -> str:
    """A display title from an upload's file name.

    The file name is decoration, never a path: only the stem survives, the
    separators become spaces, and the result is capped. What lands on disk is
    the random id, so a hostile name has nothing to reach.
    """
    stem = Path(filename or "").stem.strip()
    words = re.split(r"[\s._-]+", stem)
    title = " ".join(word for word in words if word).strip()
    return title[:60] or "Your wallpaper"


class WallpaperUploads:
    """The owner's own wallpapers: add, list, retheme, remove.

    Each upload is two files under one directory — ``<id>.webp`` for the
    picture and ``<id>.json`` beside it for title and theme. A shared index
    file would be a single point of corruption for a store whose whole job is
    holding on to something irreplaceable; a sidecar can only ever lose its own
    entry, and the directory listing stays the source of truth.

    Nothing is cached in memory: a handful of uploads is a cheap directory
    scan, and re-reading means a second window's upload is visible here without
    any invalidation protocol between them.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or (DATA_DIR / UPLOADS_DIRNAME)
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def _meta_path(self, upload_id: str) -> Path:
        return self._root / f"{upload_id}.json"

    def _image_path(self, upload_id: str) -> Path:
        return self._root / f"{upload_id}.webp"

    def _read(self, upload_id: str) -> UploadedWallpaper | None:
        image = self._image_path(upload_id)
        if not image.is_file():
            return None
        theme = "dark"
        title = "Your wallpaper"
        created = 0.0
        try:
            meta = json.loads(self._meta_path(upload_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A lost sidecar costs the title, not the picture: the image is the
            # part that cannot be reconstructed, so it still gets listed.
            meta = {}
        if isinstance(meta, dict):
            if str(meta.get("theme")) in {"light", "dark"}:
                theme = str(meta["theme"])
            if str(meta.get("title", "")).strip():
                title = str(meta["title"]).strip()[:60]
            try:
                created = float(meta.get("createdAt", 0.0))
            except (TypeError, ValueError):
                created = 0.0
        if not created:
            try:
                created = image.stat().st_mtime
            except OSError:
                created = 0.0
        return UploadedWallpaper(
            id=upload_id, title=title, theme=theme, created_at=created, path=image
        )

    def _write_meta(self, item: UploadedWallpaper) -> None:
        _atomic_write(
            self._meta_path(item.id),
            json.dumps(item.to_json(), ensure_ascii=False, indent=2).encode("utf-8"),
            prefix=".meta.",
            suffix=".json",
        )

    def list(self) -> list[UploadedWallpaper]:
        """Every upload, newest first — the one just added is the first tile."""
        if not self._root.is_dir():
            return []
        items: list[UploadedWallpaper] = []
        for image in self._root.glob("u*.webp"):
            if not _UPLOAD_ID_PATTERN.match(image.stem):
                continue
            item = self._read(image.stem)
            if item is not None:
                items.append(item)
        return sorted(items, key=lambda item: (-item.created_at, item.id))

    def get(self, upload_id: str) -> UploadedWallpaper | None:
        if not _UPLOAD_ID_PATTERN.match(upload_id):
            return None
        return self._read(upload_id)

    def add(self, data: bytes, filename: str = "") -> UploadedWallpaper:
        """Validate, re-encode, and store one uploaded picture.

        The incoming bytes are decoded by Pillow rather than trusted by their
        declared type, and what is written is the *re-encoded* image — so
        whatever else the upload carried (a forged header, an appended
        payload, EXIF) does not survive the round trip onto disk.
        """
        if not data:
            raise UploadRejected("That file is empty.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise UploadRejected(
                f"That image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                status_code=413,
            )

        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - Pillow is a hard dependency
            raise UploadRejected(
                "Image support is unavailable on this install.", status_code=503
            ) from exc

        try:
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                prepared = source.convert("RGB")
        except Exception as exc:  # noqa: BLE001 — any decode failure = not an image
            raise UploadRejected(
                "That file is not an image the app can read (PNG, JPEG, WebP, GIF or BMP)."
            ) from exc

        if prepared.width > UPLOAD_MAX_WIDTH:
            height = max(1, round(prepared.height * UPLOAD_MAX_WIDTH / prepared.width))
            prepared = prepared.resize((UPLOAD_MAX_WIDTH, height), Image.Resampling.LANCZOS)
        theme = _detect_theme(prepared)

        buffer = io.BytesIO()
        prepared.save(buffer, "WEBP", quality=UPLOAD_QUALITY, method=4)
        prepared.close()

        with self._lock:
            try:
                self._root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise UploadRejected(
                    "The app could not write to its data directory.", status_code=500
                ) from exc
            upload_id = f"u{secrets.token_hex(8)}"
            item = UploadedWallpaper(
                id=upload_id,
                title=_clean_title(filename),
                theme=theme,
                created_at=time.time(),
                path=self._image_path(upload_id),
            )
            try:
                _atomic_write(item.path, buffer.getvalue(), prefix=".upload.", suffix=".webp")
                self._write_meta(item)
            except OSError as exc:
                raise UploadRejected(
                    "The app could not store that image.", status_code=500
                ) from exc
        logger.info("Wallpaper upload stored: {} ({})", item.id, item.title)
        return item

    def set_theme(self, upload_id: str, theme: str) -> UploadedWallpaper | None:
        """Correct the light/dark guess made at upload time."""
        if theme not in {"light", "dark"}:
            raise UploadRejected("Theme must be 'light' or 'dark'.")
        with self._lock:
            item = self.get(upload_id)
            if item is None:
                return None
            updated = UploadedWallpaper(
                id=item.id,
                title=item.title,
                theme=theme,
                created_at=item.created_at,
                path=item.path,
            )
            try:
                self._write_meta(updated)
            except OSError as exc:
                raise UploadRejected(
                    "The app could not save that change.", status_code=500
                ) from exc
        return updated

    def remove(self, upload_id: str) -> bool:
        """Delete one upload, picture and sidecar and thumbnail."""
        if not _UPLOAD_ID_PATTERN.match(upload_id):
            return False
        with self._lock:
            image = self._image_path(upload_id)
            existed = image.is_file()
            for path in (
                image,
                self._meta_path(upload_id),
                self._root / THUMBS_DIRNAME / f"{upload_id}.webp",
            ):
                try:
                    path.unlink()
                except OSError:
                    # Already gone, or held open by a reader on Windows. The
                    # entry disappears from the listing either way.
                    pass
        return existed

    def thumbnail(self, item: UploadedWallpaper) -> Path:
        """A grid thumbnail for an upload, derived and cached like the library's."""
        target = self._root / THUMBS_DIRNAME / f"{item.id}.webp"
        return _derive_thumbnail(item.path, target, item.id)


def _detect_theme(image: Any) -> str:
    """Guess whether a picture belongs to light or dark mode.

    Mean luminance of a thumbnail-sized copy: bright artwork under dark chrome
    (or the reverse) is the thing that reads as a bug, and mean brightness is
    what that judgement actually turns on. A wrong guess is one click to fix,
    so this stays a cheap heuristic rather than a saliency model.
    """
    try:
        from PIL import ImageStat

        small = image.convert("L").resize((32, 32))
        mean = ImageStat.Stat(small).mean[0]
    except Exception:  # noqa: BLE001 — a guess is never worth failing an upload
        return "dark"
    return "light" if mean >= _LIGHT_THEME_LUMA else "dark"


def _atomic_write(target: Path, payload: bytes, *, prefix: str, suffix: str) -> None:
    """Write bytes so a reader sees either the old file or the whole new one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _derive_thumbnail(source_path: Path, target: Path, label: str) -> Path:
    """Shared thumbnail derivation for both stores.

    Falls back to the original file when Pillow is unavailable or the resize
    fails: a heavier grid is a far better outcome than an empty one.
    """
    try:
        if target.is_file() and target.stat().st_mtime >= source_path.stat().st_mtime:
            return target
    except OSError:
        return source_path

    try:
        from PIL import Image
    except ImportError:
        return source_path

    try:
        with Image.open(source_path) as source:
            source.load()
            thumb = source.convert("RGB")
            height = max(1, round(thumb.height * THUMB_WIDTH / thumb.width))
            thumb = thumb.resize((THUMB_WIDTH, height), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            thumb.save(buffer, "WEBP", quality=THUMB_QUALITY, method=4)
        # Staged through a temporary name: two browsers scrolling the same row
        # must not read a half-written file.
        _atomic_write(target, buffer.getvalue(), prefix=".thumb.", suffix=".webp")
        return target
    except (OSError, ValueError) as exc:
        logger.warning("Could not derive wallpaper thumbnail for {}: {}", label, exc)
        return source_path


def register_wallpaper_routes(
    app: FastAPI,
    library: WallpaperLibrary | None = None,
    uploads: WallpaperUploads | None = None,
) -> None:
    """Mount the wallpaper endpoints under ``/api``.

    Deliberately under ``/api`` rather than a static mount of its own: that
    prefix is already proxied by the Vite dev server, so the picker behaves
    identically in development and in the packaged app.
    """
    lib = library or WallpaperLibrary()
    own = uploads or WallpaperUploads()

    @app.get("/api/wallpapers")
    async def list_wallpapers() -> dict[str, Any]:
        return lib.catalog()

    # The upload routes are declared before the ``{item_id}`` ones purely for
    # reading order — they never collide, because every upload path carries the
    # literal ``uploads`` segment at a depth the catalog routes do not use.

    @app.get("/api/wallpapers/uploads")
    async def list_uploads() -> dict[str, Any]:
        return {"items": [item.to_json() for item in own.list()]}

    @app.post("/api/wallpapers/uploads", response_model=None)
    async def upload_wallpaper(
        file: UploadFile = File(...),  # noqa: B008 — FastAPI dependency default
    ) -> dict[str, Any] | JSONResponse:
        # Read with a ceiling rather than whole: UploadFile spools to disk, so
        # an oversized upload must be refused before it is fully absorbed.
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        try:
            item = own.add(data, file.filename or "")
        except UploadRejected as exc:
            return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
        return item.to_json()

    @app.patch("/api/wallpapers/uploads/{upload_id}", response_model=None)
    async def retheme_upload(upload_id: str, body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
        try:
            item = own.set_theme(upload_id, str(body.get("theme", "")))
        except UploadRejected as exc:
            return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
        if item is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return item.to_json()

    @app.delete("/api/wallpapers/uploads/{upload_id}", response_model=None)
    async def delete_upload(upload_id: str) -> dict[str, Any] | JSONResponse:
        if not own.remove(upload_id):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return {"removed": upload_id}

    @app.get("/api/wallpapers/uploads/{upload_id}/thumb", response_model=None)
    async def upload_thumb(upload_id: str) -> FileResponse | JSONResponse:
        item = own.get(upload_id)
        if item is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(
            str(own.thumbnail(item)),
            media_type="image/webp",
            headers=_CACHE_HEADERS,
        )

    @app.get("/api/wallpapers/uploads/{upload_id}/full", response_model=None)
    async def upload_full(upload_id: str) -> FileResponse | JSONResponse:
        item = own.get(upload_id)
        if item is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(
            str(item.path),
            media_type="image/webp",
            headers=_CACHE_HEADERS,
        )

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
