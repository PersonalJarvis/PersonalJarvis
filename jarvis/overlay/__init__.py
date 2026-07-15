"""On-screen overlay surfaces and helpers.

This package holds the LIVE desktop-presence building blocks:

- ``surface`` / ``linux_surface`` / ``tray_surface`` — the cross-platform
  overlay-surface factory ladder (Tk orb on Windows, best-effort on Linux
  X11, tray-only floor everywhere else).
- ``virtual_cursor`` / ``system_cursor`` — the Jarvis cursor swap during
  Computer-Use actions.
- ``drop_bridge`` / ``drop_target`` — drag-and-drop onto the floating
  overlay.

Import the submodules directly (``from jarvis.overlay.surface import
make_overlay_surface``). The former OS-Level subprocess overlay
(edge-glow / mascot / cursor-trail IPC stack) was removed in 2026-07; the
Computer-Use screen indicator that replaced its edge-glow lives in
``jarvis.cu.indicator``.
"""

from __future__ import annotations

__all__: list[str] = []
