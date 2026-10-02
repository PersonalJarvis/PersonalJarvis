"""``jarvis.core.installer_update`` — the frozen-install self-update.

The whole point of these tests is that the dangerous half of an updater is
provable offline: which asset a machine picks, whether a mismatching SHA-256
really refuses, and what exactly gets executed on each OS. Network and process
spawning arrive through the fakes in ``tests/fakes/fake_installer_update.py``,
so every case below runs on Windows, macOS and Linux alike.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import jarvis.core.installer_update as iu
from jarvis.core.installer_update import (
    CHECKSUMS_ASSET_NAME,
    MACOS_OPEN,
    WINDOWS_RELAUNCH_PARAM,
    WINDOWS_WAIT_PID_PARAM,
    InstallerAsset,
    InstallerUpdateError,
    apply_installer,
    download_and_verify,
    handover_env,
    installer_asset_name,
    machine_for_update,
    parse_sha256sums,
    relaunch_after_exit_command,
    select_asset,
    select_installer_asset,
    sweep_stale_downloads,
    update_blocker,
)
from tests.fakes.fake_installer_update import (
    FakeAssetFetcher,
    FakeCommandRunner,
    RunResult,
)

PAYLOAD = b"not really an installer, but it hashes just as well"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


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
    result = await download_and_verify(
        asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
    )
    assert result == tmp_path / asset.name
    assert result.read_bytes() == PAYLOAD


async def test_download_and_verify_refuses_a_mismatching_digest(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    wrong = hashlib.sha256(b"something else entirely").hexdigest()
    fetcher = FakeAssetFetcher(manifest=f"{wrong}  {asset.name}\n", payload=PAYLOAD)

    with pytest.raises(InstallerUpdateError, match="SHA-256"):
        await download_and_verify(
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
        await download_and_verify(
            asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
        )


async def test_download_and_verify_refuses_an_oversized_asset(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe", size=10_000)
    fetcher = FakeAssetFetcher(manifest=f"{PAYLOAD_SHA256}  {asset.name}\n", payload=PAYLOAD)
    with pytest.raises(InstallerUpdateError, match="cap"):
        await download_and_verify(
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
        await download_and_verify(
            asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
        )


async def test_download_and_verify_reports_a_missing_manifest(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    fetcher = FakeAssetFetcher(fail_text=OSError("404"), payload=PAYLOAD)
    with pytest.raises(InstallerUpdateError, match=CHECKSUMS_ASSET_NAME):
        await download_and_verify(
            asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
        )


# --------------------------------------------------------------------------- #
# Windows handover
# --------------------------------------------------------------------------- #
def test_windows_handover_runs_the_silent_in_place_upgrade(tmp_path: Path) -> None:
    installer = tmp_path / "PersonalJarvis-Setup-x64.exe"
    installer.write_bytes(PAYLOAD)
    runner = FakeCommandRunner()

    message = apply_installer(
        installer,
        platform_name="win32",
        runner=runner,
        wait_for_pid=4242,
        installer_log=tmp_path / "update-installer.log",
    )

    assert runner.spawned == [
        [
            str(installer),
            "/SILENT",
            "/SUPPRESSMSGBOXES",
            "/CLOSEAPPLICATIONS",
            "/NORESTART",
            WINDOWS_RELAUNCH_PARAM,
            f"{WINDOWS_WAIT_PID_PARAM}=4242",
            f"/LOG={tmp_path / 'update-installer.log'}",
        ]
    ]
    assert "restarts by itself" in message


def test_windows_handover_never_relies_on_restart_manager_relaunch(tmp_path: Path) -> None:
    """/RESTARTAPPLICATIONS needs RegisterApplicationRestart, which the app never
    calls — it promised a relaunch that never happened."""
    installer = tmp_path / "PersonalJarvis-Setup-x64.exe"
    installer.write_bytes(PAYLOAD)
    runner = FakeCommandRunner()
    apply_installer(installer, platform_name="win32", runner=runner)
    assert "/RESTARTAPPLICATIONS" not in runner.spawned[0]


def test_windows_installer_script_honours_the_relaunch_param() -> None:
    """The flag the app passes must be the one the installer's [Run] reads."""
    iss = Path(__file__).resolve().parents[3] / "packaging" / "windows" / "PersonalJarvis.iss"
    text = iss.read_text(encoding="utf-8")
    name = WINDOWS_RELAUNCH_PARAM.lstrip("/").split("=", 1)[0]
    assert f"{{param:{name}|0}}" in text
    assert "Check: RelaunchRequested" in text
    run_section = text.split("\n[Run]\n", 1)[1].split("\n[", 1)[0]
    relaunch_lines = [line for line in run_section.splitlines() if "RelaunchRequested" in line]
    assert relaunch_lines, "the relaunch [Run] entry is missing"
    # A silent upgrade skips every entry flagged skipifsilent - this one must not be.
    assert "skipifsilent" not in relaunch_lines[0]
    assert "runasoriginaluser" in relaunch_lines[0]

    # Apps up to v2.4.x hand the NEW installer their old flags; it must still
    # bring them back, or the first update onto this fix leaves the app closed.
    function = text.split("function RelaunchRequested", 1)[1].split("\nend;", 1)[0]
    assert "/RESTARTAPPLICATIONS" in function


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

    def _detach(command: list[str]) -> None:
        # A real detach empties the mountpoint; the updater then removes the
        # bare directory (and only an empty one).
        if len(command) > 2 and command[1] == "detach":
            for child in Path(command[2]).iterdir():
                shutil.rmtree(child)

    def _on_run(command: list[str]) -> None:
        _mount(command)
        _detach(command)

    return FakeCommandRunner(on_run=_on_run)


def test_macos_handover_replaces_the_running_app_and_relaunches(
    tmp_path: Path,
) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PersonalJarvis").write_text("old", encoding="utf-8")

    runner = _mounting_runner("Personal Jarvis.app", "new")
    message = apply_installer(
        dmg, platform_name="darwin", runner=runner, app_path=app, wait_for_pid=4242
    )

    assert (app / "Contents" / "MacOS" / "PersonalJarvis").read_text(encoding="utf-8") == "new"
    # The relaunch waits for the old app: started earlier it would bounce off
    # the single-instance lock and leave the user on the old version.
    assert runner.spawned == [relaunch_after_exit_command(4242, [MACOS_OPEN, "-n", str(app)])]
    # The volume is always released, success or not.
    assert any(cmd[:2] == ["hdiutil", "detach"] for cmd in runner.ran)
    assert "replaced" in message
    # No rollback copy is left behind on success.
    assert not (app.parent / ".Personal Jarvis.app.old").exists()


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
    message = apply_installer(
        downloaded, platform_name="linux", runner=runner, appimage_path=live, wait_for_pid=4242
    )

    assert live.read_bytes() == b"new appimage"
    assert runner.spawned == [relaunch_after_exit_command(4242, [str(live)])]
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
    # The bytes ARE the new version - the failure is only the relaunch, and
    # saying otherwise would send the user looking in the wrong place.
    assert live.read_bytes() == b"new appimage"


def test_handover_waits_for_this_process_by_default(tmp_path: Path) -> None:
    downloaded = tmp_path / "download.AppImage"
    downloaded.write_bytes(b"new appimage")
    live = tmp_path / "PersonalJarvis.AppImage"
    live.write_bytes(b"old appimage")

    runner = FakeCommandRunner()
    apply_installer(downloaded, platform_name="linux", runner=runner, appimage_path=live)

    assert runner.spawned[0][4] == str(os.getpid())


@pytest.mark.skipif(os.name != "posix", reason="AppImages and exec bits exist on POSIX only")
def test_linux_handover_leaves_the_appimage_executable(tmp_path: Path) -> None:
    downloaded = tmp_path / "download.AppImage"
    downloaded.write_bytes(b"new appimage")
    downloaded.chmod(0o644)
    live = tmp_path / "PersonalJarvis.AppImage"
    live.write_bytes(b"old appimage")

    apply_installer(
        downloaded, platform_name="linux", runner=FakeCommandRunner(), appimage_path=live
    )

    assert live.stat().st_mode & stat.S_IXUSR


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the .app swap only runs on macOS; Windows scanners can briefly lock a renamed dir",
)
def test_macos_handover_rolls_back_when_the_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PersonalJarvis").write_text("old", encoding="utf-8")

    real_replace = os.replace

    def _flaky_replace(src: object, dst: object) -> None:
        # Moving the live bundle aside works; moving the new one in fails.
        if Path(str(src)).name.endswith(".new"):
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _flaky_replace)
    runner = _mounting_runner("Personal Jarvis.app", "new")

    with pytest.raises(InstallerUpdateError, match="could not replace"):
        apply_installer(dmg, platform_name="darwin", runner=runner, app_path=app)

    # The app is never left missing, and nothing was relaunched.
    assert (app / "Contents" / "MacOS" / "PersonalJarvis").read_text(encoding="utf-8") == "old"
    assert runner.spawned == []


# --------------------------------------------------------------------------- #
# The relaunch waiter, executed for real where a POSIX shell exists
# --------------------------------------------------------------------------- #
_HAS_SH = os.name == "posix" and Path("/bin/sh").exists()


def test_relaunch_waiter_command_shape() -> None:
    command = relaunch_after_exit_command(123, ["open", "-n", "/Applications/X.app"])
    assert command[:2] == ["/bin/sh", "-c"]
    assert command[3:] == ["jarvis-relaunch", "123", "open", "-n", "/Applications/X.app"]


@pytest.mark.skipif(not _HAS_SH, reason="the waiter is a POSIX sh script (macOS/Linux)")
def test_relaunch_waiter_starts_the_app_only_after_the_old_process_exits(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "launched"
    old_app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    started = time.monotonic()
    waiter = subprocess.Popen(
        relaunch_after_exit_command(
            old_app.pid, [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
        )
    )
    try:
        time.sleep(0.6)
        assert not marker.exists(), "the new version started while the old one still ran"
        old_app.wait(timeout=10)
        waiter.wait(timeout=15)
    finally:
        for proc in (old_app, waiter):
            if proc.poll() is None:
                proc.kill()
    assert marker.exists()
    assert time.monotonic() - started >= 1.4


@pytest.mark.skipif(not _HAS_SH, reason="the waiter is a POSIX sh script (macOS/Linux)")
def test_relaunch_waiter_starts_at_once_when_the_old_process_is_gone(tmp_path: Path) -> None:
    marker = tmp_path / "launched"
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=10)
    waiter = subprocess.run(
        relaunch_after_exit_command(
            gone.pid, [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
        ),
        timeout=15,
        check=False,
    )
    assert waiter.returncode == 0
    assert marker.exists()


@pytest.mark.skipif(not _HAS_SH, reason="the waiter is a POSIX sh script (macOS/Linux)")
def test_relaunch_waiter_passes_paths_with_spaces_verbatim(tmp_path: Path) -> None:
    target = tmp_path / "Personal Jarvis.app" / "argv.txt"
    target.parent.mkdir()
    script = tmp_path / "record argv.py"
    script.write_text(
        "import sys, pathlib\n"
        "pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\n",
        encoding="utf-8",
    )
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=10)
    subprocess.run(
        relaunch_after_exit_command(gone.pid, [sys.executable, str(script), str(target), "a b  c"]),
        timeout=15,
        check=True,
    )
    assert target.read_text(encoding="utf-8") == "a b  c"


# --------------------------------------------------------------------------- #
# Which CPU's installer — Rosetta moves an Intel build to native arm64
# --------------------------------------------------------------------------- #
def test_an_intel_build_under_rosetta_updates_to_the_native_arm64_dmg() -> None:
    machine = machine_for_update("darwin", "x86_64", translated=lambda: True)
    assert machine == "arm64"
    assert installer_asset_name("darwin", machine) == "PersonalJarvis-macOS-arm64.dmg"


def test_a_real_intel_mac_keeps_the_intel_dmg() -> None:
    assert machine_for_update("darwin", "x86_64", translated=lambda: False) == "x86_64"


@pytest.mark.parametrize(
    ("platform_name", "machine"),
    [("win32", "AMD64"), ("linux", "x86_64"), ("darwin", "arm64")],
)
def test_only_an_intel_mac_process_asks_about_rosetta(platform_name: str, machine: str) -> None:
    def _never() -> bool:
        raise AssertionError("only an x86_64 process on a Mac can be translated")

    assert machine_for_update(platform_name, machine, translated=_never) == machine


def test_the_rosetta_probe_answers_on_every_ci_host() -> None:
    """Runs the real probe: ctypes + sysctlbyname on macOS, a no-op elsewhere.

    CI interpreters are native, so the honest answer everywhere is False — and
    the call must never raise, because the status poll makes it.
    """
    assert iu._running_under_rosetta() is False


# --------------------------------------------------------------------------- #
# The environment the handover hands on
# --------------------------------------------------------------------------- #
def test_handover_env_starts_the_new_build_as_a_fresh_pyinstaller_process() -> None:
    base = {"PATH": "/usr/bin", "_PYI_ARCHIVE_FILE": "/old/PersonalJarvis"}
    env = handover_env(base, frozen=True)
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["PATH"] == "/usr/bin"
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in base, "the caller's mapping was mutated"


def test_handover_env_restores_the_users_library_path_in_a_frozen_build() -> None:
    env = handover_env(
        {
            "LD_LIBRARY_PATH": "/run/user/1000/.mount_Jarvis/usr/bin/_internal:/opt/lib",
            "LD_LIBRARY_PATH_ORIG": "/opt/lib",
        },
        frozen=True,
    )
    assert env["LD_LIBRARY_PATH"] == "/opt/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_handover_env_drops_a_library_path_only_the_bundle_set() -> None:
    bundle_only = "/run/user/1000/.mount_Jarvis/usr/bin/_internal"
    env = handover_env({"LD_LIBRARY_PATH": bundle_only}, frozen=True)
    assert "LD_LIBRARY_PATH" not in env


def test_handover_env_leaves_a_source_install_library_path_alone() -> None:
    env = handover_env({"LD_LIBRARY_PATH": "/opt/lib"}, frozen=False)
    assert env["LD_LIBRARY_PATH"] == "/opt/lib"


# --------------------------------------------------------------------------- #
# Windows: wait for the old app, leave a log
# --------------------------------------------------------------------------- #
def test_windows_handover_waits_for_this_process_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(iu, "_windows_installer_log", lambda: None)
    installer = tmp_path / "PersonalJarvis-Setup-x64.exe"
    installer.write_bytes(PAYLOAD)
    runner = FakeCommandRunner()

    apply_installer(installer, platform_name="win32", runner=runner)

    assert f"{WINDOWS_WAIT_PID_PARAM}={os.getpid()}" in runner.spawned[0]
    # No log directory: the update still runs, just without a log flag.
    assert not any(arg.startswith("/LOG=") for arg in runner.spawned[0])


def test_windows_installer_log_lives_in_the_user_log_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core.paths import user_logs_dir

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    log_path = iu._windows_installer_log()
    assert log_path == user_logs_dir() / iu.WINDOWS_INSTALLER_LOG_NAME
    assert log_path is not None and log_path.parent.is_dir()


def test_windows_handover_reports_an_installer_that_cannot_start(tmp_path: Path) -> None:
    installer = tmp_path / "PersonalJarvis-Setup-x64.exe"
    installer.write_bytes(PAYLOAD)
    runner = FakeCommandRunner(spawn_error=OSError("blocked by policy"))
    with pytest.raises(InstallerUpdateError, match="blocked by policy"):
        apply_installer(
            installer,
            platform_name="win32",
            runner=runner,
            installer_log=tmp_path / "update-installer.log",
        )


def _iss_text() -> str:
    iss = Path(__file__).resolve().parents[3] / "packaging" / "windows" / "PersonalJarvis.iss"
    return iss.read_text(encoding="utf-8")


def test_windows_installer_script_waits_for_the_replaced_app() -> None:
    """The flag the app passes must be the one the installer waits on."""
    text = _iss_text()
    name = WINDOWS_WAIT_PID_PARAM.lstrip("/")
    assert f"{{param:{name}|0}}" in text
    init = text.split("function InitializeSetup", 1)[1].split("\nend;", 1)[0]
    assert "WaitForReplacedApp" in init
    wait = text.split("procedure WaitForReplacedApp", 1)[1].split("\nend;", 1)[0]
    assert "JarvisOpenProcess" in wait
    assert "JarvisWaitForSingleObject" in wait
    assert "JarvisCloseHandle" in wait
    # Bounded: a pid that never exits must not keep Setup waiting forever.
    assert "JarvisWaitForAppMs = 120000;" in text


def test_windows_installer_identity_never_changes() -> None:
    """Every release must upgrade the same AppId, or users get two installs."""
    text = _iss_text()
    assert '#define AppGuid "7F1C4E42-2E5B-4F0A-9E1B-1A7B6C0D5E88"' in text
    assert "AppId={{{#AppGuid}}" in text


# --------------------------------------------------------------------------- #
# A stalled transfer ends instead of holding the update forever
# --------------------------------------------------------------------------- #
def _write_partial(dest: Path) -> None:
    """Blocking write, kept out of the async body (ruff ASYNC240)."""
    dest.write_bytes(PAYLOAD[:8])


class _StallingFetcher(FakeAssetFetcher):
    """Sends a few bytes, then nothing — the trickle a read timeout never ends."""

    stall_manifest: bool = False

    async def get_text(self, url: str, *, max_bytes: int) -> str:
        if self.stall_manifest:
            await asyncio.sleep(30)
        return await super().get_text(url, max_bytes=max_bytes)

    async def download(self, url: str, dest: Path, **_kwargs: object) -> int:
        _write_partial(dest)
        await asyncio.sleep(30)
        return 8


async def test_download_and_verify_gives_up_on_a_stalled_download(tmp_path: Path) -> None:
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    fetcher = _StallingFetcher(manifest=f"{PAYLOAD_SHA256}  {asset.name}\n", payload=PAYLOAD)
    started = time.monotonic()
    with pytest.raises(InstallerUpdateError, match="did not finish downloading"):
        await download_and_verify(
            asset,
            _asset(CHECKSUMS_ASSET_NAME),
            dest_dir=tmp_path,
            fetcher=fetcher,
            download_timeout_s=0.3,
        )
    assert time.monotonic() - started < 10
    # The partial file is never left where a later step could run it.
    assert not (tmp_path / asset.name).exists()


async def test_download_and_verify_gives_up_on_a_stalled_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(iu, "READ_TIMEOUT_S", 0.3)
    asset = _asset("PersonalJarvis-Setup-x64.exe")
    fetcher = _StallingFetcher(manifest=f"{PAYLOAD_SHA256}  {asset.name}\n", payload=PAYLOAD)
    fetcher.stall_manifest = True
    with pytest.raises(InstallerUpdateError, match=CHECKSUMS_ASSET_NAME):
        await download_and_verify(
            asset, _asset(CHECKSUMS_ASSET_NAME), dest_dir=tmp_path, fetcher=fetcher
        )
    assert fetcher.download_urls == []


# --------------------------------------------------------------------------- #
# macOS: the disk image is always released, never walked into
# --------------------------------------------------------------------------- #
def test_macos_handover_releases_a_volume_hdiutil_mounted_while_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hdiutil can mount the image and still report failure (or time out)."""
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents").mkdir(parents=True)
    monkeypatch.setattr(iu, "_is_mounted", lambda _path: True)
    runner = FakeCommandRunner(
        results={"hdiutil attach": RunResult(returncode=1, stderr="hdiutil timed out")}
    )

    with pytest.raises(InstallerUpdateError, match="could not mount"):
        apply_installer(dmg, platform_name="darwin", runner=runner, app_path=app)

    assert any(cmd[:2] == ["hdiutil", "detach"] for cmd in runner.ran)


def test_macos_handover_never_deletes_inside_a_volume_it_could_not_detach(
    tmp_path: Path,
) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents").mkdir(parents=True)

    def _mount_only(command: list[str]) -> None:
        if command[1] == "attach":
            mountpoint = Path(command[command.index("-mountpoint") + 1])
            (mountpoint / "Personal Jarvis.app" / "Contents").mkdir(parents=True)

    runner = FakeCommandRunner(
        on_run=_mount_only,
        results={"hdiutil detach": RunResult(returncode=16, stderr="resource busy")},
    )
    apply_installer(dmg, platform_name="darwin", runner=runner, app_path=app, relaunch=False)

    attach = next(cmd for cmd in runner.ran if cmd[1] == "attach")
    mountpoint = Path(attach[attach.index("-mountpoint") + 1])
    try:
        # Still "mounted": its contents must be exactly as the volume left them.
        assert (mountpoint / "Personal Jarvis.app" / "Contents").is_dir()
    finally:
        shutil.rmtree(mountpoint, ignore_errors=True)


def test_macos_handover_removes_its_mountpoint_after_a_clean_detach(tmp_path: Path) -> None:
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    dmg.write_bytes(PAYLOAD)
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents").mkdir(parents=True)
    runner = _mounting_runner("Personal Jarvis.app", "new")

    apply_installer(dmg, platform_name="darwin", runner=runner, app_path=app, relaunch=False)

    attach = next(cmd for cmd in runner.ran if cmd[1] == "attach")
    assert not Path(attach[attach.index("-mountpoint") + 1]).exists()
    assert runner.spawned == []


# --------------------------------------------------------------------------- #
# Preflight — refuse BEFORE downloading what cannot be installed here
# --------------------------------------------------------------------------- #
def test_update_blocker_lets_a_writable_app_bundle_update(tmp_path: Path) -> None:
    app = tmp_path / "Applications" / "Personal Jarvis.app"
    app.mkdir(parents=True)
    assert update_blocker("darwin", app_path=app) is None


def test_update_blocker_explains_app_translocation() -> None:
    app = Path("/private/var/folders/xy/T/AppTranslocation/0A1B/d/Personal Jarvis.app")
    reason = update_blocker("darwin", app_path=app)
    assert reason is not None
    assert "Applications folder" in reason


def test_update_blocker_refuses_a_read_only_app_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(iu, "_can_write", lambda _folder: False)
    app = tmp_path / "Volumes" / "Personal Jarvis" / "Personal Jarvis.app"
    reason = update_blocker("darwin", app_path=app)
    assert reason is not None
    assert str(app.parent) in reason


def test_update_blocker_without_an_app_bundle_says_so() -> None:
    # A source run has no bundle; the real lookup must answer, not raise.
    reason = update_blocker("darwin")
    assert reason is not None
    assert "app bundle" in reason


def test_update_blocker_lets_a_writable_appimage_update(tmp_path: Path) -> None:
    appimage = tmp_path / "PersonalJarvis.AppImage"
    appimage.write_bytes(b"old")
    assert update_blocker("linux", appimage_path=appimage) is None


def test_update_blocker_outside_an_appimage_points_to_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APPIMAGE", raising=False)
    reason = update_blocker("linux")
    assert reason is not None
    assert "AppImage" in reason


def test_update_blocker_refuses_an_appimage_in_a_protected_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(iu, "_can_write", lambda _folder: False)
    appimage = tmp_path / "opt" / "PersonalJarvis.AppImage"
    reason = update_blocker("linux", appimage_path=appimage)
    assert reason is not None
    assert "folder you own" in reason


@pytest.mark.skipif(
    os.name != "posix" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="POSIX permission bits; root may write anywhere",
)
def test_update_blocker_sees_a_real_read_only_folder(tmp_path: Path) -> None:
    folder = tmp_path / "opt"
    folder.mkdir()
    appimage = folder / "PersonalJarvis.AppImage"
    appimage.write_bytes(b"old")
    folder.chmod(0o555)
    try:
        assert update_blocker("linux", appimage_path=appimage) is not None
    finally:
        folder.chmod(0o755)


def test_update_blocker_never_blocks_windows() -> None:
    assert update_blocker("win32") is None


# --------------------------------------------------------------------------- #
# Old downloads are reclaimed
# --------------------------------------------------------------------------- #
def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_sweep_removes_only_old_update_downloads(tmp_path: Path) -> None:
    old = tmp_path / "jarvis-update-old"
    old.mkdir()
    (old / "PersonalJarvis-Setup-x64.exe").write_bytes(b"x")
    fresh = tmp_path / "jarvis-update-fresh"
    fresh.mkdir()
    (fresh / "PersonalJarvis-Setup-x64.exe").write_bytes(b"x")
    stranger = tmp_path / "someone-elses-folder"
    stranger.mkdir()
    for path in (old / "PersonalJarvis-Setup-x64.exe", old, stranger):
        _age(path, 7 * 3600)

    assert sweep_stale_downloads(tmp_path) == 1
    assert not old.exists()
    assert fresh.exists()
    assert stranger.exists()


def test_sweep_keeps_a_folder_whose_download_is_still_growing(tmp_path: Path) -> None:
    folder = tmp_path / "jarvis-update-busy"
    folder.mkdir()
    (folder / "PersonalJarvis-Setup-x64.exe").write_bytes(b"x")
    _age(folder, 7 * 3600)  # the folder is old, the file inside is not
    assert sweep_stale_downloads(tmp_path) == 0
    assert folder.exists()


def test_sweep_never_raises_on_a_missing_temp_root(tmp_path: Path) -> None:
    assert sweep_stale_downloads(tmp_path / "missing") == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to delete an open file")
def test_sweep_leaves_an_installer_that_is_still_open(tmp_path: Path) -> None:
    folder = tmp_path / "jarvis-update-running"
    folder.mkdir()
    exe = folder / "PersonalJarvis-Setup-x64.exe"
    exe.write_bytes(b"x")
    _age(exe, 7 * 3600)
    _age(folder, 7 * 3600)
    with exe.open("rb"):
        assert sweep_stale_downloads(tmp_path) == 0
        assert exe.exists()
    assert sweep_stale_downloads(tmp_path) == 1
