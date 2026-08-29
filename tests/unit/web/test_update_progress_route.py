"""The progress side channel, end to end: bytes -> tracker -> HTTP.

``POST /api/update/apply`` is one long request, so the percentage can only reach
the UI over a second endpoint polled meanwhile. These tests prove that channel
carries real measurements rather than a decorative animation, and that the apply
route cannot leave it stuck "active" forever.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from jarvis.core.installer_update import InstallerAsset, download_and_verify
from jarvis.ui.web import update_routes
from jarvis.ui.web.update_routes import (
    INSTALL_KIND_FROZEN,
    PHASE_DOWNLOADING,
    PHASE_FAILED,
    PHASE_IDLE,
    _progress,
    router,
)
from tests.fakes.fake_installer_update import FakeAssetFetcher


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_tracker() -> None:
    """The tracker is process-global; leaving state behind would leak between tests."""
    _progress.__init__()  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# The route
# --------------------------------------------------------------------------- #
def test_progress_answers_even_when_nothing_is_running(client: TestClient) -> None:
    """The UI polls this on every click; it must never 404 or 500."""
    body = client.get("/api/update/progress").json()
    assert body["phase"] == PHASE_IDLE
    assert body["active"] is False
    assert body["percent"] == 0
    assert body["error"] is None


def test_progress_publishes_the_live_state(client: TestClient) -> None:
    _progress.begin(INSTALL_KIND_FROZEN)
    _progress.advance(PHASE_DOWNLOADING, 0.5, detail="64.0 MB / 128.0 MB")

    body = client.get("/api/update/progress").json()
    assert body["active"] is True
    assert body["phase"] == PHASE_DOWNLOADING
    assert 0 < body["percent"] < 100
    assert body["detail"] == "64.0 MB / 128.0 MB"
    assert body["kind"] == INSTALL_KIND_FROZEN


def test_progress_shape_is_stable(client: TestClient) -> None:
    """The UI binds to these fields; losing one silently blanks the bar."""
    body = client.get("/api/update/progress").json()
    assert set(body) == {
        "active",
        "phase",
        "percent",
        "detail",
        "version",
        "kind",
        "error",
        "restart_required",
        "started_at",
        "updated_at",
    }


# --------------------------------------------------------------------------- #
# The apply route's guarantees
# --------------------------------------------------------------------------- #
def test_a_second_apply_is_refused_while_one_runs(client: TestClient) -> None:
    """Two downloads would fight over the temp dir and scramble the percentage."""

    async def hold() -> None:
        async with update_routes._apply_lock:
            await asyncio.sleep(0.3)

    async def scenario() -> int:
        task = asyncio.create_task(hold())
        await asyncio.sleep(0.05)
        try:
            await update_routes.update_apply()
        except HTTPException as exc:
            return exc.status_code
        finally:
            await task
        return 200

    assert asyncio.run(scenario()) == 409


def test_a_failed_apply_always_leaves_a_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this the button reads "Updating 42%" until the app is restarted."""

    async def boom() -> dict[str, object]:
        _progress.begin(INSTALL_KIND_FROZEN)
        _progress.advance(PHASE_DOWNLOADING, 0.42)
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(update_routes, "is_frozen", lambda: True)
    monkeypatch.setattr(update_routes, "_apply_frozen", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(update_routes.update_apply())

    snapshot = _progress.snapshot()
    assert snapshot["phase"] == PHASE_FAILED
    assert snapshot["active"] is False
    assert "the disk went away" in str(snapshot["error"])


def test_an_http_failure_records_the_servers_own_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse() -> dict[str, object]:
        raise HTTPException(status_code=502, detail="git fetch failed: host unreachable")

    monkeypatch.setattr(update_routes, "is_frozen", lambda: True)
    monkeypatch.setattr(update_routes, "_apply_frozen", refuse)

    with pytest.raises(HTTPException):
        asyncio.run(update_routes.update_apply())

    assert "host unreachable" in str(_progress.snapshot()["error"])


def test_the_lock_is_released_after_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lock held by a crashed apply would make the button dead until restart."""

    async def refuse() -> dict[str, object]:
        raise HTTPException(status_code=502, detail="nope")

    monkeypatch.setattr(update_routes, "is_frozen", lambda: True)
    monkeypatch.setattr(update_routes, "_apply_frozen", refuse)

    async def scenario() -> bool:
        with pytest.raises(HTTPException):
            await update_routes.update_apply()
        return update_routes._apply_lock.locked()

    assert asyncio.run(scenario()) is False


# --------------------------------------------------------------------------- #
# Real bytes reach the tracker
# --------------------------------------------------------------------------- #
def test_the_downloader_reports_a_ramp_not_one_jump(tmp_path: Path) -> None:
    payload = b"installer-bytes" * 4096
    digest = hashlib.sha256(payload).hexdigest()
    fetcher = FakeAssetFetcher(
        manifest=f"{digest}  PersonalJarvis-Setup-x64.exe\n", payload=payload
    )
    asset = InstallerAsset(
        name="PersonalJarvis-Setup-x64.exe", url="https://x/i.exe", size=len(payload)
    )
    checksums = InstallerAsset(name="installers-SHA256SUMS.txt", url="https://x/s.txt", size=64)

    seen: list[tuple[int, int | None]] = []
    asyncio.run(
        download_and_verify(
            asset,
            checksums,
            dest_dir=tmp_path,
            fetcher=fetcher,
            on_progress=lambda written, total: seen.append((written, total)),
        )
    )

    assert len(seen) > 1, "a single 100% tick is not a progress bar"
    assert [w for w, _ in seen] == sorted(w for w, _ in seen)
    assert seen[-1] == (len(payload), len(payload))
    assert all(total == len(payload) for _, total in seen)


def test_an_unknown_total_falls_back_to_the_release_metadata(tmp_path: Path) -> None:
    """A server with no Content-Length must still yield a real percentage."""
    payload = b"x" * 2048
    digest = hashlib.sha256(payload).hexdigest()

    class NoLengthFetcher(FakeAssetFetcher):
        async def download(
            self,
            url: str,
            dest: Path,
            *,
            max_bytes: int,
            on_progress: object = None,
            **_: object,
        ) -> int:
            # Blocking write kept out of the async body (ruff ASYNC240), same
            # as the shared fake does.
            await asyncio.to_thread(dest.write_bytes, self.payload)
            if callable(on_progress):
                on_progress(len(self.payload), None)  # server stated no total
            return len(self.payload)

    seen: list[tuple[int, int | None]] = []
    asyncio.run(
        download_and_verify(
            InstallerAsset(name="i.exe", url="https://x/i.exe", size=len(payload)),
            InstallerAsset(name="s.txt", url="https://x/s.txt", size=64),
            dest_dir=tmp_path,
            fetcher=NoLengthFetcher(manifest=f"{digest}  i.exe\n", payload=payload),
            on_progress=lambda written, total: seen.append((written, total)),
        )
    )

    assert seen == [(len(payload), len(payload))]


def test_a_raising_progress_callback_never_fails_the_download(tmp_path: Path) -> None:
    """Progress is cosmetic; a 400 MB transfer must not die for a bad lambda."""
    payload = b"payload" * 1024
    digest = hashlib.sha256(payload).hexdigest()

    def explode(_written: int, _total: int | None) -> None:
        raise ValueError("the UI store went away")

    result = asyncio.run(
        download_and_verify(
            InstallerAsset(name="i.exe", url="https://x/i.exe", size=len(payload)),
            InstallerAsset(name="s.txt", url="https://x/s.txt", size=64),
            dest_dir=tmp_path,
            fetcher=FakeAssetFetcher(manifest=f"{digest}  i.exe\n", payload=payload),
            on_progress=explode,
        )
    )
    assert result.read_bytes() == payload
