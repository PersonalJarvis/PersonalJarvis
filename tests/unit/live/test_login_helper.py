"""Native login helper provisioning without Node, credentials, or real downloads."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tarfile
import threading

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from jarvis.core.http_pool import SyncHttpClientPool
from jarvis.live import login_helper as module
from jarvis.live.login_helper import LoginHelperError, NativeArtifact, install_login_helper


def _artifact(data=b"verified native fixture"):
    executable = "codex.exe" if os.name == "nt" else "codex"
    return NativeArtifact(
        "0.147.0",
        "fixture-platform",
        "fixture-target",
        executable,
        hashlib.sha256(data).hexdigest(),
    )


def _archive(artifact, data=b"verified native fixture", *, malicious=False, link=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        if malicious:
            arbitrary = tarfile.TarInfo("../../outside-file")
            arbitrary.size = 4
            archive.addfile(arbitrary, io.BytesIO(b"evil"))
        member = tarfile.TarInfo(artifact.member)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside-file"
            archive.addfile(member)
        else:
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def _pool(data, handler=None):
    def response(request):
        assert request.url.host == "registry.npmjs.org"
        assert "authorization" not in request.headers
        if handler:
            handler(request)
        return httpx.Response(200, stream=httpx.ByteStream(data))

    return SyncHttpClientPool(timeout_s=1, transport=httpx.MockTransport(response))


@pytest.fixture(autouse=True)
def no_existing_cli(monkeypatch):
    monkeypatch.setattr(module, "_existing_binary", lambda: None)


def test_install_verifies_private_binary_without_advertising_global_codex(tmp_path, monkeypatch):
    from jarvis.codex_auth import CodexAuthService
    from jarvis.core import path_augment, paths

    root = tmp_path / "user-data"
    monkeypatch.setattr(paths, "user_data_dir", lambda: root)
    monkeypatch.setattr(path_augment, "_windows_candidates", lambda: [])
    monkeypatch.setattr(path_augment, "_posix_candidates", lambda: [])
    monkeypatch.setattr(path_augment, "_registered_agent_dirs", lambda: [])
    monkeypatch.setenv("PATH", "")
    artifact = _artifact()
    result = install_login_helper(
        threading.Event(),
        root=root,
        artifact=artifact,
        http_pool=_pool(_archive(artifact, malicious=True)),
    )
    assert result == {"status": "ready", "installed": True, "version": "0.147.0"}
    target = root / "login-helpers" / "codex" / artifact.executable
    assert target.read_bytes() == b"verified native fixture"
    assert CodexAuthService()._resolve_binary() is None
    monkeypatch.setattr(module, "user_data_dir", lambda: root)
    monkeypatch.setattr(module, "native_artifact", lambda: artifact)
    verified = module.find_installed_helper()
    assert verified is not None and os.path.samefile(verified, target)
    assert module.login_helper_status()["source"] == "private"
    assert not (root / "outside-file").exists()
    assert not (tmp_path / "outside-file").exists()
    assert not list((root / "login-helpers" / "codex").glob(".codex-*.tmp"))
    assert not list((root / "login-helpers" / "codex").glob(".codex-*.tgz"))


@pytest.mark.parametrize(
    "operating_system,machine",
    [
        ("win32", "AMD64"),
        ("win32", "ARM64"),
        ("darwin", "x86_64"),
        ("darwin", "arm64"),
        ("linux", "x86_64"),
        ("linux", "aarch64"),
    ],
)
def test_every_supported_platform_uses_existing_audited_pin(monkeypatch, operating_system, machine):
    from jarvis.codex_app_server import _SUPPORTED_CODEX_VERSION, _TRUSTED_CODEX_TARGETS

    monkeypatch.setattr(module.sys, "platform", operating_system)
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    artifact = module.native_artifact()
    assert artifact.version == _SUPPORTED_CODEX_VERSION.removeprefix("codex-cli ")
    assert artifact.sha256 in {target[3] for target in _TRUSTED_CODEX_TARGETS.values()}
    assert artifact.member == f"package/vendor/{artifact.target}/bin/{artifact.executable}"


@pytest.mark.parametrize("failure", ["hash", "symlink", "compressed_limit", "inflated_limit"])
def test_invalid_artifact_never_publishes_an_executable(tmp_path, monkeypatch, failure):
    artifact = _artifact()
    data = b"modified fixture" if failure == "hash" else b"verified native fixture"
    archive = _archive(artifact, data=data, link=failure == "symlink")
    if failure == "compressed_limit":
        monkeypatch.setattr(module, "_MAX_ARCHIVE_BYTES", 1)
    if failure == "inflated_limit":
        monkeypatch.setattr(module, "_MAX_INFLATED_BYTES", 1)
    with pytest.raises(LoginHelperError):
        install_login_helper(
            threading.Event(), root=tmp_path, artifact=artifact, http_pool=_pool(archive)
        )
    assert not (tmp_path / "login-helpers" / "codex" / artifact.executable).exists()
    assert all(
        path.name == ".codex-install.lock"
        for path in (tmp_path / "login-helpers" / "codex").iterdir()
    )


def test_existing_cli_and_account_files_are_not_modified(tmp_path, monkeypatch):
    binary = tmp_path / "existing-codex"
    binary.write_bytes(b"user-managed")
    account = tmp_path / "auth.json"
    account.write_text("fixture account state", encoding="utf-8")
    monkeypatch.setattr(module, "_existing_binary", lambda: str(binary))
    result = install_login_helper(threading.Event(), root=tmp_path, artifact=_artifact())
    assert result["installed"] is False
    assert binary.read_bytes() == b"user-managed"
    assert account.read_text() == "fixture account state"
    assert not (tmp_path / "login-helpers" / "codex").exists()


def test_an_install_appearing_during_download_is_preserved(tmp_path):
    artifact = _artifact()
    destination = tmp_path / "login-helpers" / "codex" / artifact.executable

    def concurrent_install(_request):
        destination.write_bytes(b"another installer won")

    with pytest.raises(LoginHelperError) as failure:
        install_login_helper(
            threading.Event(),
            root=tmp_path,
            artifact=artifact,
            http_pool=_pool(_archive(artifact), concurrent_install),
        )
    assert failure.value.code == "install_changed"
    assert destination.read_bytes() == b"another installer won"


def test_cancellation_before_commit_removes_staging_files(tmp_path):
    artifact = _artifact()
    cancelled = threading.Event()

    def cancel(_request):
        cancelled.set()

    with pytest.raises(LoginHelperError) as failure:
        install_login_helper(
            cancelled, root=tmp_path, artifact=artifact, http_pool=_pool(_archive(artifact), cancel)
        )
    assert failure.value.code == "cancelled"
    assert not (tmp_path / "login-helpers" / "codex" / artifact.executable).exists()
    assert all(
        path.name == ".codex-install.lock"
        for path in (tmp_path / "login-helpers" / "codex").iterdir()
    )


def test_directory_symlink_is_refused_before_download(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "login-helpers").mkdir()
        (root / "login-helpers" / "codex").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are not available for this user")
    with pytest.raises(LoginHelperError) as failure:
        install_login_helper(threading.Event(), root=root, artifact=_artifact())
    assert failure.value.code == "unsafe_location"
    assert not list(outside.iterdir())


def test_concurrent_install_is_rejected_without_creating_files(tmp_path):
    with module._INSTALL_LOCK:
        with pytest.raises(LoginHelperError) as failure:
            install_login_helper(threading.Event(), root=tmp_path, artifact=_artifact())
    assert failure.value.code == "busy"
    assert not list(tmp_path.iterdir())


def test_account_login_alone_resolves_private_helper_without_coding_cli(tmp_path, monkeypatch):
    from jarvis.agent_accounts import AgentAccount, _describe_codex, login_command
    from jarvis.codex_auth import CodexAuthService

    artifact = _artifact()
    install_login_helper(
        threading.Event(), root=tmp_path, artifact=artifact, http_pool=_pool(_archive(artifact))
    )
    monkeypatch.setattr(module, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "native_artifact", lambda: artifact)
    monkeypatch.setattr(CodexAuthService, "_resolve_binary", lambda _self: None)
    account_dir = tmp_path / "account"
    account_dir.mkdir()
    account = AgentAccount("codex-fixture", "codex", "Fixture", account_dir)
    argv, _title = login_command(account)
    assert os.path.samefile(argv[0], tmp_path / "login-helpers" / "codex" / artifact.executable)
    assert argv[1:] == ["login"]
    assert CodexAuthService()._resolve_binary() is None
    # The native login owns this file; account readiness does not depend on
    # pretending the separate coding CLI and its sidecars were installed.
    (account_dir / "auth.json").write_text('{"tokens":{"access_token":"fixture-oauth"}}')
    snapshot = _describe_codex(account)
    assert snapshot.connected and snapshot.mode == "subscription"


@pytest.mark.parametrize("valid", [False, True])
def test_explicit_repair_preserves_old_private_file_until_verified(tmp_path, valid):
    artifact = _artifact()
    directory = tmp_path / "login-helpers" / "codex"
    directory.mkdir(parents=True)
    destination = directory / artifact.executable
    destination.write_bytes(b"damaged private helper")
    pool = _pool(_archive(artifact, data=b"verified native fixture" if valid else b"corruption"))
    if valid:
        result = install_login_helper(
            threading.Event(), root=tmp_path, artifact=artifact, http_pool=pool
        )
        assert result["installed"] is True
        assert destination.read_bytes() == b"verified native fixture"
    else:
        with pytest.raises(LoginHelperError):
            install_login_helper(
                threading.Event(), root=tmp_path, artifact=artifact, http_pool=pool
            )
        assert destination.read_bytes() == b"damaged private helper"


def test_private_helper_tampering_is_not_reported_ready(tmp_path, monkeypatch):
    artifact = _artifact()
    directory = tmp_path / "login-helpers" / "codex"
    directory.mkdir(parents=True)
    (directory / artifact.executable).write_bytes(b"unverified")
    monkeypatch.setattr(module, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "native_artifact", lambda: artifact)
    assert module.find_installed_helper() is None
    assert module.login_helper_status()["status"] == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_route_joins_cancelled_worker_and_cleans_staging_before_return(
    monkeypatch, disconnect
):
    from jarvis.ui.web.live_routes import install_live_login_helper

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    gone = asyncio.Event()
    finished = threading.Event()

    def installer(cancel):
        assert threading.get_ident() != main_thread
        loop.call_soon_threadsafe(started.set)
        assert cancel.wait(timeout=2)
        finished.set()
        raise LoginHelperError("cancelled", "Cancelled", status=499)

    async def receive():
        await gone.wait()
        return {"type": "http.disconnect"}

    main_thread = threading.get_ident()
    monkeypatch.setattr(module, "install_login_helper", installer)
    task = asyncio.create_task(install_live_login_helper(Request({"type": "http"}, receive)))
    await asyncio.wait_for(started.wait(), 1)
    if disconnect:
        gone.set()
        with pytest.raises(HTTPException) as failure:
            await task
        assert failure.value.status_code == 499
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set()
    assert not any(
        task.get_name().startswith("live-login-helper-")
        for task in asyncio.all_tasks()
        if not task.done()
    )


@pytest.mark.asyncio
async def test_route_returns_ready_only_after_installer_finishes_and_never_signs_in(monkeypatch):
    from jarvis.ui.web.live_routes import install_live_login_helper

    steps = []

    def installer(_cancel):
        steps.append("verified-and-published")
        return {"status": "ready", "installed": True, "version": "0.147.0"}

    async def receive():
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "install_login_helper", installer)
    result = await install_live_login_helper(Request({"type": "http"}, receive))
    steps.append(result["status"])
    assert steps == ["verified-and-published", "ready"]


@pytest.mark.asyncio
async def test_helper_status_hashing_runs_off_the_request_loop(monkeypatch):
    from jarvis.ui.web.live_routes import get_live_login_helper

    caller = threading.get_ident()

    def status():
        assert threading.get_ident() != caller
        return {"status": "missing", "installed": False, "source": "missing", "version": ""}

    monkeypatch.setattr(module, "login_helper_status", status)
    assert (await get_live_login_helper())["installed"] is False
