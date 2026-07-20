"""Executor tests: winget dispatch with a mocked subprocess.

Important:
- The command line is checked as list arguments (no shell).
- package_id injection is caught twice, by the Pydantic regex + the
  missing `shell=True`.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from jarvis.admin.executor import AdminExecutor
from jarvis.admin.schema import (
    InstallWingetOp,
    ReadRegistryOp,
    StartServiceOp,
    UninstallWingetOp,
    WriteRegistryHkcuOp,
)


class _SubprocessRecorder:
    """Replacement for ``AdminExecutor._run_subprocess``.

    Records the argv calls and returns scripted return values.
    """

    def __init__(self, scripted: list[tuple[int, str, str]] | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], int]] = []
        self.scripted = scripted or [(0, "OK", "")]

    async def __call__(self, argv, *, timeout_s):
        self.calls.append((tuple(argv), timeout_s))
        if self.scripted:
            return self.scripted.pop(0)
        return 0, "", ""


@pytest.mark.asyncio
async def test_install_winget_command_line_no_shell_meta():
    rec = _SubprocessRecorder()
    ex = AdminExecutor()
    ex._run_subprocess = rec  # type: ignore[assignment]

    op = InstallWingetOp(package_id="7zip.7zip")
    resp = await ex.execute(op)

    assert resp.success is True
    assert len(rec.calls) == 1
    argv, _to = rec.calls[0]
    # Arguments as a list — no shell metacharacters, no shell=True.
    assert argv[0] == "winget"
    assert argv[1] == "install"
    assert "--id" in argv
    assert "7zip.7zip" in argv
    assert "--silent" in argv
    assert "--accept-package-agreements" in argv
    assert "--accept-source-agreements" in argv
    # Not a single argument may contain a semicolon or &&-style meta char.
    for a in argv:
        assert ";" not in a
        assert "&&" not in a
        assert "|" not in a


@pytest.mark.asyncio
async def test_install_winget_with_version_appends_flag():
    rec = _SubprocessRecorder()
    ex = AdminExecutor()
    ex._run_subprocess = rec  # type: ignore[assignment]
    op = InstallWingetOp(package_id="7zip.7zip", version="22.01")
    resp = await ex.execute(op)
    assert resp.success
    argv, _ = rec.calls[0]
    assert "--version" in argv
    idx = argv.index("--version")
    assert argv[idx + 1] == "22.01"


@pytest.mark.asyncio
async def test_install_winget_non_zero_is_failure():
    rec = _SubprocessRecorder(scripted=[(1, "", "winget: not found")])
    ex = AdminExecutor()
    ex._run_subprocess = rec  # type: ignore[assignment]
    op = InstallWingetOp(package_id="7zip.7zip")
    resp = await ex.execute(op)
    assert resp.success is False
    assert resp.error_code == "winget_failed"


@pytest.mark.asyncio
async def test_uninstall_winget_argv_shape():
    rec = _SubprocessRecorder()
    ex = AdminExecutor()
    ex._run_subprocess = rec  # type: ignore[assignment]
    op = UninstallWingetOp(package_id="7zip.7zip")
    await ex.execute(op)
    argv, _ = rec.calls[0]
    assert argv[:2] == ("winget", "uninstall")
    assert "--id" in argv
    assert "7zip.7zip" in argv
    assert "--silent" in argv


@pytest.mark.asyncio
async def test_start_service_uses_sc_exe():
    rec = _SubprocessRecorder()
    ex = AdminExecutor()
    ex._run_subprocess = rec  # type: ignore[assignment]
    op = StartServiceOp(service="W32Time")
    resp = await ex.execute(op)
    assert resp.success
    argv, _ = rec.calls[0]
    assert argv == ("sc.exe", "start", "W32Time")


@pytest.mark.asyncio
async def test_timeout_returns_timeout_error_code():
    ex = AdminExecutor(default_timeout_s=1, winget_timeout_s=1)

    async def _slow(argv, *, timeout_s):  # noqa: ARG001
        await asyncio.sleep(5)
        return (0, "", "")

    ex._run_subprocess = _slow  # type: ignore[assignment]
    op = StartServiceOp(service="SlowService")
    resp = await ex.execute(op)
    assert resp.success is False
    assert resp.error_code == "timeout"


@pytest.mark.asyncio
async def test_run_subprocess_rejects_non_str_argv():
    ex = AdminExecutor()
    with pytest.raises(TypeError):
        await ex._run_subprocess(["winget", 123], timeout_s=1)  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_read_registry_hkcu_environment_smoke(monkeypatch):
    """``read_registry`` on HKCU\\Environment should at least return a
    response — whether success=True depends on whether PATH or similar
    is set on the test machine. We only check: no exception,
    the correct response type, error_code is correct on key-not-found.
    """
    ex = AdminExecutor()
    op = ReadRegistryOp(hive="HKCU", key_path="Environment",
                        value_name="PATH")
    resp = await ex.execute(op)
    assert resp.op_id == op.op_id
    assert resp.success in (True, False)
    if not resp.success:
        assert resp.error_code in (
            "registry_key_not_found",
            "registry_read_failed",
            "registry_unsupported",
        )


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows degradation path")
@pytest.mark.asyncio
async def test_write_registry_degrades_honestly_off_windows():
    resp = await AdminExecutor().execute(
        WriteRegistryHkcuOp(
            key_path=r"Software\PersonalJarvisTest",
            value_name="Sample",
            value_data="value",
        )
    )
    assert resp.success is False
    assert resp.error_code == "registry_unsupported"
    assert resp.error_message == "Windows Registry is unavailable on this platform."
