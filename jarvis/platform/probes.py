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
    # UnixPtyBackend has one implemented POSIX backend: ptyprocess.  The stdlib
    # ``pty`` module alone is not sufficient and must not produce a false
    # capability claim that later fails at ``UnixPtyBackend.spawn``.
    return _has_module("ptyprocess")


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


def screen_recording_granted() -> bool | None:
    """Tri-state: is screen capture (screenshot) permitted?

    ``True`` granted (or no grant needed) · ``False`` explicitly denied · ``None``
    unknown (macOS where pyobjc-Quartz is absent, so we cannot probe). Only macOS
    gates screenshots behind a TCC "Screen Recording" grant; Windows and Linux
    need no per-app grant — Wayland's capture restriction is a separate, non-TCC
    concern handled at the capture site. Without the macOS grant ``mss`` returns
    only the desktop wallpaper with no error, so Computer-Use would click blind;
    callers detect-and-degrade with a clear message (AD-13), never hard-block.
    """
    if detect_platform() != "darwin":
        return True
    try:
        from Quartz import (  # type: ignore[import-not-found]
            CGPreflightScreenCaptureAccess,
        )
    except (ImportError, ModuleNotFoundError):
        return None  # pyobjc-Quartz absent → unknown until installed.
    try:
        return bool(CGPreflightScreenCaptureAccess())
    except Exception:  # pragma: no cover - native call guard
        log.debug("CGPreflightScreenCaptureAccess() raised; treating as unknown.")
        return None


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


def has_cursor() -> bool:
    """Can the global mouse-cursor position be read on this OS (AI Pointer)?

    Windows reads it via stdlib ``ctypes`` (always available). macOS/Linux-X11
    read it via ``pynput``; Wayland forbids global cursor queries, and a headless
    host has no display — both degrade to the null cursor backend.
    """
    plat = detect_platform()
    if plat == "win32":
        return True
    if not display_present():
        return False
    if plat == "linux" and is_wayland():
        return False
    return _has_module("pynput")


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
    "screen_recording_granted",
    "has_ax_tree",
    "has_hotkey",
    "has_cursor",
    "has_overlay",
    "has_elevation",
]
