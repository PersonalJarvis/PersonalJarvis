"""Tests for the desktop-wallpaper library endpoints.

Contract (see jarvis/ui/web/wallpapers.py):
- GET /api/wallpapers                 -> catalog metadata; ``available: false``
                                         instead of an error when the generated
                                         library is not installed.
- GET /api/wallpapers/{id}/thumb      -> a small derived WebP for the browse
                                         grid, cached on disk after the first
                                         request. Never the full-size file when
                                         Pillow is available.
- GET /api/wallpapers/{id}/full       -> the original 1920x1080 artwork.
- Unknown or malformed ids            -> 404, including ids that try to walk out
                                         of the library directory.

The library itself is content living under a git-ignored ``data/`` directory,
so these tests build a miniature one in a tmp_path rather than depending on the
five hundred generated files being present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.wallpapers import (
    THUMB_WIDTH,
    WallpaperLibrary,
    register_wallpaper_routes,
)


def _write_wallpaper(path: Path, color: tuple[int, int, int]) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1920, 1080), color).save(path, "WEBP", quality=80)


@pytest.fixture()
def library(tmp_path: Path) -> WallpaperLibrary:
    """A two-entry library with the same shape as the generated one."""
    root = tmp_path / "jarvis-wallpaper-gallery"
    _write_wallpaper(root / "images" / "01-cinematic-photoreal" / "01.webp", (12, 18, 30))
    _write_wallpaper(root / "images" / "03-anime-neon" / "01.webp", (220, 90, 200))

    manifests = root / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "01-cinematic-photoreal.json").write_text(
        json.dumps(
            [
                {
                    "id": "01-cinematic-photoreal-01",
                    "title": "Flooded Observatory",
                    "style": "Cinematic Photorealistic",
                    "theme": "dark",
                    "file": "images/01-cinematic-photoreal/01.webp",
                    "prompt": "…",
                }
            ]
        ),
        encoding="utf-8",
    )
    # A manifest whose `style` is still a slug — the loader is expected to make
    # it presentable rather than show "anime-neon" beside written-out names.
    (manifests / "03-anime-neon.json").write_text(
        json.dumps(
            [
                {
                    "id": "03-anime-neon-01",
                    "title": "Neon Crossing",
                    "style": "anime-neon",
                    "theme": "light",
                    "file": "images/03-anime-neon/01.webp",
                    "prompt": "…",
                }
            ]
        ),
        encoding="utf-8",
    )
    return WallpaperLibrary(root)


@pytest.fixture()
def client(library: WallpaperLibrary) -> TestClient:
    app = FastAPI()
    register_wallpaper_routes(app, library)
    return TestClient(app)


def test_catalog_lists_every_entry(client: TestClient) -> None:
    payload = client.get("/api/wallpapers").json()

    assert payload["available"] is True
    assert payload["count"] == 2
    assert {item["id"] for item in payload["items"]} == {
        "01-cinematic-photoreal-01",
        "03-anime-neon-01",
    }
    assert {style["slug"] for style in payload["styles"]} == {
        "01-cinematic-photoreal",
        "03-anime-neon",
    }


def test_catalog_carries_no_image_bytes(client: TestClient) -> None:
    """The grid is built from metadata; the pictures come later, one by one."""
    payload = client.get("/api/wallpapers").json()

    assert set(payload["items"][0]) == {"id", "title", "style", "styleLabel", "theme"}


def test_slug_style_labels_are_made_readable(client: TestClient) -> None:
    payload = client.get("/api/wallpapers").json()
    labels = {item["id"]: item["styleLabel"] for item in payload["items"]}

    assert labels["03-anime-neon-01"] == "Anime Neon"


def test_missing_library_reports_itself_absent(tmp_path: Path) -> None:
    app = FastAPI()
    register_wallpaper_routes(app, WallpaperLibrary(tmp_path / "nothing-here"))

    payload = TestClient(app).get("/api/wallpapers").json()

    assert payload == {"available": False, "count": 0, "styles": [], "items": []}


def test_thumbnail_is_far_smaller_than_the_original(
    client: TestClient, library: WallpaperLibrary
) -> None:
    from PIL import Image

    response = client.get("/api/wallpapers/01-cinematic-photoreal-01/thumb")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    original = library.get("01-cinematic-photoreal-01")
    assert original is not None
    assert len(response.content) < original.path.stat().st_size

    import io

    with Image.open(io.BytesIO(response.content)) as thumb:
        assert thumb.width == THUMB_WIDTH


def test_thumbnail_is_cached_on_disk(client: TestClient, library: WallpaperLibrary) -> None:
    client.get("/api/wallpapers/01-cinematic-photoreal-01/thumb")

    cached = library.root / ".thumbs" / "01-cinematic-photoreal" / "01-cinematic-photoreal-01.webp"
    assert cached.is_file()
    # No leftover staging files from the atomic write.
    assert not list(cached.parent.glob("*.part"))


def test_full_serves_the_original_artwork(client: TestClient, library: WallpaperLibrary) -> None:
    response = client.get("/api/wallpapers/01-cinematic-photoreal-01/full")

    original = library.get("01-cinematic-photoreal-01")
    assert original is not None
    assert response.status_code == 200
    assert response.content == original.path.read_bytes()


@pytest.mark.parametrize(
    "item_id",
    [
        "does-not-exist-01",
        "../../../etc/passwd",
        "01-cinematic-photoreal-01/../../../secret",
        "",
    ],
)
def test_unknown_ids_are_refused(client: TestClient, item_id: str) -> None:
    assert client.get(f"/api/wallpapers/{item_id}/full").status_code == 404


def test_manifest_paths_pointing_outside_the_library_are_dropped(
    tmp_path: Path,
) -> None:
    """A hand-edited manifest must not turn the server into a file browser."""
    root = tmp_path / "gallery"
    _write_wallpaper(root / "images" / "01-style" / "01.webp", (1, 2, 3))
    secret = tmp_path / "secret.webp"
    _write_wallpaper(secret, (9, 9, 9))

    manifests = root / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "01-style.json").write_text(
        json.dumps(
            [
                {
                    "id": "01-style-01",
                    "title": "Escape",
                    "style": "Style",
                    "theme": "dark",
                    "file": "images/../../secret.webp",
                    "prompt": "…",
                }
            ]
        ),
        encoding="utf-8",
    )

    assert WallpaperLibrary(root).items() == {}
