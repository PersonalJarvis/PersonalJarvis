"""The FROZEN branch of ``/api/update`` — native installer self-update.

Kept apart from ``test_update_routes.py`` (which owns the managed/git path) so
the two install kinds cannot silently share a fixture and hide a leak. What is
pinned here:

* ``is_frozen()`` is the ONLY switch. Nothing in the frozen branch reaches for
  git, an install marker or an ``origin`` remote.
* ``status`` stays fail-open and never offers an update the release cannot
  actually deliver (no installer asset, no checksum manifest).
* ``apply`` is fail-closed and never executes anything it did not verify.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.update_routes as u
from jarvis.core.installer_update import CHECKSUMS_ASSET_NAME, InstallerUpdateError
from jarvis.ui.web.update_routes import router as update_router

SETUP_NAME = "PersonalJarvis-Setup-x64.exe"


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    u._status_cache = None
    u._status_cache_until = 0.0
    u._status_cache_root = None
    u._last_good_release = None


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(update_router)
    return TestClient(app)


def _release(
    version: str = "1.6.0", *, assets: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    if assets is None:
        assets = [
            {
                "name": SETUP_NAME,
                "browser_download_url": f"https://example.invalid/{SETUP_NAME}",
                "size": 123,
            },
            {
                "name": CHECKSUMS_ASSET_NAME,
                "browser_download_url": (f"https://example.invalid/{CHECKSUMS_ASSET_NAME}"),
                "size": 90,
            },
        ]
    return {
        "version": version,
        "tag": f"v{version}",
        "notes": "Native installers",
        "published_at": "2026-08-25T00:00:00Z",
        "release_url": f"https://example.invalid/releases/v{version}",
        "assets": assets,
    }


def _patch_frozen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    frozen: bool = True,
    asset_name: str | None = SETUP_NAME,
    running: str = "1.5.3",
) -> None:
    monkeypatch.setattr(u, "is_frozen", lambda: frozen)
    monkeypatch.setattr(u, "_running_version", lambda: running)
    monkeypatch.setattr(u, "installer_asset_name", lambda _p, _m: asset_name)


def _patch_latest(monkeypatch: pytest.MonkeyPatch, release: dict[str, Any] | None) -> None:
    async def _fake() -> dict[str, Any] | None:
        return release

    monkeypatch.setattr(u, "_fetch_latest_release", _fake)


def _forbid_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof that the frozen branch never touches the managed machinery."""

    async def _explode() -> Path | None:
        raise AssertionError("a frozen install must never resolve a git checkout")

    monkeypatch.setattr(u, "_resolve_managed_repo", _explode)


# --------------------------------------------------------------------------- #
# GET /api/update/status
# --------------------------------------------------------------------------- #
def test_status_offers_a_newer_installer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    _forbid_git(monkeypatch)
    _patch_latest(monkeypatch, _release("1.6.0"))

    body = client.get("/api/update/status").json()

    assert body["kind"] == "frozen"
    # `managed` is what the top bar gates the button on; a frozen install CAN
    # update itself, so it must be true here.
    assert body["managed"] is True
    assert body["update_available"] is True
    assert body["latest"] == "1.6.0"
    assert body["asset"]["name"] == SETUP_NAME
    assert body["notes"] == "Native installers"
    # Fields the managed path fills stay present so the UI reads one shape.
    assert body["pending_update"] is None
    assert body["last_result"] is None


def test_status_same_version_offers_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch, running="1.6.0")
    _patch_latest(monkeypatch, _release("1.6.0"))
    body = client.get("/api/update/status").json()
    assert body["update_available"] is False
    assert body["notes"] is None


def test_status_without_an_installer_asset_offers_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    # A code-only release: newer version, but no installer to hand the user.
    _patch_latest(monkeypatch, _release("1.6.0", assets=[]))
    body = client.get("/api/update/status").json()
    assert body["update_available"] is False
    assert body["asset"] is None


def test_status_without_a_checksum_manifest_offers_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    _patch_latest(
        monkeypatch,
        _release(
            "1.6.0",
            assets=[
                {
                    "name": SETUP_NAME,
                    "browser_download_url": f"https://example.invalid/{SETUP_NAME}",
                    "size": 123,
                }
            ],
        ),
    )
    body = client.get("/api/update/status").json()
    # apply would refuse an unverifiable download, so status must not offer it.
    assert body["update_available"] is False


def test_status_on_an_unsupported_platform_hides_the_button(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch, asset_name=None)
    _patch_latest(monkeypatch, _release("1.6.0"))
    body = client.get("/api/update/status").json()
    assert body["managed"] is False
    assert body["unsupported_platform"] is True
    assert body["update_available"] is False


def test_status_network_failure_is_fail_open(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    _patch_latest(monkeypatch, None)
    body = client.get("/api/update/status").json()
    assert body["check_failed"] is True
    assert body["update_available"] is False
    assert body["kind"] == "frozen"


def test_non_frozen_status_keeps_the_managed_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The dev tree must be completely unaffected by the frozen branch.
    monkeypatch.setattr(u, "is_frozen", lambda: False)
    monkeypatch.setattr(u, "_running_version", lambda: "1.5.3")

    async def _no_repo() -> Path | None:
        return None

    monkeypatch.setattr(u, "_resolve_managed_repo", _no_repo)
    body = client.get("/api/update/status").json()
    assert body["managed"] is False
    assert body["kind"] == "dev"


# --------------------------------------------------------------------------- #
# POST /api/update/apply
# --------------------------------------------------------------------------- #
def _capture_install(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace download + handover with recorders; return what they saw."""
    seen: dict[str, Any] = {}

    def _write_verified(dest_dir: Path, name: str) -> Path:
        """Blocking write, kept out of the async body (ruff ASYNC240)."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / name
        path.write_bytes(b"verified installer")
        return path

    async def _download(asset: Any, checksums: Any, *, dest_dir: Path) -> Path:
        seen["asset"] = asset
        seen["checksums"] = checksums
        return _write_verified(dest_dir, asset.name)

    def _apply(installer: Path) -> str:
        seen["installer"] = installer
        return "the Windows installer is running"

    monkeypatch.setattr(u, "download_and_verify", _download)
    monkeypatch.setattr(u, "apply_installer", _apply)
    return seen


def test_apply_downloads_verifies_and_hands_over(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    _forbid_git(monkeypatch)
    _patch_latest(monkeypatch, _release("1.6.0"))
    seen = _capture_install(monkeypatch)

    response = client.post("/api/update/apply")
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["kind"] == "frozen"
    assert body["version"] == "1.6.0"
    assert body["release_tag"] == "v1.6.0"
    # The handover restarts the app; the caller must not restart on top of it.
    assert body["restart_required"] is False
    assert seen["asset"].name == SETUP_NAME
    assert seen["checksums"].name == CHECKSUMS_ASSET_NAME
    assert seen["installer"].name == SETUP_NAME


def test_apply_refuses_when_nothing_newer_is_published(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch, running="1.6.0")
    _patch_latest(monkeypatch, _release("1.6.0"))
    assert client.post("/api/update/apply").status_code == 409


def test_apply_refuses_without_a_checksum_manifest(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    _patch_latest(
        monkeypatch,
        _release(
            "1.6.0",
            assets=[
                {
                    "name": SETUP_NAME,
                    "browser_download_url": f"https://example.invalid/{SETUP_NAME}",
                    "size": 123,
                }
            ],
        ),
    )

    def _never(installer: Path) -> str:
        raise AssertionError("nothing may be executed without a checksum manifest")

    monkeypatch.setattr(u, "apply_installer", _never)

    response = client.post("/api/update/apply")
    assert response.status_code == 502
    assert CHECKSUMS_ASSET_NAME in response.json()["detail"]


def test_apply_refuses_when_the_release_has_no_installer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    _patch_latest(monkeypatch, _release("1.6.0", assets=[]))
    response = client.post("/api/update/apply")
    assert response.status_code == 502
    assert SETUP_NAME in response.json()["detail"]


def test_apply_surfaces_a_verification_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    _patch_latest(monkeypatch, _release("1.6.0"))

    async def _download(asset: Any, checksums: Any, *, dest_dir: Path) -> Path:
        raise InstallerUpdateError("PersonalJarvis-Setup-x64.exe failed its SHA-256 check")

    def _never(installer: Path) -> str:
        raise AssertionError("a failed verification must never reach the handover")

    monkeypatch.setattr(u, "download_and_verify", _download)
    monkeypatch.setattr(u, "apply_installer", _never)

    response = client.post("/api/update/apply")
    assert response.status_code == 502
    assert "SHA-256" in response.json()["detail"]


def test_apply_on_an_unsupported_platform_is_501(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch, asset_name=None)
    _patch_latest(monkeypatch, _release("1.6.0"))
    assert client.post("/api/update/apply").status_code == 501


def test_apply_offline_is_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_frozen(monkeypatch)
    _patch_latest(monkeypatch, None)
    assert client.post("/api/update/apply").status_code == 502


def test_apply_falls_back_to_the_last_good_release(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_frozen(monkeypatch)
    _patch_latest(monkeypatch, None)
    u._last_good_release = _release("1.6.0")
    seen = _capture_install(monkeypatch)

    response = client.post("/api/update/apply")

    assert response.status_code == 200
    assert seen["asset"].name == SETUP_NAME
