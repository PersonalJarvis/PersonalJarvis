"""Phase 1b: open_app already-running short-circuit.

When the requested app is already open, focus its window instead of launching a
second instance (saves a Computer-Use step — the OBS-already-in-the-taskbar
case). Conservative: never for URLs/paths or multi-instance apps, never when
reuse is disabled, and a focus failure falls through to a normal launch.

Seam-level: window_state.is_app_running / focus_window are monkeypatched, and the
launch is stubbed, so the test is deterministic regardless of the host's open
windows.
"""
from __future__ import annotations

import types
from uuid import uuid4

from jarvis.core.protocols import ExecutionContext
from jarvis.platform.window_state import WindowInfo
from jarvis.plugins.tool import open_app as oa
from jarvis.plugins.tool.open_app import OpenAppTool


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(), user_utterance="t", config={}, memory_read=None, approved_by="auto"
    )


def _stub_launch(monkeypatch) -> list:
    """Stub resolve + Popen so no real process starts; return the Popen call log."""
    calls: list = []
    monkeypatch.setattr(
        oa, "resolve_app_launch_target",
        lambda n: types.SimpleNamespace(kind="executable", value=r"C:\fake\obs.exe"),
    )
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    return calls


async def test_focuses_when_already_running(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: WindowInfo("OBS 30.0.0"))
    focused: list = []
    monkeypatch.setattr(
        oa.window_state, "focus_window", lambda t: (focused.append(t), (True, t))[1]
    )
    res = await OpenAppTool().execute({"app_name": "obs"}, _ctx())
    assert res.success is True
    assert "already running" in (res.output or "").lower()
    assert calls == []          # never launched a second instance
    assert focused              # focus was attempted


async def test_launches_when_not_running(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: None)
    res = await OpenAppTool().execute({"app_name": "obs"}, _ctx())
    assert res.success is True
    assert "Gestartet" in (res.output or "")
    assert len(calls) == 1


async def test_multi_instance_app_always_launches(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: WindowInfo("File Explorer"))
    focused: list = []
    monkeypatch.setattr(
        oa.window_state, "focus_window", lambda t: (focused.append(t), (True, t))[1]
    )
    res = await OpenAppTool().execute({"app_name": "explorer"}, _ctx())
    assert res.success is True
    assert len(calls) == 1       # explorer is multi-instance -> launch anyway
    assert focused == []         # short-circuit skipped, never focused


async def test_reuse_existing_false_always_launches(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: WindowInfo("OBS 30"))
    res = await OpenAppTool().execute({"app_name": "obs", "reuse_existing": False}, _ctx())
    assert res.success is True
    assert len(calls) == 1


async def test_focus_failure_falls_through_to_launch(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: WindowInfo("OBS 30"))
    monkeypatch.setattr(oa.window_state, "focus_window", lambda t: (False, "lock timeout"))
    res = await OpenAppTool().execute({"app_name": "obs"}, _ctx())
    assert res.success is True
    assert "Gestartet" in (res.output or "")   # launched after focus failed
    assert len(calls) == 1


async def test_url_is_not_short_circuited(monkeypatch):
    calls = _stub_launch(monkeypatch)
    seen: list = []
    monkeypatch.setattr(
        oa.window_state, "is_app_running", lambda n: (seen.append(n), WindowInfo("x"))[1]
    )
    res = await OpenAppTool().execute({"app_name": "https://example.com"}, _ctx())
    assert res.success is True
    assert seen == []            # URL must not be treated as an app to focus
