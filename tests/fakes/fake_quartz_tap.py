"""A ``Quartz`` module stand-in whose event tap is a ``FakeEventTap``.

``QuartzHotkeyBackend.start`` imports ``Quartz`` lazily and calls
``CGEventTapCreate``. This builds a module with exactly the names the backend
touches, wired to ``tests/fakes/fake_tcc.FakeEventTap`` so the tap consults the
stateful ``FakeTCC``: created without the grant it fails or goes deaf, created
before the user was asked it auto-denies (the BUG-058 class), and every creation
lands in the ``FakeTCC`` call log. Events are delivered through
``FakeQuartz.deliver_*``, which only reaches the backend's callback while the
fake tap is live, like the real one.

Nothing here is macOS behaviour that has been observed on a Mac; the fidelity
labels of ``fake_tcc`` apply.
"""

from __future__ import annotations

import sys
import threading
import types
from typing import Any

from tests.fakes.fake_tcc import FakeEventTap, FakeTCC

_KEY_DOWN, _KEY_UP, _FLAGS_CHANGED = 10, 11, 12
_DISABLED_BY_TIMEOUT, _DISABLED_BY_USER = 13, 14


class _Handle:
    def __init__(self) -> None:
        self.enabled = False
        self.invalidated = False


class _Loop:
    def __init__(self) -> None:
        self.stopped = False
        self.wake = threading.Event()


class FakeQuartz:
    """The module (``.module``) plus the hooks a test drives it with."""

    def __init__(self, tcc: FakeTCC, *, caller: str = "quartz-backend") -> None:
        self.tcc = tcc
        self.tap = FakeEventTap(tcc, caller=caller)
        self.created: list[_Handle] = []
        self.callback: Any = None
        self._local = threading.local()
        self.module = self._build()

    # -- event delivery ---------------------------------------------------

    def deliver_key(self, keycode: int, *, down: bool = True) -> bool:
        """A key goes down (or up). ``True`` when the backend's callback ran."""
        return self._deliver(_KEY_DOWN if down else _KEY_UP, keycode, 0)

    def deliver_flags(self, flags: int) -> bool:
        """A modifier flags change. ``True`` when the backend's callback ran."""
        return self._deliver(_FLAGS_CHANGED, 0, flags)

    def _deliver(self, event_type: int, keycode: int, flags: int) -> bool:
        if self.callback is None or not self.tap.receives_events:
            return False
        self.tap.events_received += 1
        event = types.SimpleNamespace(keycode=keycode, flags=flags)
        self.callback(None, event_type, event, None)
        return True

    # -- the module -------------------------------------------------------

    def _build(self) -> types.ModuleType:
        quartz = types.ModuleType("Quartz")
        quartz.kCGEventKeyDown = _KEY_DOWN
        quartz.kCGEventKeyUp = _KEY_UP
        quartz.kCGEventFlagsChanged = _FLAGS_CHANGED
        quartz.kCGEventTapDisabledByTimeout = _DISABLED_BY_TIMEOUT
        quartz.kCGEventTapDisabledByUserInput = _DISABLED_BY_USER
        quartz.kCGKeyboardEventKeycode = 15
        quartz.kCGSessionEventTap = 16
        quartz.kCGHeadInsertEventTap = 17
        quartz.kCGEventTapOptionListenOnly = 18
        quartz.kCFRunLoopCommonModes = "common"
        quartz.CGEventMaskBit = lambda value: 1 << value

        def tap_create(_location, _placement, _options, _mask, callback, _refcon):
            self.callback = callback
            if not self.tap.create(None, listen_only=True):
                return None
            handle = _Handle()
            self.created.append(handle)
            return handle

        def get_loop():
            loop = _Loop()
            self._local.loop = loop
            return loop

        def run_loop():
            loop = self._local.loop
            while not loop.stopped:
                loop.wake.wait()
                loop.wake.clear()

        def stop_loop(loop):
            loop.stopped = True

        def wake_loop(loop):
            loop.wake.set()

        quartz.CGEventTapCreate = tap_create
        quartz.CFMachPortCreateRunLoopSource = lambda _alloc, tap, _order: ("source", tap)
        quartz.CFRunLoopGetCurrent = get_loop
        quartz.CFRunLoopAddSource = lambda _loop, _source, _mode: None
        quartz.CFRunLoopRun = run_loop
        quartz.CFRunLoopStop = stop_loop
        quartz.CFRunLoopWakeUp = wake_loop
        quartz.CFRunLoopRemoveSource = lambda _loop, _source, _mode: None
        quartz.CGEventTapEnable = lambda handle, enabled: setattr(handle, "enabled", enabled)
        quartz.CFMachPortInvalidate = lambda handle: setattr(handle, "invalidated", True)
        quartz.CGEventGetIntegerValueField = lambda event, _field: event.keycode
        quartz.CGEventGetFlags = lambda event: event.flags
        return quartz

    def install(self, monkeypatch: Any) -> FakeQuartz:
        """Make ``import Quartz`` resolve to this module for the test."""
        monkeypatch.setitem(sys.modules, "Quartz", self.module)
        return self


__all__ = ["FakeQuartz"]
