"""JarvisBarOverlay — the slim Tk on-screen bar.

Implements the same duck-typed surface API ``OrbBusBridge`` already drives, so
the bridge is reused unchanged. ``show(mode)`` selects the renderer state;
``set_level`` writes ``_ext_level`` directly (an atomic float assignment, like
the orb). Text/mouth methods are deliberate no-ops — the bar shows no text.

Signals:
- LISTENING: the speech capture path publishes input RMS via ``mic_level``;
  ``OrbBusBridge`` forwards it to this surface without opening a second mic.
- SPEAKING: the audio player publishes its output RMS via ``level_tap``, which
  this surface subscribes to on ``start()``.
- THINKING: the renderer generates a synthetic wave (no external signal).

Threading mirrors the orb: a daemon thread runs the Tk mainloop; all Tk
mutations from the bus-subscriber thread go through ``_enqueue_ui`` → a queue
drained on the Tk thread. ``set_level`` is the sole exception (atomic write).

No ``SetWindowLong`` is ever called directly, but ``-topmost`` IS re-asserted
on every reveal (``_do_show``), and Windows can silently drop the layered
color-key/alpha on that kind of style mutation (BUG-030). A reveal therefore
maps at zero opacity, composes the prepared canvas, and only then restores the
configured opacity. This keeps Tk's opaque backing surface off-screen even
when the bar spent long enough withdrawn for the idle renderer to stop
repainting.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from jarvis.ui.jarvisbar import interaction, renderer

log = logging.getLogger("jarvis.ui.jarvisbar")

COLOR_KEY_HEX = "#FF00FF"
DRAG_THRESHOLD_PX = 16
MARGIN_PX = 12
# Gap above the taskbar (~0.2 cm) when anchoring at the default bottom-center.
TASKBAR_GAP_PX = 8
# Window opacity (the pill goes semi-transparent; magenta stays fully keyed
# out). Lower = more see-through. Tune this one number for the glass look.
BAR_ALPHA = 0.6

# A topmost *flag* is not enough on Windows.  A mapped Tk window can retain
# WS_EX_TOPMOST while falling below ordinary windows in the real Z-order band
# (observed after the desktop/main window mapped over the boot-gated bar).  The
# guard repairs the native band regularly without activating, moving, or
# resizing the bar.  On macOS and X11 the same loop refreshes Tk's documented
# ``-topmost`` window-manager request.  Half a second keeps a newly mapped app
# from covering the bar perceptibly while adding negligible work.
Z_ORDER_GUARD_INTERVAL_MS = 500

# SetWindowPos flags used by the Win32 repair.  Deliberately omit SWP_NOZORDER:
# HWND_TOPMOST must be applied to the actual Z-order, not merely cached as a
# style bit.  SWP_NOACTIVATE is the important UX contract: the bar never steals
# keyboard focus from the app the user just opened.
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_NOOWNERZORDER = 0x0200
_GW_HWNDPREV = 3
_GWL_EXSTYLE = -20
_WS_EX_TOPMOST = 0x00000008
_WIN32_TOPMOST_FLAGS = (
    _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER
)

# Sound-driven look. The bar shows the speaking equalizer (bars) ONLY while real
# audio is present — mic input while you speak, or TTS output while Jarvis speaks
# — and the thinking wave during silence (brain thinking AND the silent
# TTS-synthesis lead-in). This tracks actual sound instead of the supervisor
# state, which is unreliable here (continue-listening flips SPEAKING→LISTENING
# mid-playback). AUDIBLE_LEVEL is the normalized 0..1 level above which a
# set_level() counts as "sound now"; AUDIBLE_HOLD_S keeps the bars up across the
# short word/sentence gaps so they don't flap back to the wave on every pause.
AUDIBLE_LEVEL = 0.06
AUDIBLE_HOLD_S = 0.5

# Frame-loop revival watchdog (anti-freeze). The animation re-arms only from its
# own tail, so any single silent break (a swallowed after() failure, an exception
# before the try/finally, a one-off Tk hiccup) would kill it permanently while
# the Tk mainloop keeps running — the bar stays visible, frozen on its last
# frame. A SECOND, independent after-chain (``_schedule_frame_watchdog``) watches
# a per-frame heartbeat (``_last_frame_ns``) and kicks the frame loop back to life
# when it goes stale. It renders nothing, so it is immune to the render/PIL faults
# that kill the frame loop. The threshold is far above the 16 ms tick, so a
# continuously-stamped heartbeat can never false-fire while the loop is ticking.
WATCHDOG_INTERVAL_MS = 1000
FRAME_STALL_THRESHOLD_NS = 2_000_000_000  # 2 s of silence ⇒ the loop is dead

# Frame pacing (BUG: "JarvisBar stutters" forensic, 2026-07-10). A 41s GIF
# capture frame-diffed to only ~5 visual updates/second on average, in a
# burst-then-freeze pattern (freezes up to 1.25s). Measured root causes, on
# this machine:
#
# 1. The bar's window style itself (frameless + ``-topmost`` + a color-key
#    ``-transparentcolor`` + a translucent ``-alpha``) is a Windows *layered*
#    window; every ``ImageTk.PhotoImage``/``itemconfig`` swap makes DWM
#    recomposite it. A benchmark that swaps a CONSTANT-COLOR image on an
#    identically-styled window (zero render work) still only reached ~31
#    updates/s against an ``after(16)`` (60fps) schedule — a ~15ms fixed
#    per-tick compositing tax, independent of what gets drawn. So the 60fps
#    target was already unreachable on this window style even at zero cost.
#    The idle pill draws NOTHING beyond its at-rest background (see
#    ``renderer.render``'s idle branch), so idle-at-rest frames are paying
#    that compositing tax for a BYTE-IDENTICAL image every single tick — pure
#    waste. ``_IDLE_SETTLE_TICKS`` below skips the render/PhotoImage/
#    itemconfig work once the resting pill has visibly settled.
# 2. A background thread holding the GIL (simulating wake-poll/other Python
#    work sharing this process) collapsed the SAME loop to ~4-5 renders/s
#    with 250-360ms gaps — matching the GIF's freeze pattern. This is GIL/CPU
#    contention from OTHER threads in the process; no delay-scheduling choice
#    made here can prevent it (a Windows thread-priority raise for the Tk
#    thread was benchmarked too and showed no measurable improvement against
#    pure GIL contention, so it is deliberately NOT used). What this loop CAN
#    do is shrink its own contribution to that contention — the idle-skip
#    above is the main lever, and adaptive pacing (below) prevents an
#    occasional slow render (measured up to ~30ms in "think"+hovered mode)
#    from compounding into an even longer visible gap.
#
# Idle-static skip: EVERY branch renderer.render() can reach while the coarse
# mode is "idle" is time-independent (the empty resting pill, the hovered
# idle pill's static mic glyph, the muted idle pill's static mic glyph — the
# equalizer bars and the close-X both require an active/listen/speak/think
# mode, never reached while idle). So once the resting pill's eased size has
# stopped changing (ease() with factor 0.5 converges to sub-pixel precision
# within a handful of ticks; 30 is a generous margin) for a given
# (hover, mute) combination, every further tick would repaint the exact same
# pixels regardless of hover/mute. Skipped ticks still stamp the heartbeat
# and re-arm the loop, so the watchdog/self-healing contract is untouched —
# only the render()/PhotoImage()/itemconfig() work is skipped.
_IDLE_SETTLE_TICKS = 30

# Adaptive pacing: the nominal target stays 16ms (~60fps aspirational — the
# real ceiling is lower, see above), but the actual next delay is derived
# from how long THIS tick took, not a blind constant. In the common case
# (render cost << 16ms) this is indistinguishable from the old fixed delay.
# It only matters after an unusually slow tick, where it schedules the next
# one sooner instead of adding a full 16ms on top of the overrun — this
# bounds how much a single slow frame can widen the gap to the next one.
TARGET_FRAME_MS = 16
MIN_FRAME_DELAY_MS = 1

# Once the close-X is accepted, suppress rapid follow-up clicks long enough for
# the authoritative IDLE state to arrive. Without this guard a double-click can
# hit the same screen position after the optimistic visual collapse and be
# reinterpreted as an idle-body click that immediately starts a new session.
HANGUP_CLICK_GUARD_S = 1.0


def _primary_work_area() -> tuple[int, int, int, int] | None:
    """Primary-monitor work area (left, top, right, bottom) EXCLUDING the
    taskbar, via Win32 ``SPI_GETWORKAREA``. None off Windows / on failure so
    the caller falls back to a full-screen anchor.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        SPI_GETWORKAREA = 0x0030
        rect = wintypes.RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
        if not ok:
            return None
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:  # noqa: BLE001
        return None


def _create_hidden_tk_root(tk: Any) -> Any:
    """Create a Tk root without mapping its platform-default backing window.

    ``tk.Tk()`` starts with a default geometry (roughly 400x300 physical pixels
    on a 150% Windows desktop). Configuring geometry and a color key afterwards
    can leave that original DWM surface eligible for a flash during a later
    layered-window style mutation. Withdraw synchronously so only the fully
    configured Jarvis Bar can ever be mapped.
    """
    root = tk.Tk()
    root.withdraw()
    return root


def _win32_force_topmost(root: Any, *, user32: Any | None = None) -> bool:
    """Put a Tk toplevel in Win32's real topmost Z-order band.

    Tk's ``winfo_id()`` is the inner ``TkChild`` HWND on Windows.  Window
    manager operations must target its ``TkTopLevel`` parent, so resolve that
    wrapper first and fall back to the supplied handle only when no parent is
    present.  The optional ``user32`` seam keeps the native call unit-testable
    on every host.  Returns ``False`` on unsupported hosts or any native error.
    """
    if user32 is None and sys.platform != "win32":
        return False

    hwnd_topmost: Any = -1
    try:
        if user32 is None:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            hwnd_topmost = wintypes.HWND(-1)

        inner_hwnd = int(root.winfo_id())
        outer_hwnd = int(user32.GetParent(inner_hwnd) or inner_hwnd)
        return bool(
            user32.SetWindowPos(
                outer_hwnd,
                hwnd_topmost,
                0,
                0,
                0,
                0,
                _WIN32_TOPMOST_FLAGS,
            )
        )
    except Exception:  # noqa: BLE001 - the overlay must degrade, never crash
        return False


def _win32_topmost_band_is_healthy(
    root: Any, *, user32: Any | None = None
) -> bool | None:
    """Check whether any ordinary visible HWND sits above the Jarvis Bar.

    ``WS_EX_TOPMOST`` on the bar itself cannot answer this: the reported bug
    retained that bit while the real Z-order placed three non-topmost windows
    above it.  Walking the windows above the bar detects the actual band.  A
    ``None`` result means the native check was unavailable, so the caller may
    conservatively attempt a repair.
    """
    if user32 is None and sys.platform != "win32":
        return None

    try:
        get_window_long: Any
        if user32 is None:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetWindow.restype = wintypes.HWND
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            get_window_long = getattr(user32, "GetWindowLongPtrW", None)
            if get_window_long is None:
                get_window_long = user32.GetWindowLongW
            get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_window_long.restype = ctypes.c_ssize_t
        else:
            get_window_long = getattr(user32, "GetWindowLongPtrW", None)
            if get_window_long is None:
                get_window_long = user32.GetWindowLongW

        inner_hwnd = int(root.winfo_id())
        outer_hwnd = int(user32.GetParent(inner_hwnd) or inner_hwnd)
        above = int(user32.GetWindow(outer_hwnd, _GW_HWNDPREV) or 0)
        visited: set[int] = set()
        while above and above not in visited and len(visited) < 512:
            visited.add(above)
            if bool(user32.IsWindowVisible(above)):
                ex_style = int(get_window_long(above, _GWL_EXSTYLE))
                if not ex_style & _WS_EX_TOPMOST:
                    return False
            above = int(user32.GetWindow(above, _GW_HWNDPREV) or 0)
        if above:
            return None  # corrupt/cyclic chain or an implausibly large walk
        return True
    except Exception:  # noqa: BLE001 - health probing must never break the bar
        return None


class JarvisBarOverlay:
    def __init__(
        self,
        persistent: bool = True,
        accent: str = "#e7c46e",
        opacity: float = BAR_ALPHA,
        startup_gated: bool = False,
    ) -> None:
        self._persistent = persistent
        self._accent = accent
        self._opacity = max(0.2, min(1.0, float(opacity)))  # clamp to sane range
        # The desktop boot path constructs and paints the bar early, while it is
        # still withdrawn, then releases this gate on the honest voice-usable
        # signal. This is stronger than an initial ``withdraw``: every early
        # IDLE/wake/session ``show()`` is suppressed too, so no event can make the
        # bar advertise a voice stack that is still warming. Runtime-created
        # surfaces leave the opt-in gate off and retain their immediate behavior.
        self._startup_gated = bool(startup_gated)
        self._mode = "idle"
        self._ext_level = 0.0
        # perf_counter() of the last set_level() that carried real sound
        # (>= AUDIBLE_LEVEL). Drives the wave↔bars choice in _schedule_frame.
        # 0.0 = "long ago" → starts on the wave, not the bars.
        self._last_audible_t = 0.0
        # monotonic_ns() stamped on every frame tick (alive or dropped). The
        # revival watchdog compares against this to tell a living loop from a
        # silently-dead one. 0 = "no frame has run yet" → the watchdog holds off.
        self._last_frame_ns = 0
        # Idle-static skip bookkeeping (see _IDLE_SETTLE_TICKS docstring above
        # _schedule_frame): tracks how many consecutive ticks have shared the
        # same (effective_mode, hovered, muted) key, so a settled idle pill's
        # repaint can be skipped once its eased size has stopped moving.
        self._static_tick_key: tuple[str, bool, bool] | None = None
        self._static_tick_count = 0
        self._root: Any = None
        self._canvas: Any = None
        self._renderer: renderer.JarvisBarRenderer | None = None
        self._photo: Any = None
        self._image_id: Any = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._started = threading.Event()
        self._running = False
        self._tk_thread_id: int | None = None
        self._t0 = 0.0
        self._x = 0
        self._y = 0
        self._drag: dict | None = None
        self._level_unsub: Callable[[], None] | None = None
        self._on_mute_toggle: Callable[[], None] | None = None
        self._feedback_publisher: Callable[[str, dict], None] | None = None
        self._on_show_window: Callable[[], None] | None = None
        self._hovered = False  # mouse over the bar → reveal the close cross
        # True only on a macOS root whose "-transparent" attribute took; the
        # frame loop then converts frames to RGBA (renderer.key_to_alpha).
        self._mac_transparent = False
        self._hangup_click_block_until = 0.0
        # Local mirror of the global voice-mute state (mic muted FOR JARVIS only).
        # Flipped optimistically on a mic-button click and reconciled by the
        # authoritative VoiceMuteChanged via set_muted(). A bool write is atomic
        # under the GIL, like _ext_level — read on the frame loop without a lock.
        self._muted = False

    # ------------------------------------------------------------------ #
    # Surface API consumed by OrbBusBridge                               #
    # ------------------------------------------------------------------ #
    def show(self, mode: str = "listen") -> None:
        if mode not in renderer.MODES:
            return
        self._mode = mode
        if self._root is None:
            return
        # Keep accepting state updates while boot is warming so release can show
        # the latest correct mode, but never map the native window before the
        # voice-usable signal. This guard closes the historical bypass where a
        # wake candidate revealed a merely start-withdrawn bar.
        if getattr(self, "_startup_gated", False):
            return
        if not self._persistent and mode == "idle":
            self._enqueue_ui(self._do_hide)
        else:
            self._enqueue_ui(self._do_show)

    def hide(self) -> None:
        # The bridge only calls hide() on a NON-persistent bar — it is wired
        # with hide_on_idle=not persistent, so a persistent bar receives
        # show("idle") instead and never this. swap_overlay also calls hide()
        # to force-withdraw on a style switch, so there is no persistent gate
        # here; the gate lives in the bridge wiring.
        if self._root is None:
            return
        self._enqueue_ui(self._do_hide)

    def reassert_z_order(self) -> None:
        """Re-pin an already-visible bar without treating it as a reveal.

        Wake/state updates call ``show`` repeatedly while a persistent bar is
        already mapped. Those updates must not mutate the native layered-window
        styles. The one deliberate post-boot repair uses this explicit method.
        """
        if self._root is None:
            return
        if getattr(self, "_startup_gated", False):
            return
        self._enqueue_ui(self._do_reassert_z_order)

    def release_startup_gate(self) -> bool:
        """Allow the boot-created bar to become visible exactly once.

        Returns ``True`` only when this call released an active gate. The latest
        mode has continued to track bus events while hidden, so a persistent bar
        is revealed in that mode rather than being reset to idle. A
        non-persistent bar remains withdrawn while idle and can pop normally on
        its next real session.
        """
        if not getattr(self, "_startup_gated", False):
            return False
        self._startup_gated = False
        if self._root is None:
            return True
        if not self._persistent and self._mode == "idle":
            return True
        self._enqueue_ui(self._do_show)
        return True

    def set_level(self, level: float) -> None:
        # Direct atomic write (no enqueue) — matches OrbOverlay.set_level.
        lv = 0.0 if level < 0.0 else 1.0 if level > 1.0 else float(level)
        self._ext_level = lv
        # Remember WHEN real sound last arrived (mic or TTS, both feed here via
        # their level taps). _schedule_frame uses this to show bars while sound
        # is present and the wave during silence. Atomic float write, like
        # _ext_level — safe from the audio/VAD threads with no lock.
        if lv >= AUDIBLE_LEVEL:
            self._last_audible_t = time.perf_counter()

    # The bar has no text bubble and no mouth — these stay no-ops so the
    # bridge's duck-typed calls remain safe.
    def play_animation(self, name: str, **params: Any) -> None: ...
    def stop_animation(self, name: str) -> None: ...
    def show_listening_transcript(self, text: str = "", duration_ms: int = 30000) -> None: ...
    def hide_comment(self) -> None: ...
    def start_mouth_animation(self, duration_ms: int = 60000) -> None: ...
    def stop_mouth_animation(self) -> None: ...

    def set_muted(self, muted: bool) -> None:
        """Mirror the pipeline's authoritative voice-mute state onto the bar.

        Called by OrbBusBridge on every VoiceMuteChanged (from this bar, the
        mascot, or a voice command), so the red rim + slashed-mic icon stay in
        lock-step with reality regardless of where the toggle originated. Plain
        atomic bool write — the frame loop reads it; no Tk marshal needed."""
        self._muted = bool(muted)

    def set_on_mute_toggle(self, callback: Callable[[], None] | None) -> None:
        self._on_mute_toggle = callback

    def set_feedback_publisher(self, callback: Callable[[str, dict], None] | None) -> None:
        self._feedback_publisher = callback

    def set_on_show_window(self, callback: Callable[[], None] | None) -> None:
        """Register the right-click → raise-main-window callback (set by
        OrbBusBridge, which publishes ``ShowWindowRequested`` on fire)."""
        self._on_show_window = callback

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #
    def _should_start_withdrawn(self) -> bool:
        """True while boot-gated or for the non-persistent variant.

        The boot-created persistent bar is fully configured and painted while
        withdrawn, then mapped by ``release_startup_gate`` once voice is usable.
        Other persistent bars keep their immediate-map behavior.
        """
        return (not self._persistent) or getattr(self, "_startup_gated", False)

    def start_in_thread(self, timeout: float = 3.0) -> None:
        if sys.platform == "darwin":
            # Aqua-Tk (like AppKit) is main-thread-only on macOS: creating the
            # Tk root on this worker thread aborts the WHOLE process with a
            # native, uncatchable assertion — the "Python quit unexpectedly"
            # first-boot crash (BUG-057, same class as the BUG-056 tray). The
            # desktop boot substitutes a NullOverlay before ever reaching
            # this; the gate is the backstop for any other caller.
            log.info(
                "JarvisBar not started: macOS allows Tk windows on the main "
                "thread only — running without the on-screen bar."
            )
            return

        def _run() -> None:
            try:
                self.start()
            except Exception:  # noqa: BLE001
                log.exception("JarvisBar thread start failed")

        t = threading.Thread(target=_run, name="jarvisbar-tk-mainloop", daemon=True)
        t.start()
        if not self._started.wait(timeout=timeout):
            log.error("JarvisBar window not initialised within %.1fs", timeout)

    def start(self) -> None:
        import tkinter as tk

        from PIL import ImageTk  # noqa: F401 — fail fast here if Pillow missing

        # DPI strategy (two steps, order matters — both no-ops off Windows):
        #
        # 1. ``ensure_dpi_awareness()`` — the PROCESS must be DPI-aware before
        #    the per-window pin below can hold. Also fixes the HiDPI
        #    "drag-teleport" (geometry vs pointer-event space drift) when the
        #    bar wins the boot race.
        # 2. ``pin_thread_dpi_per_monitor()`` — pin THIS thread (the bar's
        #    dedicated Tk mainloop thread) so the Tk root created below
        #    carries a PER-WINDOW per-monitor context: the bar renders its
        #    RAW pixels (its original, maintainer-approved look) on every
        #    monitor, DWM never bitmap-scales it, and pywebview's runtime
        #    ``SetProcessDPIAware()`` flip can no longer re-interpret it.
        #    Without the pin the window follows the PROCESS context: it
        #    DWM-shrank to ~2/3 when moved onto the 100 % secondary monitor
        #    (next to the 150 % primary) and jumped size/position on the
        #    boot-race flip (GIF forensics 2026-07-02).
        #
        # NEVER pin UNAWARE here: Windows then upscales the bar into a blurry
        # oversized "fat bar" — explicitly rejected by the maintainer twice
        # (2026-07-01 session, commit 5c7a5d15). The original look is RAW
        # pixels at a fixed size on every monitor.
        try:
            from jarvis.core.win32_dpi import (
                ensure_dpi_awareness,
                pin_thread_dpi_per_monitor,
            )

            ensure_dpi_awareness()
            if pin_thread_dpi_per_monitor():
                log.debug("jarvisbar Tk thread pinned PER_MONITOR_AWARE (per-window)")
        except Exception:  # noqa: BLE001 — never block the bar on a DPI hiccup
            log.debug("jarvisbar DPI-awareness setup skipped", exc_info=True)

        self._tk_thread_id = threading.get_ident()
        self._renderer = renderer.JarvisBarRenderer(accent=self._accent)

        # Some window managers map Tk's default-size root eagerly. Hide it
        # before assigning ``self._root`` or applying any styles so that stale
        # backing surface can never flash at the top-left on a later wake.
        root = _create_hidden_tk_root(tk)
        self._root = root
        root.title("JarvisBar")
        # Give the bar the Jarvis mascot icon on every OS. Tk otherwise inherits
        # the interpreter's process icon (pythonw.exe → Python logo on Windows,
        # python3 on Linux); if this frameless window ever surfaces on the
        # taskbar/dock it would advertise itself as plain Python
        # (BUG #UI-Pin-2026-05-05). Best-effort — never blocks the bar.
        try:
            from jarvis.ui.icon_utils import apply_tk_window_icon

            apply_tk_window_icon(root)
        except Exception:  # noqa: BLE001 — the bar is cosmetic; never crash on it
            log.debug("jarvisbar icon setup skipped", exc_info=True)
        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        # Per-pixel transparency, per platform: Windows keys out the magenta
        # color key on the layered window; macOS has no color key, so there
        # the WINDOW itself becomes transparent (Aqua-Tk's "-transparent" +
        # the systemTransparent background) and every frame carries a real
        # alpha channel instead (renderer.key_to_alpha in the frame loop).
        self._mac_transparent = False
        if sys.platform == "darwin":
            try:
                root.wm_attributes("-transparent", True)
                root.configure(bg="systemTransparent")
                self._mac_transparent = True
            except tk.TclError:
                log.warning(
                    "macOS -transparent unsupported — bar will show its key colour"
                )
                root.configure(bg=COLOR_KEY_HEX)
        else:
            try:
                root.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
            except tk.TclError:
                log.warning(
                    "transparentcolor unsupported — bar will show its key colour"
                )
            root.configure(bg=COLOR_KEY_HEX)
        # Window-level alpha ON TOP of the color key: the magenta stays fully
        # keyed out (verified — no bleed) while the pill itself goes
        # semi-transparent, so the desktop shows through it (Wispr-like).
        try:
            root.wm_attributes("-alpha", self._opacity)
        except tk.TclError:
            log.debug("window -alpha unsupported", exc_info=True)

        self._resolve_position(root)
        root.geometry(f"{renderer.WIN_W}x{renderer.WIN_H}+{self._x}+{self._y}")

        self._canvas = tk.Canvas(
            root,
            width=renderer.WIN_W,
            height=renderer.WIN_H,
            bg="systemTransparent" if self._mac_transparent else COLOR_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<ButtonPress-3>", self._on_right_click)
        self._canvas.bind("<Enter>", self._on_enter)
        self._canvas.bind("<Leave>", self._on_leave)

        # Drag-drop onto the bar (desktop extra, cross-platform via tkdnd).
        # TEMPORARILY DISABLED 2026-06-23 (wake-fix session): on this frameless
        # color-key topmost window, tkdnd's ``_require(root)`` + drop_target_register
        # injected PHANTOM mouse press/release events on every turn mode-switch,
        # which the click handler read as a close-X click -> a ``request_hangup``
        # STORM (6+/turn) that aborted every voice answer mid-thought (Hangup during
        # thinking). The file-drop-onto-bar feature is purely additive — the web
        # dock (POST /api/chat/drop) carries it on every OS — so disabling JUST the
        # bar registration restores voice without losing the capability. Re-enable
        # once the tkdnd phantom-event issue on the color-key window is resolved.
        if False:  # noqa: SIM223 — intentional kill-switch (see note above)
            try:
                from jarvis.overlay.drop_bridge import dispatch_drop
                from jarvis.overlay.drop_target import make_drop_target

                make_drop_target().register(self._canvas, dispatch_drop)
            except Exception:  # noqa: BLE001 — drop is optional; never block bar boot.
                log.debug("bar drop target registration skipped", exc_info=True)

        try:
            from jarvis.audio import level_tap

            self._level_unsub = level_tap.subscribe(self.set_level)
        except Exception:  # noqa: BLE001
            log.debug("level_tap subscribe failed", exc_info=True)

        self._running = True
        self._t0 = time.perf_counter()
        # Paint a complete first frame while the root is still withdrawn.
        self._schedule_frame()
        self._schedule_ui_queue()
        self._schedule_frame_watchdog()  # independent anti-freeze revival loop
        self._schedule_z_order_guard()
        if not self._should_start_withdrawn():
            self._do_show()
        self._started.set()
        root.mainloop()

    def stop(self) -> None:
        self._running = False
        if self._level_unsub is not None:
            try:
                self._level_unsub()
            except Exception:  # noqa: BLE001
                log.debug("level_tap unsubscribe failed", exc_info=True)
            self._level_unsub = None
        root = self._root
        if root is not None:
            try:
                root.after(0, root.destroy)
            except Exception:  # noqa: BLE001
                log.debug("jarvisbar destroy failed", exc_info=True)

    # ------------------------------------------------------------------ #
    # Tk-thread internals                                                #
    # ------------------------------------------------------------------ #
    def _resolve_position(self, root: Any) -> None:
        try:
            sw = int(root.winfo_screenwidth())
            sh = int(root.winfo_screenheight())
        except Exception:  # noqa: BLE001
            sw, sh = 1920, 1080
        pos: tuple[int, int] | None = None
        try:
            from jarvis.core.config_writer import DEFAULT_CONFIG_FILE

            pos = interaction.load_jarvisbar_position(DEFAULT_CONFIG_FILE)
        except Exception:  # noqa: BLE001
            pos = None
        if pos is not None:
            self._x, self._y = interaction.clamp_to_screen(
                pos[0], pos[1], screen_w=sw, screen_h=sh,
                bar_w=renderer.WIN_W, bar_h=renderer.WIN_H, margin=MARGIN_PX,
            )
        else:
            # Anchor just ABOVE the taskbar (work area), exactly centered —
            # not on the taskbar. Fall back to the full-screen bottom if the
            # work area is unavailable (non-Windows / query failure).
            wa = _primary_work_area()
            if wa is not None:
                wl, wt, wr, wb = wa
                self._x = wl + (wr - wl - renderer.WIN_W) // 2
                self._y = wb - renderer.WIN_H - TASKBAR_GAP_PX
            else:
                self._x, self._y = interaction.default_bottom_center(
                    screen_w=sw, screen_h=sh,
                    bar_w=renderer.WIN_W, bar_h=renderer.WIN_H, margin=MARGIN_PX,
                )

    def _do_show(self) -> None:
        if self._root is None:
            return
        # A persistent bar is already mapped when wake/state events arrive.
        # Re-running deiconify/topmost/transparentcolor is not a harmless no-op
        # on Windows: it mutates a layered HWND and can resurrect Tk's original
        # default-size opaque backing surface at the top-left. The renderer reads
        # ``self._mode`` directly, so a mapped window needs no native operation.
        try:
            if bool(self._root.winfo_ismapped()):
                # Keep the no-native-mutation contract for a persistent mapped
                # bar, but still submit one fresh canvas frame. This is the
                # cheapest self-heal if DWM discarded a layered surface during
                # a display/power transition while the logical HWND stayed
                # mapped.
                self._invalidate_static_frame()
                return
        except Exception:  # noqa: BLE001
            # Unusual Tk shims may not expose the query. Prefer an attempted
            # reveal over leaving a genuinely hidden bar unavailable.
            log.debug("jarvisbar mapped-state query failed", exc_info=True)

        # Reveal transaction (BUG-030): the boot-created bar can remain
        # withdrawn for many seconds while voice warms. Its idle renderer has
        # settled by then and deliberately skips byte-identical repaints. If a
        # withdrawn -> deiconified/topmost transition makes DWM discard the
        # prepared layered surface, mapping at the configured opacity exposes
        # Tk's true opaque backing rectangle indefinitely -- there is no idle
        # repaint left to replace it. Keep the HWND fully invisible during the
        # native style changes, compose the already-painted canvas once, then
        # restore the requested opacity. Every call is best-effort so a cosmetic
        # platform limitation can never block the app.
        self._apply_layered_attributes(opacity=0.0)
        try:
            self._root.deiconify()
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar deiconify failed", exc_info=True)
        self._do_reassert_z_order(opacity=0.0)
        self._refresh_prepared_frame()
        try:
            # Flush geometry/map/canvas work while the window is still fully
            # transparent. The first visible DWM composition therefore already
            # contains the magenta color-key frame instead of a black backing.
            self._root.update_idletasks()
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar reveal composition flush failed", exc_info=True)
        finally:
            self._apply_layered_attributes(opacity=self._opacity)

    def _apply_layered_attributes(self, *, opacity: float) -> None:
        """Best-effort color-key + opacity application on the Tk thread."""
        if self._root is None:
            return
        if sys.platform != "darwin":
            # The color key is a Windows layered-window concept (BUG-030
            # re-assert); macOS transparency is the immutable "-transparent"
            # attribute set at creation — nothing to re-assert there.
            try:
                self._root.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
            except Exception:  # noqa: BLE001
                log.debug(
                    "jarvisbar transparentcolor re-assert failed", exc_info=True
                )
        try:
            self._root.wm_attributes("-alpha", opacity)
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar alpha re-assert failed", exc_info=True)

    def _invalidate_static_frame(self) -> None:
        """Force the next animation tick to repaint even when idle settled."""
        self._static_tick_key = None
        self._static_tick_count = 0

    def _refresh_prepared_frame(self) -> None:
        """Resubmit the hidden pre-render to Tk before reveal becomes visible."""
        if self._canvas is None or self._image_id is None or self._photo is None:
            return
        try:
            self._canvas.itemconfig(self._image_id, image=self._photo)
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar prepared-frame refresh failed", exc_info=True)

    def _do_reassert_z_order(self, *, opacity: float | None = None) -> None:
        if self._root is None:
            return
        # Re-assert topmost after every reveal. On Windows this must use
        # SetWindowPos(HWND_TOPMOST): a live forensic found WS_EX_TOPMOST still
        # set while the HWND sat *below* three ordinary windows in the actual
        # Z-order. Repeating Tk's already-true attribute was a no-op and lift()
        # only raised within the wrong band. Other desktop window managers use
        # Tk's portable -topmost request + lift fallback.
        self._do_pin_topmost()
        # BUG-030 guard: re-asserting ``-topmost`` is itself a Win32 style
        # mutation on this layered (color-key + alpha) window, and Windows can
        # silently drop the layered attributes on such a mutation — the bar
        # then briefly renders its true opaque black backing surface instead of
        # the keyed-out magenta until the next repaint ("black border flashes
        # around the bar, then disappears" forensic, 2026-06-30). Re-apply both
        # exactly as set at creation so a dropped attribute self-heals on the
        # very next reveal instead of needing an app restart. Guarded
        # separately so a failure here can never undo the topmost re-assert.
        self._apply_layered_attributes(
            opacity=self._opacity if opacity is None else opacity
        )
        # A long-withdrawn idle bar may already be in the static-frame fast
        # path. Native style/map changes require one fresh canvas submission so
        # DWM cannot keep a stale backing surface indefinitely.
        self._invalidate_static_frame()

    def _do_pin_topmost(self) -> str:
        """Repair topmost ordering without moving or focusing the bar.

        Returns the strategy used (``"native"``, ``"tk"``, or ``"failed"``)
        so the periodic guard can heal layered attributes only when the Win32
        native path was unavailable and Tk had to mutate the window style.
        """
        if self._root is None:
            return "failed"
        if sys.platform == "win32" and _win32_force_topmost(self._root):
            return "native"
        try:
            self._root.wm_attributes("-topmost", True)
            self._root.lift()
            return "tk"
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar lift/topmost re-assert failed", exc_info=True)
            return "failed"

    def _do_hide(self) -> None:
        if self._root is None:
            return
        try:
            self._root.withdraw()
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar withdraw failed", exc_info=True)

    def _enqueue_ui(self, fn: Callable[[], None]) -> None:
        if self._root is None:
            return
        if self._tk_thread_id == threading.get_ident():
            fn()
            return
        self._ui_queue.put(fn)

    def _schedule_ui_queue(self) -> None:
        if not self._running or self._root is None:
            return
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("JarvisBar UI command failed")
        self._root.after(20, self._schedule_ui_queue)

    def _schedule_frame(self) -> None:
        if not self._running or not self._root or not self._canvas or not self._renderer:
            return
        from PIL import ImageTk

        # SELF-HEALING LOOP. This after-loop re-arms ONLY from its own tail, so a
        # single transient render/Tk error (ImageTk.PhotoImage raising, a TclError
        # during a window move, a one-off PIL glitch) used to skip the re-arm and
        # the animation died PERMANENTLY — the Tk mainloop kept running, so the
        # window stayed visible frozen on its last frame until an app restart
        # ("JarvisBar stopped moving" forensic). The render body is now wrapped so
        # one bad frame is dropped, logged, and the next tick is still armed in
        # `finally`. The loop can no longer be killed by any one exception.
        tick_started = time.perf_counter()
        try:
            now = tick_started
            t = now - self._t0
            # Sound-driven look: bars while audio is present (mic OR TTS), wave
            # while silent. The coarse self._mode only decides active-vs-idle; the
            # actual wave↔bars choice comes from how recently real sound arrived.
            # This makes the silent TTS-synthesis lead-in render as the thinking
            # wave and real speech (in or out) render as the equalizer —
            # independent of the supervisor state's continue-listening flips.
            from jarvis.audio import level_tap

            playing = level_tap.playback_active()
            effective_mode = renderer.visual_mode(
                self._mode,
                now - self._last_audible_t,
                hold_s=AUDIBLE_HOLD_S,
                playback_active=playing,
            )

            # Idle-static skip (see _IDLE_SETTLE_TICKS docstring above): every
            # branch renderer.render() can reach while ``effective_mode ==
            # "idle"`` is time-INDEPENDENT — the empty resting pill, the
            # hovered idle pill (only draws the static mic glyph; the close-X
            # and equalizer bars require an active/listen/speak mode, never
            # reached here), and the muted idle pill (same static mic glyph)
            # all draw nothing that depends on ``t``. The only thing that
            # still moves per tick is the eased pill size/color, which
            # converges (ease() factor 0.5) well within _IDLE_SETTLE_TICKS
            # ticks of any (hover, mute) combination changing. So once idle
            # has been static for that long, EVERY further tick — hovered or
            # muted or neither — would repaint byte-identical pixels; skip
            # the render/PhotoImage/itemconfig work entirely. Any change in
            # mode, hover, or mute resets the counter, so a real transition
            # (including a hover/mute flip) always renders immediately.
            tick_key = (effective_mode, self._hovered, self._muted)
            if tick_key != self._static_tick_key:
                self._static_tick_key = tick_key
                self._static_tick_count = 0
            else:
                self._static_tick_count += 1
            is_settled_idle = (
                effective_mode == "idle"
                and self._static_tick_count >= _IDLE_SETTLE_TICKS
            )

            if not is_settled_idle:
                # The level is fed live per ~60 ms TTS sub-block
                # (player._write_samples), so the equalizer reacts to Jarvis's
                # actual loudness — thin and lively, exactly like it reacts to
                # your mic. No synthetic floor (that made the bars look
                # uniformly blocky).
                img = self._renderer.render(
                    t, effective_mode, self._ext_level,
                    hovered=self._hovered, muted=self._muted,
                )
                if getattr(self, "_mac_transparent", False):
                    # macOS: no color key — carry real per-pixel alpha instead.
                    img = renderer.key_to_alpha(img)
                # PhotoImage must be retained on self, else Tk GCs it before
                # drawing.
                self._photo = ImageTk.PhotoImage(img)
                if self._image_id is None:
                    self._image_id = self._canvas.create_image(
                        0, 0, anchor="nw", image=self._photo
                    )
                else:
                    self._canvas.itemconfig(self._image_id, image=self._photo)
        except Exception:  # noqa: BLE001 — one bad frame must never freeze the bar
            log.exception("JarvisBar frame render failed — dropping one frame")
        finally:
            # Heartbeat first: a dropped-but-rearmed frame is still a LIVING loop,
            # so we stamp even on the failure path. The watchdog reads this to
            # tell alive from silently-dead.
            self._last_frame_ns = time.monotonic_ns()
            # Adaptive pacing: derive the next delay from how long THIS tick
            # actually took instead of always adding a flat 16ms on top. In the
            # common case (tick cost << target) this is indistinguishable from
            # the old fixed delay; it only shortens the next wait after an
            # unusually slow tick, so a single slow render can't compound into
            # an even longer visible gap (see TARGET_FRAME_MS docstring above).
            elapsed_ms = (time.perf_counter() - tick_started) * 1000.0
            next_delay_ms = max(MIN_FRAME_DELAY_MS, round(TARGET_FRAME_MS - elapsed_ms))
            # Re-arm unconditionally so the loop is self-healing. Guard the after()
            # call itself: if the root was torn down mid-frame, swallow the
            # TclError and stop re-arming (the window is gone — correct to stop).
            if self._running and self._root is not None:
                try:
                    self._root.after(next_delay_ms, self._schedule_frame)
                except Exception:  # noqa: BLE001
                    log.warning(
                        "JarvisBar frame re-arm skipped — watchdog will revive",
                        exc_info=True,
                    )

    def _schedule_frame_watchdog(self) -> None:
        """Independent revival loop — the second after-chain (anti-freeze). Renders
        nothing; only checks the frame-loop heartbeat and kicks ``_schedule_frame``
        back to life if it has gone silent past ``FRAME_STALL_THRESHOLD_NS`` (e.g.
        a render hang or a swallowed re-arm failure froze the bar). Its own re-arm
        is in ``finally`` so a single check error cannot kill the watchdog. A
        deliberate ``stop()`` (``_running`` False) ends it cleanly."""
        if not self._running or self._root is None:
            return
        try:
            last = self._last_frame_ns
            if last and (time.monotonic_ns() - last) > FRAME_STALL_THRESHOLD_NS:
                stalled_s = (time.monotonic_ns() - last) / 1e9
                log.warning(
                    "JarvisBar frame loop stalled %.1fs — reviving it", stalled_s
                )
                # Pre-stamp so a false alarm (a still-live loop) doesn't make us
                # re-kick every tick; the revived loop immediately re-stamps.
                self._last_frame_ns = time.monotonic_ns()
                self._schedule_frame()
        except Exception:  # noqa: BLE001 — the watchdog must itself never die
            log.debug("JarvisBar frame watchdog check failed", exc_info=True)
        finally:
            if self._running and self._root is not None:
                try:
                    self._root.after(
                        WATCHDOG_INTERVAL_MS, self._schedule_frame_watchdog
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "JarvisBar frame watchdog re-arm skipped", exc_info=True
                    )

    def _schedule_z_order_guard(self) -> None:
        """Keep a mapped bar above apps that are opened after it.

        This is intentionally independent of voice-state ``show()`` calls: a
        persistent idle bar may remain mapped for hours without another reveal,
        which is exactly when an external app can displace it. Hidden and
        startup-gated surfaces are never mapped or raised by this guard.
        """
        if not self._running or self._root is None:
            return
        try:
            mapped = bool(self._root.winfo_ismapped())
            if mapped and not getattr(self, "_startup_gated", False):
                strategy = "healthy"
                if (
                    sys.platform != "win32"
                    or _win32_topmost_band_is_healthy(self._root) is not True
                ):
                    strategy = self._do_pin_topmost()
                # The native Win32 path changes only Z-order. A rare Tk
                # fallback can touch the layered style, so immediately restore
                # color-key/alpha and request one fresh frame (BUG-030).
                if sys.platform == "win32" and strategy == "tk":
                    self._apply_layered_attributes(opacity=self._opacity)
                    self._invalidate_static_frame()
        except Exception:  # noqa: BLE001 - the guard must itself never die
            log.debug("JarvisBar Z-order guard check failed", exc_info=True)
        finally:
            if self._running and self._root is not None:
                try:
                    self._root.after(
                        Z_ORDER_GUARD_INTERVAL_MS, self._schedule_z_order_guard
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "JarvisBar Z-order guard re-arm skipped", exc_info=True
                    )

    # ------------------------------------------------------------------ #
    # Drag (reposition) + click (start a voice session)                 #
    # ------------------------------------------------------------------ #
    def _on_press(self, event: Any) -> None:
        # A press on the canvas means the pointer IS over the bar, so the close-X
        # controls are (and visually become) available even if <Enter> was missed
        # — e.g. the bar deiconified under a stationary cursor. resolve_click then
        # still gates the hang-up on the X-glyph hit-box, so this only makes a
        # DELIBERATE X-click reliable; it never widens the accidental-hangup zone.
        self._hovered = True
        self._drag = {
            "sx": event.x_root,
            "sy": event.y_root,
            "ox": event.x_root - self._x,
            "oy": event.y_root - self._y,
            "cx": event.x,  # canvas-relative x → which control zone was clicked
            "hovered": True,  # press-time hover (the pointer IS on the bar now)
            "moved": False,
        }

    def _on_motion(self, event: Any) -> None:
        d = self._drag
        if d is None or self._root is None:
            return
        dx = event.x_root - d["sx"]
        dy = event.y_root - d["sy"]
        if not d["moved"] and not interaction.is_drag(dx, dy, DRAG_THRESHOLD_PX):
            return
        d["moved"] = True
        self._x = event.x_root - d["ox"]
        self._y = event.y_root - d["oy"]
        try:
            self._root.geometry(f"{renderer.WIN_W}x{renderer.WIN_H}+{self._x}+{self._y}")
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar geometry update failed", exc_info=True)

    def _on_release(self, event: Any) -> None:
        d = self._drag
        self._drag = None
        if d is None:
            return
        if interaction.classify_release(moved=bool(d["moved"])) == "click":
            # Phantom-click guard (root cause of the request_hangup STORM): a
            # frameless color-key topmost Tk window emits SYNTHETIC press/release
            # events on withdraw/deiconify/turn-mode-switch under a stationary
            # cursor. Read as a close-X click, they fired a machine-paced
            # request_hangup storm that killed live sessions AND armed the
            # post-hangup wake-lock, so the next "Hey Jarvis" was swallowed
            # ("wake triggers, nothing happens"). Honor a click ONLY when the OS
            # pointer is really over the bar right now.
            if not self._pointer_over_bar():
                log.debug("jarvisbar: ignoring phantom click (pointer off bar)")
                return
            # Use the PRESS-time hover (consistent with the press-time cx): a
            # deliberate click that started on the bar registers even if a stray
            # <Leave> flickered _hovered before release.
            self._on_click(d.get("cx", renderer.WIN_W / 2), hovered=bool(d.get("hovered")))
            return
        try:
            sw = int(self._root.winfo_screenwidth())
            sh = int(self._root.winfo_screenheight())
            self._x, self._y = interaction.clamp_to_screen(
                self._x, self._y, screen_w=sw, screen_h=sh,
                bar_w=renderer.WIN_W, bar_h=renderer.WIN_H, margin=MARGIN_PX,
            )
            self._root.geometry(f"{renderer.WIN_W}x{renderer.WIN_H}+{self._x}+{self._y}")
            from jarvis.core.config_writer import DEFAULT_CONFIG_FILE

            interaction.save_jarvisbar_position(DEFAULT_CONFIG_FILE, self._x, self._y)
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar position persist failed", exc_info=True)

    def _pointer_over_bar(self) -> bool:
        """True when the OS pointer is really inside the bar window right now.

        Guards against synthetic/phantom button events that a frameless
        color-key topmost Tk window emits on withdraw/deiconify/turn-mode-switch
        under a stationary cursor (tkdnd was one source, now disabled; the
        deiconify path remains). Uses ``winfo_pointerxy`` + the window's own
        ``winfo_rootx/rooty/width/height`` — all Tk screen-pixel measurements in
        one coordinate space, so it stays correct under HiDPI scaling. Fails
        CLOSED (returns False) on any error or an unmapped / zero-size window:
        a missed real click is recoverable, a phantom hang-up is not.
        """
        root = self._root
        if root is None:
            return False
        try:
            if not int(root.winfo_ismapped()):
                return False
            bw = int(root.winfo_width())
            bh = int(root.winfo_height())
            if bw <= 1 or bh <= 1:
                return False
            px, py = root.winfo_pointerxy()
            bx = int(root.winfo_rootx())
            by = int(root.winfo_rooty())
            return bx <= int(px) < bx + bw and by <= int(py) < by + bh
        except Exception:  # noqa: BLE001
            return False

    def _on_enter(self, _event: Any = None) -> None:
        self._hovered = True

    def _on_leave(self, _event: Any = None) -> None:
        self._hovered = False

    def _on_right_click(self, _event: Any = None) -> None:
        """Right-click → raise the main desktop window via the injected
        callback (OrbBusBridge publishes ``ShowWindowRequested``). No callback
        wired (boot race / no bridge) → safe no-op."""
        callback = self._on_show_window
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar show-window callback failed", exc_info=True)

    def _on_click(self, click_x: float | None = None, *, hovered: bool = False) -> None:
        # Zone-routed: LEFT X → hang up (active only), RIGHT mic → toggle voice
        # mute (mic muted FOR JARVIS only), MIDDLE (idle) → start a normal
        # session. All entries are thread-safe from the Tk thread.
        if click_x is None:
            click_x = renderer.WIN_W / 2
        # The first accepted X click optimistically removes the active look.
        # Ignore follow-up clicks during that short transition so they cannot be
        # reclassified as idle-body clicks and accidentally reopen the session.
        if time.monotonic() < self._hangup_click_block_until:
            return
        try:
            from jarvis.core.runtime_refs import get_speech_pipeline

            pipeline = get_speech_pipeline()
            if pipeline is None:
                return
            # Hang-up must be a deliberate click on the VISIBLE close-X glyph
            # (the X is only drawn while hovered), never the wide left dead-zone
            # — see interaction.resolve_click + the silent-hangup forensic. The
            # active pill is ACTIVE_W, so the X glyph sits at WIN_W/2-0.42*pw.
            active = self._mode in ("listen", "think", "speak")
            pill_w = renderer.ACTIVE_W if active else None
            action = interaction.resolve_click(
                click_x, renderer.WIN_W, self._mode,
                hovered=hovered, pill_w=pill_w,
            )
            if action == "mute":
                # The mic button toggles voice mute via the bridge-wired
                # callback (publishes VoiceMuteToggleRequested → pipeline flips
                # _muted → broadcasts VoiceMuteChanged → set_muted reconciles).
                # Flip the local mirror optimistically so the red rim +
                # slashed-mic show on the very next frame — but ONLY when a
                # callback is wired, else a boot-race click would paint a false
                # slash with nothing muted behind it.
                cb = self._on_mute_toggle
                if cb is not None:
                    cb()
                    self._muted = not self._muted
            elif action == "hangup":
                # Only a LIVE session is hung up. If the bar is stuck in an
                # active "listen/think/speak" look with NO session behind it
                # (a wake confirmed then swallowed by the post-hangup wake-lock,
                # or a stray event flipped the look), a close-X would be a no-op
                # request_hangup that just traps the user. Start a session
                # instead so a click ALWAYS escapes the stuck state. Legacy
                # pipelines without is_session_active keep the plain hang-up.
                active_fn = getattr(pipeline, "is_session_active", None)
                if active_fn is not None and not active_fn():
                    start = getattr(pipeline, "request_voice_session", None)
                    if callable(start):
                        start()
                else:
                    hangup = getattr(pipeline, "request_hangup", None)
                    if callable(hangup):
                        hangup()
                        # Give immediate, local feedback instead of waiting for
                        # microphone/provider teardown plus the EventBus IDLE
                        # round-trip. The authoritative bridge state will
                        # reconcile the same value when teardown completes.
                        self._mode = "idle"
                        self._hangup_click_block_until = (
                            time.monotonic() + HANGUP_CLICK_GUARD_S
                        )
            elif action == "talk":
                pipeline.request_voice_session()
            # "none" → nothing
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar click action failed", exc_info=True)

    def _on_reset_double_click(self, _event: Any = None) -> None:
        """Reset the bar to its default bottom-center anchor.

        Bridge-compatible: OrbBusBridge looks this up via getattr on
        OrbResetRequested ("Orb reset"). Returning the bar to the default
        anchor is the bar's analogue of the orb's position reset.
        """
        if self._root is None:
            return
        try:
            sw = int(self._root.winfo_screenwidth())
            sh = int(self._root.winfo_screenheight())
            self._x, self._y = interaction.default_bottom_center(
                screen_w=sw, screen_h=sh,
                bar_w=renderer.WIN_W, bar_h=renderer.WIN_H, margin=MARGIN_PX,
            )
            self._root.geometry(f"{renderer.WIN_W}x{renderer.WIN_H}+{self._x}+{self._y}")
            from jarvis.core.config_writer import DEFAULT_CONFIG_FILE

            interaction.save_jarvisbar_position(DEFAULT_CONFIG_FILE, self._x, self._y)
        except Exception:  # noqa: BLE001
            log.debug("jarvisbar reset failed", exc_info=True)
