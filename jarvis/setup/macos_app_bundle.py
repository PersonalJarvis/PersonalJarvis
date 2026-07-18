"""Build the managed-install macOS app identity (BUG-060).

The bundle's main executable must remain a Mach-O process. A shell launcher
that ``exec``s an external virtual-environment Python loses its ``NSBundle``
identity and causes TCC grants to attach to Python or a terminal instead of
Personal Jarvis. The managed installer therefore compiles the in-repo stub
launcher (``macos_stub_launcher.c``): it embeds the active Python runtime in
the app process and runs the managed entry script, so it works with framework
AND non-framework interpreters (for example uv-managed standalone builds)
while the source and dependencies remain in the managed checkout.

The locally generated app is ad-hoc signed and is preserved byte-for-byte on
ordinary source updates so its local TCC identity does not churn. When a
rebuild is unavoidable (format bump, broken bundle), the ad-hoc code signature
changes and macOS orphans every previously recorded TCC grant — the old rows
then read as silently DENIED for the "new" app and macOS never prompts again
(BUG-083). After such a signature change this module therefore resets the
stale TCC rows for our bundle id via ``tccutil`` so the app can prompt fresh.
Public binary distribution still requires the separate Developer-ID signing
and notarization pipeline; this module never claims an ad-hoc app is a
notarized artifact.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import plistlib
import re
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
# 2: stub launcher sets only LC_CTYPE (BUG-079 — LC_ALL leaked a de_DE
# LC_NUMERIC into native libs; libvosk then emitted malformed JSON). Bumping
# forces existing bundles through a rebuild on the next ensure pass.
# 3: one forced rebuild so the new signature-change TCC reset (BUG-083) heals
# bundles whose grants were orphaned by the version-2 rebuild.
_BUNDLE_FORMAT_VERSION = 3

# TCC service names this app ever requests; reset scope is always limited to
# our own bundle id, never the whole service (BUG-083).
_TCC_SERVICES: tuple[str, ...] = (
    "Microphone",
    "ScreenCapture",
    "Accessibility",
    "ListenEvent",
    "PostEvent",
)
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

# The reason the most recent ensure_macos_app_bundle call returned None, so
# callers can surface the real failure instead of a generic warning.
_LAST_ERROR: str | None = None


def last_error() -> str | None:
    """Return why the last ensure_macos_app_bundle call failed, if it did."""
    return _LAST_ERROR


_MIC_USAGE = "Personal Jarvis listens on this microphone for your wake word and voice commands."
_SCREEN_CAPTURE_USAGE = (
    "Personal Jarvis captures the screen only when you ask it to see or control applications."
)
_APPLE_EVENTS_USAGE = (
    "Personal Jarvis lowers Music/Spotify volume while you dictate and restores it afterwards."
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


def _bundle_cdhash(bundle: Path) -> str | None:
    """Return the bundle's code-directory hash — its TCC identity — or ``None``.

    An ad-hoc signature has no certificate chain, so macOS pins TCC grants to
    the CDHash of the main executable. Two bundles with different CDHashes are
    different apps to TCC even under the same bundle id.
    """
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["/usr/bin/codesign", "--display", "--verbose=4", str(bundle)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # codesign prints the display block on stderr.
    match = re.search(r"^CDHash=([0-9a-f]+)", result.stderr or "", re.MULTILINE)
    return match.group(1) if match else None


def _tcc_reset_needed(previous_cdhash: str | None, current_cdhash: str | None) -> bool:
    """A signature change (or an unknowable previous one) orphans TCC rows."""
    return current_cdhash is not None and previous_cdhash != current_cdhash


def _reset_stale_tcc_grants(runner=subprocess.run) -> None:
    """Drop this app's orphaned TCC rows so macOS can prompt fresh (BUG-083).

    After a signature change the recorded grants belong to the OLD CDHash: the
    rebuilt app reads them as DENIED and macOS suppresses every further prompt,
    so permissions appear "auto-rejected" without the user ever being asked.
    Resetting is scoped to our bundle id, is best-effort per service, and never
    raises — a failed reset leaves behavior no worse than before. The caller
    (`_install_native_bundle`) only runs on macOS; the injectable runner keeps
    this unit-testable on every OS.
    """
    for service in _TCC_SERVICES:
        try:
            result = runner(
                ["/usr/bin/tccutil", "reset", service, BUNDLE_ID],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("TCC reset for %s did not run: %s", service, exc)
            continue
        if result.returncode == 0:
            log.info("Reset stale TCC rows for %s (%s).", service, BUNDLE_ID)
        else:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            log.warning("TCC reset for %s failed: %s", service, detail[-300:])


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
        "NSAppleEventsUsageDescription": _APPLE_EVENTS_USAGE,
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


_LINK_INFO_SCRIPT = (
    "import json, platform, sys, sysconfig\n"
    "data = {name: sysconfig.get_config_var(name) or '' for name in "
    "('PYTHONFRAMEWORK', 'LDLIBRARY', 'LIBDIR', 'PYTHONFRAMEWORKPREFIX')}\n"
    "data['base_prefix'] = sys.base_prefix\n"
    "data['include'] = sysconfig.get_paths()['include']\n"
    "data['machine'] = platform.machine()\n"
    "print(json.dumps(data))\n"
)


def _runtime_link_info(venv_python: Path) -> dict:
    """Ask the managed venv interpreter how its runtime library is laid out."""
    try:
        result = subprocess.run(
            [str(venv_python), "-B", "-c", _LINK_INFO_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not query the venv interpreter {venv_python}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown interpreter error").strip()
        raise RuntimeError(f"venv runtime introspection failed: {detail[-1200:]}")
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"venv runtime introspection returned invalid JSON: {exc}") from exc


# A fully versioned uv/python-build-standalone prefix directory name, for
# example ``cpython-3.12.13-macos-aarch64-none``. Group "head" keeps the
# implementation and major.minor; "tail" keeps the platform suffix.
_VERSIONED_PREFIX_RE = re.compile(r"^(?P<head>[^-]+-\d+\.\d+)\.\d+(?P<tail>-.*)?$")


def _prefer_unversioned_runtime(dylib: Path, base_prefix: Path | None) -> Path:
    """Prefer an unversioned sibling runtime dir when one holds the dylib.

    uv keeps a ``cpython-3.12-...`` alias beside the fully versioned
    ``cpython-3.12.13-...`` install. Linking (and rpath-ing) against the
    alias keeps the bundle working across uv patch upgrades that replace the
    versioned directory.
    """
    if base_prefix is None:
        return dylib
    match = _VERSIONED_PREFIX_RE.match(base_prefix.name)
    if match is None:
        return dylib
    try:
        relative = dylib.relative_to(base_prefix)
    except ValueError:
        return dylib
    candidate = base_prefix.with_name(match.group("head") + (match.group("tail") or ""))
    sibling = candidate / relative
    try:
        if sibling.is_file():
            return sibling
    except OSError:
        return dylib
    return dylib


def _resolve_runtime_dylib(info: dict) -> Path:
    """Locate the runtime libpython/framework dylib to link the stub against."""
    ldlibrary = str(info.get("LDLIBRARY") or "")
    if not ldlibrary:
        raise RuntimeError("the venv interpreter reported no LDLIBRARY config variable")
    roots = [
        Path(str(root)) for root in (info.get("LIBDIR"), info.get("PYTHONFRAMEWORKPREFIX")) if root
    ]
    base_prefix = str(info.get("base_prefix") or "")
    if base_prefix:
        roots.append(Path(base_prefix) / "lib")
    candidates = [root / ldlibrary for root in roots]
    for candidate in candidates:
        if candidate.is_file():
            return _prefer_unversioned_runtime(
                candidate, Path(base_prefix) if base_prefix else None
            )
    listing = ", ".join(str(candidate) for candidate in candidates) or "(none)"
    raise RuntimeError(f"no linkable Python runtime library found; tried: {listing}")


def _compile_stub(
    stub_c: Path,
    dylib: Path,
    include_dir: Path,
    machine: str,
    defines: list[str],
    out_path: Path,
) -> None:
    """Compile the in-repo stub launcher into the bundle's Mach-O executable."""
    command = [
        "/usr/bin/xcrun",
        "clang",
        "-O2",
        "-Wall",
        "-arch",
        machine,
        "-I",
        str(include_dir),
        str(stub_c),
        str(dylib),
        f"-Wl,-rpath,{dylib.parent}",
        *defines,
        "-o",
        str(out_path),
    ]
    hint = "install the Xcode Command Line Tools (xcode-select --install)"
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"clang is not available ({exc}); {hint}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"stub launcher compilation failed: {exc}; {hint}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown clang error").strip()
        raise RuntimeError(f"stub launcher compilation failed: {detail[-1200:]}; {hint}")


def _build_native_bundle(install_root: Path, work_dir: Path) -> Path:
    """Compile the stub launcher and lay out the app bundle in ``work_dir``."""
    entry = install_root / "jarvis" / "setup" / "macos_launcher_entry.py"
    if not entry.is_file():
        raise FileNotFoundError(f"macOS launcher entry is missing: {entry}")
    stub_c = Path(__file__).resolve().parent / "macos_stub_launcher.c"
    if not stub_c.is_file():
        raise FileNotFoundError(f"macOS stub launcher source is missing: {stub_c}")

    # Deliberately NOT resolve()d: the venv interpreter is a symlink chain to
    # the base runtime, and CPython's venv detection looks for pyvenv.cfg next
    # to the UNRESOLVED executable path. Resolving it would point the embedded
    # interpreter at the base install and lose the venv's site-packages.
    venv_python = _venv_python(install_root)
    info = _runtime_link_info(venv_python)
    dylib = _resolve_runtime_dylib(info)
    machine = str(info.get("machine") or "") or platform.machine()

    bundle = work_dir / APP_DIR_NAME
    macos_dir = bundle / "Contents" / "MacOS"
    resources = bundle / "Contents" / "Resources"
    macos_dir.mkdir(parents=True)
    resources.mkdir(parents=True)

    executable_name = "PersonalJarvis"
    plist = _bundle_plist()
    plist["CFBundleExecutable"] = executable_name
    icon_stem = _try_build_icns(resources)
    if icon_stem is not None:
        plist["CFBundleIconFile"] = icon_stem

    executable = macos_dir / executable_name
    # The paths become C string literals via -D macros; subprocess passes each
    # define as one argv element, so spaces in paths survive verbatim.
    defines = [
        f'-DJARVIS_VENV_PYTHON="{venv_python}"',
        f'-DJARVIS_ENTRY_SCRIPT="{entry}"',
    ]
    _compile_stub(
        stub_c,
        dylib,
        Path(str(info.get("include") or "")),
        machine,
        defines,
        executable,
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with (bundle / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(plist, stream)
    return bundle


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


def _runtime_identity_valid(
    bundle: Path,
    *,
    install_root: Path,
    diagnostics: list[str] | None = None,
) -> bool:
    """Verify native identity and imports against the managed checkout.

    When ``diagnostics`` is given, human-readable probe evidence is appended
    to it so failed checks can surface the real cause to the installer.
    """
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
        if diagnostics is not None:
            stderr_tail = (result.stderr or "").strip()[-500:]
            diagnostics.append(
                f"open returncode {result.returncode}, stderr tail: {stderr_tail or '(empty)'}"
            )
        if result.returncode != 0 or not probe.is_file():
            if diagnostics is not None and not probe.is_file():
                diagnostics.append("probe file was not written")
            return False
        payload = json.loads(probe.read_text(encoding="utf-8"))
        if diagnostics is not None:
            diagnostics.append(f"probe payload: {payload!r}")
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
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        if diagnostics is not None:
            diagnostics.append(f"identity probe failed: {type(exc).__name__}: {exc}")
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
    previous_cdhash = _bundle_cdhash(bundle) if bundle.exists() else None
    with tempfile.TemporaryDirectory(prefix=".jarvis-native-", dir=parent) as raw_work:
        work = Path(raw_work)
        built = _build_native_bundle(install_root, work)
        _sign_bundle(built)
        previous = work / "previous.app"
        if bundle.exists() or bundle.is_symlink():
            bundle.rename(previous)
        try:
            built.rename(bundle)
            if not macos_app_bundle_is_launchable(bundle):
                raise RuntimeError(
                    "the installed macOS application bundle failed the launchable "
                    f"check (native executable, metadata, or signature): {bundle}"
                )
            diagnostics: list[str] = []
            if not _runtime_identity_valid(
                bundle, install_root=install_root, diagnostics=diagnostics
            ):
                detail = "; ".join(diagnostics) or "no probe diagnostics captured"
                raise RuntimeError(
                    f"the installed app reported an unexpected runtime identity ({detail})"
                )
        except Exception:
            _remove_path(bundle)
            if previous.exists() or previous.is_symlink():
                previous.rename(bundle)
            raise
    if _tcc_reset_needed(previous_cdhash, _bundle_cdhash(bundle)):
        # The rebuild changed the app's TCC identity: every recorded grant is
        # now orphaned and would read as silently DENIED (BUG-083).
        _reset_stale_tcc_grants()
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
    global _LAST_ERROR
    _LAST_ERROR = None
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
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
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
    "last_error",
    "macos_app_bundle_is_launchable",
    "macos_app_bundle_path",
    "macos_launch_services_command",
    "remove_macos_app_bundle",
]
