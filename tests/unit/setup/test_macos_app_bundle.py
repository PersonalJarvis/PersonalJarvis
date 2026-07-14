"""macOS .app bundle writer (BUG-060).

After closing the desktop app on a Mac there was no way back: Spotlight and
/Applications showed nothing because a plain pip install ships no bundle.
``ensure_macos_app_bundle`` writes a per-user ``Personal Jarvis.app`` whose
executable script exec's the venv Python desktop launcher. Contracts:

- correct bundle layout + Info.plist (incl. NSMicrophoneUsageDescription —
  a bundled process gets its own TCC identity, so the mic prompt must carry
  an honest usage string instead of being killed for lacking one),
- launcher script points at the install's venv python and the launcher module,
- idempotent re-runs, no-op off darwin without an explicit override.

Pure file writes — provable on any OS via injectable directories (same
pattern as jarvis/autostart/macos.py).
"""
from __future__ import annotations

import os
import plistlib
import stat
from pathlib import Path

from jarvis.setup.macos_app_bundle import (
    APP_DIR_NAME,
    ensure_macos_app_bundle,
)


def _build(tmp_path: Path) -> Path:
    install_dir = tmp_path / "install"
    (install_dir / ".venv" / "bin").mkdir(parents=True)
    (install_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    apps = tmp_path / "Applications"
    bundle = ensure_macos_app_bundle(
        install_dir=install_dir, applications_dir=apps
    )
    assert bundle is not None
    return bundle


def test_bundle_layout_and_plist(tmp_path: Path) -> None:
    bundle = _build(tmp_path)
    assert bundle.name == APP_DIR_NAME  # "Personal Jarvis.app"
    plist_path = bundle / "Contents" / "Info.plist"
    with plist_path.open("rb") as fh:
        info = plistlib.load(fh)
    assert info["CFBundleExecutable"] == "PersonalJarvis"
    assert info["CFBundleIdentifier"] == "com.personal-jarvis.desktop"
    assert info["CFBundlePackageType"] == "APPL"
    # TCC: a bundled app is killed on first mic open without a usage string.
    assert "microphone" in info["NSMicrophoneUsageDescription"].lower()
    exe = bundle / "Contents" / "MacOS" / "PersonalJarvis"
    body = exe.read_text(encoding="utf-8")
    assert body.startswith("#!/bin/bash")
    assert "jarvis.ui.web.launcher" in body
    assert ".venv" in body  # exec's the install venv's python
    if os.name == "posix":
        assert exe.stat().st_mode & stat.S_IXUSR


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    first = _build(tmp_path)
    second = ensure_macos_app_bundle(
        install_dir=tmp_path / "install",
        applications_dir=tmp_path / "Applications",
    )
    assert second == first
    with (first / "Contents" / "Info.plist").open("rb") as fh:
        assert plistlib.load(fh)["CFBundleExecutable"] == "PersonalJarvis"


def test_noop_off_darwin_without_override(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert ensure_macos_app_bundle() is None


def test_missing_icon_tooling_still_builds_bundle(tmp_path: Path, monkeypatch) -> None:
    # iconutil/PIL failures are best-effort: no icon key, bundle still works.
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab, "_try_build_icns", lambda _res: None)
    bundle = _build(tmp_path)
    with (bundle / "Contents" / "Info.plist").open("rb") as fh:
        info = plistlib.load(fh)
    assert "CFBundleIconFile" not in info
