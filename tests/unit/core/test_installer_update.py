"""``jarvis.core.installer_update`` — the frozen-install self-update.

The whole point of these tests is that the dangerous half of an updater is
provable offline: which asset a machine picks, whether a mismatching SHA-256
really refuses, and what exactly gets executed on each OS. Network and process
spawning arrive through the fakes in ``tests/fakes/fake_installer_update.py``,
so every case below runs on Windows, macOS and Linux alike.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.core import installer_update
from jarvis.core.installer_update import (
    CHECKSUMS_ASSET_NAME,
    CHECKSUMS_SIGNATURE_ASSET_NAME,
    InstallerAsset,
    InstallerUpdateError,
    apply_installer,
    download_and_verify,
    installer_asset_name,
    parse_sha256sums,
    run_native_update_supervisor,
    select_asset,
    select_installer_asset,
)
from jarvis.core.paths import user_data_dir
from tests.fakes.fake_installer_update import (
    FakeAssetFetcher,
    FakeCommandRunner,
    RunResult,
)

PAYLOAD = b"not really an installer, but it hashes just as well"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture(autouse=True)
def _isolate_native_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "runtime-data"))


def test_embedded_release_trust_root_matches_checked_in_key() -> None:
    key_file = Path(__file__).resolve().parents[3] / "install" / "keys" / "offline-ceremony.pub"
    assert installer_update._RELEASE_PUBLIC_KEY_PEM == key_file.read_bytes()


async def _download_and_verify(
    asset: InstallerAsset,
    checksums: InstallerAsset,
    *,
    dest_dir: Path,
    fetcher: FakeAssetFetcher,
    **kwargs: object,
) -> Path:
    """Sign each scenario's manifest with a test key, including invalid digests."""
    key = Ed25519PrivateKey.generate()
    fetcher.manifest = "# release: v1.6.0\n" + fetcher.manifest
    fetcher.signature = base64.b64encode(key.sign(fetcher.manifest.encode()))
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return await download_and_verify(
        asset,
        checksums,
        _asset(CHECKSUMS_SIGNATURE_ASSET_NAME),
        release_tag="v1.6.0",
        dest_dir=dest_dir,
        fetcher=fetcher,
        public_key_pem=public,
        **kwargs,
    )


def _asset(name: str, size: int = len(PAYLOAD)) -> InstallerAsset:
    return InstallerAsset(name=name, url=f"https://example.invalid/{name}", size=size)


def _release_assets(*names: str) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "browser_download_url": f"https://example.invalid/{name}",
            "size": len(PAYLOAD),
        }
        for name in names
    ]


# --------------------------------------------------------------------------- #
# Asset naming
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("platform_name", "machine", "expected"),
    [
        ("win32", "AMD64", "PersonalJarvis-Setup-x64.exe"),
        ("win32", "x86_64", "PersonalJarvis-Setup-x64.exe"),
        ("darwin", "arm64", "PersonalJarvis-macOS-arm64.dmg"),
        ("darwin", "x86_64", "PersonalJarvis-macOS-x64.dmg"),
        ("linux", "x86_64", "PersonalJarvis-Linux-x86_64.AppImage"),
        ("linux2", "AMD64", "PersonalJarvis-Linux-x86_64.AppImage"),
    ],
)
def test_installer_asset_name_matches_the_release_contract(
    platform_name: str, machine: str, expected: str
) -> None:
    assert installer_asset_name(platform_name, machine) == expected


@pytest.mark.parametrize(
    ("platform_name", "machine"),
    [
        ("win32", "ARM64"),  # no Windows-on-ARM installer is published
        ("linux", "aarch64"),
        ("freebsd", "x86_64"),
    ],
)
def test_unsupported_platforms_have_no_asset(platform_name: str, machine: str) -> None:
    # Fail closed: no name means the caller reports "not available here" rather
    # than downloading something built for another machine.
    assert installer_asset_name(platform_name, machine) is None


def test_select_installer_asset_picks_this_machines_file() -> None:
    assets = _release_assets(
        "PersonalJarvis-Setup-x64.exe",
        "PersonalJarvis-macOS-arm64.dmg",
        CHECKSUMS_ASSET_NAME,
    )
    picked = select_installer_asset(assets, platform_name="darwin", machine="arm64")
    assert picked is not None
    assert picked.name == "PersonalJarvis-macOS-arm64.dmg"


def test_select_asset_rejects_a_non_https_url() -> None:
    assets = [{"name": "x.exe", "browser_download_url": "http://example.invalid/x.exe"}]
    assert select_asset(assets, "x.exe") is None


def test_select_asset_ignores_malformed_entries() -> None:
    assets = ["not-a-dict", {"name": "x.exe"}, *_release_assets("x.exe")]
    picked = select_asset(assets, "x.exe")
    # The first two entries are unusable; the well-formed one still wins.
    assert picked is None or picked.name == "x.exe"


# --------------------------------------------------------------------------- #
# Checksum manifest
# --------------------------------------------------------------------------- #
def test_parse_sha256sums_handles_both_sha256sum_markers() -> None:
    text = (
        f"{PAYLOAD_SHA256}  PersonalJarvis-Setup-x64.exe\n"
        f"{PAYLOAD_SHA256} *PersonalJarvis-Linux-x86_64.AppImage\n"
    )
    parsed = parse_sha256sums(text)
    assert parsed["PersonalJarvis-Setup-x64.exe"] == PAYLOAD_SHA256
    assert parsed["PersonalJarvis-Linux-x86_64.AppImage"] == PAYLOAD_SHA256


def test_parse_sha256sums_skips_junk_without_losing_good_lines() -> None:
    text = (
        "# a comment\n"
        "\n"
        "not-a-digest  PersonalJarvis-Setup-x64.exe\n"
        "zz" + "0" * 62 + "  bad-hex.exe\n"
        f"{PAYLOAD_SHA256}  dist/installers/PersonalJarvis-Setup-x64.exe\n"
    )
    parsed = parse_sha256sums(text)
    # The path prefix is stripped so a manifest produced inside a directory
    # still matches the flat asset name.
    assert parsed == {"PersonalJarvis-Setup-x64.exe": PAYLOAD_SHA256}


# --------------------------------------------------------------------------- #
# Download + verification
# --------------------------------------------------------------------------- #
async def test_download_and_verify_returns_the_file_on_a_matching_digest(
    tmp_path: Path,
) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    fetcher = FakeAssetFetcher(manifest=f"{PAYLOAD_SHA256}  {asset.name}\n", payload=PAYLOAD)
    result = await _download_and_verify(
        asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
    )
    assert result == tmp_path / asset.name
    assert result.read_bytes() == PAYLOAD


async def test_signed_manifest_refuses_tampering_before_downloading(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    key = Ed25519PrivateKey.generate()
    original = f"# release: v1.6.0\n{PAYLOAD_SHA256}  {asset.name}\n"
    fetcher = FakeAssetFetcher(
        manifest=original.replace(PAYLOAD_SHA256, "0" * 64),
        signature=base64.b64encode(key.sign(original.encode())),
        payload=PAYLOAD,
    )
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with pytest.raises(InstallerUpdateError, match="Ed25519"):
        await download_and_verify(
            asset,
            _asset(CHECKSUMS_ASSET_NAME),
            _asset(CHECKSUMS_SIGNATURE_ASSET_NAME),
            release_tag="v1.6.0",
            dest_dir=tmp_path,
            fetcher=fetcher,
            public_key_pem=public,
        )
    assert fetcher.download_urls == []


async def test_signed_manifest_refuses_replay_from_another_release(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    key = Ed25519PrivateKey.generate()
    old = f"# release: v1.5.0\n{PAYLOAD_SHA256}  {asset.name}\n"
    fetcher = FakeAssetFetcher(
        manifest=old,
        signature=base64.b64encode(key.sign(old.encode())),
        payload=PAYLOAD,
    )
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with pytest.raises(InstallerUpdateError, match="different release"):
        await download_and_verify(
            asset,
            _asset(CHECKSUMS_ASSET_NAME),
            _asset(CHECKSUMS_SIGNATURE_ASSET_NAME),
            release_tag="v1.6.0",
            dest_dir=tmp_path,
            fetcher=fetcher,
            public_key_pem=public,
        )
    assert fetcher.download_urls == []


async def test_download_and_verify_refuses_a_mismatching_digest(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    wrong = hashlib.sha256(b"something else entirely").hexdigest()
    fetcher = FakeAssetFetcher(manifest=f"{wrong}  {asset.name}\n", payload=PAYLOAD)

    with pytest.raises(InstallerUpdateError, match="SHA-256"):
        await _download_and_verify(
            asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
        )
    # Nothing unverified may survive where a later step could execute it.
    assert not (tmp_path / asset.name).exists()


async def test_download_and_verify_refuses_when_the_manifest_omits_the_asset(
    tmp_path: Path,
) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    fetcher = FakeAssetFetcher(
        manifest=f"{PAYLOAD_SHA256}  PersonalJarvis-macOS-arm64.dmg\n", payload=PAYLOAD
    )
    with pytest.raises(InstallerUpdateError, match="no entry"):
        await _download_and_verify(
            asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
        )


async def test_download_and_verify_refuses_an_oversized_asset(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe", size=10_000)
    fetcher = FakeAssetFetcher(manifest=f"{PAYLOAD_SHA256}  {asset.name}\n", payload=PAYLOAD)
    with pytest.raises(InstallerUpdateError, match="cap"):
        await _download_and_verify(
            asset,
            _asset(CHECKSUMS_ASSET_NAME),
            dest_dir=tmp_path,
            fetcher=fetcher,
            max_bytes=1_000,
        )
    assert not (tmp_path / asset.name).exists()


async def test_download_and_verify_reports_a_transport_failure(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    fetcher = FakeAssetFetcher(
        manifest=f"{PAYLOAD_SHA256}  {asset.name}\n",
        payload=PAYLOAD,
        fail_download=OSError("connection reset"),
    )
    with pytest.raises(InstallerUpdateError, match="connection reset"):
        await _download_and_verify(
            asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
        )


async def test_download_and_verify_reports_a_missing_manifest(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    fetcher = FakeAssetFetcher(fail_text=OSError("404"), payload=PAYLOAD)
    with pytest.raises(InstallerUpdateError, match=CHECKSUMS_ASSET_NAME):
        await _download_and_verify(
            asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
        )


# --------------------------------------------------------------------------- #
# Windows handover
# --------------------------------------------------------------------------- #
def test_windows_handover_runs_the_silent_in_place_upgrade(tmp_path: Path) -> None:
    installer = tmp_path / "PersonalJarvis-Setup-x64.exe"
    installer.write_bytes(PAYLOAD)
    runner = FakeCommandRunner()

    message = apply_installer(installer, platform_name="win32", runner=runner)

    assert runner.spawned == [
        [
            str(installer),
            "/SILENT",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
            "/NORESTART",
        ]
    ]
    assert "restarts by itself" in message


def test_handover_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InstallerUpdateError, match="does not exist"):
        apply_installer(
            tmp_path / "nope.exe",
            platform_name="win32",
            runner=FakeCommandRunner(),
        )


def test_handover_refuses_an_unsupported_platform(tmp_path: Path) -> None:
    installer = tmp_path / "whatever"
    installer.write_bytes(PAYLOAD)
    with pytest.raises(InstallerUpdateError, match="no installer handover"):
        apply_installer(installer, platform_name="sunos5", runner=FakeCommandRunner())


# --------------------------------------------------------------------------- #
# macOS handover
# --------------------------------------------------------------------------- #
def _mounting_runner(app_name: str, payload: str) -> FakeCommandRunner:
    """A runner whose ``hdiutil attach`` really populates the mountpoint."""

    def _mount(command: list[str]) -> None:
        if len(command) < 2 or command[1] != "attach":
            return
        mountpoint = Path(command[command.index("-mountpoint") + 1])
        bundle = mountpoint / app_name / "Contents" / "MacOS"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "PersonalJarvis").write_text(payload, encoding="utf-8")

    return FakeCommandRunner(on_run=_mount)


def test_macos_handover_replaces_the_running_app_and_relaunches(
    tmp_path: Path,
) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PersonalJarvis").write_text("old", encoding="utf-8")

    runner = _mounting_runner("Personal Jarvis.app", "new")
    message = apply_installer(dmg, platform_name="darwin", runner=runner, app_path=app)

    assert (app / "Contents" / "MacOS" / "PersonalJarvis").read_text(encoding="utf-8") == "new"
    assert runner.spawned == [["open", "-n", str(app)]]
    # The volume is always released, success or not.
    assert any(cmd[:2] == ["hdiutil", "detach"] for cmd in runner.ran)
    assert "replaced" in message
    # Preserve the prior bundle for a later rollback.
    assert (
        app.parent / ".Personal Jarvis.app.previous" / "Contents" / "MacOS" / "PersonalJarvis"
    ).read_text(encoding="utf-8") == "old"


def test_macos_failed_relaunch_restores_prior_bundle(tmp_path: Path) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PersonalJarvis").write_text("old", encoding="utf-8")
    runner = _mounting_runner("Personal Jarvis.app", "new")
    runner.spawn_error = OSError("launch failed")
    with pytest.raises(InstallerUpdateError, match="restored the previous version"):
        apply_installer(dmg, platform_name="darwin", runner=runner, app_path=app)
    assert (app / "Contents" / "MacOS" / "PersonalJarvis").read_text(encoding="utf-8") == "old"


def test_macos_handover_keeps_the_old_app_when_the_mount_fails(tmp_path: Path) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PersonalJarvis").write_text("old", encoding="utf-8")

    runner = FakeCommandRunner(
        results={"hdiutil attach": RunResult(returncode=1, stderr="no mountable file")}
    )
    with pytest.raises(InstallerUpdateError, match="could not mount"):
        apply_installer(dmg, platform_name="darwin", runner=runner, app_path=app)

    assert (app / "Contents" / "MacOS" / "PersonalJarvis").read_text(encoding="utf-8") == "old"
    assert runner.spawned == []


def test_macos_handover_refuses_without_a_resolvable_app(tmp_path: Path) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    with pytest.raises(InstallerUpdateError, match="locate the running"):
        apply_installer(
            dmg,
            platform_name="darwin",
            runner=FakeCommandRunner(),
            app_path=None,
        )


def test_macos_handover_falls_back_to_the_only_bundle_on_the_volume(
    tmp_path: Path,
) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-x64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents").mkdir(parents=True)

    # The DMG names the bundle differently from the installed one; there is
    # still exactly one, so the swap is unambiguous.
    runner = _mounting_runner("Personal Jarvis 2.app", "new")
    apply_installer(dmg, platform_name="darwin", runner=runner, app_path=app)
    assert (app / "Contents" / "MacOS" / "PersonalJarvis").read_text(encoding="utf-8") == "new"


# --------------------------------------------------------------------------- #
# Linux handover
# --------------------------------------------------------------------------- #
def test_linux_handover_replaces_the_appimage_in_place(tmp_path: Path) -> None:
    downloaded = tmp_path / "download" / "PersonalJarvis-Linux-x86_64.AppImage"
    downloaded.parent.mkdir()
    downloaded.write_bytes(b"new appimage")
    live = tmp_path / "opt" / "PersonalJarvis.AppImage"
    live.parent.mkdir()
    live.write_bytes(b"old appimage")

    runner = FakeCommandRunner()
    message = apply_installer(downloaded, platform_name="linux", runner=runner, appimage_path=live)

    assert live.read_bytes() == b"new appimage"
    assert (live.parent / f".{live.name}.previous").read_bytes() == b"old appimage"
    assert runner.spawned == [[str(live)]]
    assert "replaced" in message
    # The staging file must not survive the atomic rename.
    assert not (live.parent / f".{live.name}.new").exists()


def test_linux_handover_refuses_outside_an_appimage(tmp_path: Path) -> None:
    downloaded = tmp_path / "PersonalJarvis-Linux-x86_64.AppImage"
    downloaded.write_bytes(b"new appimage")
    with pytest.raises(InstallerUpdateError, match=r"\$APPIMAGE"):
        apply_installer(
            downloaded,
            platform_name="linux",
            runner=FakeCommandRunner(),
            appimage_path=None,
        )


def test_linux_handover_reports_a_failed_relaunch(tmp_path: Path) -> None:
    downloaded = tmp_path / "download.AppImage"
    downloaded.write_bytes(b"new appimage")
    live = tmp_path / "PersonalJarvis.AppImage"
    live.write_bytes(b"old appimage")

    runner = FakeCommandRunner(spawn_error=OSError("exec format error"))
    with pytest.raises(InstallerUpdateError, match="could not be relaunched"):
        apply_installer(downloaded, platform_name="linux", runner=runner, appimage_path=live)
    assert live.read_bytes() == b"old appimage"


@pytest.mark.parametrize("healthy", [True, False])
def test_native_supervisor_checks_new_version_and_restores_on_failure(
    tmp_path: Path, healthy: bool
) -> None:
    installer = tmp_path / "download.AppImage"
    installer.write_bytes(b"new appimage")
    live = tmp_path / "PersonalJarvis.AppImage"
    live.write_bytes(b"old appimage")
    manifest = tmp_path / "transaction.json"
    receipt = tmp_path / "shutdown.ok"
    receipt.write_text("graceful shutdown complete", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "parent_pid": 42,
                "platform": "linux",
                "installer": str(installer),
                "installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                "target": str(live),
                "version": "1.6.0",
                "previous_version": "1.5.3",
                "health_port": 47821,
                "shutdown_receipt": str(receipt),
            }
        ),
        encoding="utf-8",
    )
    runner = FakeCommandRunner()
    result = run_native_update_supervisor(
        manifest,
        runner=runner,
        alive=lambda _pid: False,
        health=lambda port, version, nonce: port == 47821 and bool(nonce)
        and (version == "1.6.0" if healthy else version == "1.5.3"),
        sleep=lambda _seconds: None,
        health_seconds=0.05,
    )
    assert result is healthy
    assert live.read_bytes() == (b"new appimage" if healthy else b"old appimage")
    assert runner.spawned == ([[str(live)]] if healthy else [[str(live)], [str(live)]])
    assert runner.terminated == ([] if healthy else [10001])
    assert all(len(item["JARVIS_UPDATE_HEALTH_NONCE"]) == 32 for item in runner.spawn_envs)
    if not healthy:
        assert runner.spawn_envs[0] != runner.spawn_envs[1]
    assert not manifest.exists()
    result_path = installer_update.native_update_result_dir() / ".jarvis-update-result.json"
    verdict = json.loads(result_path.read_text(encoding="utf-8"))
    assert verdict["ok"] is healthy
    assert verdict["rolled_back"] is not healthy
    assert isinstance(verdict["completed_at"], int)


def test_native_result_write_replaces_previous_verdict_atomically() -> None:
    installer_update.write_native_update_result(ok=False, rolled_back=True)
    installer_update.write_native_update_result(ok=True, rolled_back=False)
    directory = installer_update.native_update_result_dir()
    payload = json.loads((directory / ".jarvis-update-result.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["rolled_back"] is False
    assert list(directory.glob(".jarvis-update-result-*")) == []


def test_native_result_dir_falls_back_to_user_data_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_DATA_DIR")
    assert installer_update.native_update_result_dir() == user_data_dir()


def test_native_supervisor_refuses_changed_verified_installer(tmp_path: Path) -> None:
    installer = tmp_path / "download.AppImage"
    installer.write_bytes(b"tampered")
    live = tmp_path / "PersonalJarvis.AppImage"
    live.write_bytes(b"old appimage")
    manifest = tmp_path / "transaction.json"
    receipt = tmp_path / "shutdown.ok"
    receipt.write_text("graceful shutdown complete", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "parent_pid": 42,
                "platform": "linux",
                "installer": str(installer),
                "installer_sha256": "0" * 64,
                "target": str(live),
                "version": "1.6.0",
                "previous_version": "1.5.3",
                "health_port": 47821,
                "shutdown_receipt": str(receipt),
            }
        ),
        encoding="utf-8",
    )
    runner = FakeCommandRunner()
    assert run_native_update_supervisor(manifest, runner=runner, alive=lambda _pid: False) is False
    assert live.read_bytes() == b"old appimage"
    assert runner.spawned == [[str(live)]]


def test_native_supervisor_cancels_swap_without_graceful_shutdown_receipt(
    tmp_path: Path,
) -> None:
    installer = tmp_path / "download.AppImage"
    installer.write_bytes(b"new appimage")
    live = tmp_path / "PersonalJarvis.AppImage"
    live.write_bytes(b"old appimage")
    manifest = tmp_path / "transaction.json"
    manifest.write_text(json.dumps({
        "schema": 1, "parent_pid": 42, "platform": "linux",
        "installer": str(installer),
        "installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "target": str(live), "version": "1.6.0", "previous_version": "1.5.3",
        "health_port": 47821, "shutdown_receipt": str(tmp_path / "missing.ok"),
    }), encoding="utf-8")
    runner = FakeCommandRunner()
    assert run_native_update_supervisor(manifest, runner=runner, alive=lambda _pid: False) is False
    assert live.read_bytes() == b"old appimage"
    assert runner.spawned == [[str(live)]]


@pytest.mark.skipif(os.name == "nt", reason="macOS bundle swaps require POSIX directory renames")
def test_macos_supervisor_restores_bundle_after_failed_health(tmp_path: Path) -> None:
    dmg = tmp_path / "new.dmg"
    dmg.write_bytes(b"mounted fixture")
    app = tmp_path / "Personal Jarvis.app"
    executable = app / "Contents" / "MacOS" / "PersonalJarvis"
    executable.parent.mkdir(parents=True)
    executable.write_text("old", encoding="utf-8")
    manifest = tmp_path / "transaction.json"
    receipt = tmp_path / "shutdown.ok"
    receipt.write_text("graceful shutdown complete", encoding="utf-8")
    manifest.write_text(json.dumps({
        "schema": 1, "parent_pid": 42, "platform": "darwin",
        "installer": str(dmg),
        "installer_sha256": hashlib.sha256(dmg.read_bytes()).hexdigest(),
        "target": str(app), "executable_relative": "Contents/MacOS/PersonalJarvis",
        "version": "1.6.0", "previous_version": "1.5.3", "health_port": 47821,
        "shutdown_receipt": str(receipt),
    }), encoding="utf-8")
    runner = _mounting_runner("Personal Jarvis.app", "new")
    assert run_native_update_supervisor(
        manifest, runner=runner, alive=lambda _pid: False,
        health=lambda _port, version, _nonce: version == "1.5.3",
        sleep=lambda _seconds: None, health_seconds=0.05,
    ) is False
    assert executable.read_text(encoding="utf-8") == "old"
    assert runner.terminated == [10001]
    assert runner.spawned == [[str(executable)], [str(executable)]]


def test_native_supervisor_relaunches_old_app_when_staging_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = tmp_path / "download.AppImage"
    installer.write_bytes(b"new appimage")
    live = tmp_path / "PersonalJarvis.AppImage"
    live.write_bytes(b"old appimage")
    manifest = tmp_path / "transaction.json"
    receipt = tmp_path / "shutdown.ok"
    receipt.write_text("graceful shutdown complete", encoding="utf-8")
    manifest.write_text(json.dumps({
        "schema": 1, "parent_pid": 42, "platform": "linux",
        "installer": str(installer),
        "installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "target": str(live), "version": "1.6.0", "previous_version": "1.5.3",
        "health_port": 47821,
        "shutdown_receipt": str(receipt),
    }), encoding="utf-8")
    def fail_copy(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(installer_update.shutil, "copyfile", fail_copy)
    runner = FakeCommandRunner()
    assert run_native_update_supervisor(manifest, runner=runner, alive=lambda _pid: False) is False
    assert live.read_bytes() == b"old appimage"
    assert runner.spawned == [[str(live)]]


@pytest.mark.parametrize(
    ("returned_version", "returned_nonce", "expected"),
    [
        ("1.6.0", "owned-token", True),
        ("1.6.0", "other-token", False),
        ("1.5.3", "owned-token", False),
    ],
)
def test_native_health_requires_this_launchs_version_and_nonce(
    monkeypatch: pytest.MonkeyPatch,
    returned_version: str,
    returned_nonce: str,
    expected: bool,
) -> None:
    def answer(*_args: object, **_kwargs: object) -> io.BytesIO:
        return io.BytesIO(json.dumps({
            "ok": True,
            "version": returned_version,
            "update_nonce": returned_nonce,
        }).encode())

    monkeypatch.setattr(installer_update.urllib.request, "urlopen", answer)
    assert installer_update._health_has_version(47821, "1.6.0", "owned-token") is expected
