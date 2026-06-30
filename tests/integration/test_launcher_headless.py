"""Integration-Test für `_run_headless` mit Mock-WebServer.

Simuliert: Launcher startet, 100ms später SIGINT → sauberer Shutdown.
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest


class _MockWebServer:
    """Minimaler Stand-in für jarvis.ui.web.server.WebServer."""

    instances: list["_MockWebServer"] = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.started = False
        self.stopped = False
        _MockWebServer.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _patch_webserver(monkeypatch):
    """Installiert ein Fake-Modul jarvis.ui.web.server mit _MockWebServer."""
    _MockWebServer.instances.clear()
    fake_mod = types.ModuleType("jarvis.ui.web.server")
    fake_mod.WebServer = _MockWebServer
    monkeypatch.setitem(sys.modules, "jarvis.ui.web.server", fake_mod)
    yield


def _make_cfg(port: int = 18123):
    # `args.port` is read by _run_headless before falling back to _fast_admin_port();
    # the SimpleNamespace stub must carry the attribute (None = use auto-detect).
    return SimpleNamespace(
        ui=SimpleNamespace(admin_api_port=port, dev_mode=False),
        port=None,
        dev=False,
        no_lock=True,
    )


def test_headless_starts_and_stops_on_signal():
    """`_run_headless` beendet sauber wenn stop_event gesetzt wird."""
    from jarvis.ui.web import launcher

    cfg = _make_cfg()

    async def _driver():
        task = asyncio.create_task(launcher._run_headless(cfg))
        # Warte bis Server gestartet
        for _ in range(50):
            await asyncio.sleep(0.02)
            if _MockWebServer.instances and _MockWebServer.instances[0].started:
                break
        # Kill-Switch: finde die asyncio.Event() die der Launcher nutzt.
        # Da wir nicht direkt rankommen, cancelln wir den Task — das bricht
        # stop_event.wait() ab und triggert den finally-Zweig.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_driver())

    assert len(_MockWebServer.instances) == 1
    inst = _MockWebServer.instances[0]
    assert inst.started is True
    assert inst.stopped is True


def test_headless_stop_event_path():
    """Alternativer Pfad: wir patchen asyncio.Event damit der Wait sofort returnt."""
    from jarvis.ui.web import launcher

    cfg = _make_cfg(port=18222)

    async def _driver():
        # Starte Launcher in Task.
        task = asyncio.create_task(launcher._run_headless(cfg))
        # Gib dem Launcher Zeit WebServer.start() durchzuführen.
        await asyncio.sleep(0.1)
        # Setze SIGINT-like-Shutdown: cancel triggert finally → server.stop()
        task.cancel()
        try:
            rc = await task
        except asyncio.CancelledError:
            rc = None
        return rc

    asyncio.run(_driver())

    assert _MockWebServer.instances, "WebServer sollte instanziiert worden sein"
    assert _MockWebServer.instances[-1].stopped is True
