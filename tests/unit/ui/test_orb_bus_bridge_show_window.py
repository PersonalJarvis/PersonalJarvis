"""OrbBusBridge must wire the overlay's right-click gesture to a bus publish.

Mirrors the proven mute-toggle wiring (``set_on_mute_toggle`` →
``_publish_mute_toggle`` → ``VoiceMuteToggleRequested``): the bridge injects a
callback into whichever surface is current (bar OR mascot), and the callback
publishes ``ShowWindowRequested`` so the DesktopApp can raise its window.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Repo-Root in sys.path so the top-level `ui.orb.*` package is importable.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

try:  # noqa: SIM105 — intentional try-import for the discovery quirk
    from ui.orb.bus_bridge import OrbBusBridge  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "ui.orb not on the pytest pythonpath — top-level namespace package. "
        "Run with `python -m pytest tests/unit/ui/...` from the repo root.",
        allow_module_level=True,
    )


class _FakeBus:
    def subscribe(self, *_args, **_kwargs) -> None:
        pass


class _FakeSurface:
    """Duck-typed overlay surface exposing exactly what attach() touches."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.show_window_callback = None

    def show(self, mode: str = "listen") -> None:
        self.calls.append(("show", mode))

    def set_on_mute_toggle(self, callback) -> None:
        self.calls.append(("set_on_mute_toggle", callback))

    def set_feedback_publisher(self, callback) -> None:
        self.calls.append(("set_feedback_publisher", callback))

    def set_on_show_window(self, callback) -> None:
        self.calls.append(("set_on_show_window", callback))
        self.show_window_callback = callback


async def test_attach_registers_show_window_callback_with_surface() -> None:
    """attach() must inject a callback via ``set_on_show_window`` so the
    surface's right-click can publish on the bus."""
    surface = _FakeSurface()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=surface, idle_animations_enabled=False)  # type: ignore[arg-type]

    bridge.attach()

    set_calls = [c for c in surface.calls if c[0] == "set_on_show_window"]
    assert len(set_calls) == 1
    assert callable(set_calls[0][1])


async def test_set_surface_reinjects_show_window_callback() -> None:
    """A live style swap (bar↔mascot) must re-inject the callback into the
    new surface, exactly like the mute-toggle + feedback publishers."""
    bridge = OrbBusBridge(
        bus=_FakeBus(), orb=_FakeSurface(), idle_animations_enabled=False  # type: ignore[arg-type]
    )
    bridge.attach()

    new_surface = _FakeSurface()
    bridge.set_surface(new_surface)

    set_calls = [c for c in new_surface.calls if c[0] == "set_on_show_window"]
    assert len(set_calls) == 1
    assert callable(set_calls[0][1])


async def test_show_window_callback_publishes_show_window_request() -> None:
    """The injected callback must publish ``ShowWindowRequested`` on the real
    bus with a non-empty forensic ``source``."""
    from jarvis.core.bus import EventBus
    from jarvis.core.events import ShowWindowRequested

    bus = EventBus()
    seen: list[ShowWindowRequested] = []
    bus.subscribe(ShowWindowRequested, lambda ev: seen.append(ev))

    surface = _FakeSurface()
    bridge = OrbBusBridge(bus=bus, orb=surface, idle_animations_enabled=False)
    bridge.attach()
    callback = surface.show_window_callback
    assert callback is not None

    # The callback marshals onto the running loop (run_coroutine_threadsafe).
    callback()
    await asyncio.sleep(0.05)

    assert len(seen) == 1
    assert seen[0].source == "overlay_rightclick"
