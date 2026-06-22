"""Robust Node.js resolution for worker spawns.

Live forensic 2026-06-20: jarvis was launched (by the hermes-agent runtime) with
a PATH that did NOT contain the Node.js directory. ``shutil.which("node")`` —
which searches the inherited PATH — therefore returned None, so any node-direct
spawn fell back to the fragile ``codex.CMD`` shim (whose own bare-``node``
lookup ALSO failed) and every mission died ``task_error``.

``resolve_node_executable`` must look BEYOND the inherited PATH: when
``shutil.which`` misses, probe the well-known Windows Node.js install locations
so a degraded launch environment can no longer hide node from the worker.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from jarvis.missions.workers.process_utils import resolve_node_executable


def test_resolve_node_prefers_which(tmp_path: Path, monkeypatch) -> None:
    """When node is on PATH, return exactly what shutil.which finds."""
    fake = tmp_path / "node.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: str(fake) if "node" in n.lower() else None)
    assert resolve_node_executable() == str(fake)


@pytest.mark.skipif(sys.platform != "win32", reason="well-known dirs are Windows-specific")
def test_resolve_node_via_wellknown_when_which_fails(tmp_path: Path, monkeypatch) -> None:
    """The crux of the 2026-06-20 incident: node off the inherited PATH.
    shutil.which returns None, but a well-known Node.js dir holds node.exe —
    resolution must still succeed (else the worker falls back to the broken
    .CMD shim)."""
    node_dir = tmp_path / "nodejs"
    node_dir.mkdir()
    (node_dir / "node.exe").write_text("", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    from jarvis.missions.workers import process_utils as pu
    monkeypatch.setattr(pu, "_windows_node_dir_candidates", lambda: [str(node_dir)])

    assert resolve_node_executable() == str(node_dir / "node.exe")


def test_resolve_node_returns_none_when_nowhere(monkeypatch) -> None:
    """No node anywhere → None (caller degrades to the bare binary fallback)."""
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    from jarvis.missions.workers import process_utils as pu
    monkeypatch.setattr(pu, "_windows_node_dir_candidates", lambda: [])
    assert resolve_node_executable() is None
