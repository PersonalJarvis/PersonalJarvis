"""Route + guard tests for the in-app updater (jarvis/ui/web/update_routes.py).

Covers the safety-critical contract: an unmanaged checkout (dev tree / manual
clone) never sees an update and can never be self-reset, version comparison is
fail-closed on an unknown running version, and the network check is fail-open.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.update_routes as u
from jarvis.ui.web.update_routes import router as update_router


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    # The status cache is module-global; clear it so each test sees a fresh check.
    u._status_cache = None
    u._status_cache_until = 0.0


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(update_router)
    return TestClient(app)


def _patch_managed(monkeypatch: pytest.MonkeyPatch, root: Path | None) -> None:
    async def _fake() -> Path | None:
        return root

    monkeypatch.setattr(u, "_resolve_managed_repo", _fake)


def _patch_latest(monkeypatch: pytest.MonkeyPatch, result: dict | None) -> None:
    async def _fake() -> dict | None:
        return result

    monkeypatch.setattr(u, "_fetch_latest_release", _fake)


# --------------------------------------------------------------------------- #
# Version comparison + remote normalization
# --------------------------------------------------------------------------- #
def test_is_newer_basic() -> None:
    assert u._is_newer("1.0.2", "1.0.1")
    assert u._is_newer("2.0.0", "1.9.9")
    assert not u._is_newer("1.0.1", "1.0.1")
    assert not u._is_newer("1.0.0", "1.0.1")


def test_is_newer_fail_closed_on_unknown() -> None:
    # If we can't tell what we're running, never offer an update.
    assert not u._is_newer("1.0.2", "unknown")
    assert not u._is_newer("1.0.2", "")
    assert not u._is_newer("", "1.0.1")


def test_versions_equal_is_normalized_and_fail_closed() -> None:
    assert u._versions_equal("1.0.2", "1.0.2")
    assert u._versions_equal("1.0.2", "1.0.2+build.1") is False
    assert not u._versions_equal("invalid", "invalid")


def test_remote_is_official_accepts_only_exact_repo() -> None:
    # https, ssh, and a local path on either slash style all resolve.
    assert u._remote_is_official("https://github.com/PersonalJarvis/PersonalJarvis.git")
    assert u._remote_is_official("git@github.com:PersonalJarvis/PersonalJarvis.git")
    assert u._remote_is_official("C:\\x\\PersonalJarvis\\PersonalJarvis")
    # A different repo is rejected...
    assert not u._remote_is_official("https://github.com/someone/fork.git")
    # ...and so is a look-alike fork whose name merely starts with the slug...
    assert not u._remote_is_official(
        "https://github.com/PersonalJarvis/PersonalJarvisEvil.git"
    )
    # ...or one under a different owner with the right repo name.
    assert not u._remote_is_official("https://github.com/evil/PersonalJarvis.git")


# --------------------------------------------------------------------------- #
# GET /api/update/status
# --------------------------------------------------------------------------- #
def test_status_unmanaged_hides_button(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_managed(monkeypatch, None)
    monkeypatch.setattr(u, "_running_version", lambda: "1.0.1")
    body = client.get("/api/update/status").json()
    assert body["managed"] is False
    assert body["update_available"] is False


def test_status_newer_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)
    monkeypatch.setattr(u, "_running_version", lambda: "1.0.1")
    _patch_latest(
        monkeypatch,
        {
            "version": "1.0.2",
            "notes": "New stuff",
            "published_at": "2026-07-03T00:00:00Z",
            "release_url": "https://example/releases/v1.0.2",
        },
    )
    body = client.get("/api/update/status").json()
    assert body["managed"] is True
    assert body["update_available"] is True
    assert body["latest"] == "1.0.2"
    assert body["notes"] == "New stuff"


def test_status_same_version_no_update(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)
    monkeypatch.setattr(u, "_running_version", lambda: "1.0.2")
    _patch_latest(
        monkeypatch,
        {"version": "1.0.2", "notes": "", "published_at": None, "release_url": None},
    )
    body = client.get("/api/update/status").json()
    assert body["managed"] is True
    assert body["update_available"] is False
    assert body["notes"] is None


def test_status_network_error_is_fail_open(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)
    monkeypatch.setattr(u, "_running_version", lambda: "1.0.1")
    _patch_latest(monkeypatch, None)  # GitHub unreachable
    body = client.get("/api/update/status").json()
    assert body["managed"] is True
    assert body["update_available"] is False
    assert body.get("check_failed") is True


# --------------------------------------------------------------------------- #
# POST /api/update/apply
# --------------------------------------------------------------------------- #
def test_apply_refuses_unmanaged_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_managed(monkeypatch, None)
    # Even a spoofed client can never trigger a self-reset on a dev tree.
    assert client.post("/api/update/apply").status_code == 403


def test_apply_happy_path_pulls_and_signals_restart(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)
    (tmp_path / ".jarvis-managed-install").write_text(
        '{"profile": "full"}\n', encoding="utf-8"
    )
    sentinel = tmp_path / "running-checkout.txt"
    sentinel.write_text("old", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(u, "_running_version", lambda: "1.0.1")
    _patch_latest(
        monkeypatch,
        {
            "version": "1.0.2",
            "tag": "v1.0.2",
            "notes": "",
            "published_at": None,
            "release_url": None,
        },
    )

    async def _fake_git(args, *, cwd, timeout_s=60.0):
        calls.append(args)
        return 0, "", ""

    async def _fake_git_output(args, *, cwd, timeout_s=15.0):
        calls.append(args)
        if args == ["rev-parse", "HEAD"]:
            return "a" * 40
        if args == ["rev-parse", "FETCH_HEAD^{commit}"]:
            return "b" * 40
        if args == ["show", f"{'b' * 40}:jarvis/__init__.py"]:
            return '__version__ = "1.0.2"'
        return None

    monkeypatch.setattr(u, "_git", _fake_git)
    monkeypatch.setattr(u, "_git_output", _fake_git_output)
    body = client.post("/api/update/apply").json()
    assert body["ok"] is True
    assert body["prepared"] is True
    assert body["restart_required"] is True
    assert body["version"] == "1.0.2"
    assert body["release_tag"] == "v1.0.2"
    assert body["deps_pending"] is True
    assert body["ui_bundle_pending"] is True
    assert body["desktop_integration_pending"] is True
    assert body["desktop_integration_ok"] is None
    assert body["desktop_integration_warning"] is None
    assert [
        "fetch",
        "--depth",
        "1",
        "origin",
        "refs/tags/v1.0.2",
    ] in calls
    assert not any(call[:2] == ["reset", "--hard"] for call in calls)
    assert sentinel.read_text(encoding="utf-8") == "old"

    pending = json.loads(
        (tmp_path / u._PENDING_UPDATE_NAME).read_text(encoding="utf-8")
    )
    assert pending["previous_revision"] == "a" * 40
    assert pending["target_revision"] == "b" * 40
    assert pending["profile"] == "full"


def test_apply_git_fetch_failure_is_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)
    monkeypatch.setattr(u, "_running_version", lambda: "1.0.1")
    _patch_latest(monkeypatch, {"version": "1.0.2", "tag": "v1.0.2"})

    async def _fake_git(args, *, cwd, timeout_s=60.0):
        return 1, "", "network down"

    async def _fake_git_output(args, *, cwd, timeout_s=15.0):
        return "a" * 40

    monkeypatch.setattr(u, "_git", _fake_git)
    monkeypatch.setattr(u, "_git_output", _fake_git_output)
    assert client.post("/api/update/apply").status_code == 502


def test_apply_preserves_headless_profile_for_deferred_installer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)
    (tmp_path / ".jarvis-managed-install").write_text(
        '{"profile": "headless"}\n', encoding="utf-8"
    )
    monkeypatch.setattr(u, "_running_version", lambda: "1.0.1")
    _patch_latest(monkeypatch, {"version": "1.0.2", "tag": "v1.0.2"})

    async def _fake_git(args, *, cwd, timeout_s=60.0):
        return 0, "", ""

    async def _fake_git_output(args, *, cwd, timeout_s=15.0):
        if args == ["rev-parse", "HEAD"]:
            return "1" * 40
        if args == ["rev-parse", "FETCH_HEAD^{commit}"]:
            return "2" * 40
        if args == ["show", f"{'2' * 40}:jarvis/__init__.py"]:
            return '__version__ = "1.0.2"'
        return None

    monkeypatch.setattr(u, "_git", _fake_git)
    monkeypatch.setattr(u, "_git_output", _fake_git_output)
    body = client.post("/api/update/apply").json()
    assert body["install_profile"] == "headless"
    assert body["desktop_integration_pending"] is False
    pending = json.loads(
        (tmp_path / u._PENDING_UPDATE_NAME).read_text(encoding="utf-8")
    )
    assert pending["profile"] == "headless"


def test_apply_refuses_when_no_new_release(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)
    monkeypatch.setattr(u, "_running_version", lambda: "1.0.2")
    _patch_latest(monkeypatch, {"version": "1.0.2", "tag": "v1.0.2"})

    response = client.post("/api/update/apply")

    assert response.status_code == 409
    assert not (tmp_path / u._PENDING_UPDATE_NAME).exists()


def test_pending_update_write_replaces_old_result(tmp_path: Path) -> None:
    result = tmp_path / u._UPDATE_RESULT_NAME
    result.write_text('{"ok": false}\n', encoding="utf-8")

    u._write_pending_update(
        tmp_path,
        previous_revision="a" * 40,
        target_revision="b" * 40,
        profile="full",
    )

    assert not result.exists()
    assert not (tmp_path / f"{u._PENDING_UPDATE_NAME}.tmp").exists()
    payload = json.loads(
        (tmp_path / u._PENDING_UPDATE_NAME).read_text(encoding="utf-8")
    )
    assert payload["schema"] == 1


def test_legacy_marker_falls_back_to_desktop_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".jarvis-managed-install").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(u.sys, "platform", "win32")
    assert u._managed_install_profile(tmp_path) == "full"


def test_legacy_linux_marker_distinguishes_desktop_from_headless(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".jarvis-managed-install").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(u.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert u._managed_install_profile(tmp_path) == "headless"

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert u._managed_install_profile(tmp_path) == "full"
