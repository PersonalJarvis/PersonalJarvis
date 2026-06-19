"""Cross-platform hotkey backends (Wave 1, sub-task 1.4; AD-6/AD-7/AD-8).

The global-hotkey feature follows the uniform AD-6 seam: a ``HotkeyBackend``
``Protocol``, a per-OS implementation, a ``sys.platform`` factory, and a
graceful null-fallback that logs an English message and never raises.

* Windows keeps the battle-tested ``global-hotkeys`` package — its left/right-Alt
  fold, the single-checker refcount, the remove-by-string contract, and the
  pre-remove-on-reentry sequence carry hard-won BUG fixes (the F1+F2-went-dead
  class). That logic was **relocated verbatim** into
  ``jarvis/trigger/backends/global_hotkeys.py`` (AD-7); it is not refactored.
* macOS/Linux (X11) gain ``pynput`` via ``PynputBackend``.
* Wayland (and any box where ``capabilities.has_hotkey`` is ``False``) gets
  ``NoopBackend``: it logs once that global hotkeys are unavailable by OS design
  and otherwise no-ops (AD-8 / AD-OE6 "zero silent drops").

Import-cleanliness contract (HN-7): nothing here — nor in any backend module —
imports a platform-only package (``global_hotkeys`` / ``pynput``) at module
scope. Each backend lazy-imports its dependency inside ``register``/``start``,
so ``import jarvis.trigger.backends`` succeeds on a headless €5-VPS that has
neither package installed.

``HotkeyBinding`` is the existing ``[combo_str, on_press, on_release]`` shape the
``global_hotkeys`` registry uses; the backends consume it unchanged so the
``HotkeyTrigger`` call site stays the single producer of those rows.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

# A single binding row, mirroring the global_hotkeys registry format:
#   [normalized_combo_str, on_press | None, on_release | None]
HotkeyBinding = list
# The event-emit callback the trigger passes down; backends are free to ignore
# it (the global_hotkeys backend wires handlers directly into the binding rows).
OnEvent = Callable[[str], None]


@runtime_checkable
class HotkeyBackend(Protocol):
    """The seam every per-OS hotkey implementation satisfies (AD-6).

    The lifecycle mirrors what ``HotkeyTrigger.__aenter__``/``__aexit__`` already
    drive: ``register`` arms the bindings, ``start`` begins listening,
    ``stop``/``unregister`` tear down. ``received_any_event`` is the AD-8
    introspection hook — on macOS a backend that registered but saw zero events
    is the signal to surface the Input-Monitoring / Accessibility grant hint.
    """

    def register(self, bindings: list[HotkeyBinding], on_event: OnEvent | None = None) -> None:
        """Arm ``bindings``. Must degrade (log + no-op), never raise."""
        ...

    def unregister(self) -> None:
        """Remove every binding this backend armed. Idempotent, never raises."""
        ...

    def start(self) -> None:
        """Begin listening for the armed bindings. Idempotent."""
        ...

    def stop(self) -> None:
        """Stop listening. Idempotent; safe to call without a prior ``start``."""
        ...

    def received_any_event(self) -> bool:
        """True once at least one bound chord has actually fired (AD-8)."""
        ...


def make_hotkey_backend() -> HotkeyBackend:
    """Select the hotkey backend for this host (AD-8).

    * ``win32`` → ``GlobalHotkeysBackend`` (relocated Windows logic, AD-7).
    * else if ``capabilities.has_hotkey`` → ``PynputBackend`` (macOS / Linux-X11).
    * else → ``NoopBackend`` (Wayland or no ``pynput``; logged-once no-op, AD-8).

    Never raises and never returns ``GlobalHotkeysBackend`` off Windows — the
    factory itself is the AD-6 graceful seam. The backend modules are imported
    lazily so importing this package on a host missing ``pynput`` /
    ``global_hotkeys`` stays clean (HN-7).
    """
    from jarvis.platform import detect_platform
    from jarvis.platform.capabilities import detect_capabilities

    platform = detect_platform()
    if platform == "win32":
        from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

        return GlobalHotkeysBackend()

    caps = detect_capabilities()
    if caps.has_hotkey:
        from jarvis.trigger.backends.pynput import PynputBackend

        return PynputBackend()

    from jarvis.trigger.backends.noop import NoopBackend

    return NoopBackend()


__all__ = [
    "HotkeyBackend",
    "HotkeyBinding",
    "OnEvent",
    "make_hotkey_backend",
]
