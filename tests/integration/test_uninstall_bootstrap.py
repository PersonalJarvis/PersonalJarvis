"""End-to-end tests for the uninstall bootstraps (install/uninstall.ps1 + .sh).

Regression for the locked-folder uninstall failure: with the Jarvis app still
running, its process kept venv files locked on Windows and the bootstrap's
final recursive delete died with a red PermissionDenied stacktrace. The
bootstraps now stop every process running out of the install dir, retry the
delete, and fail with an honest message instead of a crash.

SAFETY — these tests run ONLY on throwaway CI runners (``CI`` env var), never
on a developer machine: the bootstraps' no-venv fallback path removes the real
per-user desktop registration (Start-menu shortcut + registry keys on Windows,
the app bundle / .desktop entry on POSIX), which must never happen to a live
personal install. Everything else is sandboxed: the "install" being removed is
a throwaway fake directory injected via ``JARVIS_INSTALL_DIR``, populated with
a copied system long-runner (ping/sleep) so a process is genuinely executing
out of the tree when the bootstrap runs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("CI"),
        reason=(
            "CI runners only — the no-venv fallback path touches the real "
            "per-user app registration, never exercise it on a developer machine"
        ),
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_fake_install(tmp_path: Path) -> Path:
    """A directory the bootstrap accepts as an install, WITHOUT a venv python.

    The missing venv python routes the bootstrap through its fallback branch,
    so the script's own process-stop + retry-delete logic is what does the
    work — nothing here depends on `python -m jarvis --uninstall`.
    """
    fake = tmp_path / "fake-jarvis-install"
    (fake / "jarvis").mkdir(parents=True)
    (fake / "pyproject.toml").write_text("[project]\nname='personal-jarvis'\n", encoding="utf-8")
    return fake


def _start_process_from(fake: Path) -> subprocess.Popen[bytes]:
    """Run a harmless long-runner whose executable lives inside the fake dir.

    On Windows this puts real file locks on the tree — the exact condition
    that broke the original uninstall.
    """
    if sys.platform == "win32":
        bin_dir = fake / ".venv" / "Scripts"
        bin_dir.mkdir(parents=True, exist_ok=True)
        target = bin_dir / "jarvis-fake.exe"
        source = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ping.exe"
        shutil.copy2(source, target)
        return subprocess.Popen(  # noqa: S603 — self-copied system binary
            [str(target), "-n", "120", "127.0.0.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    bin_dir = fake / ".venv" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "jarvis-fake"
    sleep_bin = shutil.which("sleep")
    assert sleep_bin, "CI runner without a sleep binary"
    shutil.copy2(sleep_bin, target)
    target.chmod(0o755)
    return subprocess.Popen(  # noqa: S603 — self-copied system binary
        [str(target), "120"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_bootstrap(fake: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "JARVIS_INSTALL_DIR": str(fake)}
    if sys.platform == "win32":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        assert shell, "CI runner without PowerShell"
        cmd = [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "install" / "uninstall.ps1"),
            "--yes",
        ]
    else:
        cmd = ["bash", str(REPO_ROOT / "install" / "uninstall.sh"), "--yes"]
    return subprocess.run(  # noqa: S603 — repo-owned scripts against a fake dir
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        stdin=subprocess.DEVNULL,
    )


def test_bootstrap_removes_fake_install_despite_running_process(tmp_path: Path) -> None:
    fake = _make_fake_install(tmp_path)
    proc = _start_process_from(fake)
    try:
        time.sleep(1)
        assert proc.poll() is None, "the fake app must be running when the bootstrap starts"

        result = _run_bootstrap(fake)

        assert result.returncode == 0, (
            f"bootstrap failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert not fake.exists(), "the install dir must be fully removed"
        # The running process was stopped by the script, not by our cleanup.
        assert proc.poll() is not None, "the bootstrap must stop the running app"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_bootstrap_dry_run_touches_nothing(tmp_path: Path) -> None:
    fake = _make_fake_install(tmp_path)

    env = {**os.environ, "JARVIS_INSTALL_DIR": str(fake)}
    if sys.platform == "win32":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        assert shell
        cmd = [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "install" / "uninstall.ps1"),
            "--dry-run",
        ]
    else:
        cmd = ["bash", str(REPO_ROOT / "install" / "uninstall.sh"), "--dry-run"]
    result = subprocess.run(  # noqa: S603
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 0, (
        f"dry run failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert fake.exists(), "a dry run must never delete the install dir"
