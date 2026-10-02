"""The REAL network edge of the frozen updater: ``HttpxAssetFetcher``.

``test_installer_update.py`` fakes the fetcher wholesale; this file keeps the
real httpx client and fakes only the wire, through ``httpx.MockTransport``.
That is where redirects, size caps and progress actually happen, so this is
where they are proven:

* GitHub answers an asset URL with a redirect to its download CDN — followed,
  but only ever over https.
* The checksum manifest and the installer are both size-capped WHILE they
  stream, never after the whole body sits in memory or on disk.
* Progress reports a real total when the server states one and falls back to
  the release metadata when it does not.

No socket is opened; every case runs on Windows, macOS and Linux alike.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest

from jarvis.core.installer_update import (
    CHECKSUMS_ASSET_NAME,
    MAX_CHECKSUMS_BYTES,
    HttpxAssetFetcher,
    InstallerAsset,
    InstallerUpdateError,
    download_and_verify,
)

ASSET_NAME = "PersonalJarvis-Setup-x64.exe"
PAYLOAD = b"a real installer would be a few hundred MB " * 2048
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
RELEASE = "https://github.com/PersonalJarvis/PersonalJarvis/releases/download/v9.9.9"
CDN = "https://release-assets.githubusercontent.com/github-production-release-asset"


def _asset(name: str, size: int = len(PAYLOAD)) -> InstallerAsset:
    return InstallerAsset(name=name, url=f"{RELEASE}/{name}", size=size)


class _Wire:
    """A scripted GitHub: URL -> response factory, plus a log of every hop."""

    def __init__(self, routes: dict[str, Callable[[], httpx.Response]]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requested.append(url)
        factory = self.routes.get(url)
        return factory() if factory is not None else httpx.Response(404)

    def fetcher(self) -> HttpxAssetFetcher:
        return HttpxAssetFetcher(transport=httpx.MockTransport(self.handler))


def _redirect(target: str) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(302, headers={"Location": target})


def _body(payload: bytes) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(200, content=payload)


def _github_release(
    *, payload: bytes = PAYLOAD, manifest: str | None = None
) -> dict[str, Callable[[], httpx.Response]]:
    """The two hops a real release download takes for each asset."""
    text = manifest if manifest is not None else f"{DIGEST}  {ASSET_NAME}\n"
    return {
        f"{RELEASE}/{ASSET_NAME}": _redirect(f"{CDN}/installer"),
        f"{CDN}/installer": _body(payload),
        f"{RELEASE}/{CHECKSUMS_ASSET_NAME}": _redirect(f"{CDN}/manifest"),
        f"{CDN}/manifest": _body(text.encode("utf-8")),
    }


async def test_the_real_client_follows_githubs_redirect_and_verifies(tmp_path: Path) -> None:
    wire = _Wire(_github_release())
    ticks: list[tuple[int, int | None]] = []

    installer = await download_and_verify(
        _asset(ASSET_NAME),
        _asset(CHECKSUMS_ASSET_NAME, size=0),
        dest_dir=tmp_path,
        fetcher=wire.fetcher(),
        on_progress=lambda written, total: ticks.append((written, total)),
    )

    assert installer.read_bytes() == PAYLOAD
    assert f"{CDN}/installer" in wire.requested
    # The bar starts at zero and ends on exactly the published size.
    assert ticks[0] == (0, len(PAYLOAD))
    assert ticks[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert [written for written, _total in ticks] == sorted(written for written, _ in ticks)


async def test_a_tampered_download_is_refused_through_the_real_client(tmp_path: Path) -> None:
    wire = _Wire(_github_release(payload=PAYLOAD + b"one injected byte"))
    with pytest.raises(InstallerUpdateError, match="SHA-256"):
        await download_and_verify(
            _asset(ASSET_NAME),
            _asset(CHECKSUMS_ASSET_NAME),
            dest_dir=tmp_path,
            fetcher=wire.fetcher(),
        )
    assert not (tmp_path / ASSET_NAME).exists()


async def test_a_redirect_to_plain_http_is_refused_before_it_is_sent(tmp_path: Path) -> None:
    routes = _github_release()
    routes[f"{RELEASE}/{ASSET_NAME}"] = _redirect("http://downgrade.invalid/installer")
    routes["http://downgrade.invalid/installer"] = _body(PAYLOAD)
    wire = _Wire(routes)

    with pytest.raises(InstallerUpdateError, match="non-https"):
        await download_and_verify(
            _asset(ASSET_NAME),
            _asset(CHECKSUMS_ASSET_NAME),
            dest_dir=tmp_path,
            fetcher=wire.fetcher(),
        )

    assert "http://downgrade.invalid/installer" not in wire.requested
    assert not (tmp_path / ASSET_NAME).exists()


async def test_a_manifest_redirected_to_plain_http_is_refused(tmp_path: Path) -> None:
    routes = _github_release()
    routes[f"{RELEASE}/{CHECKSUMS_ASSET_NAME}"] = _redirect("http://downgrade.invalid/sums")
    wire = _Wire(routes)

    with pytest.raises(InstallerUpdateError, match="non-https"):
        await download_and_verify(
            _asset(ASSET_NAME),
            _asset(CHECKSUMS_ASSET_NAME),
            dest_dir=tmp_path,
            fetcher=wire.fetcher(),
        )
    # Nothing was downloaded without a trustworthy manifest.
    assert f"{CDN}/installer" not in wire.requested


async def test_an_http_error_status_is_a_refusal(tmp_path: Path) -> None:
    routes = _github_release()
    routes[f"{CDN}/installer"] = lambda: httpx.Response(503)
    with pytest.raises(InstallerUpdateError, match="could not download"):
        await download_and_verify(
            _asset(ASSET_NAME),
            _asset(CHECKSUMS_ASSET_NAME),
            dest_dir=tmp_path,
            fetcher=_Wire(routes).fetcher(),
        )
    assert not (tmp_path / ASSET_NAME).exists()


async def test_an_oversized_manifest_is_refused_while_it_streams(tmp_path: Path) -> None:
    """The cap must bite mid-stream: a hostile endpoint never fills memory."""
    produced = 0

    async def endless() -> AsyncIterator[bytes]:
        nonlocal produced
        # Bounded only so a regression fails the test instead of hanging it.
        while produced < 50 * MAX_CHECKSUMS_BYTES:
            produced += 4096
            yield b"#" * 4096

    routes = _github_release()
    routes[f"{CDN}/manifest"] = lambda: httpx.Response(200, content=endless())

    with pytest.raises(InstallerUpdateError, match="larger than"):
        await download_and_verify(
            _asset(ASSET_NAME),
            _asset(CHECKSUMS_ASSET_NAME),
            dest_dir=tmp_path,
            fetcher=_Wire(routes).fetcher(),
        )
    assert produced < 2 * MAX_CHECKSUMS_BYTES


async def test_an_oversized_installer_is_refused_while_it_streams(tmp_path: Path) -> None:
    produced = 0

    async def endless() -> AsyncIterator[bytes]:
        nonlocal produced
        while produced < 50 * len(PAYLOAD):
            produced += 65536
            yield b"\0" * 65536

    routes = _github_release()
    routes[f"{CDN}/installer"] = lambda: httpx.Response(200, content=endless())

    with pytest.raises(InstallerUpdateError, match="exceeded"):
        await download_and_verify(
            # The release metadata does not know the size, so only the stream
            # guard can stop it.
            _asset(ASSET_NAME, size=0),
            _asset(CHECKSUMS_ASSET_NAME),
            dest_dir=tmp_path,
            fetcher=_Wire(routes).fetcher(),
            max_bytes=len(PAYLOAD),
        )
    assert produced < 4 * len(PAYLOAD)
    assert not (tmp_path / ASSET_NAME).exists()


async def test_progress_without_a_content_length_uses_the_release_size(tmp_path: Path) -> None:
    async def chunked() -> AsyncIterator[bytes]:
        for start in range(0, len(PAYLOAD), 10_000):
            yield PAYLOAD[start : start + 10_000]

    routes = _github_release()
    # An async body has no Content-Length: a proxy's chunked transfer.
    routes[f"{CDN}/installer"] = lambda: httpx.Response(200, content=chunked())
    ticks: list[tuple[int, int | None]] = []

    await download_and_verify(
        _asset(ASSET_NAME),
        _asset(CHECKSUMS_ASSET_NAME),
        dest_dir=tmp_path,
        fetcher=_Wire(routes).fetcher(),
        on_progress=lambda written, total: ticks.append((written, total)),
    )

    assert ticks[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert {total for _written, total in ticks} == {len(PAYLOAD)}


async def test_progress_without_any_known_size_stays_indeterminate(tmp_path: Path) -> None:
    async def chunked() -> AsyncIterator[bytes]:
        yield PAYLOAD

    routes = _github_release()
    routes[f"{CDN}/installer"] = lambda: httpx.Response(200, content=chunked())
    ticks: list[tuple[int, int | None]] = []

    await download_and_verify(
        _asset(ASSET_NAME, size=0),
        _asset(CHECKSUMS_ASSET_NAME),
        dest_dir=tmp_path,
        fetcher=_Wire(routes).fetcher(),
        on_progress=lambda written, total: ticks.append((written, total)),
    )

    # Never a guessed total: the UI shows bytes, not an invented percentage.
    assert {total for _written, total in ticks} == {None}


async def test_the_fetcher_identifies_itself(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    routes = _github_release()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        factory = routes.get(str(request.url))
        return factory() if factory is not None else httpx.Response(404)

    await download_and_verify(
        _asset(ASSET_NAME),
        _asset(CHECKSUMS_ASSET_NAME),
        dest_dir=tmp_path,
        fetcher=HttpxAssetFetcher(transport=httpx.MockTransport(handler)),
    )

    assert seen
    assert all(request.headers["User-Agent"] for request in seen)
    assert all(request.url.scheme == "https" for request in seen)
