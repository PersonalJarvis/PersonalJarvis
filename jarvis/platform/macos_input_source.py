"""macOS keyboard-layout (TIS) main-thread guard (BUG-065).

macOS 15 enforces that the Text Input Source Services APIs
(``TISCopyCurrentKeyboardInputSource`` / ``TSMGetInputSourceProperty``,
HIToolbox) run on the MAIN thread: called from any other thread they trip
``dispatch_assert_queue`` and the OS kills the whole process with an
uncatchable SIGILL ("Personal Jarvis quit unexpectedly"). ``pynput`` calls
exactly these functions off-main — ``keyboard.Listener._run`` enters
``keycode_context()`` on the listener thread, and ``keyboard.Controller()``
builds its keycode map on whatever thread constructs it (the jarvis backend
thread) — so arming global hotkeys or the CU keyboard tool crashed the app.

The layout context pynput needs is a tiny immutable value:
``(keyboard_type: int, layout_data: bytes)``. So:

1. :func:`prime_keyboard_layout_cache` captures that tuple ONCE while
   provably on the main thread — direct ctypes, microseconds, no pynput
   import on the boot path (AP-26). Called from the desktop window
   chokepoint (``run_window_only``, the one place every boot path runs on
   the main thread) and opportunistically from any caller that happens to
   be on the main thread (headless boots).
2. :func:`install_pynput_layout_guard` patches pynput's ``keycode_context``
   so off-main callers reuse the cached tuple and never touch TIS; on the
   main thread the original still runs (and refreshes the cache). With no
   cache available an off-main call raises an honest ``RuntimeError``
   instead of letting the OS SIGILL the process — callers degrade (hotkeys
   off / pyautogui fallback) per AD-6.

Staleness (user switches keyboard layout after boot) is acceptable: pynput
itself snapshots the layout once per listener run, so the pre-fix behavior
was equally static; a restart refreshes the snapshot.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading

log = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
# ``(keyboard_type, layout_data)`` exactly as pynput's ``keycode_context``
# yields it; ``None`` until a main-thread capture succeeded.
_LAYOUT_CACHE: tuple[int | None, bytes | None] | None = None
_GUARD_INSTALLED = False


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def _capture_layout_context() -> tuple[int | None, bytes | None]:
    """Read ``(keyboard_type, layout_data)`` via raw ctypes.

    MUST run on the main thread (the whole point of this module). Mirrors
    pynput's ``keycode_context`` source preference: current keyboard input
    source first, ASCII-capable layout as fallback.
    """
    import ctypes  # noqa: PLC0415 - darwin-only, keep module import cheap
    import ctypes.util  # noqa: PLC0415

    carbon_path = ctypes.util.find_library("Carbon")
    cf_path = ctypes.util.find_library("CoreFoundation")
    if not carbon_path or not cf_path:
        raise OSError("Carbon / CoreFoundation framework not found")
    carbon = ctypes.cdll.LoadLibrary(carbon_path)
    cf = ctypes.cdll.LoadLibrary(cf_path)

    carbon.TISCopyCurrentKeyboardInputSource.argtypes = []
    carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
    carbon.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.argtypes = []
    carbon.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.restype = (
        ctypes.c_void_p
    )
    carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
    carbon.LMGetKbdType.argtypes = []
    carbon.LMGetKbdType.restype = ctypes.c_uint32
    layout_data_key = ctypes.c_void_p.in_dll(
        carbon, "kTISPropertyUnicodeKeyLayoutData"
    )
    cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFDataGetBytePtr.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]

    keyboard_type: int | None = None
    layout_data: bytes | None = None
    for source_fn in (
        carbon.TISCopyCurrentKeyboardInputSource,
        carbon.TISCopyCurrentASCIICapableKeyboardLayoutInputSource,
    ):
        source = source_fn()
        if not source:
            continue
        try:
            keyboard_type = int(carbon.LMGetKbdType())
            # Get-rule reference owned by ``source`` — copy the bytes out
            # BEFORE the CFRelease below.
            data = carbon.TISGetInputSourceProperty(source, layout_data_key)
            if data:
                length = cf.CFDataGetLength(data)
                ptr = cf.CFDataGetBytePtr(data)
                if ptr and length > 0:
                    layout_data = ctypes.string_at(ptr, length)
        finally:
            cf.CFRelease(source)
        if layout_data is not None:
            break
    return keyboard_type, layout_data


def keyboard_layout_cache_ready() -> bool:
    """True once a main-thread layout snapshot is cached (darwin only)."""
    return _LAYOUT_CACHE is not None


def _store_layout(context: tuple[int | None, bytes | None]) -> None:
    global _LAYOUT_CACHE
    # A context without layout bytes would NULL-deref inside UCKeyTranslate
    # later — only a complete snapshot is worth caching.
    if context[1] is None:
        return
    with _CACHE_LOCK:
        _LAYOUT_CACHE = context


def prime_keyboard_layout_cache() -> bool:
    """Snapshot the keyboard layout if we are on the darwin main thread.

    No-op ``True`` off darwin, no-op ``False`` off the main thread (the
    capture itself would trip the very assertion this module exists to
    avoid). Never raises.
    """
    if sys.platform != "darwin":
        return True
    if _LAYOUT_CACHE is not None:
        return True
    if not _on_main_thread():
        return False
    try:
        context = _capture_layout_context()
    except Exception:  # noqa: BLE001 - a failed probe must never crash boot
        log.warning(
            "Could not snapshot the macOS keyboard layout on the main "
            "thread — global hotkeys / keyboard actuation will be disabled "
            "to avoid the TIS off-main-thread crash.",
            exc_info=True,
        )
        return False
    _store_layout(context)
    if _LAYOUT_CACHE is None:
        log.warning(
            "macOS returned no keyboard layout data — global hotkeys / "
            "keyboard actuation will be disabled for this session."
        )
        return False
    return True


def install_pynput_layout_guard() -> bool:
    """Patch pynput's ``keycode_context`` with the main-thread guard.

    Idempotent; returns ``True`` when the guard is (already) installed.
    Off darwin this is a no-op ``True``. Never raises.
    """
    global _GUARD_INSTALLED
    if sys.platform != "darwin":
        return True
    if _GUARD_INSTALLED:
        return True
    try:
        from pynput._util import darwin as pynput_darwin  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - optional [desktop] extra absent
        log.debug("pynput not importable — layout guard not installed.")
        return False

    original_keycode_context = pynput_darwin.keycode_context

    @contextlib.contextmanager
    def _guarded_keycode_context():
        """Main thread: real TIS call (refreshes the cache). Off-main:
        cached snapshot only — TIS off-main is an uncatchable SIGILL."""
        if _on_main_thread():
            with original_keycode_context() as context:
                _store_layout(context)
                yield context
            return
        cached = _LAYOUT_CACHE
        if cached is None:
            raise RuntimeError(
                "macOS keyboard layout was not captured on the main thread; "
                "refusing to call the TIS APIs off-main (the OS would kill "
                "the process with SIGILL, BUG-065)."
            )
        yield cached

    pynput_darwin.keycode_context = _guarded_keycode_context
    # ``pynput.keyboard._darwin`` imports the symbol by name — rebind it
    # there too if that module is (or becomes) loaded.
    try:
        from pynput.keyboard import _darwin as pynput_kbd_darwin  # noqa: PLC0415

        pynput_kbd_darwin.keycode_context = _guarded_keycode_context
    except Exception:  # noqa: BLE001 - keyboard submodule may be absent
        log.debug(
            "pynput.keyboard._darwin not importable — util-level guard only.",
            exc_info=True,
        )
    _GUARD_INSTALLED = True
    return True


def ensure_pynput_layout_guard() -> bool:
    """Prime (when possible) + install the guard; ``True`` when pynput's
    keyboard paths are safe to use from any thread on this host."""
    if sys.platform != "darwin":
        return True
    prime_keyboard_layout_cache()
    if not install_pynput_layout_guard():
        return False
    return keyboard_layout_cache_ready()


def _reset_for_tests() -> None:
    """Drop the cache + installed flag — test-isolation hook only."""
    global _LAYOUT_CACHE, _GUARD_INSTALLED
    with _CACHE_LOCK:
        _LAYOUT_CACHE = None
    _GUARD_INSTALLED = False


__all__ = [
    "ensure_pynput_layout_guard",
    "install_pynput_layout_guard",
    "keyboard_layout_cache_ready",
    "prime_keyboard_layout_cache",
]
