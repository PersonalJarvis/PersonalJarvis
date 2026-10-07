"""Pass the right to take the foreground on to another process (Windows).

Windows lets a process activate a window only while it holds the foreground
right. A launch the user just started from the Start menu, the taskbar or a
desktop icon has it; the process it hands its boot to, and an instance that is
already running in the background, do not. Without the right, the running
instance's attempt to raise its own window is refused, and a second launch
read that refusal as "no window" and offered to kill a healthy app
(2026-10-06, twelve false "probably stuck" prompts in a day).

``AllowSetForegroundWindow`` hands the right on for the next activation. It
only works while the caller holds the right itself, so every hop of a launch
passes it on as early as it can. Other platforms have no such lock: the call
is a no-op there.
"""

from __future__ import annotations

import sys

from loguru import logger

#: ``ASFW_ANY`` as an unsigned DWORD — any process may take the foreground next.
_ASFW_ANY = 0xFFFFFFFF


def allow_foreground(pid: int | None = None) -> bool:
    """Let ``pid`` (or any process when ``None``) take the foreground next.

    Returns whether Windows accepted the grant. ``False`` is normal when this
    process holds no foreground right of its own; nothing else changes then.
    """
    if sys.platform != "win32":
        return False
    target = _ASFW_ANY
    if pid is not None:
        try:
            target = int(pid)
        except (TypeError, ValueError):  # no usable pid: grant foreground to any process
            target = _ASFW_ANY
        if target <= 0:
            target = _ASFW_ANY
    try:
        import ctypes

        return bool(ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint32(target).value))
    except Exception as exc:  # noqa: BLE001 - a nicety, never a reason to fail a launch
        logger.debug("could not pass the foreground right on to {}: {}", pid, exc)
        return False
