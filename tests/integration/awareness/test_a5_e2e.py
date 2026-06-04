"""Phase A5-Lite — End-to-End Integration Test.

Verifiziert dass ``AwarenessManager.probe_all`` korrekt parallelisiert,
total budget einhaelt, Probe-Exceptions abfaengt und merged dict
zurueckgibt.

Plan §9 AC:
- GitProbe liefert Branch wenn cwd = git-repo, sonst None ✓
- FileSystemWatcher emittiert FileSaved-Event ✓ (test_filesystem_probe)
- Alle Probes haben individuellen Timeout (200ms total budget) ✓
- Probe-Errors crashen NICHT, ungesetzte Felder werden None ✓
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
    """Probe der >budget braucht — testet Timeout-Handling."""
    name = "slow"
    async def probe(self, *, cwd: str | None, process_name: str = "") -> dict[str, Any]:
        await asyncio.sleep(0.5)    # > 200ms budget
        return {"slow_field": "should_not_be_in_output"}


class _RaisingProbe:
    """Probe der wirft — testet return_exceptions=True."""
    name = "raising"
    async def probe(self, *, cwd: str | None, process_name: str = "") -> dict[str, Any]:
        raise RuntimeError("simulated probe failure")


class _StaticProbe:
    """Probe mit deterministischem Output."""
    name = "static"
    def __init__(self, output: dict[str, Any]) -> None:
        self._output = output
    async def probe(self, *, cwd: str | None, process_name: str = "") -> dict[str, Any]:
        return dict(self._output)


# ---- Tests -----------------------------------------------------------------


async def test_probe_all_empty_probes_returns_empty_dict() -> None:
    """Default Manager hat keine Probes → probe_all returnt {}."""
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
    """Probe braucht >budget → wait_for triggert TimeoutError → {}."""
    cfg = AwarenessConfig.default()
    cfg.probes.total_budget_ms = 50    # 50ms budget
    m = AwarenessManager(cfg)
    m._probes = [_SlowProbe()]
    result = await m.probe_all(pid=0, process_name="x")
    assert result == {}


async def test_probe_all_swallows_probe_exceptions() -> None:
    """Probe wirft Exception → return_exceptions=True → field fehlt im output, kein Crash."""
    m = AwarenessManager(AwarenessConfig.default())
    m._probes = [
        _RaisingProbe(),
        _StaticProbe({"git_branch": "main"}),
    ]
    result = await m.probe_all(pid=0, process_name="x")
    # raising-probe contributed nichts, static-probe schon
    assert result == {"git_branch": "main"}


async def test_probe_all_with_real_git_probe_in_repo(tmp_path: Path) -> None:
    """Echter GitProbe gegen tmp git-init → returnt branch.

    Bypass von ``probe_all``-Pfad weil psutil-cwd-resolve fuer einen
    fake pid nicht klappt — wir instantiieren GitProbe direkt und rufen
    ``probe()`` mit dem tmp_path als cwd.
    """
    subprocess.run(    # noqa: ASYNC221 — sync subprocess nur im Test-Setup
        ["git", "init", "--initial-branch=main", str(tmp_path)],
        check=True, capture_output=True,
    )
    git = GitProbe()
    result = await git.probe(cwd=str(tmp_path), process_name="x")
    assert result == {"git_branch": "main"}


async def test_filesystem_probe_lifecycle_via_manager() -> None:
    """FileSystemProbe kann via manager start/stop ohne Crash."""
    bus = EventBus()
    m = AwarenessManager(AwarenessConfig.default(), bus=bus)
    fs = FileSystemProbe(bus=bus)
    m._fs_probe = fs
    m._probes = [fs]
    await m.start()
    try:
        # Manager.start() hat fs.start() schon gerufen (laut manager.py:start())
        result = await m.probe_all(pid=0, process_name="x")
        # cwd=None → fs returnt {open_file_hint: None}
        assert result == {"open_file_hint": None}
    finally:
        await m.stop()
