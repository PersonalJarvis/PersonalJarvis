"""WhisperBarOverlay — the slim Tk on-screen bar.

Implements the same duck-typed surface API ``OrbBusBridge`` already drives, so
the bridge is reused unchanged. ``show(mode)`` selects the renderer state;
``set_level`` writes ``_ext_level`` directly (an atomic float assignment, like
the orb). Text/mouth methods are deliberate no-ops — the bar shows no text.

Signals:
- LISTENING: the bridge starts its own ``MicListener`` that calls ``set_level``.
- SPEAKING: the audio player publishes its output RMS via ``level_tap``, which
  this surface subscribes to on ``start()``.
- THINKING: the renderer generates a synthetic wave (no external signal).

Threading mirrors the orb: a daemon thread runs the Tk mainloop; all Tk
mutations from the bus-subscriber thread go through ``_enqueue_ui`` → a queue
drained on the Tk thread. ``set_level`` is the sole exception (atomic write).

No ``SetWindowLong`` is ever called, so this surface is not exposed to the
BUG-030 LWA color-key destruction risk. ``-transparentcolor`` is set once at
window creation and never mutated.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from jarvis.ui.whisperbar import interaction, renderer

log = logging.getLogger("jarvis.ui.whisperbar")

COLOR_KEY_HEX = "#FF00FF"
DRAG_THRESHOLD_PX = 16
MARGIN_PX = 12
# Gap above the taskbar (~0.2 cm) when anchoring at the default bottom-center.
TASKBAR_GAP_PX = 8
# Window opacity (the pill goes semi-transparent; magenta stays fully keyed
# out). Lower = more see-through. Tune this one number for the glass look.
BAR_ALPHA = 0.6

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


class WhisperBarOverlay:
    def __init__(
        self,
        persistent: bool = True,
        accent: str = "#e7c46e",
        opacity: float = BAR_ALPHA,
    ) -> None:
        self._persistent = persistent
        self._accent = accent
        self._opacity = max(0.2, min(1.0, float(opacity)))  # clamp to sane range
        self._mode = "idle"
        self._ext_level = 0.0
        # perf_counter() of the last set_level() that carried real sound
        # (>= AUDIBLE_LEVEL). Drives the wave↔bars choice in _schedule_frame.
        # 0.0 = "long ago" → starts on the wave, not the bars.
        self._last_audible_t = 0.0
        self._root: Any = None
        self._canvas: Any = None
        self._renderer: renderer.WhisperBarRenderer | None = None
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

    # ------------------------------------------------------------------ #
    # Surface API consumed by OrbBusBridge                               #
    # ------------------------------------------------------------------ #
    def show(self, mode: str = "listen") -> None:
        if mode not in renderer.MODES:
            return
        self._mode = mode
        if self._root is None:
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
    def start_in_thread(self, timeout: float = 3.0) -> None:
        def _run() -> None:
            try:
                self.start()
            except Exception:  # noqa: BLE001
                log.exception("WhisperBar thread start failed")

        t = threading.Thread(target=_run, name="whisperbar-tk-mainloop", daemon=True)
        t.start()
        if not self._started.wait(timeout=timeout):
            log.error("WhisperBar window not initialised within %.1fs", timeout)

    def start(self) -> None:
        import tkinter as tk

        from PIL import ImageTk  # noqa: F401 — fail fast here if Pillow missing

        self._tk_thread_id = threading.get_ident()
        self._renderer = renderer.WhisperBarRenderer(accent=self._accent)

        root = tk.Tk()
        self._root = root
        root.title("JarvisWhisperBar")
        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        try:
            root.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
        except tk.TclError:
            log.warning("transparentcolor unsupported — bar will show its key colour")
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
            bg=COLOR_KEY_HEX,
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

        if not self._persistent:
            root.withdraw()  # only-when-active variant starts hidden

        try:
            from jarvis.audio import level_tap

            self._level_unsub = level_tap.subscribe(self.set_level)
        except Exception:  # noqa: BLE001
            log.debug("level_tap subscribe failed", exc_info=True)

        self._running = True
        self._t0 = time.perf_counter()
        self._started.set()
        self._schedule_ui_queue()
        self._schedule_frame()
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
                log.debug("whisperbar destroy failed", exc_info=True)

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

            pos = interaction.load_whisperbar_position(DEFAULT_CONFIG_FILE)
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
        try:
            self._root.deiconify()
        except Exception:  # noqa: BLE001
            log.debug("whisperbar deiconify failed", exc_info=True)

    def _do_hide(self) -> None:
        if self._root is None:
            return
        try:
            self._root.withdraw()
        except Exception:  # noqa: BLE001
            log.debug("whisperbar withdraw failed", exc_info=True)

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
                log.exception("WhisperBar UI command failed")
        self._root.after(20, self._schedule_ui_queue)

    def _schedule_frame(self) -> None:
        if not self._running or not self._root or not self._canvas or not self._renderer:
            return
        from PIL import ImageTk

        now = time.perf_counter()
        t = now - self._t0
        # Sound-driven look: bars while audio is present (mic OR TTS), wave while
        # silent. The coarse self._mode only decides active-vs-idle; the actual
        # wave↔bars choice comes from how recently real sound arrived. This makes
        # the silent TTS-synthesis lead-in render as the thinking wave and real
        # speech (in or out) render as the equalizer — independent of the
        # supervisor state's continue-listening flips.
        from jarvis.audio import level_tap

        playing = level_tap.playback_active()
        effective_mode = renderer.visual_mode(
            self._mode,
            now - self._last_audible_t,
            hold_s=AUDIBLE_HOLD_S,
            playback_active=playing,
        )
        # The level is fed live per ~60 ms TTS sub-block (player._write_samples),
        # so the equalizer reacts to Jarvis's actual loudness — thin and lively,
        # exactly like it reacts to your mic. No synthetic floor (that made the
        # bars look uniformly blocky).
        img = self._renderer.render(t, effective_mode, self._ext_level, hovered=self._hovered)
        # PhotoImage must be retained on self, else Tk GCs it before drawing.
        self._photo = ImageTk.PhotoImage(img)
        if self._image_id is None:
            self._image_id = self._canvas.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self._canvas.itemconfig(self._image_id, image=self._photo)
        self._root.after(16, self._schedule_frame)  # ~60 FPS

    # ------------------------------------------------------------------ #
    # Drag (reposition) + click (start a voice session)                 #
    # ------------------------------------------------------------------ #
    def _on_press(self, event: Any) -> None:
        self._drag = {
            "sx": event.x_root,
            "sy": event.y_root,
            "ox": event.x_root - self._x,
            "oy": event.y_root - self._y,
            "cx": event.x,  # canvas-relative x → which control zone was clicked
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
            log.debug("whisperbar geometry update failed", exc_info=True)

    def _on_release(self, event: Any) -> None:
        d = self._drag
        self._drag = None
        if d is None:
            return
        if interaction.classify_release(moved=bool(d["moved"])) == "click":
            self._on_click(d.get("cx", renderer.WIN_W / 2))
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

            interaction.save_whisperbar_position(DEFAULT_CONFIG_FILE, self._x, self._y)
        except Exception:  # noqa: BLE001
            log.debug("whisperbar position persist failed", exc_info=True)

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
            log.debug("whisperbar show-window callback failed", exc_info=True)

    def _on_click(self, click_x: float | None = None) -> None:
        # Zone-routed: LEFT X → hang up (active only), RIGHT square → toggle
        # endpoint-free dictation, MIDDLE (idle) → start a normal session. All
        # entries are thread-safe from the Tk thread.
        if click_x is None:
            click_x = renderer.WIN_W / 2
        try:
            from jarvis.core.runtime_refs import get_speech_pipeline

            pipeline = get_speech_pipeline()
            if pipeline is None:
                return
            action = interaction.resolve_click(click_x, renderer.WIN_W, self._mode)
            if action == "dictate":
                toggle = getattr(pipeline, "request_ptt_toggle", None)
                if callable(toggle):
                    toggle()
            elif action == "hangup":
                hangup = getattr(pipeline, "request_hangup", None)
                if callable(hangup):
                    hangup()
            elif action == "talk":
                pipeline.request_voice_session()
            # "none" → nothing
        except Exception:  # noqa: BLE001
            log.debug("whisperbar click action failed", exc_info=True)

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

            interaction.save_whisperbar_position(DEFAULT_CONFIG_FILE, self._x, self._y)
        except Exception:  # noqa: BLE001
            log.debug("whisperbar reset failed", exc_info=True)
