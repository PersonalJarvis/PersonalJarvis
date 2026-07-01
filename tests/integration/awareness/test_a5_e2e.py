"""Phase A5-Lite — End-to-end integration test.

Verifies that ``AwarenessManager.probe_all`` parallelizes correctly,
honors the total budget, catches probe exceptions, and returns a
merged dict.

Plan §9 AC:
- GitProbe returns the branch when cwd = git repo, else None ✓
- FileSystemWatcher emits a FileSaved event ✓ (test_filesystem_probe)
- All probes have an individual timeout (200ms total budget) ✓
- Probe errors do NOT crash; unset fields become None ✓
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.probes import FileSystemProbe, GitProbe
from jarvis.core.bus import EventBus

# ---- Fakes -----------------------------------------------------------------


class _SlowProbe:
    """Probe that needs >budget — tests timeout handling."""
    name = "slow"
    async def probe(self, *, cwd: str | None, process_name: str = "") -> dict[str, Any]:
        await asyncio.sleep(0.5)    # > 200ms budget
        return {"slow_field": "should_not_be_in_output"}


class _RaisingProbe:
    """Probe that raises — tests return_exceptions=True."""
    name = "raising"
    async def probe(self, *, cwd: str | None, process_name: str = "") -> dict[str, Any]:
        raise RuntimeError("simulated probe failure")


class _StaticProbe:
    """Probe with deterministic output."""
    name = "static"
    def __init__(self, output: dict[str, Any]) -> None:
        self._output = output
    async def probe(self, *, cwd: str | None, process_name: str = "") -> dict[str, Any]:
        return dict(self._output)


# ---- Tests -----------------------------------------------------------------


async def test_probe_all_empty_probes_returns_empty_dict() -> None:
    """Default manager has no probes → probe_all returns {}."""
    m = AwarenessManager(AwarenessConfig.default())
    result = await m.probe_all(pid=1234, process_name="Code.exe")
    assert result == {}


async def test_probe_all_merges_multiple_probe_outputs() -> None:
    """2 StaticProbes → merged dict."""
    m = AwarenessManager(AwarenessConfig.default())
    m._probes = [
        _StaticProbe({"git_branch": "main"}),
        _StaticProbe({"open_file_hint": "C:/x/y.py"}),
    ]
    result = await m.probe_all(pid=0, process_name="Code.exe")
    assert result == {"git_branch": "main", "open_file_hint": "C:/x/y.py"}


async def test_probe_all_timeout_returns_empty_dict() -> None:
    """Probe needs >budget → wait_for triggers TimeoutError → {}."""
    cfg = AwarenessConfig.default()
    cfg.probes.total_budget_ms = 50    # 50ms budget
    m = AwarenessManager(cfg)
    m._probes = [_SlowProbe()]
    result = await m.probe_all(pid=0, process_name="x")
    assert result == {}


async def test_probe_all_swallows_probe_exceptions() -> None:
    """Probe raises an exception → return_exceptions=True → field missing from output, no crash."""
    m = AwarenessManager(AwarenessConfig.default())
    m._probes = [
        _RaisingProbe(),
        _StaticProbe({"git_branch": "main"}),
    ]
    result = await m.probe_all(pid=0, process_name="x")
    # raising-probe contributed nothing, static-probe did
    assert result == {"git_branch": "main"}


async def test_probe_all_with_real_git_probe_in_repo(tmp_path: Path) -> None:
    """Real GitProbe against a tmp git-init → returns the branch.

    Bypasses the ``probe_all`` path because psutil's cwd-resolve doesn't
    work for a fake pid — we instantiate GitProbe directly and call
    ``probe()`` with tmp_path as cwd.
    """
    subprocess.run(    # noqa: ASYNC221 — sync subprocess only in test setup
        ["git", "init", "--initial-branch=main", str(tmp_path)],
        check=True, capture_output=True,
    )
    git = GitProbe()
    result = await git.probe(cwd=str(tmp_path), process_name="x")
    assert result == {"git_branch": "main"}


async def test_filesystem_probe_lifecycle_via_manager() -> None:
    """FileSystemProbe can start/stop via the manager without crashing."""
    bus = EventBus()
    m = AwarenessManager(AwarenessConfig.default(), bus=bus)
    fs = FileSystemProbe(bus=bus)
    m._fs_probe = fs
    m._probes = [fs]
    await m.start()
    try:
        # Manager.start() already called fs.start() (per manager.py:start())
        result = await m.probe_all(pid=0, process_name="x")
        # cwd=None → fs returns {open_file_hint: None}
        assert result == {"open_file_hint": None}
    finally:
        await m.stop()
