"""No-op hotkey backend for hosts where global hotkeys are unavailable (AD-8).

Returned by ``make_hotkey_backend()`` when ``capabilities.has_hotkey`` is
``False``. Two quite different situations land here and they need different
answers, which is why :func:`explain_unavailable` measures rather than assumes:

* **Wayland** — a process cannot install a global keyboard grab by OS design
  (the compositor owns input routing). Nothing the user installs changes that;
  the fix is a compositor-level shortcut pointing at the CLI.
* **X11 without ``pynput``** — the far more common case, and entirely fixable:
  ``pip install 'personal-jarvis[desktop-linux]'``. It is a separate opt-in
  extra because pynput hard-requires ``evdev``, which is source-only and
  compiles against the kernel headers — pulling it into ``[full]`` would break
  the advertised install on a stock slim image.

Rather than crash or silently do nothing (the "said it worked, nothing
happened" class — AD-OE6), this backend logs one clear English message
explaining the actual situation and then no-ops every call. The voice path is
unaffected: the user leans on the wake word.

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


def explain_unavailable() -> str:
    """Why global hotkeys do not work here, and what (if anything) fixes it.

    The message used to say "Wayland" unconditionally, which was wrong on the
    far more common case: an X11 desktop where ``pynput`` simply is not
    installed. Telling a user their session is the problem when the fix is one
    ``pip install`` away is the "said it worked, nothing happened" class of
    dishonesty applied to a diagnostic — so the reason is now measured.

    English, user-facing, and safe to surface in the UI as well as the log.
    """
    try:
        from jarvis.platform import detect_platform
        from jarvis.platform.probes import is_wayland

        platform = detect_platform()
    except Exception:  # noqa: BLE001 — a probe failure must not break the message
        return (
            "this host cannot register a global keyboard shortcut. Voice still "
            "works via the wake word, and dictation can be started from the "
            "Jarvis Bar, the Dictation view, or `jarvis api dictation start`."
        )

    if platform == "linux":
        try:
            wayland = is_wayland()
        except Exception:  # noqa: BLE001
            wayland = False
        if wayland:
            return (
                "Wayland does not let an application grab a global shortcut — "
                "the compositor owns them by design. Bind a shortcut in your "
                "desktop settings to `jarvis api dictation start` instead; "
                "voice also still works via the wake word."
            )
        return (
            "the pynput package is not installed. On an X11 desktop, "
            "`pip install 'personal-jarvis[desktop-linux]'` enables global "
            "shortcuts (it builds evdev, so a C compiler and the kernel "
            "headers are required). Until then, use the wake word, the Jarvis "
            "Bar, or `jarvis api dictation start`."
        )

    if platform == "darwin":
        return (
            "macOS needs both the Accessibility and Input Monitoring "
            "permissions, and the pyobjc Quartz package. Grant them under "
            "Settings > Permissions, then restart Jarvis."
        )

    return (
        "the global-hotkeys package is not installed. `pip install "
        "'personal-jarvis[desktop]'` enables the shortcuts; voice still works "
        "via the wake word."
    )


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
        log.info("Global hotkeys are unavailable here: %s", explain_unavailable())

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


__all__ = ["NoopBackend", "_reset_noop_log_flag_for_tests", "explain_unavailable"]
