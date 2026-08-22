"""The wallpaper publish lane: field rules, byte handling, submit — no network.

The lane's promise is narrower than the package lane's and its failures are
different in kind, so it gets its own file: a wallpaper carries no manifest to
validate, but it does carry bytes a stranger produced, and the rules that
matter most here are the ones about those bytes.
"""

from __future__ import annotations

import io
from typing import Any

import httpx
import pytest

from jarvis.marketplace import publish
from jarvis.marketplace.token_store import Tokens


def _draft(**overrides: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "title": "Rain Antenna City",
        "description": "Neon rooftops in the rain.",
        "license": "CC0-1.0",
        "theme": "dark",
        "rights": True,
    }
    draft.update(overrides)
    return draft


class FakeStore:
    def __init__(self, tokens: Tokens | None = None) -> None:
        self._tokens = tokens

    def load(self, key: str) -> Tokens | None:
        return self._tokens

    def save(self, key: str, tokens: Tokens) -> None:
        self._tokens = tokens

    def delete(self, key: str) -> None:
        self._tokens = None


def _png(width: int = 64, height: int = 32, *, with_exif: bool = False) -> bytes:
    """A real PNG, optionally carrying EXIF, as a browser would hand one over."""
    from PIL import Image

    image = Image.new("RGB", (width, height), (40, 60, 90))
    buffer = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        # 0x9286 = UserComment, 0x0001 under GPSInfo would be the interesting
        # one in the field; either proves the round trip drops what it carried.
        exif[0x9286] = "shot at home"
        image.save(buffer, "PNG", exif=exif)
    else:
        image.save(buffer, "PNG")
    return buffer.getvalue()


# --- validate_wallpaper_draft ---------------------------------------------


def test_valid_draft_normalizes_to_the_form_the_endpoint_reads() -> None:
    fields, errors = publish.validate_wallpaper_draft(_draft())
    assert errors == []
    assert fields == {
        "title": "Rain Antenna City",
        "license": "CC0-1.0",
        "rights": "yes",
        "description": "Neon rooftops in the rain.",
        "theme": "dark",
    }


def test_optional_fields_are_omitted_rather_than_sent_empty() -> None:
    fields, errors = publish.validate_wallpaper_draft(_draft(description="", theme=""))
    assert errors == [] and fields is not None
    assert "description" not in fields and "theme" not in fields


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"title": ""}, "title"),
        ({"title": "x" * (publish.MAX_TITLE_CHARS + 1)}, "title"),
        # Slugifies to nothing at the endpoint, which would 422 after the
        # upload had already travelled.
        ({"title": "!!! ---"}, "title"),
        ({"description": "x" * (publish.MAX_DESCRIPTION_CHARS + 1)}, "description"),
        ({"license": ""}, "license"),
        ({"license": "MIT"}, "license"),
        ({"license": "All rights reserved"}, "license"),
        ({"theme": "sepia"}, "theme"),
        ({"rights": False}, "rights"),
    ],
)
def test_rejections_carry_the_field(overrides: dict[str, Any], field: str) -> None:
    fields, errors = publish.validate_wallpaper_draft(_draft(**overrides))
    assert fields is None
    assert any(e["field"] == field for e in errors)


def test_every_advertised_license_is_accepted() -> None:
    for license_id in publish.WALLPAPER_LICENSES:
        fields, errors = publish.validate_wallpaper_draft(_draft(license=license_id))
        assert errors == [] and fields is not None


def test_the_rights_statement_cannot_be_faked_with_a_truthy_string() -> None:
    """``"no"`` is truthy in Python — the check must be identity, not truth."""
    fields, errors = publish.validate_wallpaper_draft(_draft(rights="no"))
    assert fields is None
    assert any(e["field"] == "rights" for e in errors)


# --- prepare_wallpaper_image ----------------------------------------------


def test_the_published_bytes_are_re_encoded_webp_not_the_originals() -> None:
    original = _png()
    encoded, filename = publish.prepare_wallpaper_image(original)
    assert filename == "wallpaper.webp"
    assert encoded[:4] == b"RIFF" and encoded[8:12] == b"WEBP"
    assert encoded != original


def test_exif_does_not_survive_the_round_trip() -> None:
    from PIL import Image

    carrying = _png(with_exif=True)
    with Image.open(io.BytesIO(carrying)) as before:
        assert dict(before.getexif())  # the fixture really did carry something

    encoded, _ = publish.prepare_wallpaper_image(carrying)
    with Image.open(io.BytesIO(encoded)) as after:
        assert not dict(after.getexif())


def test_an_oversized_picture_is_capped_at_4k_rather_than_refused() -> None:
    from PIL import Image

    encoded, _ = publish.prepare_wallpaper_image(_png(5000, 2500))
    with Image.open(io.BytesIO(encoded)) as result:
        assert result.width == 3840


def test_a_picture_under_the_cap_keeps_its_size() -> None:
    from PIL import Image

    encoded, _ = publish.prepare_wallpaper_image(_png(1200, 800))
    with Image.open(io.BytesIO(encoded)) as result:
        assert (result.width, result.height) == (1200, 800)


@pytest.mark.parametrize(
    ("data", "status"),
    [
        (b"", 422),
        (b"not an image at all", 422),
        # A forged header with no image behind it: the declared type never
        # decides anything, the decoder does.
        (b"\x89PNG\r\n\x1a\n" + b"junk" * 20, 422),
    ],
)
def test_bytes_that_are_not_an_image_are_refused(data: bytes, status: int) -> None:
    with pytest.raises(publish.SubmitError) as exc:
        publish.prepare_wallpaper_image(data)
    assert exc.value.status == status
    assert exc.value.field == "file"


def test_the_result_stays_inside_the_endpoints_size_cap() -> None:
    encoded, _ = publish.prepare_wallpaper_image(_png(3840, 2160))
    assert len(encoded) <= publish.MAX_WALLPAPER_BYTES


# --- submit_wallpaper ------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_sends_bearer_multipart_and_returns_the_server_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        publish, "publish_wallpaper_endpoint", lambda: "https://pj.example/api/submit-wallpaper"
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["type"] = request.headers.get("Content-Type", "")
        seen["body"] = request.content
        return httpx.Response(201, json={"name": "rain-antenna-city"})

    fields, _ = publish.validate_wallpaper_draft(_draft())
    assert fields is not None
    result = await publish.submit_wallpaper(
        fields,
        b"IMAGEBYTES",
        store=FakeStore(Tokens(access="tok")),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )

    assert result == {"name": "rain-antenna-city"}
    assert seen["auth"] == "Bearer tok"
    assert seen["type"].startswith("multipart/form-data")
    # The rights statement and the picture both have to be in the body — the
    # endpoint refuses without either, and a form that quietly dropped one
    # would fail far from where it was caused.
    assert b'name="rights"' in seen["body"] and b"yes" in seen["body"]
    assert b"IMAGEBYTES" in seen["body"]


@pytest.mark.asyncio
async def test_the_client_never_claims_a_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Identity comes from the token. Anything else would be a claim to check."""
    monkeypatch.setattr(
        publish, "publish_wallpaper_endpoint", lambda: "https://pj.example/api/submit-wallpaper"
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(201, json={"name": "x"})

    fields, _ = publish.validate_wallpaper_draft(_draft())
    assert fields is not None
    await publish.submit_wallpaper(
        fields,
        b"IMAGEBYTES",
        store=FakeStore(Tokens(access="tok")),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )
    assert b'name="publisher"' not in seen["body"]
    assert b'name="publisher_id"' not in seen["body"]


@pytest.mark.asyncio
async def test_submit_without_a_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        publish, "publish_wallpaper_endpoint", lambda: "https://pj.example/api/submit-wallpaper"
    )
    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit_wallpaper({"title": "x"}, b"bytes", store=FakeStore(None))  # type: ignore[arg-type]
    assert exc.value.status == 401


@pytest.mark.asyncio
async def test_the_quota_refusal_reaches_the_user_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        publish, "publish_wallpaper_endpoint", lambda: "https://pj.example/api/submit-wallpaper"
    )
    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit_wallpaper(
            {"title": "x"},
            b"bytes",
            store=FakeStore(Tokens(access="tok")),  # type: ignore[arg-type]
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    429, json={"error": "daily limit of 3 wallpaper uploads reached"}
                )
            ),
        )
    assert exc.value.status == 429
    assert "daily limit" in exc.value.error


@pytest.mark.asyncio
async def test_a_deployment_without_the_lane_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "publish_wallpaper_endpoint", lambda: "")
    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit_wallpaper({"title": "x"}, b"bytes", store=FakeStore(None))  # type: ignore[arg-type]
    assert exc.value.status == 503


@pytest.mark.asyncio
async def test_an_unreachable_endpoint_is_502_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        publish, "publish_wallpaper_endpoint", lambda: "https://pj.example/api/submit-wallpaper"
    )

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit_wallpaper(
            {"title": "x"},
            b"bytes",
            store=FakeStore(Tokens(access="tok")),  # type: ignore[arg-type]
            transport=httpx.MockTransport(boom),
        )
    assert exc.value.status == 502


# --- live_status -----------------------------------------------------------


@pytest.mark.asyncio
async def test_live_status_sees_a_published_wallpaper(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wallpaper is live when the same feed the store reads carries it."""
    from jarvis.marketplace import community_source

    index = community_source.CommunityIndex(
        revision=13,
        wallpapers=[
            {
                "name": "rain-antenna-city",
                "version": "1.0.0",
                "title": "Rain Antenna City",
                "image_url": "https://example.invalid/rain.webp",
            }
        ],
    )

    async def fake_get_index(force: bool = False) -> tuple[Any, str]:
        return index, "ok"

    monkeypatch.setattr(community_source, "get_index", fake_get_index)
    assert (await publish.live_status("rain-antenna-city", "1.0.0"))["live"] is True
    assert (await publish.live_status("rain-antenna-city", "2.0.0"))["live"] is False


# --- POST /api/marketplace/publish/submit-wallpaper ------------------------


@pytest.fixture()
def route_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """The route wired to a wallpaper store on disk and a stubbed endpoint."""
    from fastapi import FastAPI

    from jarvis.ui.web import marketplace_publish_routes as routes
    from jarvis.ui.web.wallpapers import WallpaperUploads

    store = WallpaperUploads(root=tmp_path / "uploads")
    monkeypatch.setattr(routes, "TokenStore", lambda: FakeStore(Tokens(access="tok")))
    monkeypatch.setattr("jarvis.ui.web.wallpapers.WallpaperUploads", lambda: store)
    monkeypatch.setattr(
        publish, "publish_wallpaper_endpoint", lambda: "https://pj.example/api/submit-wallpaper"
    )

    async def fake_submit(fields: Any, image: bytes, filename: str = "wallpaper.webp") -> Any:
        return {"name": "rain-antenna-city"}

    monkeypatch.setattr(publish, "submit_wallpaper", fake_submit)

    app = FastAPI()
    app.include_router(routes.router)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return client, store


@pytest.mark.asyncio
async def test_route_publishes_one_of_the_pickers_own_pictures(route_client: Any) -> None:
    client, store = route_client
    item = store.add(_png(), "my-city.png")
    async with client:
        resp = await client.post(
            "/api/marketplace/publish/submit-wallpaper",
            json={**_draft(), "upload_id": item.id},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "rain-antenna-city"
    assert body["version"] == "1.0.0"
    # The line other people will run to get it — computed by the same module
    # the store and the CLI use, never spelled out in the view.
    assert body["install"]


@pytest.mark.asyncio
async def test_route_refuses_to_republish_an_imported_community_picture(
    route_client: Any,
) -> None:
    """Someone else's wallpaper must not get a new author by passing through
    the picker — that would launder authorship, not share a picture."""
    client, store = route_client
    from jarvis.ui.web.wallpapers import WallpaperOrigin

    item = store.add(
        _png(),
        "borrowed.png",
        source="marketplace",
        origin=WallpaperOrigin(source_id="rain-antenna-city", publisher="someone"),
    )
    async with client:
        resp = await client.post(
            "/api/marketplace/publish/submit-wallpaper",
            json={**_draft(), "upload_id": item.id},
        )
    assert resp.status_code == 409
    assert "community" in resp.json()["detail"]["error"]


@pytest.mark.asyncio
async def test_route_reports_a_vanished_picture_as_404(route_client: Any) -> None:
    client, _ = route_client
    async with client:
        resp = await client.post(
            "/api/marketplace/publish/submit-wallpaper",
            json={**_draft(), "upload_id": "u0000000000000000"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_route_checks_the_fields_before_it_touches_the_image(
    route_client: Any,
) -> None:
    client, _ = route_client
    async with client:
        resp = await client.post(
            "/api/marketplace/publish/submit-wallpaper",
            # No upload exists under this id — a 422 rather than a 404 proves
            # the field check ran first, which is what the form needs.
            json={**_draft(), "upload_id": "u0000000000000000", "rights": False},
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "rights"
