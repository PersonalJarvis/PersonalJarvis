"""No-op hotkey backend for hosts where global hotkeys are unavailable (AD-8).

Returned by ``make_hotkey_backend()`` when ``capabilities.has_hotkey`` is
``False`` — most importantly on a Linux **Wayland** session, where a process
cannot install a global keyboard grab by OS design (the compositor owns input
routing; there is no portable global-hotkey API). Rather than crash or silently
do nothing (the "said it worked, nothing happened" class — AD-OE6), this backend
logs one clear English message explaining the situation and then no-ops every
call. The voice path is unaffected: the user leans on the wake word.

The message is emitted at most once per process (a module-level flag), so a
restart-heavy session does not spam the log.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Process-wide "already explained" flag so the message logs exactly once.
_LOGGED_ONCE = False


def _reset_noop_log_flag_for_tests() -> None:
    """Reset the logged-once flag — test-isolation hook only."""
    global _LOGGED_ONCE
    _LOGGED_ONCE = False


class NoopBackend:
    """A ``HotkeyBackend`` that explains itself once, then does nothing.

    Every method is a safe no-op; ``received_any_event`` is always ``False``
    (nothing can fire). Selected when the host cannot register a global hotkey —
    e.g. Wayland, or a box without ``pynput`` installed.
    """

    def __init__(self) -> None:
        self._explain_once()

    def _explain_once(self) -> None:
        global _LOGGED_ONCE
        if _LOGGED_ONCE:
            return
        _LOGGED_ONCE = True
        log.info(
            "Global hotkey unavailable on Wayland by OS design — leaning on the "
            "wake word instead. Voice and the mascot click still work; on an X11 "
            "session install the [desktop] extra to enable global hotkeys."
        )

    def register(self, bindings, on_event=None) -> None:
        return None

    def unregister(self) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def received_any_event(self) -> bool:
        return False


__all__ = ["NoopBackend", "_reset_noop_log_flag_for_tests"]
