"""Native installation proof includes cleanup, with real Windows file locks."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import stat
import tempfile
import threading
from pathlib import Path

import pytest

from tests.fakes.swarm_packaging_cleanup import CompletedSmoke, mapped_extension

ROOT = Path(__file__).resolve().parents[2]
NATIVE_SMOKE = runpy.run_path(str(ROOT / "packaging" / "verify_swarm_install.py"))


def test_cleanup_rejects_an_unowned_target(tmp_path):
    marker = tmp_path / "keep"
    marker.write_text("unowned", encoding="utf-8")
    with pytest.raises(NATIVE_SMOKE["WorkspaceCleanupError"], match="Refusing cleanup"):
        NATIVE_SMOKE["remove_workspace"](tmp_path)
    assert marker.read_text(encoding="utf-8") == "unowned"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink probe")
def test_cleanup_rejects_a_retargeted_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep"
    marker.write_text("unowned", encoding="utf-8")
    workspace = tmp_path / "jarvis-native-smoke-link"
    workspace.symlink_to(outside, target_is_directory=True)
    with pytest.raises(NATIVE_SMOKE["WorkspaceCleanupError"], match="target changed"):
        NATIVE_SMOKE["remove_workspace"](workspace)
    assert marker.read_text(encoding="utf-8") == "unowned"


def test_unproven_process_stop_never_attempts_file_cleanup(monkeypatch):
    workspace = NATIVE_SMOKE["smoke_workspace"]
    removed = []
    monkeypatch.setitem(workspace.__wrapped__.__globals__, "remove_workspace", removed.append)
    retained = None
    try:
        with pytest.raises(NATIVE_SMOKE["WorkspaceCleanupError"], match="workspace retained"):
            with workspace() as retained:
                raise NATIVE_SMOKE["ContainmentError"]("Synthetic unproven shutdown")
        assert retained.is_dir() and not removed
    finally:
        if retained is not None:
            shutil.rmtree(retained)


@pytest.mark.skipif(os.name != "nt", reason="Windows DOS read-only attribute")
def test_windows_cleanup_removes_readonly_installer_files(tmp_path):
    root = Path(tempfile.mkdtemp(prefix="jarvis-native-smoke-", dir=tmp_path)).resolve()
    target = root / "installer-payload.pyd"
    target.write_bytes(b"installer payload")
    target.chmod(stat.S_IREAD)
    assert target.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    NATIVE_SMOKE["remove_workspace"](root)
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows mapped-image deletion semantics")
def test_windows_cleanup_retries_an_actual_mapped_dll_until_released(monkeypatch, tmp_path):
    root = Path(tempfile.mkdtemp(prefix="jarvis-native-smoke-", dir=tmp_path)).resolve()
    attempts = []
    first_failure = threading.Event()
    actual_remove = shutil.rmtree
    with mapped_extension(root) as release:

        def remove(*args, **kwargs):
            try:
                actual_remove(*args, **kwargs)
            except PermissionError as exc:
                attempts.append(exc.winerror)
                first_failure.set()
                raise

        def release_after_failed_delete():
            if first_failure.wait(timeout=5):
                release()

        releaser = threading.Thread(target=release_after_failed_delete, daemon=True)
        monkeypatch.setattr(shutil, "rmtree", remove)
        releaser.start()
        try:
            NATIVE_SMOKE["remove_workspace"](root)
        finally:
            first_failure.set()
            releaser.join(timeout=5)
        assert not releaser.is_alive()
    assert attempts and attempts[0] == 5
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows mapped-image deletion semantics")
def test_windows_cleanup_retains_a_persistently_mapped_dll(monkeypatch, tmp_path):
    root = Path(tempfile.mkdtemp(prefix="jarvis-native-smoke-", dir=tmp_path)).resolve()
    remove = NATIVE_SMOKE["remove_workspace"]
    monkeypatch.setitem(remove.__globals__, "_CLEANUP_TIMEOUT_S", 0.2)
    with mapped_extension(root):
        with pytest.raises(
            NATIVE_SMOKE["WorkspaceCleanupError"], match="workspace retained"
        ) as error:
            remove(root)
        assert error.value.__cause__.winerror == 5
        assert (root / "mask.pyd").is_file()
    remove(root)
    assert not root.exists()


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_native_report_pass_requires_completed_workspace_cleanup(
    monkeypatch, tmp_path, cleanup_fails
):
    run = NATIVE_SMOKE["run"]
    smoke = CompletedSmoke()
    installer = tmp_path / "installer.fixture"
    installer.write_bytes(b"synthetic installer")
    report = tmp_path / "report.json"
    report.write_text('{"status":"pass"}', encoding="utf-8")
    actual_cleanup = NATIVE_SMOKE["remove_workspace"]
    retained = []

    def cleanup(root):
        assert json.loads(report.read_text(encoding="utf-8"))["status"] == "running"
        if cleanup_fails:
            retained.append(root)
            raise NATIVE_SMOKE["WorkspaceCleanupError"]("Synthetic persistent file lock")
        actual_cleanup(root)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for name in ("install", "running_app", "wait_ready"):
        monkeypatch.setitem(run.__globals__, name, getattr(smoke, name))
    monkeypatch.setitem(run.__globals__, "SwarmApi", lambda _port, _key: smoke)
    monkeypatch.setitem(run.__globals__, "remove_workspace", cleanup)
    try:
        if cleanup_fails:
            with pytest.raises(NATIVE_SMOKE["WorkspaceCleanupError"], match="persistent file lock"):
                run(installer, report)
        else:
            result = run(installer, report)
            assert result["status"] == "pass" and result["cleanup"] == "pass"
        saved = json.loads(report.read_text(encoding="utf-8"))
        assert smoke.installations == 2
        assert saved["fresh_install"]["native_wasm"] == "pass"
        assert saved["same_artifact_replacement"]["persistent_team_and_lead"] == "pass"
        assert saved["status"] == ("failed" if cleanup_fails else "pass")
        assert saved["cleanup"] == ("failed" if cleanup_fails else "pass")
    finally:
        for root in retained:
            actual_cleanup(root)
