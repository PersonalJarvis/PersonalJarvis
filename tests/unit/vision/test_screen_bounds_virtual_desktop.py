"""The UI-tree on-screen filter must span the WHOLE virtual desktop.

Root cause of the 2026-07-02 19:06 stall: every tree source clipped its
on-screen overlap filter to the PRIMARY monitor (Windows: GetSystemMetrics
0/1; macOS: CGMainDisplayID; Linux: a hardcoded 1920x1080), so a window on
ANY secondary monitor lost its whole accessibility tree — no clickable
anchors, no field hints, no focus evidence, and the click-verification had
nothing to consult. The filter must use the union of ALL monitors
(:func:`jarvis.platform.monitors.virtual_desktop_bounds`), resolved through
the one platform seam on every OS.
"""
from __future__ import annotations

import subprocess
import sys
import types

from jarvis.platform import monitors as monitors_mod
from jarvis.vision.pruning import RawNode
from jarvis.vision.uia_tree import UIATreeSource

# A window fully on the LEFT secondary monitor (negative X): the incident
# geometry, nothing on the primary.
_SECONDARY_WINDOW = [
    RawNode(role="Window", name="Chrome (guest)",
            bounds=(-2000, 100, 1600, 900), depth=0, parent_index=-1),
    RawNode(role="Edit", name="Address and search bar",
            bounds=(-1900, 140, 1200, 36), depth=1, parent_index=0,
            focused=True),
    RawNode(role="Button", name="Back",
            bounds=(-1980, 140, 32, 32), depth=1, parent_index=0),
]


def _traverser(depth, window_title_filter=None):
    return ("Chrome (guest)", 4242, list(_SECONDARY_WINDOW))


async def test_uia_source_keeps_nodes_on_a_secondary_monitor(monkeypatch):
    monkeypatch.setattr(
        monitors_mod, "virtual_desktop_bounds",
        lambda: (-2560, 0, 6400, 2160),
    )
    src = UIATreeSource(traverser=_traverser)  # no injected bounds
    obs = await src.observe()
    roles = [(n.role, n.focused) for n in obs.nodes]
    assert ("Edit", True) in roles, (
        "the focused address bar on the secondary monitor must survive "
        "the on-screen filter"
    )
    assert any(r == "Button" for r, _ in roles)


async def test_uia_source_falls_back_when_virtual_bounds_unknown(monkeypatch):
    # Headless probe: helper says None -> the source keeps its legacy
    # primary-monitor fallback instead of crashing or filtering everything.
    monkeypatch.setattr(
        monitors_mod, "virtual_desktop_bounds", lambda: None,
    )
    src = UIATreeSource(traverser=_traverser)
    obs = await src.observe()
    assert obs is not None  # never raises


# ---------------------------------------------------------------------------
# The shared platform helper
# ---------------------------------------------------------------------------

def test_x11_virtual_bounds_reads_root_geometry(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["xdotool", "getdisplaygeometry"]
        return subprocess.CompletedProcess(cmd, 0, stdout="6400 2160\n",
                                           stderr="")

    monkeypatch.setattr(monitors_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(monitors_mod.shutil, "which",
                        lambda n: f"/usr/bin/{n}")
    assert monitors_mod._x11_virtual_bounds() == (0, 0, 6400, 2160)


def test_x11_virtual_bounds_none_without_xdotool(monkeypatch):
    monkeypatch.setattr(monitors_mod.shutil, "which", lambda n: None)
    assert monitors_mod._x11_virtual_bounds() is None


def test_macos_virtual_bounds_unions_all_displays(monkeypatch):
    class _Rect:
        def __init__(self, x, y, w, h):
            self.origin = types.SimpleNamespace(x=x, y=y)
            self.size = types.SimpleNamespace(width=w, height=h)

    rects = {1: _Rect(0, 0, 3456, 2234), 2: _Rect(3456, -200, 1920, 1080)}
    mod = types.ModuleType("Quartz")
    mod.CGGetActiveDisplayList = lambda mx, a, b: (0, [1, 2], 2)
    mod.CGDisplayBounds = lambda did: rects[did]
    monkeypatch.setitem(sys.modules, "Quartz", mod)
    assert monitors_mod._macos_virtual_bounds() == (0, -200, 5376, 2434)


def test_virtual_desktop_bounds_never_raises_on_this_host():
    bounds = monitors_mod.virtual_desktop_bounds()
    assert bounds is None or (len(bounds) == 4 and bounds[2] > 0)
