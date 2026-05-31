"""Shared fixtures/helpers for the autostart port unit tests."""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.platform import PlatformName
from jarvis.platform.capabilities import Capabilities


def make_caps(
    *,
    platform: PlatformName = "linux",
    display_present: bool = True,
) -> Capabilities:
    """A Capabilities snapshot with only the fields the autostart port reads
    varied; the rest get harmless defaults."""
    return Capabilities(
        platform=platform,
        has_hotkey=False,
        has_ax_tree=False,
        has_overlay=False,
        has_pty=False,
        has_elevation=False,
        display_present=display_present,
        is_wayland=False,
        ax_permission_granted=None,
    )


def make_cfg(*, enabled: bool = True, start_minimized: bool = True) -> SimpleNamespace:
    """A minimal stand-in for JarvisConfig with just an ``autostart`` block."""
    return SimpleNamespace(
        autostart=SimpleNamespace(enabled=enabled, start_minimized=start_minimized)
    )
