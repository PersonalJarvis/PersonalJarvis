"""Build the managed-install macOS app identity (BUG-060).

The bundle's main executable must remain a Mach-O process. A shell launcher
that ``exec``s an external virtual-environment Python loses its ``NSBundle``
identity and causes TCC grants to attach to Python or a terminal instead of
Personal Jarvis. The managed installer therefore uses py2app's alias mode: its
native bootstrap embeds the active Python runtime in the app process while the
source and dependencies remain in the managed checkout.

The locally generated app is ad-hoc signed and is preserved byte-for-byte on
ordinary source updates so its local TCC identity does not churn. Public binary
distribution still requires the separate Developer-ID signing and notarization
pipeline; this module never claims an ad-hoc app is a notarized artifact.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

APP_NAME = "Personal Jarvis"
APP_DIR_NAME = f"{APP_NAME}.app"
BUNDLE_ID = "com.personal-jarvis.desktop"
_BUNDLE_FORMAT_VERSION = 1
_MACHO_MAGICS = frozenset(
    {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
)

_MIC_USAGE = "Personal Jarvis listens on this microphone for your wake word and voice commands."
_SCREEN_CAPTURE_USAGE = (
    "Personal Jarvis captures the screen only when you ask it to see or control applications."
)


def _version() -> str:
    try:
        from jarvis import __version__

        return __version__
    except Exception:  # noqa: BLE001 - version metadata is cosmetic here
        return "0.0.0"


def _default_install_dir() -> Path:
    """Return the parent of the installed ``jarvis`` package directory."""
    import jarvis

    return Path(jarvis.__file__).resolve().parents[1]


def _venv_python(install_dir: Path) -> Path:
    candidate = install_dir / ".venv" / "bin" / "python"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return Path(sys.executable)


def macos_app_bundle_path(*, applications_dir: Path | None = None) -> Path:
    """Return the one canonical per-user application-bundle path."""
    root = applications_dir or (Path.home() / "Applications")
    return root / APP_DIR_NAME


def _is_macho_executable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
        with path.open("rb") as stream:
            magic = stream.read(4)
    except OSError:
        return False
    # Windows cannot represent POSIX execute bits. The only Windows caller is
    # the explicit cross-platform fixture seam; production validation runs on
    # macOS and therefore still requires an executable mode.
    executable_mode = os.name == "nt" or bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return executable_mode and magic in _MACHO_MAGICS


def _codesign_issue(bundle: Path) -> str | None:
    """Return the codesign verification failure detail, or ``None`` if valid.

    Deliberately verifies WITHOUT ``--strict`` and ``--deep``: the local app
    is a py2app *alias* bundle whose entire design is symlinking the managed
    checkout and Python runtime, and strict validation rejects every symlink
    that leaves the bundle ("invalid destination for symbolic link") — it
    failed on 100% of freshly built bundles on real macOS (Intel and Apple
    Silicon alike). The ad-hoc signature only has to give the app a stable
    local TCC identity; distribution-grade validation belongs to the separate
    Developer-ID signing and notarization pipeline.
    """
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", str(bundle)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"codesign verification did not run: {exc}"
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout or "unknown codesign error").strip()


def _codesign_valid(bundle: Path) -> bool:
    return _codesign_issue(bundle) is None


def macos_app_bundle_is_launchable(bundle: Path | None = None) -> bool:
    """Validate native executable, canonical metadata, and code signature."""
    candidate = bundle or macos_app_bundle_path()
    info_path = candidate / "Contents" / "Info.plist"
    if (
        candidate.name != APP_DIR_NAME
        or candidate.is_symlink()
        or not candidate.is_dir()
        or not info_path.is_file()
    ):
        return False
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException):
        return False
    executable_name = info.get("CFBundleExecutable")
    if (
        not isinstance(executable_name, str)
        or Path(executable_name).name != executable_name
        or info.get("CFBundleIdentifier") != BUNDLE_ID
        or info.get("CFBundlePackageType") != "APPL"
        or info.get("JarvisBundleFormatVersion") != _BUNDLE_FORMAT_VERSION
    ):
        return False
    executable = candidate / "Contents" / "MacOS" / executable_name
    return (
        executable.is_file()
        and not executable.is_symlink()
        and _is_macho_executable(executable)
        and _codesign_valid(candidate)
    )


def macos_launch_services_command(
    bundle: Path | None = None,
    *,
    background: bool = False,
    wait_for_exit: bool = False,
    new_instance: bool = False,
    arguments: tuple[str, ...] = (),
) -> list[str]:
    """Build the ``open`` argv that enters through LaunchServices."""
    candidate = bundle or macos_app_bundle_path()
    command = ["/usr/bin/open"]
    if background:
        command.append("-g")
    if wait_for_exit:
        command.append("-W")
    if new_instance:
        command.append("-n")
    command.extend(["-a", str(candidate)])
    if arguments:
        command.append("--args")
        command.extend(arguments)
    return command


def _try_build_icns(resources_dir: Path) -> str | None:
    """Best-effort ``jarvis.icns`` creation; never block bundle creation."""
    if sys.platform != "darwin":
        return None
    try:
        from PIL import Image  # noqa: PLC0415 - optional at this boundary

        source = Path(__file__).resolve().parents[1] / "assets" / "icons" / "jarvis.png"
        if not source.is_file():
            return None
        with tempfile.TemporaryDirectory() as raw_tmp:
            iconset = Path(raw_tmp) / "jarvis.iconset"
            iconset.mkdir()
            image = Image.open(source).convert("RGBA")
            for size in (16, 32, 64, 128, 256, 512):
                image.resize((size, size)).save(iconset / f"icon_{size}x{size}.png")
                image.resize((size * 2, size * 2)).save(iconset / f"icon_{size}x{size}@2x.png")
            output = resources_dir / "jarvis.icns"
            result = subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(output)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            if result.returncode == 0 and output.is_file():
                return "jarvis"
    except Exception as exc:  # noqa: BLE001 - the icon is optional
        log.debug("icns build skipped: %s", exc)
    return None


def _bundle_plist() -> dict[str, object]:
    """Return metadata shared by local and future signed bundle builders."""
    return {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": _version(),
        "CFBundleVersion": _version(),
        "JarvisBundleFormatVersion": _BUNDLE_FORMAT_VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": _MIC_USAGE,
        "NSScreenCaptureUsageDescription": _SCREEN_CAPTURE_USAGE,
    }


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _write_cross_platform_fixture_bundle(bundle: Path) -> Path:
    """Create a structural fixture when a test injects a path off macOS."""
    _remove_path(bundle)
    contents = bundle / "Contents"
    macos_dir = contents / "MacOS"
    resources = contents / "Resources"
    macos_dir.mkdir(parents=True)
    resources.mkdir(parents=True)
    executable_name = "PersonalJarvis"
    executable = macos_dir / executable_name
    executable.write_bytes(b"\xcf\xfa\xed\xfeJARVIS_TEST_FIXTURE\n")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    info = _bundle_plist()
    info["CFBundleExecutable"] = executable_name
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    return bundle


def _build_py2app_alias(install_root: Path, work_dir: Path) -> Path:
    """Build a native alias launcher with the managed virtual environment."""
    entry = install_root / "jarvis" / "setup" / "macos_launcher_entry.py"
    if not entry.is_file():
        raise FileNotFoundError(f"macOS launcher entry is missing: {entry}")

    resources = work_dir / "resources"
    resources.mkdir()
    options: dict[str, object] = {
        "argv_emulation": False,
        "plist": _bundle_plist(),
    }
    icon_stem = _try_build_icns(resources)
    if icon_stem is not None:
        options["iconfile"] = str(resources / f"{icon_stem}.icns")

    setup_source = (
        "from setuptools import setup\n\n"
        "setup(\n"
        f"    name={APP_NAME!r},\n"
        f"    version={_version()!r},\n"
        f"    app={[str(entry)]!r},\n"
        f"    options={{'py2app': {options!r}}},\n"
        ")\n"
    )
    setup_path = work_dir / "setup.py"
    setup_path.write_text(setup_source, encoding="utf-8")
    result = subprocess.run(
        [str(_venv_python(install_root)), str(setup_path), "py2app", "--alias"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown py2app error").strip()
        raise RuntimeError(f"py2app alias build failed: {detail[-1200:]}")
    candidates = tuple((work_dir / "dist").glob("*.app"))
    if len(candidates) != 1:
        raise RuntimeError(f"py2app produced {len(candidates)} application bundles; expected one")
    return candidates[0]


def _sign_bundle(bundle: Path) -> None:
    result = subprocess.run(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown codesign error").strip()
        raise RuntimeError(f"ad-hoc code signing failed: {detail[-1200:]}")
    issue = _codesign_issue(bundle)
    if issue is not None:
        raise RuntimeError(
            f"the generated macOS bundle failed code-signature verification: {issue[-1200:]}"
        )


def _runtime_identity_valid(bundle: Path, *, install_root: Path) -> bool:
    """Verify native identity and imports against the managed checkout."""
    if sys.platform != "darwin":
        return True
    descriptor, raw_probe = tempfile.mkstemp(prefix="jarvis-bundle-probe-", suffix=".json")
    os.close(descriptor)
    probe = Path(raw_probe)
    probe.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            macos_launch_services_command(
                bundle,
                wait_for_exit=True,
                new_instance=True,
                arguments=("--jarvis-identity-probe", str(probe)),
            ),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        if result.returncode != 0 or not probe.is_file():
            return False
        payload = json.loads(probe.read_text(encoding="utf-8"))
        expected_bundle = bundle.resolve()
        reported_bundle = Path(str(payload.get("bundle_path", ""))).resolve()
        executable = Path(str(payload.get("executable", ""))).resolve()
        launcher_file = Path(str(payload.get("launcher_file", ""))).resolve()
        reported_install_root = Path(str(payload.get("install_root", ""))).resolve()
        executable_root = expected_bundle / "Contents" / "MacOS"
        expected_install_root = install_root.resolve()
        expected_launcher_root = expected_install_root / "jarvis" / "ui" / "web"
        return (
            payload.get("bundle_id") == BUNDLE_ID
            and reported_bundle == expected_bundle
            and executable.is_relative_to(executable_root)
            and launcher_file.is_file()
            and launcher_file.is_relative_to(expected_launcher_root)
            and reported_install_root == expected_install_root
            and payload.get("python_version")
            == f"{sys.version_info.major}.{sys.version_info.minor}"
            and payload.get("machine") == platform.machine()
        )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return False
    finally:
        probe.unlink(missing_ok=True)


def _current_process_identity_valid(bundle: Path, *, install_root: Path) -> bool:
    """Recognize the already-running canonical app without spawning a probe."""
    if sys.platform != "darwin":
        return False
    try:
        from Foundation import NSBundle  # type: ignore[import-not-found]

        import jarvis.ui.web.launcher as launcher

        current = NSBundle.mainBundle()
        current_bundle = Path(str(current.bundlePath() or "")).resolve()
        launcher_file = Path(str(launcher.__file__ or "")).resolve()
        expected_root = install_root.resolve()
        return (
            str(current.bundleIdentifier() or "") == BUNDLE_ID
            and current_bundle == bundle.resolve()
            and launcher_file.is_file()
            and launcher_file.is_relative_to(expected_root / "jarvis" / "ui" / "web")
        )
    except (ImportError, OSError, TypeError, ValueError):
        return False


def _install_native_bundle(install_root: Path, bundle: Path) -> Path:
    """Build beside the destination and replace it atomically with rollback."""
    parent = bundle.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".jarvis-py2app-", dir=parent) as raw_work:
        work = Path(raw_work)
        built = _build_py2app_alias(install_root, work)
        _sign_bundle(built)
        previous = work / "previous.app"
        if bundle.exists() or bundle.is_symlink():
            bundle.rename(previous)
        try:
            built.rename(bundle)
            if not macos_app_bundle_is_launchable(bundle):
                raise RuntimeError("the installed macOS application bundle is invalid")
            if not _runtime_identity_valid(bundle, install_root=install_root):
                raise RuntimeError("the installed app reported an unexpected runtime identity")
        except Exception:
            _remove_path(bundle)
            if previous.exists() or previous.is_symlink():
                previous.rename(bundle)
            raise
    return bundle


def ensure_macos_app_bundle(
    *,
    install_dir: Path | None = None,
    applications_dir: Path | None = None,
) -> Path | None:
    """Ensure ``~/Applications/Personal Jarvis.app`` has a stable identity.

    A valid existing bundle is preserved byte-for-byte so normal source
    updates cannot churn its local TCC identity. Off macOS, this is a no-op
    unless a caller explicitly injects an applications directory for tests.
    """
    if applications_dir is None:
        if sys.platform != "darwin":
            log.info("App bundle skipped: only macOS uses .app bundles.")
            return None
        applications_dir = Path.home() / "Applications"
    try:
        install_root = (install_dir or _default_install_dir()).resolve()
        bundle = macos_app_bundle_path(applications_dir=applications_dir)
        if macos_app_bundle_is_launchable(bundle):
            if _current_process_identity_valid(
                bundle, install_root=install_root
            ) or _runtime_identity_valid(bundle, install_root=install_root):
                return bundle
            log.warning(
                "Existing macOS bundle failed its runtime/import probe; rebuilding: %s",
                bundle,
            )
        if sys.platform != "darwin":
            return _write_cross_platform_fixture_bundle(bundle)
        installed = _install_native_bundle(install_root, bundle)
        log.info("Native macOS app bundle installed: %s", installed)
        return installed
    except Exception as exc:  # noqa: BLE001 - installer consumes the None result
        log.warning("macOS app bundle could not be written: %s", exc)
        return None


def remove_macos_app_bundle(*, applications_dir: Path | None = None) -> bool:
    """Delete the bundle on uninstall and report whether it is gone."""
    if applications_dir is None:
        if sys.platform != "darwin":
            return True
        applications_dir = Path.home() / "Applications"
    bundle = macos_app_bundle_path(applications_dir=applications_dir)
    if not bundle.exists() and not bundle.is_symlink():
        return True
    try:
        _remove_path(bundle)
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
    "macos_app_bundle_is_launchable",
    "macos_app_bundle_path",
    "macos_launch_services_command",
    "remove_macos_app_bundle",
]
