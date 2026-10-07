"""No-browser doubles for the managed/plain Chrome ownership handoff."""

from __future__ import annotations

import asyncio
from pathlib import Path


class NativeSurface:
    def __init__(self, events: list, name: str = "plain") -> None:
        self.events = events
        self.name = name
        self.failed = False
        self.calls: list[tuple[str, dict]] = []

    def close(self) -> None:
        self.events.append((self.name, "capture_closed"))

    def frame(self) -> dict:
        return {"bytes": b"fixture", "width": 800, "height": 600, "timestamp": 1.0}

    def input(self, op: str, args: dict) -> None:
        self.calls.append((op, args))


class AutomationHandle:
    def __init__(self, events: list, name: str) -> None:
        self.events = events
        self.name = name
        self.fail_close = False

    @property
    def pages(self):
        raise AssertionError("Page inspection is forbidden in login mode")

    async def close(self) -> None:
        self.events.append((self.name, "closed"))
        if self.fail_close:
            raise RuntimeError("Owned browser is still closing")

    async def stop(self) -> None:
        self.events.append((self.name, "stopped"))


class PlainChromeFactory:
    def __init__(self, events: list) -> None:
        self.events = events
        self.instances: list[PlainChrome] = []
        self.start_error = False

    def __call__(self, profile: Path, executable: str, *, creationflags: int) -> PlainChrome:
        result = PlainChrome(self, profile, executable, creationflags)
        self.instances.append(result)
        return result


class PlainChrome:
    def __init__(self, factory, profile, executable, creationflags) -> None:
        self.factory = factory
        self.profile = profile
        self.executable = executable
        self.creationflags = creationflags
        self.native = NativeSurface(factory.events)
        self.close_error = False
        self.close_gate: asyncio.Event | None = None
        self.close_started = asyncio.Event()

    async def start(self) -> NativeSurface:
        self.factory.events.append(("plain", "started"))
        if self.factory.start_error:
            raise RuntimeError("Chrome did not produce a window")
        return self.native

    async def close(self) -> None:
        self.factory.events.append(("plain", "closing"))
        self.close_started.set()
        if self.close_error:
            raise RuntimeError("Chrome is still closing")
        if self.close_gate:
            await self.close_gate.wait()
        self.factory.events.append(("plain", "closed"))
        self.native.close()


class StartupPage:
    url = "chrome://newtab/"

    def on(self, event, callback) -> None:
        pass


class StartupContext:
    pages = [StartupPage()]

    async def route(self, pattern, callback) -> None:
        pass

    def on(self, event, callback) -> None:
        pass


class PlaywrightStartup:
    def __init__(self) -> None:
        self.chromium = self
        self.launches: list[tuple[str, dict]] = []
        self.started = False

    async def start(self):
        self.started = True
        return self

    async def launch_persistent_context(self, profile, **options):
        self.launches.append((profile, options))
        return StartupContext()

    async def install_cursor(self, context) -> None:
        pass
