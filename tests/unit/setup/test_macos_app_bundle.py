"""Tests for the stable native macOS application identity (BUG-060)."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from jarvis.setup.macos_app_bundle import (
    APP_DIR_NAME,
    ensure_macos_app_bundle,
    macos_app_bundle_is_launchable,
    macos_app_bundle_path,
    macos_launch_services_command,
)


def _build(tmp_path: Path, monkeypatch) -> Path:
    import jarvis.setup.macos_app_bundle as mab

    # The explicit-path branch builds a structural native fixture off macOS.
    # Real py2app/codesign/LaunchServices coverage lives in macOS CI.
    monkeypatch.setattr(mab.sys, "platform", "linux")
    install_dir = tmp_path / "install"
    (install_dir / ".venv" / "bin").mkdir(parents=True)
    (install_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    bundle = ensure_macos_app_bundle(
        install_dir=install_dir,
        applications_dir=tmp_path / "Applications",
    )
    assert bundle is not None
    return bundle


def test_bundle_layout_and_plist(tmp_path: Path, monkeypatch) -> None:
    bundle = _build(tmp_path, monkeypatch)
    assert bundle.name == APP_DIR_NAME
    plist_path = bundle / "Contents" / "Info.plist"
    with plist_path.open("rb") as stream:
        info = plistlib.load(stream)
    executable_name = info["CFBundleExecutable"]
    assert info["CFBundleIdentifier"] == "com.personal-jarvis.desktop"
    assert info["CFBundlePackageType"] == "APPL"
    assert "microphone" in info["NSMicrophoneUsageDescription"].lower()
    assert "screen" in info["NSScreenCaptureUsageDescription"].lower()
    assert "NSAppleEventsUsageDescription" not in info
    executable = bundle / "Contents" / "MacOS" / executable_name
    assert executable.read_bytes()[:4] == b"\xcf\xfa\xed\xfe"
    if os.name != "nt":
        assert executable.stat().st_mode & stat.S_IXUSR
    assert macos_app_bundle_is_launchable(bundle) is True


def test_rerun_preserves_existing_bundle_byte_for_byte(tmp_path: Path, monkeypatch) -> None:
    first = _build(tmp_path, monkeypatch)
    executable_name = plistlib.loads(
        (first / "Contents" / "Info.plist").read_bytes()
    )["CFBundleExecutable"]
    executable = first / "Contents" / "MacOS" / executable_name
    marker = executable.read_bytes()
    second = ensure_macos_app_bundle(
        install_dir=tmp_path / "install",
        applications_dir=tmp_path / "Applications",
    )
    assert second == first
    assert executable.read_bytes() == marker


def test_failed_runtime_probe_rebuilds_instead_of_preserving(
    tmp_path: Path, monkeypatch
) -> None:
    import jarvis.setup.macos_app_bundle as mab

    bundle = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "_codesign_valid", lambda _bundle: True)
    monkeypatch.setattr(
        mab,
        "_current_process_identity_valid",
        lambda _bundle, *, install_root: False,
    )
    monkeypatch.setattr(
        mab,
        "_runtime_identity_valid",
        lambda _bundle, *, install_root: False,
    )
    rebuilt: list[tuple[Path, Path]] = []

    def _rebuild(install_root: Path, destination: Path) -> Path:
        rebuilt.append((install_root, destination))
        return destination

    monkeypatch.setattr(mab, "_install_native_bundle", _rebuild)
    install_root = tmp_path / "install"

    assert ensure_macos_app_bundle(
        install_dir=install_root,
        applications_dir=tmp_path / "Applications",
    ) == bundle
    assert rebuilt == [(install_root.resolve(), bundle)]


def test_running_canonical_app_skips_second_launchservices_probe(
    tmp_path: Path, monkeypatch
) -> None:
    import jarvis.setup.macos_app_bundle as mab

    bundle = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "_codesign_valid", lambda _bundle: True)
    monkeypatch.setattr(
        mab,
        "_current_process_identity_valid",
        lambda _bundle, *, install_root: True,
    )
    monkeypatch.setattr(
        mab,
        "_runtime_identity_valid",
        lambda *_args, **_kwargs: pytest.fail("must not spawn a second app probe"),
    )

    assert ensure_macos_app_bundle(
        install_dir=tmp_path / "install",
        applications_dir=tmp_path / "Applications",
    ) == bundle


def test_noop_off_darwin_without_override(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert ensure_macos_app_bundle() is None


def test_bundle_path_and_launchservices_command(tmp_path: Path, monkeypatch) -> None:
    bundle = _build(tmp_path, monkeypatch)
    probe = str(tmp_path / "probe.json")
    assert macos_app_bundle_path(
        applications_dir=tmp_path / "Applications"
    ) == bundle
    assert macos_launch_services_command(
        bundle,
        background=True,
        wait_for_exit=True,
    ) == ["/usr/bin/open", "-g", "-W", "-a", str(bundle)]
    assert macos_launch_services_command(
        bundle,
        wait_for_exit=True,
        new_instance=True,
        arguments=("--jarvis-identity-probe", probe),
    ) == [
        "/usr/bin/open",
        "-W",
        "-n",
        "-a",
        str(bundle),
        "--args",
        "--jarvis-identity-probe",
        probe,
    ]


def test_shell_executable_is_rejected(tmp_path: Path, monkeypatch) -> None:
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "linux")
    bundle = tmp_path / APP_DIR_NAME
    executable = bundle / "Contents" / "MacOS" / "PersonalJarvis"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    info = {
        "CFBundleExecutable": "PersonalJarvis",
        "CFBundleIdentifier": "com.personal-jarvis.desktop",
        "CFBundlePackageType": "APPL",
        "JarvisBundleFormatVersion": 1,
    }
    with (bundle / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    assert macos_app_bundle_is_launchable(bundle) is False


def test_incomplete_bundle_is_not_launchable(tmp_path: Path) -> None:
    bundle = tmp_path / APP_DIR_NAME
    bundle.mkdir()
    assert macos_app_bundle_is_launchable(bundle) is False


def test_launcher_identity_probe_uses_main_bundle(tmp_path: Path, monkeypatch) -> None:
    from jarvis.setup.macos_launcher_entry import main

    bundle = SimpleNamespace(
        bundleIdentifier=lambda: "com.personal-jarvis.desktop",
        bundlePath=lambda: "/Users/test/Applications/Personal Jarvis.app",
        executablePath=lambda: (
            "/Users/test/Applications/Personal Jarvis.app/Contents/MacOS/launcher"
        ),
    )
    foundation = ModuleType("Foundation")
    foundation.NSBundle = SimpleNamespace(mainBundle=lambda: bundle)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    probe = tmp_path / "probe.json"
    assert main(["--jarvis-identity-probe", str(probe)]) == 0
    assert json.loads(probe.read_text(encoding="utf-8")) == {
        "bundle_id": "com.personal-jarvis.desktop",
        "bundle_path": "/Users/test/Applications/Personal Jarvis.app",
        "executable": (
            "/Users/test/Applications/Personal Jarvis.app/Contents/MacOS/launcher"
        ),
        "launcher_file": str(
            Path(__file__).resolve().parents[3]
            / "jarvis"
            / "ui"
            / "web"
            / "launcher.py"
        ),
        "install_root": str(Path(__file__).resolve().parents[3]),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "machine": platform.machine(),
    }
