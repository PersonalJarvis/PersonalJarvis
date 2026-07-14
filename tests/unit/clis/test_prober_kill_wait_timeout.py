"""Regression tests: a probed child process that survives ``kill()`` must
not wedge the prober (or the synchronous ``asyncio.run(bootstrap())`` path
in jarvis/clis/loader.py) forever.

Before this fix, ``proc.kill(); await proc.wait()`` had no timeout — a
``.cmd``/``.bat`` shim's actual grandchild process surviving the kill signal
would hang the probe indefinitely.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.clis import prober as prober_mod
from jarvis.clis.prober import CliStatusProber
from jarvis.clis.spec import AuthConfig, CliSpec, InstallMethods, RiskConfig


class _HangingProcess:
    """Fake subprocess whose ``communicate()``/``wait()`` never return —
    simulates a shim child that survives ``kill()``."""

    def __init__(self) -> None:
        self.killed = False
        self.returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(3600)
        return b"", b""  # pragma: no cover

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        await asyncio.sleep(3600)
        return 0  # pragma: no cover


def _spec(name: str = "gh") -> CliSpec:
    return CliSpec(
        name=name,
        display_name=name.upper(),
        description="d",
        homepage="",
        binary_name=name,
        check_command=(name, "--version"),
        version_parse_regex=r"(\d+)",
        install=InstallMethods(manual_url="https://x"),
        auth=AuthConfig(type="oauth_cli", status_command=(name, "auth", "status")),
        risk=RiskConfig(default_tier="monitor"),
    )


@pytest.mark.asyncio
async def test_probe_binary_returns_when_kill_wait_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prober_mod, "CHECK_TIMEOUT_S", 0.01)
    monkeypatch.setattr(prober_mod, "KILL_WAIT_TIMEOUT_S", 0.01)
    monkeypatch.setattr(prober_mod.shutil, "which", lambda _name: "/usr/bin/gh")

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _HangingProcess:
        return _HangingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    prober = CliStatusProber()
    # Outer safety net: if the fix regresses, this fails loudly instead of
    # hanging the whole test run forever.
    installed, version, path = await asyncio.wait_for(
        prober._probe_binary(_spec()), timeout=2.0
    )
    assert installed is True
    assert version is None
    assert path == "/usr/bin/gh"


@pytest.mark.asyncio
async def test_probe_auth_returns_unknown_when_kill_wait_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prober_mod, "AUTH_TIMEOUT_S", 0.01)
    monkeypatch.setattr(prober_mod, "KILL_WAIT_TIMEOUT_S", 0.01)
    monkeypatch.setattr(
        prober_mod, "resolve_executable", lambda name: f"/usr/bin/{name}"
    )

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _HangingProcess:
        return _HangingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    prober = CliStatusProber()
    status = await asyncio.wait_for(prober._probe_auth(_spec()), timeout=2.0)
    assert status == "unknown"


@pytest.mark.asyncio
async def test_probe_completes_end_to_end_when_both_stages_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full ``probe()`` (binary + auth) must return a status, never hang, even
    when BOTH the version-check and the auth-status child survive kill()."""
    monkeypatch.setattr(prober_mod, "CHECK_TIMEOUT_S", 0.01)
    monkeypatch.setattr(prober_mod, "AUTH_TIMEOUT_S", 0.01)
    monkeypatch.setattr(prober_mod, "KILL_WAIT_TIMEOUT_S", 0.01)
    monkeypatch.setattr(prober_mod.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        prober_mod, "resolve_executable", lambda name: f"/usr/bin/{name}"
    )

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _HangingProcess:
        return _HangingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    prober = CliStatusProber()
    status = await asyncio.wait_for(prober.probe(_spec()), timeout=2.0)
    assert status.installed is True
    assert status.auth_status == "unknown"
