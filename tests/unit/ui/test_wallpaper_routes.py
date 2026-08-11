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
- POST /api/wallpapers/uploads        -> store one of the owner's own pictures,
                                         re-encoded, with a guessed light/dark.
- GET/PATCH/DELETE .../uploads[/{id}] -> list them, correct the guess, remove one.

The library itself is content living under a git-ignored ``data/`` directory,
so these tests build a miniature one in a tmp_path rather than depending on the
five hundred generated files being present.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.wallpapers import (
    MAX_UPLOAD_BYTES,
    THUMB_WIDTH,
    UPLOAD_MAX_WIDTH,
    WallpaperLibrary,
    WallpaperLibraryInstaller,
    WallpaperUploads,
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
def uploads(tmp_path: Path) -> WallpaperUploads:
    """An empty upload store, well away from the maintainer's real one."""
    return WallpaperUploads(tmp_path / "jarvis-wallpaper-uploads")


@pytest.fixture()
def client(library: WallpaperLibrary, uploads: WallpaperUploads) -> TestClient:
    app = FastAPI()
    register_wallpaper_routes(app, library, uploads)
    return TestClient(app)


def _image_bytes(
    color: tuple[int, int, int],
    size: tuple[int, int] = (1920, 1080),
    fmt: str = "PNG",
) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


def _upload(client: TestClient, data: bytes, name: str = "my holiday_photo.png") -> dict:
    response = client.post(
        "/api/wallpapers/uploads",
        files={"file": (name, data, "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()


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


# ----------------------------------------------------------------------
# Uploads — the owner's own pictures.
# ----------------------------------------------------------------------


def test_upload_is_listed_and_served(client: TestClient) -> None:
    item = _upload(client, _image_bytes((10, 12, 20)))

    listed = client.get("/api/wallpapers/uploads").json()["items"]
    assert [entry["id"] for entry in listed] == [item["id"]]

    full = client.get(f"/api/wallpapers/uploads/{item['id']}/full")
    assert full.status_code == 200
    assert full.headers["content-type"] == "image/webp"


def test_upload_keeps_the_library_catalog_untouched(client: TestClient) -> None:
    """Two stores, two endpoints: an upload is not a library entry."""
    _upload(client, _image_bytes((10, 12, 20)))

    catalog = client.get("/api/wallpapers").json()

    assert catalog["count"] == 2
    assert all(not item["id"].startswith("u") for item in catalog["items"])


def test_upload_title_comes_from_the_file_name(client: TestClient) -> None:
    item = _upload(client, _image_bytes((10, 12, 20)), name="sunset_over-the_bay.jpg")

    assert item["title"] == "sunset over the bay"


def test_upload_without_a_usable_name_still_gets_a_title(client: TestClient) -> None:
    item = _upload(client, _image_bytes((10, 12, 20)), name="___.png")

    assert item["title"] == "Your wallpaper"


def test_dark_and_light_pictures_are_told_apart(client: TestClient) -> None:
    """The guess is what decides which mode the app switches into."""
    night = _upload(client, _image_bytes((8, 10, 24)))
    noon = _upload(client, _image_bytes((238, 236, 228)))

    assert night["theme"] == "dark"
    assert noon["theme"] == "light"


def test_the_guess_can_be_corrected(client: TestClient) -> None:
    item = _upload(client, _image_bytes((8, 10, 24)))

    response = client.patch(f"/api/wallpapers/uploads/{item['id']}", json={"theme": "light"})

    assert response.status_code == 200
    assert response.json()["theme"] == "light"
    listed = client.get("/api/wallpapers/uploads").json()["items"]
    assert listed[0]["theme"] == "light"


def test_a_nonsense_theme_is_refused(client: TestClient) -> None:
    item = _upload(client, _image_bytes((8, 10, 24)))

    response = client.patch(f"/api/wallpapers/uploads/{item['id']}", json={"theme": "sepia"})

    assert response.status_code == 400


def test_oversized_pictures_are_scaled_down_on_the_way_in(client: TestClient) -> None:
    """A wallpaper never needs more pixels than the largest desktop."""
    from PIL import Image

    item = _upload(client, _image_bytes((30, 40, 50), size=(6000, 3000)))

    full = client.get(f"/api/wallpapers/uploads/{item['id']}/full")
    with Image.open(io.BytesIO(full.content)) as stored:
        assert stored.width == UPLOAD_MAX_WIDTH


def test_upload_thumbnail_is_derived_and_cached(
    client: TestClient, uploads: WallpaperUploads
) -> None:
    from PIL import Image

    item = _upload(client, _image_bytes((30, 40, 50)))

    response = client.get(f"/api/wallpapers/uploads/{item['id']}/thumb")

    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as thumb:
        assert thumb.width == THUMB_WIDTH
    assert (uploads.root / ".thumbs" / f"{item['id']}.webp").is_file()


def test_a_file_that_is_not_an_image_is_refused(client: TestClient) -> None:
    """A forged content type must not put arbitrary bytes on disk."""
    response = client.post(
        "/api/wallpapers/uploads",
        files={"file": ("payload.png", b"MZ\x90\x00 not a picture", "image/png")},
    )

    assert response.status_code == 400
    assert client.get("/api/wallpapers/uploads").json()["items"] == []


def test_an_empty_file_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/wallpapers/uploads", files={"file": ("empty.png", b"", "image/png")}
    )

    assert response.status_code == 400


def test_an_oversized_file_is_refused_rather_than_absorbed(client: TestClient) -> None:
    response = client.post(
        "/api/wallpapers/uploads",
        files={"file": ("huge.png", b"\x00" * (MAX_UPLOAD_BYTES + 1), "image/png")},
    )

    assert response.status_code == 413


def test_deleting_an_upload_removes_every_trace(
    client: TestClient, uploads: WallpaperUploads
) -> None:
    item = _upload(client, _image_bytes((30, 40, 50)))
    client.get(f"/api/wallpapers/uploads/{item['id']}/thumb")

    assert client.delete(f"/api/wallpapers/uploads/{item['id']}").status_code == 200

    assert client.get("/api/wallpapers/uploads").json()["items"] == []
    assert client.get(f"/api/wallpapers/uploads/{item['id']}/full").status_code == 404
    assert list(uploads.root.glob(f"{item['id']}*")) == []
    assert not (uploads.root / ".thumbs" / f"{item['id']}.webp").exists()


def test_deleting_something_that_is_not_there_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/wallpapers/uploads/u0123456789abcdef").status_code == 404


@pytest.mark.parametrize(
    "upload_id",
    ["../../../etc/passwd", "01-cinematic-photoreal-01", "u00", "uZZZZZZZZZZZZZZZZ"],
)
def test_malformed_upload_ids_are_refused(client: TestClient, upload_id: str) -> None:
    assert client.get(f"/api/wallpapers/uploads/{upload_id}/full").status_code == 404
    assert client.delete(f"/api/wallpapers/uploads/{upload_id}").status_code == 404


def test_uploads_are_listed_newest_first(client: TestClient) -> None:
    """The picture just added is the one being looked for."""
    first = _upload(client, _image_bytes((10, 10, 10)), name="one.png")
    second = _upload(client, _image_bytes((20, 20, 20)), name="two.png")

    listed = client.get("/api/wallpapers/uploads").json()["items"]

    assert [entry["id"] for entry in listed] == [second["id"], first["id"]]


def test_an_upload_survives_a_lost_sidecar(client: TestClient, uploads: WallpaperUploads) -> None:
    """The picture is the irreplaceable half; a missing title must not hide it."""
    item = _upload(client, _image_bytes((10, 10, 10)))
    (uploads.root / f"{item['id']}.json").unlink()

    listed = client.get("/api/wallpapers/uploads").json()["items"]

    assert [entry["id"] for entry in listed] == [item["id"]]
    assert listed[0]["title"] == "Your wallpaper"


def test_a_missing_upload_directory_is_an_empty_list_not_an_error(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    register_wallpaper_routes(
        app,
        WallpaperLibrary(tmp_path / "nothing-here"),
        WallpaperUploads(tmp_path / "no-uploads-either"),
    )

    assert TestClient(app).get("/api/wallpapers/uploads").json() == {"items": []}


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


# ---------------------------------------------------------------------------
# The library installer: how a fresh machine gets the five hundred wallpapers
# the repository deliberately does not carry.
# ---------------------------------------------------------------------------


def _library_archive(tmp_path: Path, name: str = "library.zip") -> Path:
    """A miniature packaged library, shaped like the released one."""
    import zipfile

    image = tmp_path / "src-image.webp"
    _write_wallpaper(image, (30, 40, 50))
    manifest = [
        {
            "id": "01-cinematic-photoreal-01",
            "title": "Flooded Observatory",
            "style": "Cinematic Photorealistic",
            "theme": "dark",
            "file": "images/01-cinematic-photoreal/01.webp",
        }
    ]
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifests/01-cinematic-photoreal.json", json.dumps(manifest))
        bundle.write(image, "images/01-cinematic-photoreal/01.webp")
    return archive


def _wait_for_install(client: TestClient, timeout: float = 10.0) -> dict:
    """Poll the status endpoint until the daemon thread settles."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/api/wallpapers/library").json()
        if status["state"] in {"done", "error"}:
            return status
        time.sleep(0.02)
    pytest.fail(f"install never settled: {status}")


def _install_client(tmp_path: Path, archive_name: str = "library.zip") -> TestClient:
    """An app whose installer pulls from a local archive instead of GitHub."""
    library = WallpaperLibrary(tmp_path / "data" / "jarvis-wallpaper-gallery")
    installer = WallpaperLibraryInstaller(
        library, url=(tmp_path / archive_name).as_uri()
    )
    app = FastAPI()
    register_wallpaper_routes(
        app, library, WallpaperUploads(tmp_path / "uploads"), installer
    )
    return TestClient(app)


def test_installing_the_library_fills_the_catalog(tmp_path: Path) -> None:
    _library_archive(tmp_path)
    client = _install_client(tmp_path)
    assert client.get("/api/wallpapers").json()["available"] is False

    started = client.post("/api/wallpapers/library/install").json()
    assert started["state"] in {"downloading", "unpacking", "done"}
    assert _wait_for_install(client)["state"] == "done"

    payload = client.get("/api/wallpapers").json()
    assert payload["available"] is True
    assert payload["items"][0]["id"] == "01-cinematic-photoreal-01"
    # The downloaded archive and the staging directory are cleaned up.
    leftovers = list((tmp_path / "data").glob(".wallpaper-library*"))
    assert leftovers == []


def test_an_already_installed_library_is_not_downloaded_again(
    tmp_path: Path, library: WallpaperLibrary
) -> None:
    installer = WallpaperLibraryInstaller(library, url="https://127.0.0.1:1/nope.zip")
    app = FastAPI()
    register_wallpaper_routes(app, library, WallpaperUploads(tmp_path / "u"), installer)
    client = TestClient(app)

    assert client.get("/api/wallpapers/library").json()["installed"] is True
    # Starting anyway is a no-op answered with "done", not a download attempt.
    assert client.post("/api/wallpapers/library/install").json()["state"] == "done"


def test_an_unreachable_archive_is_an_error_state_not_a_crash(tmp_path: Path) -> None:
    client = _install_client(tmp_path, archive_name="missing.zip")

    client.post("/api/wallpapers/library/install")
    status = _wait_for_install(client)

    assert status["state"] == "error"
    assert status["error"]
    assert client.get("/api/wallpapers").json()["available"] is False


def test_an_archive_reaching_outside_the_library_is_refused(tmp_path: Path) -> None:
    """Zip-slip: a member path must not be able to escape the staging area."""
    import zipfile

    archive = tmp_path / "library.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifests/01-style.json", "[]")
        bundle.writestr("images/../../evil.txt", "gotcha")
    client = _install_client(tmp_path)

    client.post("/api/wallpapers/library/install")
    status = _wait_for_install(client)

    assert status["state"] == "error"
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "data" / "jarvis-wallpaper-gallery").exists()


def test_an_archive_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    (tmp_path / "library.zip").write_bytes(b"this is an html error page")
    client = _install_client(tmp_path)

    client.post("/api/wallpapers/library/install")
    status = _wait_for_install(client)

    assert status["state"] == "error"
    assert "archive" in status["error"]
