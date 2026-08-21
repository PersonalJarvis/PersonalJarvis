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
    # Real clang/codesign/LaunchServices coverage lives in macOS CI.
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
    assert "dictate" in info["NSAppleEventsUsageDescription"].lower()
    executable = bundle / "Contents" / "MacOS" / executable_name
    assert executable.read_bytes()[:4] == b"\xcf\xfa\xed\xfe"
    if os.name != "nt":
        assert executable.stat().st_mode & stat.S_IXUSR
    assert macos_app_bundle_is_launchable(bundle) is True


def test_rerun_preserves_existing_bundle_byte_for_byte(tmp_path: Path, monkeypatch) -> None:
    first = _build(tmp_path, monkeypatch)
    executable_name = plistlib.loads((first / "Contents" / "Info.plist").read_bytes())[
        "CFBundleExecutable"
    ]
    executable = first / "Contents" / "MacOS" / executable_name
    marker = executable.read_bytes()
    second = ensure_macos_app_bundle(
        install_dir=tmp_path / "install",
        applications_dir=tmp_path / "Applications",
    )
    assert second == first
    assert executable.read_bytes() == marker


def test_failed_runtime_probe_rebuilds_instead_of_preserving(tmp_path: Path, monkeypatch) -> None:
    import jarvis.setup.macos_app_bundle as mab

    bundle = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "_codesign_issue", lambda _bundle: None)
    monkeypatch.setattr(
        mab,
        "_running_managed_bundle",
        lambda *, install_root, diagnostics=None: None,
    )
    monkeypatch.setattr(
        mab,
        "_runtime_identity_valid",
        lambda _bundle, *, install_root, diagnostics=None: False,
    )
    rebuilt: list[tuple[Path, Path]] = []

    def _rebuild(install_root: Path, destination: Path) -> Path:
        rebuilt.append((install_root, destination))
        return destination

    monkeypatch.setattr(mab, "_install_native_bundle", _rebuild)
    install_root = tmp_path / "install"

    assert (
        ensure_macos_app_bundle(
            install_dir=install_root,
            applications_dir=tmp_path / "Applications",
        )
        == bundle
    )
    assert rebuilt == [(install_root.resolve(), bundle)]


def test_running_canonical_app_skips_second_launchservices_probe(
    tmp_path: Path, monkeypatch
) -> None:
    import jarvis.setup.macos_app_bundle as mab

    bundle = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "_codesign_issue", lambda _bundle: None)
    monkeypatch.setattr(
        mab,
        "_running_managed_bundle",
        lambda *, install_root, diagnostics=None: bundle,
    )
    monkeypatch.setattr(
        mab,
        "_runtime_identity_valid",
        lambda *_args, **_kwargs: pytest.fail("must not spawn a second app probe"),
    )

    assert (
        ensure_macos_app_bundle(
            install_dir=tmp_path / "install",
            applications_dir=tmp_path / "Applications",
        )
        == bundle
    )


def test_runtime_identity_probe_diagnoses_open_failure(
    tmp_path: Path, monkeypatch
) -> None:
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(
        mab.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=3,
            stdout="",
            stderr="kLSNoExecutableErr: the app cannot be launched\n",
        ),
    )
    diagnostics: list[str] = []

    assert mab._runtime_identity_valid(
        tmp_path / APP_DIR_NAME,
        install_root=tmp_path,
        diagnostics=diagnostics,
    ) is False

    joined = "\n".join(diagnostics)
    assert "open returncode 3" in joined
    assert "kLSNoExecutableErr" in joined


def test_runtime_identity_probe_diagnoses_missing_probe_file(
    tmp_path: Path, monkeypatch
) -> None:
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(
        mab.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    diagnostics: list[str] = []

    assert mab._runtime_identity_valid(
        tmp_path / APP_DIR_NAME,
        install_root=tmp_path,
        diagnostics=diagnostics,
    ) is False

    joined = "\n".join(diagnostics)
    assert "open returncode 0" in joined
    assert "probe file was not written" in joined


def test_ensure_bundle_records_last_error_reason(tmp_path: Path, monkeypatch) -> None:
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "macos_app_bundle_is_launchable", lambda _bundle: False)

    def _explode(_install_root: Path, _bundle: Path) -> Path:
        raise RuntimeError("stub launcher compilation failed: no cc")

    monkeypatch.setattr(mab, "_install_native_bundle", _explode)

    assert ensure_macos_app_bundle(
        install_dir=tmp_path / "install",
        applications_dir=tmp_path / "Applications",
    ) is None
    assert mab.last_error() == "RuntimeError: stub launcher compilation failed: no cc"


def test_noop_off_darwin_without_override(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert ensure_macos_app_bundle() is None


def test_bundle_path_and_launchservices_command(tmp_path: Path, monkeypatch) -> None:
    bundle = _build(tmp_path, monkeypatch)
    probe = str(tmp_path / "probe.json")
    assert macos_app_bundle_path(applications_dir=tmp_path / "Applications") == bundle
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


def test_codesign_verify_never_uses_strict_or_deep(tmp_path: Path, monkeypatch) -> None:
    """The alias bundle symlinks OUTSIDE itself by design, and strict
    validation rejects exactly that ("invalid destination for symbolic
    link") — it failed on every freshly built bundle on real macOS CI.
    Local verification must stay a plain identity check."""
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "darwin")
    seen: list[list[str]] = []

    def _fake_run(argv, **_kwargs):
        seen.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mab.subprocess, "run", _fake_run)
    assert mab._codesign_issue(tmp_path / APP_DIR_NAME) is None
    assert len(seen) == 1
    assert seen[0][:2] == ["/usr/bin/codesign", "--verify"]
    assert "--strict" not in seen[0]
    assert "--deep" not in seen[0]


def test_sign_bundle_reports_the_codesign_verify_detail(tmp_path: Path, monkeypatch) -> None:
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "darwin")

    def _fake_run(argv, **_kwargs):
        if "--verify" in argv:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="invalid destination for symbolic link in bundle",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mab.subprocess, "run", _fake_run)
    with pytest.raises(RuntimeError, match="invalid destination for symbolic link"):
        mab._sign_bundle(tmp_path / APP_DIR_NAME)


def test_resolve_runtime_dylib_standalone_layout(tmp_path: Path) -> None:
    import jarvis.setup.macos_app_bundle as mab

    prefix = tmp_path / "cpython-3.12.13-macos-aarch64-none"
    (prefix / "lib").mkdir(parents=True)
    dylib = prefix / "lib" / "libpython3.12.dylib"
    dylib.write_bytes(b"")
    info = {
        "base_prefix": str(prefix),
        "LIBDIR": str(prefix / "lib"),
        "LDLIBRARY": "libpython3.12.dylib",
        "PYTHONFRAMEWORKPREFIX": "",
    }
    assert mab._resolve_runtime_dylib(info) == dylib


def test_resolve_runtime_dylib_prefers_unversioned_uv_sibling(tmp_path: Path) -> None:
    import jarvis.setup.macos_app_bundle as mab

    versioned = tmp_path / "cpython-3.12.13-macos-aarch64-none"
    unversioned = tmp_path / "cpython-3.12-macos-aarch64-none"
    for prefix in (versioned, unversioned):
        (prefix / "lib").mkdir(parents=True)
        (prefix / "lib" / "libpython3.12.dylib").write_bytes(b"")
    info = {
        "base_prefix": str(versioned),
        "LIBDIR": str(versioned / "lib"),
        "LDLIBRARY": "libpython3.12.dylib",
        "PYTHONFRAMEWORKPREFIX": "",
    }
    assert mab._resolve_runtime_dylib(info) == (
        unversioned / "lib" / "libpython3.12.dylib"
    )


def test_resolve_runtime_dylib_framework_layout(tmp_path: Path) -> None:
    import jarvis.setup.macos_app_bundle as mab

    framework_prefix = tmp_path / "Library" / "Frameworks"
    runtime = framework_prefix / "Python.framework" / "Versions" / "3.13"
    runtime.mkdir(parents=True)
    dylib = runtime / "Python"
    dylib.write_bytes(b"")
    info = {
        "base_prefix": str(runtime),
        "LIBDIR": str(runtime / "lib"),
        "LDLIBRARY": "Python.framework/Versions/3.13/Python",
        "PYTHONFRAMEWORKPREFIX": str(framework_prefix),
    }
    assert mab._resolve_runtime_dylib(info) == dylib


def test_resolve_runtime_dylib_missing_names_every_candidate(tmp_path: Path) -> None:
    import jarvis.setup.macos_app_bundle as mab

    info = {
        "base_prefix": str(tmp_path / "prefix"),
        "LIBDIR": str(tmp_path / "lib"),
        "LDLIBRARY": "libpython3.12.dylib",
        "PYTHONFRAMEWORKPREFIX": "",
    }
    with pytest.raises(RuntimeError) as excinfo:
        mab._resolve_runtime_dylib(info)
    message = str(excinfo.value)
    assert str(tmp_path / "lib" / "libpython3.12.dylib") in message
    assert str(tmp_path / "prefix" / "lib" / "libpython3.12.dylib") in message


def _native_build_fixture(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """Prepare a fake install root, runtime layout, and clang subprocess."""
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "_try_build_icns", lambda _resources: None)

    install_root = tmp_path / "install"
    entry = install_root / "jarvis" / "setup" / "macos_launcher_entry.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    venv_python = install_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    venv_python.chmod(0o755)

    runtime = tmp_path / "runtime"
    (runtime / "lib").mkdir(parents=True)
    dylib = runtime / "lib" / "libpython3.12.dylib"
    dylib.write_bytes(b"")
    include_dir = runtime / "include" / "python3.12"
    include_dir.mkdir(parents=True)
    info = {
        "base_prefix": str(runtime),
        "include": str(include_dir),
        "PYTHONFRAMEWORK": "",
        "LDLIBRARY": "libpython3.12.dylib",
        "LIBDIR": str(runtime / "lib"),
        "PYTHONFRAMEWORKPREFIX": "",
        "machine": "arm64",
    }
    monkeypatch.setattr(mab, "_runtime_link_info", lambda _python: info)

    commands: list[list[str]] = []

    def _fake_clang(command, **_kwargs):
        commands.append(list(command))
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 12)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mab.subprocess, "run", _fake_clang)
    return SimpleNamespace(
        mab=mab,
        install_root=install_root,
        entry=entry,
        venv_python=venv_python,
        dylib=dylib,
        include_dir=include_dir,
        commands=commands,
    )


def test_build_native_bundle_layout_and_clang_invocation(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _native_build_fixture(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()

    bundle = fixture.mab._build_native_bundle(fixture.install_root, work)

    assert bundle == work / APP_DIR_NAME
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "PersonalJarvis"
    assert info["CFBundleIdentifier"] == "com.personal-jarvis.desktop"
    from jarvis.setup.macos_app_bundle import _BUNDLE_FORMAT_VERSION

    assert info["JarvisBundleFormatVersion"] == _BUNDLE_FORMAT_VERSION
    assert "dictate" in info["NSAppleEventsUsageDescription"].lower()
    executable = bundle / "Contents" / "MacOS" / "PersonalJarvis"
    assert executable.read_bytes()[:4] == b"\xcf\xfa\xed\xfe"
    if os.name != "nt":
        assert executable.stat().st_mode & stat.S_IXUSR
    (command,) = fixture.commands
    assert command[:2] == ["/usr/bin/xcrun", "clang"]
    assert command[command.index("-arch") + 1] == "arm64"
    assert command[command.index("-I") + 1] == str(fixture.include_dir)
    assert str(fixture.dylib) in command
    assert f"-Wl,-rpath,{fixture.dylib.parent}" in command
    assert f'-DJARVIS_VENV_PYTHON="{fixture.venv_python.resolve()}"' in command
    assert f'-DJARVIS_ENTRY_SCRIPT="{fixture.entry}"' in command
    assert command[command.index("-o") + 1] == str(executable)


def test_missing_clang_surfaces_xcode_hint_in_last_error(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _native_build_fixture(tmp_path, monkeypatch)
    mab = fixture.mab

    def _no_clang(*_args, **_kwargs):
        raise FileNotFoundError("/usr/bin/xcrun")

    monkeypatch.setattr(mab.subprocess, "run", _no_clang)

    with pytest.raises(RuntimeError, match="xcode-select --install"):
        mab._compile_stub(
            tmp_path / "stub.c",
            fixture.dylib,
            fixture.include_dir,
            "arm64",
            [],
            tmp_path / "out",
        )

    assert ensure_macos_app_bundle(
        install_dir=fixture.install_root,
        applications_dir=tmp_path / "Applications",
    ) is None
    assert "xcode-select --install" in (mab.last_error() or "")


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
        "executable": ("/Users/test/Applications/Personal Jarvis.app/Contents/MacOS/launcher"),
        "launcher_file": str(
            Path(__file__).resolve().parents[3] / "jarvis" / "ui" / "web" / "launcher.py"
        ),
        "install_root": str(Path(__file__).resolve().parents[3]),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "machine": platform.machine(),
    }


def test_icns_iconset_members_use_only_apple_valid_names(
    tmp_path: Path, monkeypatch
) -> None:
    """iconutil accepts exactly icon_{16,32,128,256,512}x*[@2x].png — one
    out-of-grammar member (icon_64x64.png) makes it reject the whole iconset,
    so the app silently shipped without an icon."""
    pytest.importorskip("PIL")
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "darwin")
    captured: list[str] = []

    def _fake_iconutil(command, **_kwargs):
        iconset = Path(command[3])
        captured.extend(sorted(entry.name for entry in iconset.glob("*")))
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"icns")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mab.subprocess, "run", _fake_iconutil)
    resources = tmp_path / "Resources"
    resources.mkdir()

    assert mab._try_build_icns(resources) == "jarvis"

    valid = {
        f"icon_{size}x{size}{scale}.png"
        for size in (16, 32, 128, 256, 512)
        for scale in ("", "@2x")
    }
    assert captured, "no iconset members were generated"
    assert set(captured) <= valid


def test_tcc_reset_needed_only_on_a_real_signature_change() -> None:
    from jarvis.setup.macos_app_bundle import _tcc_reset_needed

    # A rebuild that produced a NEW code signature orphans the recorded TCC
    # rows (BUG-083) — including the fresh-install case with no prior bundle.
    assert _tcc_reset_needed("aaaa", "bbbb") is True
    assert _tcc_reset_needed(None, "bbbb") is True
    # Identical signature or an unreadable new one must never trigger a reset.
    assert _tcc_reset_needed("aaaa", "aaaa") is False
    assert _tcc_reset_needed("aaaa", None) is False
    assert _tcc_reset_needed(None, None) is False


def test_reset_stale_tcc_grants_scopes_every_service_to_our_bundle_id() -> None:
    import jarvis.setup.macos_app_bundle as mab

    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    mab._reset_stale_tcc_grants(runner=runner)

    assert commands == [
        ["/usr/bin/tccutil", "reset", service, mab.BUNDLE_ID]
        for service in mab._TCC_SERVICES
    ]
    assert set(mab._TCC_SERVICES) == {
        "Microphone",
        "ScreenCapture",
        "Accessibility",
        "ListenEvent",
        "PostEvent",
    }


def test_reset_stale_tcc_grants_survives_a_failing_tccutil() -> None:
    import jarvis.setup.macos_app_bundle as mab

    attempts: list[str] = []

    def runner(command, **_kwargs):
        attempts.append(command[2])
        if command[2] == "ScreenCapture":
            raise OSError("tccutil missing")
        return SimpleNamespace(returncode=1, stdout="", stderr="denied")

    # Best-effort contract: one broken service never aborts the sweep.
    mab._reset_stale_tcc_grants(runner=runner)

    assert attempts == list(mab._TCC_SERVICES)


def test_a_recurring_rebuild_wipes_the_grants_only_once(tmp_path: Path, monkeypatch) -> None:
    """BUG-159: a rebuild loop must not re-ask for every permission each start.

    The reset is the right move once. Repeating it while the user has not yet
    answered the first one destroys the grants they just re-gave — which is
    exactly what "I allow everything, restart, and it asks again" looks like.
    """
    import jarvis.platform.permissions as permissions
    import jarvis.setup.macos_app_bundle as mab

    marker = tmp_path / "macos-tcc-reset.json"
    monkeypatch.setattr(permissions, "identity_reset_marker_path", lambda: marker)
    hashes = iter(["cdhash-two", "cdhash-three"])
    monkeypatch.setattr(mab, "_bundle_cdhash", lambda _bundle: next(hashes))
    sweeps: list[int] = []
    monkeypatch.setattr(mab, "_reset_stale_tcc_grants", lambda: sweeps.append(1))

    bundle = tmp_path / APP_DIR_NAME
    mab._reset_or_explain(bundle, "cdhash-one")
    assert sweeps == [1]
    assert marker.is_file()

    # Second rebuild, first reset still unanswered: explain, never wipe again.
    mab._reset_or_explain(bundle, "cdhash-two")

    assert sweeps == [1]
    assert marker.is_file()


# --- BUG-161: a rebuild loop that wiped the grants on every single start


def test_a_rebuild_that_reproduces_the_same_app_is_not_repeated(
    tmp_path: Path, monkeypatch
) -> None:
    """The loop that made the app forget every permission on every start.

    The identity probe can keep refusing a bundle for a reason no rebuild
    touches. Building the identical app again changes only its ad-hoc
    signature, and macOS answers a changed signature by orphaning every TCC
    grant — so the user allows everything, restarts, and is asked again.
    """
    import jarvis.setup.macos_app_bundle as mab

    bundle = _build(tmp_path, monkeypatch)
    marker = tmp_path / "macos-bundle-rebuild.json"
    monkeypatch.setattr(mab, "_rebuild_marker_path", lambda: marker)
    monkeypatch.setattr(mab, "_bundle_cdhash", lambda _bundle: "cdhash-one")
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "_codesign_issue", lambda _bundle: None)
    monkeypatch.setattr(mab, "_running_managed_bundle", lambda *, install_root, **_kw: None)
    monkeypatch.setattr(
        mab,
        "_runtime_identity_valid",
        lambda _bundle, *, install_root, diagnostics=None: False,
    )
    rebuilds: list[Path] = []

    def _rebuild(install_root: Path, destination: Path) -> Path:
        rebuilds.append(destination)
        return destination

    monkeypatch.setattr(mab, "_install_native_bundle", _rebuild)
    install_dir = tmp_path / "install"
    applications = tmp_path / "Applications"

    first = ensure_macos_app_bundle(install_dir=install_dir, applications_dir=applications)
    second = ensure_macos_app_bundle(install_dir=install_dir, applications_dir=applications)

    assert first == second == bundle
    assert rebuilds == [bundle]


def test_a_changed_interpreter_still_earns_a_fresh_rebuild(tmp_path: Path, monkeypatch) -> None:
    """The guard suppresses a repeat, never a build that could come out different."""
    import jarvis.setup.macos_app_bundle as mab

    bundle = _build(tmp_path, monkeypatch)
    marker = tmp_path / "macos-bundle-rebuild.json"
    marker.write_text(
        json.dumps(
            {
                "cdhash": "cdhash-one",
                "install_root": str((tmp_path / "install").resolve()),
                "python": "3.11",
                "machine": platform.machine(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mab, "_rebuild_marker_path", lambda: marker)
    monkeypatch.setattr(mab, "_bundle_cdhash", lambda _bundle: "cdhash-one")
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "_codesign_issue", lambda _bundle: None)
    monkeypatch.setattr(mab, "_running_managed_bundle", lambda *, install_root, **_kw: None)
    monkeypatch.setattr(
        mab,
        "_runtime_identity_valid",
        lambda _bundle, *, install_root, diagnostics=None: False,
    )
    rebuilds: list[Path] = []
    monkeypatch.setattr(
        mab,
        "_install_native_bundle",
        lambda install_root, destination: (rebuilds.append(destination), destination)[1],
    )

    # The recorded note carries Python 3.11; this interpreter is a different
    # minor version, so a fresh build genuinely can produce a different app.
    assert f"{sys.version_info.major}.{sys.version_info.minor}" != "3.11"
    ensure_macos_app_bundle(
        install_dir=tmp_path / "install",
        applications_dir=tmp_path / "Applications",
    )

    assert rebuilds == [bundle]


def test_a_broken_bundle_is_always_rebuilt_however_often_it_recurs(
    tmp_path: Path, monkeypatch
) -> None:
    """A bundle that cannot launch is a real repair — the loop guard must not block it."""
    import jarvis.setup.macos_app_bundle as mab

    bundle = _build(tmp_path, monkeypatch)
    marker = tmp_path / "macos-bundle-rebuild.json"
    monkeypatch.setattr(mab, "_rebuild_marker_path", lambda: marker)
    monkeypatch.setattr(mab, "_bundle_cdhash", lambda _bundle: "cdhash-one")
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "_launchable_issue", lambda _candidate: "executable is missing")
    monkeypatch.setattr(mab, "_running_managed_bundle", lambda *, install_root, **_kw: None)
    rebuilds: list[Path] = []
    monkeypatch.setattr(
        mab,
        "_install_native_bundle",
        lambda install_root, destination: (rebuilds.append(destination), destination)[1],
    )
    install_dir = tmp_path / "install"
    applications = tmp_path / "Applications"

    ensure_macos_app_bundle(install_dir=install_dir, applications_dir=applications)
    ensure_macos_app_bundle(install_dir=install_dir, applications_dir=applications)

    assert rebuilds == [bundle, bundle]


def test_the_running_app_is_accepted_wherever_the_user_keeps_it(
    tmp_path: Path, monkeypatch
) -> None:
    """A copy in /Applications must never trigger a rebuild of the ~ slot.

    Both copies share our bundle id, so the rebuild's ``tccutil reset`` strips
    the permissions of the app that is running at that moment (BUG-161).
    """
    import jarvis.setup.macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "darwin")
    shared_copy = tmp_path / "SharedApplications" / APP_DIR_NAME
    monkeypatch.setattr(
        mab, "_running_managed_bundle", lambda *, install_root, **_kw: shared_copy
    )
    monkeypatch.setattr(
        mab,
        "_install_native_bundle",
        lambda *_args: pytest.fail("a running managed app must never be rebuilt"),
    )
    registered: list[Path] = []
    monkeypatch.setattr(mab, "register_with_launch_services", registered.append)

    result = ensure_macos_app_bundle(
        install_dir=tmp_path / "install",
        applications_dir=tmp_path / "Applications",
    )

    assert result == shared_copy
    assert registered == [shared_copy]


def test_the_running_bundle_probe_ignores_the_install_location(monkeypatch) -> None:
    """Only the bundle id and the managed checkout decide — never the path."""
    import jarvis.setup.macos_app_bundle as mab
    import jarvis.ui.web.launcher as launcher

    install_root = Path(launcher.__file__).resolve().parents[3]
    running = Path("/Applications") / APP_DIR_NAME
    foundation = ModuleType("Foundation")
    foundation.NSBundle = SimpleNamespace(  # type: ignore[attr-defined]
        mainBundle=lambda: SimpleNamespace(
            bundlePath=lambda: str(running),
            bundleIdentifier=lambda: mab.BUNDLE_ID,
        )
    )
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setattr(mab.sys, "platform", "darwin")

    assert mab._running_managed_bundle(install_root=install_root) == running.resolve()


def test_a_foreign_bundle_id_is_never_taken_for_the_managed_app(monkeypatch) -> None:
    import jarvis.setup.macos_app_bundle as mab

    foundation = ModuleType("Foundation")
    foundation.NSBundle = SimpleNamespace(  # type: ignore[attr-defined]
        mainBundle=lambda: SimpleNamespace(
            bundlePath=lambda: "/Applications/Terminal.app",
            bundleIdentifier=lambda: "com.apple.Terminal",
        )
    )
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    diagnostics: list[str] = []

    assert (
        mab._running_managed_bundle(install_root=Path.cwd(), diagnostics=diagnostics) is None
    )
    assert any("bundle id" in reason for reason in diagnostics)
