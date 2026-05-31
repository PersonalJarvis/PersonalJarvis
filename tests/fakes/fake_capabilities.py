"""Hand-built ``Capabilities`` fakes for the three platforms (EK-3).

Per CLAUDE.md the project uses real fakes, never ``unittest.mock``. These give
tests a deterministic capability snapshot per OS without probing the real host,
so a port's seam-factory can be exercised for macOS/Linux from a Windows box.
"""

from __future__ import annotations

from jarvis.platform.capabilities import Capabilities


def fake_windows_capabilities(**overrides) -> Capabilities:
    """A fully-capable Windows desktop snapshot."""
    base = dict(
        platform="win32",
        has_hotkey=True,
        has_ax_tree=True,
        has_overlay=True,
        has_pty=True,
        has_elevation=True,
        display_present=True,
        is_wayland=False,
        ax_permission_granted=True,
    )
    base.update(overrides)
    return Capabilities(**base)


def fake_macos_capabilities(**overrides) -> Capabilities:
    """A macOS desktop snapshot; AX permission unknown until first use (None)."""
    base = dict(
        platform="darwin",
        has_hotkey=True,
        has_ax_tree=True,
        has_overlay=True,
        has_pty=True,
        has_elevation=True,
        display_present=True,
        is_wayland=False,
        ax_permission_granted=None,
    )
    base.update(overrides)
    return Capabilities(**base)


def fake_linux_capabilities(**overrides) -> Capabilities:
    """A Linux X11 desktop snapshot (use ``is_wayland=True`` for the Wayland case)."""
    base = dict(
        platform="linux",
        has_hotkey=True,
        has_ax_tree=True,
        has_overlay=True,
        has_pty=True,
        has_elevation=True,
        display_present=True,
        is_wayland=False,
        ax_permission_granted=False,
    )
    base.update(overrides)
    return Capabilities(**base)


def fake_headless_capabilities(**overrides) -> Capabilities:
    """A headless Linux VPS snapshot — no display, no GUI/input features."""
    base = dict(
        platform="linux",
        has_hotkey=False,
        has_ax_tree=False,
        has_overlay=False,
        has_pty=True,
        has_elevation=True,
        display_present=False,
        is_wayland=False,
        ax_permission_granted=False,
    )
    base.update(overrides)
    return Capabilities(**base)


__all__ = [
    "fake_windows_capabilities",
    "fake_macos_capabilities",
    "fake_linux_capabilities",
    "fake_headless_capabilities",
]
