"""Outer desktop deadlines must let a healthy browser flush complete."""

from types import SimpleNamespace

from jarvis.ui import desktop_app
from jarvis.ui.web.server import _SOCIETY_SHUTDOWN_TIMEOUT_S


def test_desktop_does_not_stop_loop_before_healthy_profile_flush(monkeypatch, tmp_path):
    state = {"flushed": False, "stopped_after_flush": False, "early_stop": False}

    class BackendLoop:
        def is_running(self):
            return True

        def call_soon_threadsafe(self, callback):
            callback()

        def stop(self):
            state["early_stop"] |= not state["flushed"]
            state["stopped_after_flush"] = state["flushed"]

    class Server:
        async def stop(self):
            pass  # The deterministic future below models an eight-second flush.

    class CleanupFuture:
        def __init__(self, server_close, owns_loop, loop):
            self.server_close = server_close
            self.owns_loop, self.loop = owns_loop, loop

        def result(self, timeout=None):
            if self.server_close:
                if timeout is not None and timeout < 8:
                    raise TimeoutError("The healthy profile is still flushing")
                state["flushed"] = True
                if self.owns_loop:
                    self.loop.stop()

        def done(self):
            return not self.server_close or state["flushed"]

    def schedule(coroutine, loop):
        owns_loop = coroutine.cr_code.co_name == "_drain_backend_shutdown"
        is_server_close = coroutine.cr_code.co_name == "stop" or owns_loop
        coroutine.close()
        return CleanupFuture(is_server_close, owns_loop, loop)

    monkeypatch.setattr(desktop_app.asyncio, "run_coroutine_threadsafe", schedule)
    monkeypatch.setattr(desktop_app, "META_FILE_PATH", tmp_path / "desktop-sidecar")
    app = desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)
    app._shutdown_done = False
    app._destroy_background_keeper = lambda: None
    app._stop_overlay = lambda: None
    app._virtual_cursor = app._jarvis_cursor = app._pipeline_task = None
    app._backend_loop = BackendLoop()
    app._server = Server()
    app._bootstrap = app._backend_thread = app._tray = None
    assert app.shutdown() == 0
    future = getattr(app, "_backend_shutdown_future", None)
    if future is not None and not future.done():
        # Newer shells leave cleanup owned by the backend after their short GUI
        # wait. Completing that future must still precede stopping its loop.
        assert not state["early_stop"]
        future.result(timeout=8)
    assert state == {"flushed": True, "stopped_after_flush": True, "early_stop": False}


def test_force_exit_outwaits_cleanup_and_preserves_longer_requested_deadline(monkeypatch):
    delays, exits = [], []

    class TimerThread:
        def __init__(self, *, target, name, daemon):
            assert daemon
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(desktop_app, "threading", SimpleNamespace(Thread=TimerThread))
    monkeypatch.setattr(desktop_app, "time", SimpleNamespace(sleep=delays.append))
    monkeypatch.setattr(desktop_app, "os", SimpleNamespace(_exit=exits.append))
    app = desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)
    app._arm_force_exit(after_s=20)
    app._arm_force_exit(after_s=60)
    # The outer watchdog must cover preliminary shutdown, the server's complete
    # cleanup and the thread join; it must never shorten a caller's longer cap.
    assert delays[0] >= 12 + _SOCIETY_SHUTDOWN_TIMEOUT_S + 3
    assert delays[1] == 60
    assert exits == [0, 0]
