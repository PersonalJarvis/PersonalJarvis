"""Regression: Tk-thread orb gestures must marshal their ``bus.publish`` onto
the *captured backend asyncio loop*, never a throwaway ``asyncio.run`` loop.

Forensic (2026-06-28, ``data/jarvis_desktop.log`` 16:59:47): an orb
double-click mute fired ``_publish_mute_toggle`` on the overlay's Tk thread,
which has no asyncio loop. The old code fell back to ``asyncio.run(coro)`` —
spinning a throwaway loop — and ``bus.publish`` then dispatched the per-WS-client
``_forward`` subscriber, whose ``asyncio.Lock`` (``send_lock``) is bound to the
real backend loop. Acquiring it from the throwaway loop raised
``RuntimeError: <Lock> is bound to a different event loop`` for every connected
client (a "WS forward failed" storm). The mute flag flipped ON but the
``VoiceMuteChanged`` broadcast could not reach the UI, so the mic stayed muted
with no visible cue and the voice session sat in LISTENING — "I spoke and it
never started thinking" — until the session died ``reason=error`` and the app
self-restarted.

These tests pin the fix: the bridge captures the backend loop and gestures use
``run_coroutine_threadsafe`` onto it.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

# Repo root in sys.path so the top-level module `ui.orb.*` is importable.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

try:  # noqa: SIM105 — bewusster Try-Import wegen Discovery-Quirk
    from ui.orb.bus_bridge import OrbBusBridge  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "ui.orb not available on the pytest PYTHONPATH — top-level namespace package.",
        allow_module_level=True,
    )

class _FakeOrb:
    def __init__(self) -> None:
        self.mute_callback = None

    def set_on_mute_toggle(self, callback) -> None:
        self.mute_callback = callback


class _RecordingBus:
    """Records the asyncio loop each ``publish`` coroutine actually runs on."""

    def __init__(self) -> None:
        self.ran_on: list[asyncio.AbstractEventLoop] = []
        self.done = threading.Event()

    def subscribe(self, *_args, **_kwargs) -> None:
        pass

    async def publish(self, _event) -> None:
        self.ran_on.append(asyncio.get_running_loop())
        self.done.set()


def test_mute_toggle_publishes_on_captured_backend_loop() -> None:
    """The Tk-thread mute gesture must run ``publish`` on the backend loop,
    not a throwaway ``asyncio.run`` loop (the cross-event-loop ``send_lock``
    crash). Calling from the main test thread mirrors the loop-less Tk thread.
    """
    backend = asyncio.new_event_loop()
    thread = threading.Thread(target=backend.run_forever, daemon=True)
    thread.start()
    try:
        bus = _RecordingBus()
        bridge = OrbBusBridge(bus=bus, orb=_FakeOrb())  # type: ignore[arg-type]
        # Simulate attach()/_on_state having captured the live backend loop.
        bridge._loop = backend  # noqa: SLF001

        # Fire the gesture from this (loop-less) thread = the overlay Tk thread.
        bridge._publish_mute_toggle()  # noqa: SLF001

        assert bus.done.wait(timeout=2.0), "mute publish never executed"
        assert bus.ran_on[0] is backend, (
            "publish ran on a throwaway loop, not the captured backend loop — "
            "the cross-event-loop send_lock crash would recur"
        )
    finally:
        backend.call_soon_threadsafe(backend.stop)
        thread.join(timeout=2.0)
        backend.close()


async def test_remember_loop_captures_running_backend_loop() -> None:
    """``_remember_loop`` (called from the backend-loop handlers ``attach`` and
    ``_on_state``) records the live loop, so a Tk gesture firing later has a real
    loop to marshal onto instead of falling back to a throwaway ``asyncio.run``."""
    bridge = OrbBusBridge(bus=_RecordingBus(), orb=_FakeOrb())  # type: ignore[arg-type]
    assert bridge._loop is None  # noqa: SLF001

    bridge._remember_loop()  # noqa: SLF001 — exactly what _on_state/attach delegate to

    assert bridge._loop is asyncio.get_running_loop()  # noqa: SLF001
