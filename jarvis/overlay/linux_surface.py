"""``LinuxBestEffortOverlay`` — Linux transparent-orb attempt (Wave 2, 2.6; AD-11).

On a Linux compositing window manager (X11 with a compositor like picom/mutter/
kwin), Tk's ``-transparentcolor`` color-key trick sometimes works; under a
non-compositing WM, or under Wayland (where the color-key cannot be keyed out at
all), it does not — and the failure mode the color-key avoids is an opaque magenta
box on screen. So this surface **probes** whether the transparent orb is viable
and, when it is not, **falls through to** :class:`~jarvis.overlay.tray_surface.TrayOnlySurface`
with a logged English message (AD-11). It never shows an opaque box.

The probe is cheap and side-effect-light: Wayland (``capabilities.is_wayland``) is
an immediate fall-through; otherwise a guarded ``wm_attributes("-transparentcolor")``
probe on a throwaway, never-mapped Tk root decides. Any failure → tray floor.

Import-cleanliness (HN-7): Tk and the wrapped surfaces are imported lazily inside
method bodies; importing this module on a headless box stays clean.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _color_key_supported() -> bool:
    """Probe whether Tk ``-transparentcolor`` is accepted on this Linux session.

    Builds a throwaway, **withdrawn** (never mapped) ``tk.Tk()`` root and tries to
    set ``-transparentcolor``. On X11 without a compositor Tk raises ``TclError``
    for that attribute; on a compositor it is accepted. Any failure (no Tk, no
    display, raise) → ``False`` so the caller degrades to the tray. The root is
    always destroyed.
    """
    root = None
    try:
        import tkinter as tk  # lazy: GUI-only

        root = tk.Tk()
        root.withdraw()  # never map it — no flash, no opaque box on screen.
        root.wm_attributes("-transparentcolor", "#FF00FF")
        return True
    except Exception:  # noqa: BLE001 — TclError, no display, etc. → unsupported.
        log.debug("Linux color-key probe: -transparentcolor unsupported", exc_info=True)
        return False
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                log.debug("Linux color-key probe: root destroy failed", exc_info=True)


class LinuxBestEffortOverlay:
    """Attempt the Tk transparent orb on Linux; degrade to the tray floor.

    Delegates to a concrete inner surface chosen at :meth:`start` time:
    :class:`~jarvis.overlay.surface.TkColorKeyOverlay` when the color-key probe
    passes on a compositing X11 session, else
    :class:`~jarvis.overlay.tray_surface.TrayOnlySurface`. All four Protocol
    methods forward to whichever inner surface was chosen; before ``start`` they
    are safe no-ops.

    ``probe=`` / ``inner=`` are injectable so headless tests can force the
    compositor-present and the no-compositor/Wayland branches deterministically.
    """

    def __init__(
        self,
        *,
        capabilities: Any = None,
        probe: Any = None,
        inner: Any = None,
    ) -> None:
        self._caps = capabilities
        self._probe = probe or _color_key_supported
        self._inner: Any = inner
        self._started = False

    def _select_inner(self) -> Any:
        """Choose the inner surface. Wayland → tray immediately; else probe."""
        if self._inner is not None:
            return self._inner

        wayland = bool(getattr(self._caps, "is_wayland", False))
        if wayland:
            log.info(
                "Linux Wayland session detected — the transparent orb color-key "
                "is unavailable by OS design; using the system-tray floor instead."
            )
            from jarvis.overlay.tray_surface import TrayOnlySurface

            self._inner = TrayOnlySurface()
            return self._inner

        supported = False
        try:
            supported = bool(self._probe())
        except Exception:  # noqa: BLE001 — a probe must never crash selection.
            log.debug("Linux color-key probe raised", exc_info=True)
            supported = False

        if supported:
            log.info(
                "Linux compositor supports color-key transparency — using the "
                "transparent Tk orb (best effort; live-verify on a real desktop)."
            )
            from jarvis.overlay.surface import TkColorKeyOverlay

            self._inner = TkColorKeyOverlay()
        else:
            log.info(
                "Linux session does not support color-key transparency (no "
                "compositing window manager) — using the system-tray floor "
                "instead so no opaque box is shown."
            )
            from jarvis.overlay.tray_surface import TrayOnlySurface

            self._inner = TrayOnlySurface()
        return self._inner

    def start(self) -> None:
        if self._started:
            return
        try:
            inner = self._select_inner()
            inner.start()
            self._started = True
        except Exception:  # noqa: BLE001 — AD-6: never crash boot.
            log.exception(
                "LinuxBestEffortOverlay: start failed; no orb presence this "
                "session (the rest of Jarvis is unaffected)."
            )

    def stop(self) -> None:
        self._started = False
        inner = self._inner
        if inner is None:
            return
        try:
            inner.stop()
        except Exception:  # noqa: BLE001
            log.debug("LinuxBestEffortOverlay.stop failed", exc_info=True)

    def set_state(self, state: str) -> None:
        inner = self._inner
        if inner is None:
            return
        try:
            inner.set_state(state)
        except Exception:  # noqa: BLE001
            log.debug("LinuxBestEffortOverlay.set_state failed", exc_info=True)

    def is_visible(self) -> bool:
        inner = self._inner
        if inner is None:
            return False
        try:
            return bool(inner.is_visible())
        except Exception:  # noqa: BLE001
            return False


__all__ = ["LinuxBestEffortOverlay", "_color_key_supported"]
