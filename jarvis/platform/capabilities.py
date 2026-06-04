"""Cached ``Capabilities`` snapshot (Wave 0, sub-task 0.1 + 0.2; AD-5).

The six platform ports read their feature flags from one frozen snapshot
computed once per process. This is the single "what works on this box" answer
that the setup wizard and the graceful-degrade messages (AD-13) render from.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from . import PlatformName, detect_platform, probes


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Immutable snapshot of the host's cross-platform capabilities (AD-5).

    Mirrors the frozen-dataclass style of ``jarvis/core/protocols.py`` wire
    types. ``ax_permission_granted`` is tri-state: ``True`` granted /
    ``False`` denied / ``None`` unknown-until-first-use (macOS without pyobjc).
    """

    platform: PlatformName
    has_hotkey: bool
    has_ax_tree: bool
    has_overlay: bool
    has_pty: bool
    has_elevation: bool
    has_cursor: bool
    display_present: bool
    is_wayland: bool
    ax_permission_granted: bool | None


@functools.lru_cache(maxsize=1)
def detect_capabilities() -> Capabilities:
    """Compute the host capability snapshot exactly once (cached)."""
    return Capabilities(
        platform=detect_platform(),
        has_hotkey=probes.has_hotkey(),
        has_ax_tree=probes.has_ax_tree(),
        has_overlay=probes.has_overlay(),
        has_pty=probes.has_pty(),
        has_elevation=probes.has_elevation(),
        has_cursor=probes.has_cursor(),
        display_present=probes.display_present(),
        is_wayland=probes.is_wayland(),
        ax_permission_granted=probes.ax_permission_granted(),
    )


def reset_capabilities_cache() -> None:
    """Clear the cached snapshot — test-isolation hook.

    Mirrors ``jarvis/trigger/hotkey.py`` ``_reset_checker_state_for_tests``: tests
    that monkeypatch the environment or ``find_spec`` call this so the next
    ``detect_capabilities()`` re-probes instead of returning a stale snapshot.
    """
    detect_capabilities.cache_clear()


__all__ = ["Capabilities", "detect_capabilities", "reset_capabilities_cache"]
