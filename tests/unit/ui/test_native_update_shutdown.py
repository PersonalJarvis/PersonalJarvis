"""Native update requires a completed backend shutdown before swapping files."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import jarvis.ui.desktop_app as desktop_module


def test_strict_shutdown_reports_server_stop_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    class Future:
        def __init__(self, fail: bool) -> None:
            self.fail = fail

        def result(self, *, timeout: float) -> None:
            if self.fail:
                raise TimeoutError("server stop stalled")

    def schedule(coroutine: object, _loop: object) -> Future:
        nonlocal calls
        calls += 1
        coroutine.close()  # type: ignore[attr-defined]
        return Future(fail=calls == 2)

    async def stop() -> None:
        pass

    monkeypatch.setattr(desktop_module.asyncio, "run_coroutine_threadsafe", schedule)
    monkeypatch.setattr(desktop_module, "META_FILE_PATH", tmp_path / "meta.json")
    app = SimpleNamespace(
        _shutdown_done=False,
        _destroy_background_keeper=lambda: None,
        _orb=None,
        _virtual_cursor=None,
        _jarvis_cursor=None,
        _backend_loop=SimpleNamespace(
            is_running=lambda: True,
            call_soon_threadsafe=lambda *_args: None,
            stop=lambda: None,
        ),
        _server=SimpleNamespace(stop=stop, _pty=None),
        _pipeline_task=None,
        _bootstrap=None,
        _backend_thread=SimpleNamespace(join=lambda **_kwargs: None, is_alive=lambda: False),
        _tray=None,
        _tray_bridge_thread=None,
    )

    result = desktop_module.DesktopApp.shutdown(app, require_clean=True)

    assert calls == 2  # PTY cleanup, then server.stop
    assert result == 1
    assert app._shutdown_clean is False
