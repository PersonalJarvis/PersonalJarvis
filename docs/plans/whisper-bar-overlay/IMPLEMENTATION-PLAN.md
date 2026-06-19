# Whisper-Bar Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, chosen by maintainer goal) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a slim Wispr-Flow-style pill bar as the default on-screen representation of Jarvis (idle dots / mic-driven listening bars / synthetic thinking wave / TTS-driven speaking bars), keeping the mascot orb selectable.

**Architecture:** A new `WhisperBarOverlay` Tk surface implements the same duck-typed API `OrbBusBridge` already drives, so the bridge is reused unchanged. State transitions arrive via `SystemStateChanged` (bus, low-freq). Mic level during LISTENING is fed by the bridge's existing `MicListener → set_level` (free). TTS level during SPEAKING is the one new signal: a throttle-free RMS computed in `player._flush_pending`, published out-of-band (NOT the EventBus) via a tiny `level_tap` module → `set_level`. The thinking wave is synthetic (time-driven). Selection via `[ui].orb_style = "whisper_bar" | "mascot" | "none"`, default `whisper_bar`.

**Tech Stack:** Python 3.11, tkinter (color-key transparency), numpy + PIL (per-frame draw), pydantic v2 (config), pytest.

**Verified facts (from code, file:line):**
- Surface API the bridge calls: `show(mode)` modes ∈ {"idle","listen","speak","think"} (`overlay.py:1122`, callers `bus_bridge.py:400/432/448/465/608`), `hide()`, `set_level(float)` (direct, not enqueued), `play_animation(name,**p)`, `stop_animation(name)`, optional `show_listening_transcript`/`hide_comment`/`start_mouth_animation`/`stop_mouth_animation`/`set_on_mute_toggle`/`set_feedback_publisher` — all optional ones called via `getattr+callable` guard. Bridge also reads `getattr(orb,"_root",None)` + `getattr(orb,"_on_reset_double_click",None)`.
- `OrbBusBridge(bus, orb, idle_animations_enabled=True, hide_on_idle=True)`; `.attach()` no args (`bus_bridge.py:154`).
- Desktop wiring branch: `desktop_app.py:1167` (`orb_style = self.cfg.ui.orb_style or "mascot"`), construct → `start_in_thread()` → `OrbBusBridge(bus=bus, orb=orb).attach()` (`:1176`).
- Tk setup: `tk.Tk()`, `overrideredirect(True)`, `wm_attributes("-topmost",True)`, `wm_attributes("-transparentcolor", "#FF00FF")`, `configure(bg="#FF00FF")` (`overlay.py:1327`). `DRAG_THRESHOLD_PX=16`.
- `_enqueue_ui`/`_schedule_ui_queue` (drain every 20ms)/`_schedule_frame` (`after(16)`) (`overlay.py:2126/2138/2190`). `start_in_thread(auto_demo=False, timeout=3.0)` blocks on `self._started` event (`:2158`).
- Drag: bindings `<ButtonPress-1>/<B1-Motion>/<ButtonRelease-1>` (`overlay.py:1443`); on release `if not state.moved: return  # click` (`:1604`).
- `MicListener(on_level: Callable[[float],None], device=None)` fully decoupled (`mic_listener.py`); bridge starts its own during LISTENING calling `orb.set_level` (`bus_bridge.py:663`).
- `drag_persistence`: `save_position_to_toml(path, MascotPosition)`, `load_position_from_toml(path)`, `clamp_to_work_area(x,y,geo,*,mascot_size_px,margin_px)`, `screens_from_tk(root)`, section `[overlay.mascot]`.
- Player RMS tap point: `_flush_pending` after `await asyncio.to_thread(self._write_samples,...)` (`player.py:594`); `arr = np.frombuffer(pcm, np.int16)` (`:593`) in scope; `AudioPlayer.__init__(..., bus=None)` stores `self._bus` (`:258`); `AudioOutFirst` at `:604`.
- Click-to-talk: `pipeline.request_voice_session()` sync + thread-safe (`pipeline.py:2227`); reachable via `jarvis.core.runtime_refs.get_speech_pipeline()` (set at `desktop_app.py:1428`).
- `UIConfig(BaseModel)` plain (no `extra`), `orb_style: str = "mascot"` (`config.py:808`), `orb_mascot_path: str = ""` (`:810`). No validator on orb_style.

---

## File Structure

| File | Responsibility |
|---|---|
| `jarvis/ui/whisperbar/__init__.py` | Package marker + exports (`WhisperBarOverlay`) |
| `jarvis/audio/level_tap.py` | Process-local pub/sub for the TTS output level (out-of-band, NOT EventBus); zero-cost when no subscriber |
| `jarvis/ui/whisperbar/renderer.py` | Pure draw math: `ease`, `bar_heights`, `wave_points`, `WhisperBarRenderer.render(t,mode,level)->PIL.Image`. No Tk/IO. |
| `jarvis/ui/whisperbar/interaction.py` | `is_drag(dx,dy,threshold)`, `classify_release(moved)`, `resolve_bar_placement(...)`, position persistence wrapper |
| `jarvis/ui/whisperbar/overlay.py` | `WhisperBarOverlay` Tk window/thread; implements the surface API; drag+click-to-talk; registers `set_level` as TTS level sink |
| `jarvis/core/config.py` *(modify)* | `orb_style` default → `"whisper_bar"`; add `bar_persistent: bool=True`, `bar_accent: str="#e7c46e"` |
| `jarvis/audio/player.py` *(modify)* | RMS in `_flush_pending` → `level_tap.publish` (gated on subscribers) |
| `jarvis/ui/desktop_app.py` *(modify)* | branch `_start_speech_and_orb` on `orb_style`; `hide_on_idle=not bar_persistent` |
| `tests/unit/ui/whisperbar/test_renderer.py` | renderer math + render smoke |
| `tests/unit/ui/whisperbar/test_interaction.py` | click/drag classification + placement |
| `tests/unit/audio/test_level_tap.py` | pub/sub + no-subscriber no-op |
| `tests/unit/ui/whisperbar/test_surface_contract.py` | WhisperBarOverlay exposes every method the bridge calls |
| `tests/unit/audio/test_player_level_tap.py` | player publishes RMS through level_tap |

---

## Task 1: `level_tap` — out-of-band TTS level channel

**Files:** Create `jarvis/audio/level_tap.py`; Test `tests/unit/audio/test_level_tap.py`

- [ ] **Step 1 — failing test**
```python
# tests/unit/audio/test_level_tap.py
from jarvis.audio import level_tap

def test_no_subscriber_is_noop_and_reports_empty():
    level_tap.reset()
    assert level_tap.has_subscribers() is False
    level_tap.publish(0.5)  # must not raise

def test_subscriber_receives_clamped_level():
    level_tap.reset()
    got = []
    unsub = level_tap.subscribe(got.append)
    assert level_tap.has_subscribers() is True
    level_tap.publish(2.0)   # clamp to 1.0
    level_tap.publish(-1.0)  # clamp to 0.0
    assert got == [1.0, 0.0]
    unsub()
    assert level_tap.has_subscribers() is False
    level_tap.publish(0.7)   # no subscriber → ignored
    assert got == [1.0, 0.0]

def test_failing_subscriber_is_swallowed():
    level_tap.reset()
    def boom(_): raise RuntimeError("x")
    level_tap.subscribe(boom)
    level_tap.publish(0.3)  # must not propagate
```
- [ ] **Step 2 — run, expect ImportError/fail**
`& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/audio/test_level_tap.py -q`
- [ ] **Step 3 — implement**
```python
# jarvis/audio/level_tap.py
"""Process-local, out-of-band level channel for the TTS output amplitude.

Deliberately NOT the EventBus: amplitude updates fire ~8x/second and would
spam the flight-recorder wildcard subscriber (5s cap). The whisper-bar
overlay registers a sink; the audio player publishes the per-flush RMS. When
no sink is registered, publishing is a cheap no-op (the player skips the RMS
computation entirely via has_subscribers()).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

_log = logging.getLogger("jarvis.audio.level_tap")
_lock = threading.Lock()
_subscribers: list[Callable[[float], None]] = []


def subscribe(sink: Callable[[float], None]) -> Callable[[], None]:
    """Register a level sink. Returns an unsubscribe callable."""
    with _lock:
        _subscribers.append(sink)

    def _unsub() -> None:
        with _lock:
            try:
                _subscribers.remove(sink)
            except ValueError:
                pass

    return _unsub


def has_subscribers() -> bool:
    with _lock:
        return bool(_subscribers)


def publish(level: float) -> None:
    """Push a level in [0,1] to all sinks. Clamps; swallows sink errors."""
    lv = 0.0 if level < 0.0 else 1.0 if level > 1.0 else float(level)
    with _lock:
        sinks = tuple(_subscribers)
    for sink in sinks:
        try:
            sink(lv)
        except Exception:  # noqa: BLE001 — a bad sink must never break audio
            _log.debug("level_tap sink failed", exc_info=True)


def reset() -> None:
    """Test helper: drop all subscribers."""
    with _lock:
        _subscribers.clear()
```
- [ ] **Step 4 — run, expect PASS**
- [ ] **Step 5 — commit** `feat(audio): out-of-band level_tap channel for TTS amplitude`

## Task 2: `renderer` — pure draw math

**Files:** Create `jarvis/ui/whisperbar/renderer.py`, `jarvis/ui/whisperbar/__init__.py`; Test `tests/unit/ui/whisperbar/test_renderer.py`

- [ ] **Step 1 — failing tests**
```python
# tests/unit/ui/whisperbar/test_renderer.py
import math
from jarvis.ui.whisperbar import renderer as R

def test_ease_moves_toward_target():
    assert R.ease(0.0, 1.0, 0.5) == 0.5
    assert R.ease(0.5, 1.0, 0.5) == 0.75

def test_bar_heights_zero_level_is_min():
    hs = R.bar_heights(0.0, 0.0, 7, max_h=40.0, min_h=4.0)
    assert len(hs) == 7
    assert all(abs(h - 4.0) < 1e-6 for h in hs)

def test_bar_heights_grow_with_level():
    lo = sum(R.bar_heights(0.3, 0.2, 7, max_h=40.0, min_h=4.0))
    hi = sum(R.bar_heights(0.3, 0.9, 7, max_h=40.0, min_h=4.0))
    assert hi > lo
    for h in R.bar_heights(1.7, 1.0, 7, max_h=40.0, min_h=4.0):
        assert 4.0 <= h <= 40.0 + 1e-6

def test_wave_points_bounded_inside_pill():
    pts = R.wave_points(0.4, 200, 52, cx=150, cy=36, n=48)
    assert len(pts) == 49
    for x, y in pts:
        assert 50 <= x <= 250
        assert 36 - 26 <= y <= 36 + 26  # within ±height*0.5

def test_render_returns_image_for_every_mode():
    rnd = R.WhisperBarRenderer(accent="#e7c46e")
    for mode in ("idle", "listen", "speak", "think"):
        img = rnd.render(0.1, mode, 0.5)
        assert img.size == (R.WIN_W, R.WIN_H)
        assert img.mode == "RGB"

def test_idle_collapses_expansion_over_frames():
    rnd = R.WhisperBarRenderer()
    for _ in range(40):
        rnd.render(0.0, "listen", 0.5)
    expanded = rnd._st.expand
    for _ in range(80):
        rnd.render(0.0, "idle", 0.0)
    assert rnd._st.expand < expanded
    assert rnd._st.expand < 0.1
```
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement** (full module — `__init__.py` exports `WhisperBarOverlay` lazily to stay headless-safe)
```python
# jarvis/ui/whisperbar/__init__.py
"""Whisper-bar overlay package (slim default on-screen representation)."""
from __future__ import annotations

__all__ = ["WhisperBarOverlay"]


def __getattr__(name: str):  # lazy: avoid importing tkinter on headless import
    if name == "WhisperBarOverlay":
        from jarvis.ui.whisperbar.overlay import WhisperBarOverlay
        return WhisperBarOverlay
    raise AttributeError(name)
```
```python
# jarvis/ui/whisperbar/renderer.py  (numpy/PIL — desktop only, imported lazily by overlay)
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

COLOR_KEY_RGB = (255, 0, 255)
PILL_BG = (14, 13, 12)
PILL_BORDER = (44, 41, 36)
IDLE_DOT = (107, 103, 96)

WIN_W = 300
WIN_H = 72
COLLAPSED_W = 168
COLLAPSED_H = 30
EXPANDED_W = 284
EXPANDED_H = 52
N_BARS = 7
N_DOTS = 5
BAR_MIN_H = 4.0
BAR_MAX_H = 38.0
MODES = ("idle", "listen", "speak", "think")


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def ease(current: float, target: float, factor: float) -> float:
    """Exponential ease of current toward target. factor in (0,1]."""
    return current + (target - current) * factor


def bar_heights(t: float, level: float, n: int, *, max_h: float, min_h: float) -> list[float]:
    """Equalizer bar heights, deterministic in (t, level).

    level<=0 → all bars at min_h. Each bar has a distinct phase so the row
    never moves in lockstep. Height is bounded by [min_h, max_h].
    """
    level = 0.0 if level < 0.0 else 1.0 if level > 1.0 else level
    out: list[float] = []
    for i in range(n):
        phase = i * 0.9
        osc = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 9.0 + phase))  # 0.55..1.0
        out.append(min_h + (max_h - min_h) * level * osc)
    return out


def wave_points(t: float, width: int, height: int, cx: float, cy: float, n: int = 48) -> list[tuple[float, float]]:
    """Travelling sine polyline for THINKING, tapered to stay inside the pill."""
    pts: list[tuple[float, float]] = []
    half = width / 2.0
    amp = height * 0.32
    for k in range(n + 1):
        u = k / n
        x = cx - half + u * width
        envelope = math.sin(u * math.pi)
        y = cy + math.sin(u * math.pi * 3.0 - t * 4.0) * amp * envelope
        pts.append((x, y))
    return pts


@dataclass
class _RenderState:
    display_level: float = 0.0
    expand: float = 0.0  # 0 collapsed .. 1 expanded


class WhisperBarRenderer:
    def __init__(self, accent: str = "#e7c46e") -> None:
        self._accent = _hex_to_rgb(accent)
        self._st = _RenderState()

    def render(self, t: float, mode: str, ext_level: float) -> Image.Image:
        active = mode in ("listen", "speak")
        self._st.expand = ease(self._st.expand, 0.0 if mode == "idle" else 1.0, 0.25)
        self._st.display_level = ease(
            self._st.display_level, ext_level if active else 0.0, 0.35
        )

        frame = np.empty((WIN_H, WIN_W, 3), dtype=np.uint8)
        frame[:, :] = COLOR_KEY_RGB
        img = Image.fromarray(frame, "RGB")
        d = ImageDraw.Draw(img)

        pw = COLLAPSED_W + (EXPANDED_W - COLLAPSED_W) * self._st.expand
        ph = COLLAPSED_H + (EXPANDED_H - COLLAPSED_H) * self._st.expand
        cx, cy = WIN_W / 2.0, WIN_H / 2.0
        d.rounded_rectangle(
            [cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2],
            radius=ph / 2, fill=PILL_BG, outline=PILL_BORDER, width=1,
        )

        if mode == "idle":
            self._draw_dots(d, cx, cy)
        elif mode == "think":
            self._draw_wave(d, t, EXPANDED_W - 40, ph, cx, cy)
        else:
            self._draw_bars(d, t, cx, cy)
        return img

    def _draw_dots(self, d: ImageDraw.ImageDraw, cx: float, cy: float) -> None:
        gap = 16
        x0 = cx - gap * (N_DOTS - 1) / 2.0
        for i in range(N_DOTS):
            x = x0 + i * gap
            d.ellipse([x - 2.5, cy - 2.5, x + 2.5, cy + 2.5], fill=IDLE_DOT)

    def _draw_bars(self, d: ImageDraw.ImageDraw, t: float, cx: float, cy: float) -> None:
        hs = bar_heights(t, self._st.display_level, N_BARS, max_h=BAR_MAX_H, min_h=BAR_MIN_H)
        span = 150
        x0 = cx - span / 2.0
        step = span / (N_BARS - 1)
        for i, h in enumerate(hs):
            x = x0 + i * step
            d.rounded_rectangle([x - 3, cy - h / 2, x + 3, cy + h / 2], radius=3, fill=self._accent)

    def _draw_wave(self, d: ImageDraw.ImageDraw, t: float, width: int, ph: float, cx: float, cy: float) -> None:
        pts = wave_points(t, width, int(ph), cx, cy, n=48)
        d.line(pts, fill=self._accent, width=3, joint="curve")
```
- [ ] **Step 4 — run, expect PASS**
- [ ] **Step 5 — commit** `feat(ui): whisper-bar pure renderer (dots/bars/wave)`

## Task 3: `interaction` — click vs drag + placement

**Files:** Create `jarvis/ui/whisperbar/interaction.py`; Test `tests/unit/ui/whisperbar/test_interaction.py`

- [ ] **Step 1 — failing tests**
```python
# tests/unit/ui/whisperbar/test_interaction.py
from jarvis.ui.whisperbar import interaction as I

def test_is_drag_threshold():
    assert I.is_drag(10, 5, 16) is False   # 15 < 16
    assert I.is_drag(10, 7, 16) is True    # 17 >= 16
    assert I.is_drag(-20, 0, 16) is True

def test_classify_release():
    assert I.classify_release(moved=False) == "click"
    assert I.classify_release(moved=True) == "drag"

def test_default_bottom_center_placement():
    x, y = I.default_bottom_center(screen_w=1920, screen_h=1080, bar_w=300, bar_h=72, margin=12)
    assert x == (1920 - 300) // 2
    assert y == 1080 - 72 - 12
```
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement**
```python
# jarvis/ui/whisperbar/interaction.py
"""Click/drag classification + default placement for the whisper bar.

Mirrors the orb's proven movement-threshold model (overlay.py:1604): a press
that never moves past the threshold is a CLICK (→ start a voice session); a
press that moves past it is a DRAG (→ reposition + persist). No duration gate
is needed — moving the pointer is what arms a drag.
"""
from __future__ import annotations


def is_drag(dx: int, dy: int, threshold: int) -> bool:
    """Manhattan-distance drag test (matches DRAG_THRESHOLD_PX=16)."""
    return (abs(dx) + abs(dy)) >= threshold


def classify_release(*, moved: bool) -> str:
    return "drag" if moved else "click"


def default_bottom_center(*, screen_w: int, screen_h: int, bar_w: int, bar_h: int, margin: int) -> tuple[int, int]:
    """Default anchor: horizontally centered, just above the taskbar."""
    x = (screen_w - bar_w) // 2
    y = screen_h - bar_h - margin
    return x, y
```
- [ ] **Step 4 — run, expect PASS**
- [ ] **Step 5 — commit** `feat(ui): whisper-bar interaction (click vs drag, placement)`

## Task 4: `WhisperBarOverlay` — Tk surface

**Files:** Create `jarvis/ui/whisperbar/overlay.py`; Test `tests/unit/ui/whisperbar/test_surface_contract.py`

Implements the bridge's surface API. `show(mode)` maps to renderer mode; `set_level` writes `_ext_level` directly (atomic, like the orb). Text/mouth methods are no-ops. On `start()`, subscribes `self.set_level` to `level_tap` (TTS amplitude). Click on release-without-move → `runtime_refs.get_speech_pipeline().request_voice_session()`. Drag persists position via `drag_persistence` (reuses `[overlay.mascot]` section — shared pin acceptable for v1). Color-key transparency identical to the orb; NO `SetWindowLong` calls → no BUG-030 exposure.

- [ ] **Step 1 — failing contract test**
```python
# tests/unit/ui/whisperbar/test_surface_contract.py
import inspect
from jarvis.ui.whisperbar.overlay import WhisperBarOverlay

REQUIRED = [
    "show", "hide", "set_level", "play_animation", "stop_animation",
    "show_listening_transcript", "hide_comment",
    "start_mouth_animation", "stop_mouth_animation",
    "set_on_mute_toggle", "set_feedback_publisher", "start_in_thread",
]

def test_surface_exposes_every_method_the_bridge_calls():
    for name in REQUIRED:
        assert callable(getattr(WhisperBarOverlay, name, None)), name

def test_show_accepts_bridge_modes_without_tk():
    bar = WhisperBarOverlay.__new__(WhisperBarOverlay)  # no Tk window
    bar._mode = "idle"; bar._root = None; bar._ui_queue = None
    bar._tk_thread_id = None
    import threading; bar._tk_thread_id = threading.get_ident()
    for mode in ("idle", "listen", "speak", "think"):
        bar.show(mode)            # maps + stores, enqueue no-ops when root None
        assert bar._mode == mode
    bar.set_level(0.7); assert abs(bar._ext_level - 0.7) < 1e-9
    bar.set_level(5.0); assert bar._ext_level == 1.0
    # no-ops must not raise
    bar.play_animation("wave"); bar.stop_animation("think")
    bar.show_listening_transcript("x"); bar.hide_comment()
    bar.start_mouth_animation(); bar.stop_mouth_animation()
```
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement** full Tk class (`overlay.py`). Key requirements (write verbatim during execution):
  - Constants reused from renderer: `WIN_W/WIN_H`, `COLOR_KEY_HEX="#FF00FF"`, `DRAG_THRESHOLD_PX=16`.
  - `__init__(self, persistent: bool = True, accent: str = "#e7c46e")` stores config; `_ext_level=0.0`, `_mode="idle"`, `_root=None`, `_ui_queue=queue.Queue()`, `_started=threading.Event()`, `_running=False`, `_level_unsub=None`, `_drag=None`, `_x/_y` position, `_tk_thread_id=None`.
  - `show(mode)`: validate ∈ MODES else ignore; `self._mode = mode`; if `not persistent and mode=="idle"` enqueue hide; else enqueue `_ensure_visible`.
  - `set_level(level)`: clamp + assign `self._ext_level` (NOT enqueued).
  - no-ops: `play_animation`, `stop_animation`, `show_listening_transcript`, `hide_comment`, `start_mouth_animation`, `stop_mouth_animation`, `set_on_mute_toggle`, `set_feedback_publisher` (store callbacks but bar emits none).
  - `_enqueue_ui` / `_schedule_ui_queue` / `_schedule_frame` / `start` / `start_in_thread`: copied structure from `overlay.py` (`tk.Tk`, `overrideredirect`, `-topmost`, `-transparentcolor`, canvas, bindings, `after(16)` render). On `start`: compute default bottom-center via `interaction.default_bottom_center` (or persisted), `level_tap.subscribe(self.set_level)` storing `_level_unsub`, set `_started`.
  - Drag handlers: `_on_press/_on_motion/_on_release` mirroring orb; on release `if not moved: _on_click()` else persist via `save_position_to_toml`.
  - `_on_click()`: `from jarvis.core.runtime_refs import get_speech_pipeline; p=get_speech_pipeline(); p and p.request_voice_session()` (guarded try/except).
  - `stop()`: `_running=False`, call `_level_unsub()` if set, destroy root.
- [ ] **Step 4 — run, expect PASS** (contract test runs without opening a window)
- [ ] **Step 5 — commit** `feat(ui): WhisperBarOverlay Tk surface (bridge-compatible)`

## Task 5: config — selection + persistence flags

**Files:** Modify `jarvis/core/config.py:808`; Test `tests/unit/core/test_ui_config_bar.py`

- [ ] **Step 1 — failing test**
```python
# tests/unit/core/test_ui_config_bar.py
from jarvis.core.config import UIConfig

def test_defaults_select_whisper_bar():
    c = UIConfig()
    assert c.orb_style == "whisper_bar"
    assert c.bar_persistent is True
    assert c.bar_accent == "#e7c46e"

def test_legacy_mascot_still_accepted():
    assert UIConfig(orb_style="mascot").orb_style == "mascot"
    assert UIConfig(orb_style="none").orb_style == "none"
```
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement**: in `UIConfig`, change `orb_style: str = "mascot"` → `orb_style: str = "whisper_bar"`; add after `orb_mascot_path`:
```python
    # Whisper-bar: persistent (always-visible) vs only-when-active.
    bar_persistent: bool = True
    # Hex accent that lights up during activity.
    bar_accent: str = "#e7c46e"
```
- [ ] **Step 4 — run, expect PASS**
- [ ] **Step 5 — commit** *(deferred — config.py carries parallel edits; do NOT stage. See Execution Notes.)*

## Task 6: player — RMS through level_tap

**Files:** Modify `jarvis/audio/player.py` (`_flush_pending`, after `:596`); Test `tests/unit/audio/test_player_level_tap.py`

- [ ] **Step 1 — failing test** (feeds a chunk iterator through a fake stream; asserts level_tap received a value)
```python
# tests/unit/audio/test_player_level_tap.py
import numpy as np, pytest
from jarvis.audio import level_tap

@pytest.mark.asyncio
async def test_player_publishes_rms_when_subscribed(monkeypatch):
    from jarvis.audio import player as P
    level_tap.reset(); got = []; level_tap.subscribe(got.append)

    pl = P.AudioPlayer.__new__(P.AudioPlayer)  # bypass device init
    # Minimal attrs play_chunks/_flush_pending touch — see _flush_pending body.
    # (During execution: stub _ensure_stream + _write_samples via monkeypatch on
    #  to_thread so no real PortAudio is opened, then drive one loud chunk.)
    ...
    assert got and max(got) > 0.0
```
*(During execution: implement the test by monkeypatching `asyncio.to_thread` to run the target synchronously and stubbing `_open_output_stream`/`_write_samples`; drive a single full-scale int16 chunk and assert `got` is non-empty and >0.)*
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement**: inside `_flush_pending`, immediately after `await asyncio.to_thread(self._write_samples, stm, arr, pending_rate, dev_rate)` (`player.py:594-596`), add:
```python
                # Out-of-band TTS amplitude for the whisper-bar (never the bus).
                if level_tap.has_subscribers() and arr.size:
                    rms = float(np.sqrt(np.mean(np.square(arr.astype(np.float32) * (1.0 / 32768.0)))))
                    level_tap.publish(rms)
```
and `from jarvis.audio import level_tap` at the top of `player.py`.
- [ ] **Step 4 — run, expect PASS**
- [ ] **Step 5 — commit** `feat(audio): publish TTS output RMS via level_tap`

## Task 7: desktop_app — wire the selection branch

**Files:** Modify `jarvis/ui/desktop_app.py:1167-1176`. No unit test (GUI wiring) — covered by manual live sign-off + an import smoke.

- [ ] **Step 1** — replace the orb construction block with a branch:
```python
        orb_style = self.cfg.ui.orb_style or "whisper_bar"
        orb_mascot_path = self.cfg.ui.orb_mascot_path or None
        if orb_style == "none":
            self._orb = None
        elif orb_style == "whisper_bar":
            from jarvis.ui.whisperbar import WhisperBarOverlay
            from ui.orb.bus_bridge import OrbBusBridge
            bar = WhisperBarOverlay(
                persistent=self.cfg.ui.bar_persistent,
                accent=self.cfg.ui.bar_accent,
            )
            bar.start_in_thread()
            OrbBusBridge(bus=bus, orb=bar, hide_on_idle=not self.cfg.ui.bar_persistent).attach()
            self._orb = bar
        else:  # "mascot" (and any legacy value)
            from ui.orb.bus_bridge import OrbBusBridge
            from ui.orb.overlay import OrbOverlay
            orb = OrbOverlay(sticky=False, mic_reactive=False, style=orb_style, mascot_path=orb_mascot_path)
            orb.start_in_thread()
            OrbBusBridge(bus=bus, orb=orb).attach()
            self._orb = orb
```
(keep the surrounding `try/except Exception → self._orb=None` intact)
- [ ] **Step 2** — import smoke: `& "C:\Program Files\Python311\python.exe" -c "import jarvis.ui.desktop_app"`
- [ ] **Step 3** — commit *(deferred — desktop_app.py carries parallel edits; do NOT stage.)*

## Task 8: full validation pass

- [ ] Run the whole new suite + adjacent:
`& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/ui/whisperbar tests/unit/audio/test_level_tap.py tests/unit/audio/test_player_level_tap.py tests/unit/core/test_ui_config_bar.py -v`
- [ ] `ruff check jarvis/ui/whisperbar jarvis/audio/level_tap.py`
- [ ] Headless boot guard: `& "C:\Program Files\Python311\python.exe" -c "import jarvis.ui.whisperbar; print('ok')"` (must NOT import tkinter).
- [ ] Regression: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/audio -q` (player untouched paths still green).
- [ ] Dispatch `code-reviewer` over the new files + the two touch hunks.
- [ ] Adversarial verify (workflow) of the risky claims: color-key transparency, click-vs-drag, level_tap zero-cost-when-idle, hide_on_idle persistence.

## Execution Notes (binding)

- **Two touch-files carry uncommitted parallel-session edits** (`config.py` +66, `desktop_app.py` +38). Edit them additively; **never `git add` them** — leave uncommitted (repo norm: feature lands uncommitted, maintainer restarts). Commit only the new isolated files.
- **Live default for the maintainer:** the code default flips to `whisper_bar`, but if the live `jarvis.toml` pins `[ui].orb_style = "mascot"`, the maintainer must unset it (or set `whisper_bar`) to see the bar — do NOT edit `jarvis.toml` (drift-guard). Report this in the handoff.
- **Restart required** for live effect (pythonw holds the RAM bundle / pipeline).
- Live GUI behavior (transparency, four animations, drag, click-to-talk) is **maintainer sign-off**, not CI.
