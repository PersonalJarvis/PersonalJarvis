"""macOS ``.app`` bundle so Jarvis is discoverable like a real app (BUG-060).

A plain pip install ships no bundle, so after closing the desktop app on a
Mac there was no way back: Spotlight found nothing, Launchpad and
``/Applications`` showed nothing, and relaunching required a terminal
command. :func:`ensure_macos_app_bundle` writes a minimal per-user
``~/Applications/Personal Jarvis.app`` whose executable is a two-line bash
script exec'ing the install venv's Python desktop launcher — Spotlight
indexes ``~/Applications``, so Cmd+Space finds "Personal Jarvis".

Bonus: a real bundle gives TCC an identity. The microphone permission
prompt names "Personal Jarvis" with the honest usage string below, instead
of attributing (or denying) access to whichever terminal happened to start
the process.

The bundle build is pure stdlib file writes — CI-provable on any OS via the
injectable directories (same pattern as ``jarvis/autostart/macos.py``). Only
the optional icon conversion shells out to ``iconutil`` (darwin-only,
best-effort: a missing icon never blocks the bundle).
"""
from __future__ import annotations

import logging
import plistlib
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "Personal Jarvis"
APP_DIR_NAME = f"{APP_NAME}.app"
BUNDLE_ID = "com.personal-jarvis.desktop"
_EXECUTABLE_NAME = "PersonalJarvis"

_MIC_USAGE = (
    "Personal Jarvis listens on this microphone for your wake word and "
    "voice commands."
)
_APPLE_EVENTS_USAGE = (
    "Personal Jarvis uses AppleScript to focus and arrange application "
    "windows you ask it to control."
)


def _version() -> str:
    try:
        from jarvis import __version__

        return __version__
    except Exception:  # noqa: BLE001 — version is cosmetic here
        return "0.0.0"


def _default_install_dir() -> Path:
    """The install root — the parent of the ``jarvis`` package directory."""
    import jarvis

    return Path(jarvis.__file__).resolve().parents[1]


def _venv_python(install_dir: Path) -> Path:
    candidate = install_dir / ".venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    # Editable dev install / non-standard layout: whatever runs us now.
    return Path(sys.executable)


def _try_build_icns(resources_dir: Path) -> str | None:
    """Best-effort ``jarvis.icns`` from the packaged PNG. darwin-only.

    Returns the CFBundleIconFile stem, or ``None`` — a missing icon must
    never block the bundle (macOS falls back to the generic app icon).
    """
    if sys.platform != "darwin":
        return None
    try:
        from PIL import Image  # noqa: PLC0415 — optional at this call site

        src = Path(__file__).resolve().parents[1] / "assets" / "icons" / "jarvis.png"
        if not src.is_file():
            return None
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "jarvis.iconset"
            iconset.mkdir()
            img = Image.open(src).convert("RGBA")
            for size in (16, 32, 64, 128, 256, 512):
                img.resize((size, size)).save(iconset / f"icon_{size}x{size}.png")
                img.resize((size * 2, size * 2)).save(
                    iconset / f"icon_{size}x{size}@2x.png"
                )
            out = resources_dir / "jarvis.icns"
            result = subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode == 0 and out.is_file():
                return "jarvis"
    except Exception as exc:  # noqa: BLE001 — icon is a nicety, never a blocker
        log.debug("icns build skipped: %s", exc)
    return None


def ensure_macos_app_bundle(
    *,
    install_dir: Path | None = None,
    applications_dir: Path | None = None,
) -> Path | None:
    """Write (or refresh) ``~/Applications/Personal Jarvis.app``.

    Returns the bundle path, or ``None`` off darwin (unless
    ``applications_dir`` is injected — the cross-platform test seam).
    Never raises: a failed bundle write is logged and returns ``None`` —
    the app itself keeps working, only rediscovery stays terminal-based.
    """
    if applications_dir is None:
        if sys.platform != "darwin":
            log.info("App bundle skipped: only macOS uses .app bundles.")
            return None
        applications_dir = Path.home() / "Applications"
    try:
        install_root = install_dir or _default_install_dir()
        python = _venv_python(install_root)

        bundle = applications_dir / APP_DIR_NAME
        contents = bundle / "Contents"
        macos_dir = contents / "MacOS"
        resources = contents / "Resources"
        macos_dir.mkdir(parents=True, exist_ok=True)
        resources.mkdir(parents=True, exist_ok=True)

        exe = macos_dir / _EXECUTABLE_NAME
        exe.write_text(
            "#!/bin/bash\n"
            "# Launches the Personal Jarvis desktop app from its install venv.\n"
            f'exec "{python}" -m jarvis.ui.web.launcher "$@"\n',
            encoding="utf-8",
        )
        exe.chmod(
            exe.stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
        )

        info: dict[str, object] = {
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": _EXECUTABLE_NAME,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": _version(),
            "CFBundleVersion": _version(),
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": _MIC_USAGE,
            "NSAppleEventsUsageDescription": _APPLE_EVENTS_USAGE,
        }
        icon_stem = _try_build_icns(resources)
        if icon_stem is not None:
            info["CFBundleIconFile"] = icon_stem

        with (contents / "Info.plist").open("wb") as fh:
            plistlib.dump(info, fh)

        log.info("macOS app bundle written: %s", bundle)
        return bundle
    except Exception as exc:  # noqa: BLE001 — rediscovery nicety, never fatal
        log.warning("macOS app bundle could not be written: %s", exc)
        return None


def remove_macos_app_bundle(*, applications_dir: Path | None = None) -> bool:
    """Delete the bundle (uninstall path). Returns True when it is gone."""
    if applications_dir is None:
        if sys.platform != "darwin":
            return True
        applications_dir = Path.home() / "Applications"
    bundle = applications_dir / APP_DIR_NAME
    if not bundle.exists():
        return True
    try:
        import shutil

        shutil.rmtree(bundle)
        log.info("macOS app bundle removed: %s", bundle)
        return True
    except OSError as exc:
        log.warning("Could not remove %s: %s", bundle, exc)
        return False


__all__ = [
    "APP_DIR_NAME",
    "APP_NAME",
    "BUNDLE_ID",
    "ensure_macos_app_bundle",
    "remove_macos_app_bundle",
]
