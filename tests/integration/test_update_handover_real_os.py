"""The native-installer handover against the REAL operating system.

The unit tests fake the process table and the disk image tool; these do not.
Each one runs where its mechanism exists and skips honestly elsewhere:

* every OS — :class:`SubprocessCommandRunner` really spawns, really detaches,
  and hands the child the reset PyInstaller environment;
* macOS and Linux — the AppImage swap with the real ``/bin/sh`` relaunch
  waiter, which must start the new file only after the old process exited;
* macOS — a real ``.dmg`` built with ``hdiutil``, mounted, copied out of and
  detached by the real handover;
* Windows — the real Inno Setup script, compiled twice, installed, then
  upgraded over a RUNNING copy through the exact command the app sends. This
  one installs software, so it runs only when ``JARVIS_INSTALLER_E2E=1`` (the
  CI updater lane sets it). It uses its own AppId and a throwaway folder, so
  even a local run can never touch a real Personal Jarvis install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import jarvis.core.installer_update as iu
from jarvis.core.installer_update import (
    SubprocessCommandRunner,
    apply_installer,
)

REPO = Path(__file__).resolve().parents[2]


def _wait_until(condition: Callable[[], bool], *, timeout_s: float, poll_s: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(poll_s)
    return condition()


# --------------------------------------------------------------------------- #
# Every OS: the real runner
# --------------------------------------------------------------------------- #
def test_the_real_runner_reports_exit_code_and_output() -> None:
    rc, out, _err = SubprocessCommandRunner().run(
        [sys.executable, "-c", "import sys; print('ok'); sys.exit(3)"], timeout_s=60
    )
    assert rc == 3
    assert out.strip() == "ok"


def test_the_real_runner_reports_a_missing_binary() -> None:
    rc, _out, err = SubprocessCommandRunner().run(
        ["jarvis-no-such-binary-for-the-updater"], timeout_s=10
    )
    assert rc == -1
    assert "not available" in err


def test_the_real_runner_reports_a_timeout() -> None:
    rc, _out, err = SubprocessCommandRunner().run(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5
    )
    assert rc == -1
    assert "timed out" in err


def test_the_real_runner_never_waits_for_input() -> None:
    """hdiutil asks for a licence on some images; a prompt must read EOF, not hang."""
    started = time.monotonic()
    rc, out, _err = SubprocessCommandRunner().run(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout_s=30
    )
    assert rc == 0
    assert out.strip() == "''"
    assert time.monotonic() - started < 25


def test_a_detached_child_gets_a_fresh_pyinstaller_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", str(tmp_path / "old" / "PersonalJarvis"))
    record = tmp_path / "env.txt"
    script = tmp_path / "child.py"
    script.write_text(
        "import os, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(\n"
        "    os.environ.get('PYINSTALLER_RESET_ENVIRONMENT', '<unset>'), encoding='utf-8')\n",
        encoding="utf-8",
    )

    SubprocessCommandRunner().spawn_detached([sys.executable, str(script), str(record)])

    assert _wait_until(record.exists, timeout_s=30)
    assert _wait_until(lambda: record.read_text(encoding="utf-8") == "1", timeout_s=5)


# --------------------------------------------------------------------------- #
# macOS + Linux: the AppImage swap and the real relaunch waiter
# --------------------------------------------------------------------------- #
_HAS_SH = os.name == "posix" and Path("/bin/sh").exists()


@pytest.mark.skipif(not _HAS_SH, reason="the relaunch waiter is a POSIX sh script")
def test_appimage_handover_swaps_and_relaunches_after_the_old_app_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pretend to be the frozen build whose bootloader rewrote the library path.
    monkeypatch.setattr(iu, "is_frozen", lambda: True)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path / ".mount_old" / "_internal"))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib/from-the-user")

    record = tmp_path / "relaunched.txt"
    live = tmp_path / "My Apps" / "Personal Jarvis.AppImage"
    live.parent.mkdir()
    live.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    live.chmod(0o755)

    downloaded = tmp_path / "download" / "PersonalJarvis-Linux-x86_64.AppImage"
    downloaded.parent.mkdir()
    downloaded.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            printf '%s\\n%s\\n%s\\n' new "${{PYINSTALLER_RESET_ENVIRONMENT:-}}" \\
              "${{LD_LIBRARY_PATH:-}}" > '{record}'
            """
        ),
        encoding="utf-8",
    )
    downloaded.chmod(0o644)  # the download is not executable; the swap makes it so

    old_app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        message = apply_installer(
            downloaded,
            platform_name="linux",
            runner=SubprocessCommandRunner(),
            appimage_path=live,
            wait_for_pid=old_app.pid,
        )
        assert "replaced" in message
        time.sleep(0.8)
        assert not record.exists(), "the new version started while the old one still ran"
        old_app.wait(timeout=30)
    finally:
        if old_app.poll() is None:
            old_app.kill()

    assert _wait_until(
        lambda: record.exists() and len(record.read_text(encoding="utf-8").splitlines()) == 3,
        timeout_s=30,
    )
    assert record.read_text(encoding="utf-8").splitlines() == [
        "new",
        "1",
        "/usr/local/lib/from-the-user",
    ]
    assert os.access(live, os.X_OK)
    assert not (live.parent / f".{live.name}.new").exists()


# --------------------------------------------------------------------------- #
# macOS: a real disk image
# --------------------------------------------------------------------------- #
_HAS_HDIUTIL = sys.platform == "darwin" and shutil.which("hdiutil") is not None


def _make_dmg(source: Path, dmg: Path) -> None:
    """``hdiutil create`` is occasionally 'Resource busy' on CI; retry briefly."""
    last: subprocess.CompletedProcess[str] | None = None
    for _attempt in range(3):
        last = subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "Personal Jarvis",
                "-srcfolder",
                str(source),
                "-format",
                "UDZO",
                "-ov",
                str(dmg),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if last.returncode == 0:
            return
        time.sleep(2)
    pytest.fail(f"hdiutil create failed: {last.stderr if last else ''}")


@pytest.mark.skipif(not _HAS_HDIUTIL, reason="real disk images exist on macOS only")
def test_macos_handover_swaps_the_bundle_out_of_a_real_disk_image(tmp_path: Path) -> None:
    source = tmp_path / "dmg-root"
    bundle = source / "Personal Jarvis.app" / "Contents"
    (bundle / "MacOS").mkdir(parents=True)
    (bundle / "MacOS" / "PersonalJarvis").write_text("new", encoding="utf-8")
    (bundle / "Frameworks").mkdir()
    # Framework bundles are full of relative symlinks; they must survive the copy.
    os.symlink("../MacOS/PersonalJarvis", bundle / "Frameworks" / "Current")
    # Real DMGs carry a drag-to-install link next to the app.
    os.symlink("/Applications", source / "Applications")
    dmg = tmp_path / "PersonalJarvis-macOS-arm64.dmg"
    _make_dmg(source, dmg)

    app = tmp_path / "Applications" / "Personal Jarvis.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PersonalJarvis").write_text("old", encoding="utf-8")
    (app / "Contents" / "stale-from-the-old-version.txt").write_text("x", encoding="utf-8")

    message = apply_installer(
        dmg,
        platform_name="darwin",
        runner=SubprocessCommandRunner(),
        app_path=app,
        relaunch=False,
    )

    assert "replaced" in message
    assert (app / "Contents" / "MacOS" / "PersonalJarvis").read_text(encoding="utf-8") == "new"
    assert not (app / "Contents" / "stale-from-the-old-version.txt").exists()
    link = app / "Contents" / "Frameworks" / "Current"
    assert link.is_symlink()
    assert link.read_text(encoding="utf-8") == "new"
    assert not (app.parent / ".Personal Jarvis.app.old").exists()
    assert not (app.parent / ".Personal Jarvis.app.new").exists()
    # The volume was released: no mount of ours is left behind.
    info = subprocess.run(
        ["hdiutil", "info"], capture_output=True, text=True, timeout=60, check=False
    )
    assert "jarvis-dmg-" not in info.stdout


# --------------------------------------------------------------------------- #
# Windows: the real Inno Setup upgrade over a running app
# --------------------------------------------------------------------------- #
_E2E = os.environ.get("JARVIS_INSTALLER_E2E") == "1"
# NOT the product's AppId: this throwaway install must never upgrade, or be
# uninstalled as, a real Personal Jarvis.
TEST_APP_GUID = "D0C7E2A1-5F4B-4E1C-9A3D-7E6B5C4A3F21"

_FAKE_APP_SOURCE = r"""
using System;
using System.IO;
using System.Threading;

class FakeJarvis {
    static int Main(string[] args) {
        string home = AppDomain.CurrentDomain.BaseDirectory;
        if (args.Length == 2 && args[0] == "hold") {
            // The "running old app": holds its own image locked, then quits slowly.
            File.WriteAllText(Path.Combine(home, "holding.txt"), "1");
            Thread.Sleep(int.Parse(args[1]) * 1000);
            return 0;
        }
        string version = File.ReadAllText(Path.Combine(home, "version.txt")).Trim();
        File.AppendAllText(Path.Combine(home, "launches.txt"), version + Environment.NewLine);
        return 0;
    }
}
"""


def _iscc() -> Path | None:
    found = shutil.which("ISCC.exe") or shutil.which("ISCC")
    candidates = [Path(found)] if found else []
    for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(base)
        if root:
            sub = ("Programs", "Inno Setup 6") if base == "LOCALAPPDATA" else ("Inno Setup 6",)
            candidates.append(Path(root, *sub, "ISCC.exe"))
    return next((c for c in candidates if c.is_file()), None)


def _csc() -> Path | None:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    csc = Path(windir) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    return csc if csc.is_file() else None


def _uninstall_key_exists() -> bool:
    import winreg

    key = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        "\\{" + TEST_APP_GUID + "}_is1"
    )
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, key))
    except OSError:
        return False
    return True


def _build_setup(iscc: Path, fake_exe: Path, version: str, work: Path) -> Path:
    source = work / f"src-{version}"
    source.mkdir()
    shutil.copy2(fake_exe, source / "PersonalJarvis.exe")
    (source / "version.txt").write_text(version, encoding="utf-8")
    out = work / f"out-{version}"
    subprocess.run(
        [
            str(iscc),
            "/Q",
            f"/DAppVersion={version}",
            f"/DSourceDir={source}",
            f"/DOutputDir={out}",
            f"/DAppGuid={TEST_APP_GUID}",
            str(REPO / "packaging" / "windows" / "PersonalJarvis.iss"),
        ],
        check=True,
        timeout=600,
    )
    setup = out / "PersonalJarvis-Setup-x64.exe"
    assert setup.is_file()
    return setup


@pytest.mark.skipif(
    sys.platform != "win32" or not _E2E,
    reason="installs software: Windows only, opt in with JARVIS_INSTALLER_E2E=1",
)
def test_windows_installer_upgrades_a_running_app_and_brings_it_back(tmp_path: Path) -> None:
    iscc, csc = _iscc(), _csc()
    if iscc is None or csc is None:
        pytest.skip("needs Inno Setup 6 (ISCC.exe) and the .NET Framework C# compiler")

    work = tmp_path / "work"
    work.mkdir()
    (work / "FakeJarvis.cs").write_text(_FAKE_APP_SOURCE, encoding="utf-8")
    fake_exe = work / "FakeJarvis.exe"
    subprocess.run(
        [str(csc), "/nologo", "/target:winexe", f"/out:{fake_exe}", str(work / "FakeJarvis.cs")],
        check=True,
        timeout=300,
    )
    setup_v1 = _build_setup(iscc, fake_exe, "1.0.0", work)
    setup_v2 = _build_setup(iscc, fake_exe, "2.0.0", work)

    app_dir = tmp_path / "Personal Jarvis Test"
    logs = tmp_path / "log dir"  # a space: the /LOG= argument must survive quoting
    logs.mkdir()
    holder: subprocess.Popen[bytes] | None = None
    try:
        first = subprocess.run(
            [
                str(setup_v1),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/NOICONS",
                f"/DIR={app_dir}",
                "/MERGETASKS=!addtopath,!desktopicon",
                f"/LOG={logs / 'first install.log'}",
            ],
            timeout=600,
            check=False,
        )
        assert first.returncode == 0, "the first install failed"
        assert (app_dir / "version.txt").read_text(encoding="utf-8") == "1.0.0"

        # The old version is running and holds its executable locked, and it
        # needs a few seconds to quit — longer than Setup takes to start.
        holder = subprocess.Popen([str(app_dir / "PersonalJarvis.exe"), "hold", "6"])
        assert _wait_until((app_dir / "holding.txt").exists, timeout_s=30)

        installer_log = logs / "update installer.log"
        message = apply_installer(
            setup_v2,
            platform_name="win32",
            runner=SubprocessCommandRunner(),
            wait_for_pid=holder.pid,
            installer_log=installer_log,
        )
        assert "restarts by itself" in message
        holder.wait(timeout=60)

        launches = app_dir / "launches.txt"
        assert _wait_until(launches.exists, timeout_s=300, poll_s=1.0), (
            "the upgraded app never came back; installer log:\n"
            + (installer_log.read_text(errors="replace") if installer_log.exists() else "-")
        )
        assert _wait_until(
            lambda: "Log closed." in installer_log.read_text(errors="replace"),
            timeout_s=120,
            poll_s=1.0,
        )
        log_text = installer_log.read_text(errors="replace")
        assert (app_dir / "version.txt").read_text(encoding="utf-8") == "2.0.0"
        # Came back exactly once, as the NEW version.
        assert launches.read_text(encoding="utf-8").split() == ["2.0.0"]
        assert f"for process {holder.pid} to exit" in log_text
    finally:
        if holder is not None and holder.poll() is None:
            holder.kill()
        uninstaller = app_dir / "unins000.exe"
        if uninstaller.exists():
            subprocess.run(
                [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                timeout=300,
                check=False,
            )
            # The uninstaller hands off to a temp copy and returns at once.
            _wait_until(lambda: not _uninstall_key_exists(), timeout_s=120, poll_s=1.0)
    assert not _uninstall_key_exists(), "the throwaway install was not removed"
