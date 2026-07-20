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
    """Stub resolve + Popen so no real process starts; return the Popen call log.

    Also stubs the post-launch foreground raise to a fast no-op so the launch
    tests stay deterministic and host-independent (the real raise polls
    list_windows for up to 3 s). Tests that care about the raise re-stub it.
    """
    calls: list = []
    monkeypatch.setattr(
        oa, "resolve_app_launch_target",
        lambda n: types.SimpleNamespace(kind="executable", value=r"C:\fake\obs.exe"),
    )
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(oa.window_state, "raise_after_launch", lambda n, **k: (False, ""))
    return calls


async def test_focuses_when_already_running(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: WindowInfo("OBS 30.0.0"))
    raised: list = []
    monkeypatch.setattr(
        oa.window_state, "raise_window", lambda w: (raised.append(w.title), (True, w.title))[1]
    )
    res = await OpenAppTool().execute({"app_name": "obs"}, _ctx())
    assert res.success is True
    assert "already running" in (res.output or "").lower()
    assert calls == []          # never launched a second instance
    assert raised               # hardened raise was attempted on the existing window


async def test_launches_when_not_running(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: None)
    res = await OpenAppTool().execute({"app_name": "obs"}, _ctx())
    assert res.success is True
    assert "Gestartet" in (res.output or "")  # i18n-allow: matches the tool's real (currently German) readback text
    assert len(calls) == 1


async def test_multi_instance_app_always_launches(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa, "KNOWN_APPS", oa._KNOWN_APPS_WIN)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: WindowInfo("File Explorer"))
    raised: list = []
    monkeypatch.setattr(
        oa.window_state, "raise_window", lambda w: (raised.append(w.title), (True, w.title))[1]
    )
    res = await OpenAppTool().execute({"app_name": "explorer"}, _ctx())
    assert res.success is True
    assert len(calls) == 1       # explorer is multi-instance -> launch anyway
    assert raised == []          # short-circuit skipped, never raised


async def test_reuse_existing_false_always_launches(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: WindowInfo("OBS 30"))
    res = await OpenAppTool().execute({"app_name": "obs", "reuse_existing": False}, _ctx())
    assert res.success is True
    assert len(calls) == 1


async def test_focus_failure_falls_through_to_launch(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: WindowInfo("OBS 30"))
    monkeypatch.setattr(oa.window_state, "raise_window", lambda w: (False, "lock timeout"))
    res = await OpenAppTool().execute({"app_name": "obs"}, _ctx())
    assert res.success is True
    assert "Gestartet" in (res.output or "")   # launched after the raise failed  # i18n-allow: matches the tool's real (currently German) readback text
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


# --- post-launch foreground raise (the "opens in background" bug) ------------


async def test_fresh_launch_raises_window_to_foreground(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: None)
    raised: list = []
    monkeypatch.setattr(
        oa.window_state, "raise_after_launch",
        lambda n, **k: (raised.append(n), (True, "New Tab - Google Chrome"))[1],
    )
    res = await OpenAppTool().execute({"app_name": "chrome"}, _ctx())
    assert res.success is True
    assert len(calls) == 1               # launched once
    assert raised == ["chrome"]          # and actively pulled to the front
    assert "vorn" in (res.output or "").lower()   # honest readback


async def test_raise_miss_keeps_success(monkeypatch):
    # The launch already succeeded; a foreground-raise miss must NOT flip the
    # result to failure — it only softens the readback back to plain "Gestartet".  # i18n-allow: quotes the tool's real (currently German) readback text
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: None)
    monkeypatch.setattr(oa.window_state, "raise_after_launch", lambda n, **k: (False, "no window"))
    res = await OpenAppTool().execute({"app_name": "chrome"}, _ctx())
    assert res.success is True
    assert "Gestartet" in (res.output or "")  # i18n-allow: matches the tool's real (currently German) readback text
    assert "vorn" not in (res.output or "").lower()
    assert len(calls) == 1


async def test_raise_crash_never_breaks_launch(monkeypatch):
    calls = _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: None)

    def boom(_n, **_k):
        raise RuntimeError("focus blew up")

    monkeypatch.setattr(oa.window_state, "raise_after_launch", boom)
    res = await OpenAppTool().execute({"app_name": "chrome"}, _ctx())
    assert res.success is True            # crash in the raise never fails the launch
    assert "Gestartet" in (res.output or "")  # i18n-allow: matches the tool's real (currently German) readback text
    assert len(calls) == 1


async def test_url_launch_does_not_raise(monkeypatch):
    # A URL reuses an existing browser window; the app-name raise would not
    # apply, so it must be skipped (no pointless 3 s poll).
    _stub_launch(monkeypatch)
    monkeypatch.setattr(oa.window_state, "is_app_running", lambda n: None)
    raised: list = []
    monkeypatch.setattr(
        oa.window_state, "raise_after_launch", lambda n, **k: raised.append(n) or (True, n)
    )
    res = await OpenAppTool().execute({"app_name": "https://example.com"}, _ctx())
    assert res.success is True
    assert raised == []                  # raise skipped for URLs
