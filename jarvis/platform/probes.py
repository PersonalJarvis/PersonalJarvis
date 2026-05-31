"""Per-capability probes feeding ``Capabilities`` (Wave 0, sub-task 0.2).

Each probe answers exactly one "does this box support X" question and is
defensive by contract: it swallows its own exceptions to a logged ``False``
(or ``None`` for the tri-state AX-permission probe) so a missing optional
dependency, a denied OS permission, or a headless session can never crash the
capability snapshot. None of these functions import a platform-only package at
module scope (HN-7); the lazy import lives inside the function body.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil

from . import detect_platform

log = logging.getLogger(__name__)


def _has_module(name: str) -> bool:
    """True if ``name`` is importable, without importing it.

    ``find_spec`` can itself raise ``ModuleNotFoundError``/``ValueError`` for a
    parent package that is absent or broken — treat any failure as "not there".
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):  # pragma: no cover
        return False


def display_present() -> bool:
    """Is there a graphical display to draw on / capture input from?"""
    plat = detect_platform()
    if plat in ("win32", "darwin"):
        return True
    # Linux: an X11 or Wayland display must be advertised in the environment.
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def is_wayland() -> bool:
    """True on a Linux Wayland session (feeds AD-8: hotkey no-op on Wayland)."""
    if detect_platform() != "linux":
        return False
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def has_pty() -> bool:
    """Is a pseudo-terminal backend importable (Wave 1.1)?"""
    if detect_platform() == "win32":
        return _has_module("winpty")
    # POSIX: ptyprocess (added by Wave 1.1) or the stdlib pty fallback.
    return _has_module("ptyprocess") or _has_module("pty")


def ax_permission_granted() -> bool | None:
    """Tri-state: is accessibility-tree access permitted?

    ``True`` granted · ``False`` explicitly denied/unreachable · ``None`` unknown
    (macOS where pyobjc is absent, so we cannot probe). AD-13: callers must
    detect-and-degrade, never hard-block, on ``None``/``False``.
    """
    plat = detect_platform()
    if plat == "win32":
        return True  # UIA needs no permission grant.
    if plat == "darwin":
        try:
            from ApplicationServices import (  # type: ignore[import-not-found]
                AXIsProcessTrusted,
            )
        except (ImportError, ModuleNotFoundError):
            return None  # pyobjc absent → unknown until installed.
        try:
            return bool(AXIsProcessTrusted())
        except Exception:  # pragma: no cover - native call guard
            log.debug("AXIsProcessTrusted() raised; treating as unknown.")
            return None
    # Linux: AT-SPI usable only if the accessibility bus is reachable.
    return bool(os.environ.get("AT_SPI_BUS") or os.environ.get("DBUS_SESSION_BUS_ADDRESS"))


def has_ax_tree() -> bool:
    """Is a UI-element accessibility tree backend available for this OS?"""
    plat = detect_platform()
    if plat == "win32":
        return _has_module("pywinauto")
    if plat == "darwin":
        return _has_module("Quartz")  # pyobjc-framework-Quartz
    # Linux: pyatspi is distro-packaged (AD-14), not a pip extra.
    return _has_module("pyatspi") or _has_module("gi")


def has_hotkey() -> bool:
    """Can a global hotkey be registered on this OS?"""
    plat = detect_platform()
    if plat == "win32":
        return _has_module("global_hotkeys")
    # macOS/Linux use pynput; Wayland blocks global grabs by design (AD-8).
    return _has_module("pynput") and not is_wayland()


def has_overlay() -> bool:
    """Can a transparent floating overlay (orb) be drawn (AD-11)?"""
    return display_present() and _has_module("tkinter")


def has_elevation() -> bool:
    """Is a privilege-escalation mechanism present (AD-12)?"""
    plat = detect_platform()
    if plat == "win32":
        return _has_module("win32pipe")
    if plat == "darwin":
        return True  # Authorization Services / osascript are always present.
    # Linux: polkit (pkexec) or sudo.
    return bool(shutil.which("pkexec") or shutil.which("sudo"))


__all__ = [
    "display_present",
    "is_wayland",
    "has_pty",
    "ax_permission_granted",
    "has_ax_tree",
    "has_hotkey",
    "has_overlay",
    "has_elevation",
]
