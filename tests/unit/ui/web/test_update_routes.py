"""Route + guard tests for the in-app updater (jarvis/ui/web/update_routes.py).

Covers the safety-critical contract: an unmanaged checkout (dev tree / manual
clone) never sees an update and can never be self-reset, version comparison is
fail-closed on an unknown running version, and the network check is fail-open.
"""
from __future__ import annotations

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
    calls: list[list[str]] = []

    async def _fake_git(args, *, cwd, timeout_s=60.0):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(u, "_git", _fake_git)
    monkeypatch.setattr(u, "_version_on_disk", lambda root: "1.0.2")
    body = client.post("/api/update/apply").json()
    assert body["ok"] is True
    assert body["restart_required"] is True
    assert body["version"] == "1.0.2"
    assert ["fetch", "--depth", "1", "origin", "main"] in calls
    assert ["reset", "--hard", "origin/main"] in calls


def test_apply_git_fetch_failure_is_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)

    async def _fake_git(args, *, cwd, timeout_s=60.0):
        return 1, "", "network down"

    monkeypatch.setattr(u, "_git", _fake_git)
    assert client.post("/api/update/apply").status_code == 502


def test_apply_refreshes_deps_only_when_lockfile_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_managed(monkeypatch, tmp_path)
    # _hash_file is called before and after the reset; return distinct values
    # to simulate a changed requirements.txt.
    states = iter(["hash-before", "hash-after"])
    monkeypatch.setattr(u, "_hash_file", lambda path: next(states))

    async def _fake_git(args, *, cwd, timeout_s=60.0):
        return 0, "", ""

    monkeypatch.setattr(u, "_git", _fake_git)
    monkeypatch.setattr(u, "_version_on_disk", lambda root: "1.0.2")
    refreshed: dict[str, bool] = {}

    async def _fake_refresh(root):
        refreshed["called"] = True
        return True, ""

    monkeypatch.setattr(u, "_refresh_dependencies", _fake_refresh)
    body = client.post("/api/update/apply").json()
    assert body["deps_refreshed"] is True
    assert refreshed.get("called") is True
