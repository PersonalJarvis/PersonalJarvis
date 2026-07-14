"""Cross-platform Orb ``OverlaySurface`` seam (Wave 2, sub-task 2.5; AD-6/AD-7/AD-11).

The orb overlay follows the uniform AD-6 seam: an ``OverlaySurface`` ``Protocol``,
a per-OS implementation, a ``detect_platform()`` factory, and a graceful
null-fallback that logs an English message and never raises.

The visual ladder (AD-11):

* ``win32`` / ``darwin`` → :class:`TkColorKeyOverlay`, which **wraps** the live Tk
  orb (``ui/orb/overlay.py`` ``OrbOverlay``). Tk's ``-transparentcolor`` (Win32
  ``LWA_COLORKEY`` on Windows, the Cocoa equivalent on macOS) works on both, per
  the Wave-0 verdict in ``docs/plans/cross-platform-mac-linux/ADR-orb-framework.md``.
  The color-key rendering path inside ``OrbOverlay`` is **not** touched (AD-7) —
  the wrapper only adapts its lifecycle (``start``/``stop``/``set_state``/
  ``is_visible``) to this Protocol.
* ``linux`` with a display and not Wayland → :class:`LinuxBestEffortOverlay`
  (defined in ``jarvis/overlay/linux_surface.py``), which itself degrades to the
  tray floor when the color-key probe fails.
* anything else (no display, ``not has_overlay``, headless €5-VPS) →
  :class:`TrayOnlySurface` (``jarvis/overlay/tray_surface.py``), the universal
  floor driving the already-cross-platform pystray tray (``jarvis/ui/tray.py``).

The Windows system-cursor swap (``jarvis/overlay/system_cursor.py``,
``SetSystemCursor``) stays Windows-only — it has no macOS/Linux equivalent and is
deliberately **not** wired into this surface (AD-11). Its existing call site in
``jarvis/ui/desktop_app.py`` stays gated on Windows.

Import-cleanliness contract (HN-7): nothing here imports Tk, the ``ui.orb``
package, ``pystray``, or any platform-only package at module scope. Every such
import is lazy + guarded inside a method body, so ``import jarvis.overlay.surface``
succeeds on a headless Linux VPS that has none of them installed.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class OverlaySurface(Protocol):
    """The minimal surface the desktop bridge drives the orb through (AD-6/AD-11).

    Implementations must degrade (log + no-op), never raise, and be idempotent —
    ``start``/``stop`` may be called more than once, in any order. ``set_state``
    accepts the orb's coarse lifecycle state (``"idle" | "listening" | "thinking"
    | "speaking" | "error" | "paused"``); a backend that cannot render a given
    state maps it to the nearest visual it can show.
    """

    def start(self) -> None:
        """Bring the surface up. Idempotent, never raises."""
        ...

    def stop(self) -> None:
        """Tear the surface down. Idempotent, never raises."""
        ...

    def set_state(self, state: str) -> None:
        """Reflect the orb lifecycle ``state``. Never raises."""
        ...

    def is_visible(self) -> bool:
        """True while the surface is presenting something to the user."""
        ...


# The orb's coarse lifecycle states (mirrors the modes ``ui/orb/bus_bridge.py``
# drives: idle/listen/think/speak) plus the tray's error/paused. Backends map
# these onto whatever visual vocabulary they own.
_ORB_STATE_TO_ORB_MODE: dict[str, str] = {
    "idle": "idle",
    "listening": "listen",
    "thinking": "think",
    "speaking": "speak",
    "error": "idle",
    "paused": "idle",
}

# The set of states for which the orb should be visible on screen. ``idle`` keeps
# the orb hidden (the live behaviour: the orb pops on wake, hides when idle).
_VISIBLE_STATES: frozenset[str] = frozenset({"listening", "thinking", "speaking"})


class TkColorKeyOverlay:
    """Wraps the live Tk ``OrbOverlay`` behind :class:`OverlaySurface` (AD-7).

    Default surface on ``win32`` and ``darwin`` (Tk ``-transparentcolor`` works on
    both — Wave-0 ADR). This adapter owns **no** rendering: it lazily constructs
    the live ``ui.orb.overlay.OrbOverlay`` and drives its existing thread-safe
    ``start_in_thread`` / ``show`` / ``hide`` / ``set_mode`` API. The color-key
    path inside ``OrbOverlay`` is left untouched.

    An inner orb can be injected (``inner=``) so headless tests exercise the
    lifecycle + state mapping against a fake without constructing a real Tk
    window (the offscreen guard cannot create a real ``tk.Tk()`` on a CI box).
    """

    def __init__(
        self,
        *,
        inner: Any = None,
        sticky: bool = False,
        mic_reactive: bool = False,
        style: str | None = None,
        mascot_path: str | None = None,
    ) -> None:
        self._inner = inner
        self._inner_kwargs = dict(
            sticky=sticky,
            mic_reactive=mic_reactive,
            style=style,
            mascot_path=mascot_path,
        )
        self._started = False
        self._visible = False
        self._state = "idle"

    def _ensure_inner(self) -> Any:
        """Lazily build the live Tk orb (or use the injected one). Never at
        module scope — the ``ui.orb`` package pulls in Tk/PIL/numpy and needs the
        repo root on ``sys.path`` (HN-7)."""
        if self._inner is None:
            from ui.orb.overlay import OrbOverlay  # lazy: heavy + Tk-bound

            self._inner = OrbOverlay(**self._inner_kwargs)
        return self._inner

    def start(self) -> None:
        if self._started:
            return
        try:
            inner = self._ensure_inner()
            inner.start_in_thread()
            self._started = True
        except Exception:  # noqa: BLE001 — AD-6: a GUI start must never crash boot.
            log.exception(
                "TkColorKeyOverlay: orb start failed; the orb will be absent "
                "this session (the rest of Jarvis is unaffected)."
            )

    def stop(self) -> None:
        inner = self._inner
        self._started = False
        self._visible = False
        if inner is None:
            return
        try:
            # OrbOverlay has no explicit ``stop``; hiding is the live teardown
            # (the Tk mainloop runs in a daemon thread that dies with the process).
            inner.hide()
        except Exception:  # noqa: BLE001
            log.debug("TkColorKeyOverlay.stop: inner hide failed", exc_info=True)

    def set_state(self, state: str) -> None:
        self._state = state
        inner = self._inner
        if inner is None or not self._started:
            return
        mode = _ORB_STATE_TO_ORB_MODE.get(state, "idle")
        try:
            if state in _VISIBLE_STATES:
                inner.show(mode=mode)
                self._visible = True
            else:
                inner.hide()
                self._visible = False
        except Exception:  # noqa: BLE001
            log.debug("TkColorKeyOverlay.set_state failed", exc_info=True)

    def is_visible(self) -> bool:
        return self._visible


def make_overlay_surface(*, capabilities: Any = None) -> OverlaySurface:
    """Select the orb surface for this host (AD-11). **Never raises** (AD-6).

    * ``win32`` with ``capabilities.has_overlay`` → :class:`TkColorKeyOverlay`.
    * ``darwin`` → tray floor (Aqua-Tk is main-thread-only; a worker-thread
      Tk root aborts the process natively — BUG-057).
    * ``linux`` with a display and not Wayland → :class:`LinuxBestEffortOverlay`
      (which itself degrades to the tray when the color-key probe fails).
    * everything else (``not has_overlay``, no display, Wayland, headless) →
      :class:`TrayOnlySurface`.

    The implementation modules are imported lazily so importing this module on a
    host missing Tk / pystray stays clean (HN-7). Any failure in selection or
    construction falls through to the tray floor rather than propagating.
    """
    try:
        from jarvis.platform import detect_platform
        from jarvis.platform.capabilities import detect_capabilities

        caps = capabilities if capabilities is not None else detect_capabilities()
        # The capability snapshot carries the canonical platform (AD-5); prefer it
        # so an injected snapshot drives the right branch deterministically. Only
        # re-detect when the snapshot somehow lacks a platform field.
        plat = getattr(caps, "platform", None) or detect_platform()

        # darwin deliberately falls through to the tray floor: this factory's
        # surfaces run their Tk mainloop on a worker thread, and Aqua-Tk (like
        # AppKit) is main-thread-only on macOS — a Tk root there aborts the
        # process natively (BUG-057, same class as the BUG-056 tray).
        if plat == "win32" and getattr(caps, "has_overlay", False):
            return TkColorKeyOverlay()

        if plat == "linux":
            display = getattr(caps, "display_present", False)
            wayland = getattr(caps, "is_wayland", False)
            has_overlay = getattr(caps, "has_overlay", False)
            if has_overlay and display and not wayland:
                from jarvis.overlay.linux_surface import LinuxBestEffortOverlay

                return LinuxBestEffortOverlay(capabilities=caps)

        # Floor: tray-only (headless VPS, Wayland, no Tk, or no display).
        from jarvis.overlay.tray_surface import TrayOnlySurface

        return TrayOnlySurface()
    except Exception:  # noqa: BLE001 — AD-6: the factory itself is the safe seam.
        log.exception(
            "make_overlay_surface: selection failed; falling back to the "
            "tray-only floor so the user still gets some presence."
        )
        from jarvis.overlay.tray_surface import TrayOnlySurface

        return TrayOnlySurface()


__all__ = [
    "OverlaySurface",
    "TkColorKeyOverlay",
    "make_overlay_surface",
]
