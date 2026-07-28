# JarvisBar "black box around the pill" — transparency deep dive

**Status:** analysis, 2026-07-28. Companion to BUG-075 and BUG-093 in
[`docs/BUGS.md`](BUGS.md); the surface lifecycle around it lives in
[`overlay-state-machine.md`](overlay-state-machine.md).

The bar is a frameless always-on-top window that must show *only* its pill.
Everything around the pill is padding that has to disappear. This document
records how that disappearing act actually works per OS, which layer has failed
in each reported round, and — importantly — how to tell a real on-screen defect
apart from a screen-capture artifact, because the two look identical in a
screenshot and have completely different fixes.

---

## 1. How the padding is made to vanish (per OS)

There is **no shared transparency implementation**. There are two, and they fail
independently:

| OS | Surface | Mechanism |
|---|---|---|
| Windows | `JarvisBarOverlay` (Tk) | Layered window **color key**: the renderer paints the padding pure magenta `#FF00FF`; `wm_attributes("-transparentcolor")` makes Win32 drop exactly those pixels (`LWA_COLORKEY`). |
| Linux | `JarvisBarOverlay` (Tk) | Same color-key path, compositor permitting. |
| macOS | `QtJarvisBarOverlay` (Qt) | Real per-pixel alpha: `WA_TranslucentBackground` + every paint clearing the destination under `CompositionMode_Source`. |

The renderer is shared and identical everywhere:
`jarvis/ui/jarvisbar/renderer.py::JarvisBarRenderer.render` starts every frame
with

```python
frame = np.empty((WIN_H, WIN_W, 3), dtype=np.uint8)
frame[:, :] = COLOR_KEY_RGB          # (255, 0, 255)
```

so the padding leaves the renderer as **magenta, never black**. This matters for
diagnosis: the color of the visible box tells you which layer broke.

**Read the box's color first — it is the single most diagnostic pixel:**

* **Magenta padding** → the frame is correct, but the *window* is not keying it
  out. `-transparentcolor` was rejected, or the layered attributes were lost
  (window recreated, moved across monitors, re-shown after `withdraw`).
* **Black padding** → nothing is keying the padding out, because what is on
  screen is no longer the window Jarvis owns. On Windows this is almost always
  the **ghost window** (§4). Otherwise: an RGBA path ran where a color-key path
  was expected (magenta with `alpha=0` resolves to `(0,0,0)` when flattened
  without a backdrop).
* **Opaque grey/appearance-colored padding** → macOS Tk backing (BUG-075).

---

## 2. macOS is where this bug actually lives

Two rounds, both macOS-only, both presenting as "a box around the pill":

**BUG-075 (FIXED 2026-07-17) — solid grey box.** Tk 8.6 aqua mapped a
`systemTransparent` background onto a genuinely clear window backing. Tk 9 —
which is what uv's python-build-standalone ships, i.e. *every fresh macOS
install* — paints it as an opaque appearance color instead. The fix
(`_apply_macos_clear_backing` in `jarvis/ui/jarvisbar/overlay.py`) sets every
`NSWindow` of the host process non-opaque with `NSColor.clearColor()` via
AppKit.

**BUG-093 (FIXED 2026-07-19) — black rectangle + retained frames.** The AppKit
pass turned out to be insufficient. The window, its `TKContentView`, and its
backing layer were already non-opaque; the offending pixels lived one level
higher, in the **Canvas backing store**. Aqua-Tk 9 composites an RGBA
`PhotoImage` with source-over semantics, so source pixels with `alpha == 0` are
*no-ops rather than replacements*: they can neither erase the initial opaque
Canvas backing nor clear pixels left by the preceding, larger animation frame.
That produced both symptoms at once — a black rectangle, and concentric ghost
outlines of every earlier pill size.

The fix was a **platform split**, not another patch: on Darwin the companion
host builds `QtJarvisBarOverlay`, which keeps the same deterministic PIL
renderer but presents each frame on a `WA_TranslucentBackground` Qt tool window
and clears the whole destination under `CompositionMode_Source` before drawing.
Transparent pixels then *replace* old alpha instead of blending over it.

**Why macOS is structurally the fragile one:** Windows gets transparency from a
compositor primitive that has nothing to do with how the frame was drawn — the
OS discards magenta pixels regardless. macOS gets it from *correct alpha
compositing inside the toolkit*, so any toolkit-version change in blend
semantics, backing opacity, or layer backing reintroduces the box. Two different
Tk-version behaviours already did exactly that, two days apart. The Qt path
removes the Tk dependency but replaces it with a PySide6 one.

---

## 3. Windows: the color key works — while the window is being pumped

Measured on a live Windows 11 install, 4K display, with the bar running
(2026-07-28):

```
JarvisBar (TkTopLevel)  WS_EX_LAYERED set
GetLayeredWindowAttributes -> key=0x00ff00ff  alpha=153  flags=3
```

`flags=3` is `LWA_COLORKEY | LWA_ALPHA`. **This is normal and not a defect** —
Tk sets both flags unconditionally; a window with no `-alpha` at all reports the
same `flags=3` with `alpha=255`. Do not "fix" this.

An isolated probe rebuilt two frameless Tk windows over a known backdrop, both
showing a real `JarvisBarRenderer` frame — one with `-transparentcolor` +
`-alpha 0.6` (exactly the live configuration), one with the color key alone —
and read back the composited screen with `BitBlt(..., SRCCOPY | CAPTUREBLT)`:

```
A colorkey + alpha 0.6 : black=0   (backdrop visible through the padding)
B colorkey only        : black=0   goldish=406 (pill visible)
```

**Zero black pixels in either case** — *while the window is being pumped*. That
qualifier is the whole story, and missing it once cost a wrong diagnosis: the
color key is not a property of the pixels, it is a property of **the window**,
and it protects the padding only for as long as that window is the thing on
screen.

---

## 4. The ghost window — why the padding turns black on Windows (BUG-118)

When a top-level window stops pumping messages for roughly five seconds,
Windows hides it and puts a stand-in window of class `Ghost` at the same
rectangle, painted by the DWM rather than by the app. **The ghost is an
ordinary window: it carries no `WS_EX_LAYERED`,** so the magenta colour key is
never applied to it and the entire window rectangle lands on screen as an
opaque black box around the pill.

Measured on the live app, both windows present at once:

```
hwnd=4004742  class='TkTopLevel'  pid=88416(app)  HUNG=True  LAYERED=True   key=0x00ff00ff
hwnd=8196144  class='Ghost'       pid=1340(dwm)   HUNG=True  LAYERED=False  no key
              identical rect 1873,2031 93x39      screen over it: black=3141
```

Reproduced from first principles — a color-keyed frameless Tk window showing a
real renderer frame, whose UI thread is then deliberately blocked:

```
control (no fix):  before stall black=0   during stall black=1982 / 2870
with the fix:      before stall black=0   during stall black=0
```

**This does not require the app to be broken.** The bar paints from a Tk loop,
and that loop stops pumping whenever *another* thread holds the GIL through a
long CPU-bound stretch. The trigger in the field was exactly that: the backend
thread sat `active+gil` inside `load_config()` → `JarvisConfig(**data)` on the
event loop, reached from `resolve_provider_endpoint()` in `_ensure_client` on
every brain call (same family as the freeze fixed in `325af16d`, which cached
the TOML parse but not the pydantic construction above it).

So the user-visible symptom of a **stall** is a **rendering defect**, which is
why this reads as "the bar has a black border" rather than "the app is busy".

**Fix.** `disable_windows_app_ghosting()` (`jarvis/core/process_utils.py`),
called from both color-key surfaces — the bar (`jarvis/ui/jarvisbar/overlay.py`)
and the mascot (`ui/orb/overlay.py`). `DisableProcessWindowsGhosting` is
process-wide and cannot be undone, which is the right trade: a frameless
click-through overlay gains nothing from a ghost (no title bar to grey out, no
close button to offer), and the app keeps its Restart control, the tray icon,
and Task Manager as ways out of a hang. Windows-only, a logged no-op elsewhere.
Guards: `tests/unit/core/test_process_utils_ghosting.py`.

**Note on captures.** Screenshot APIs also disagree about layered windows —
`PIL.ImageGrab.grab()` over the bar's rectangle returned the desktop *without
the bar at all*, while `BitBlt` with `CAPTUREBLT` composited it correctly. So a
capture can hide the bar, and it is worth knowing which path you are measuring
with; but it was **not** the cause here, and a black box on the physical
display is always real.

---

## 5. Structural weak points (why this keeps coming back)

1. **Two implementations, no shared guard.** Windows/Linux color-key and macOS
   Qt alpha are exercised by different code with no common regression test that
   asserts "the padding is not visible". Either can regress alone.
2. **The macOS path depends on an optional extra.** `QtJarvisBarOverlay` needs
   `pyside6-essentials`, which lives in the `[desktop]` extra (shipped with the
   advertised `[full]` profile). A base install on macOS loses the bar entirely
   rather than degrading to a visible-but-boxed one.
3. **`_apply_macos_clear_backing` imports `AppKit`,** which is provided by
   `pyobjc-framework-Cocoa`. That package is **not declared explicitly** in the
   `desktop-macos` extra — it arrives only transitively via
   `pyobjc-framework-Quartz`. It still serves the Tk mascot surface, so the
   dependency should be named rather than inherited.
4. **The Tk macOS branch in `overlay.py` is now unreachable for the bar** (the
   host selects Qt on Darwin) but still present, so a future reader may fix the
   wrong branch.
5. **The bar shares a process — and a GIL — with the backend.** On Windows and
   Linux the bar paints in-process, so any long CPU-bound stretch elsewhere
   freezes its paint loop. Ghosting is now off, so that no longer *looks* like
   a rendering bug, but the bar still stops animating. macOS does not have this
   coupling: there the bar lives in its own companion process. Moving the
   Windows/Linux bar out of process would remove the class entirely.

---

## 6. Reproduce / verify

```bash
# What the running window is actually configured with (Windows):
#   read WS_EX_LAYERED + GetLayeredWindowAttributes for the "JarvisBar" HWND.
#   key must be 0x00ff00ff; flags=3 is expected, not a bug.

# What the renderer emits (any OS) — the padding must be magenta:
python -c "from jarvis.ui.jarvisbar import renderer as R; \
r=R.JarvisBarRenderer(); f=r.render(t=.3, mode='listen', ext_level=.7); \
print(f.mode, f.size, f.getpixel((0,0)))"
# -> RGB (82, 35) (255, 0, 255)
```

On macOS the equivalent check is whether `QtJarvisBarOverlay` was selected at
all — the host logs its surface choice; a Tk bar on Darwin means the platform
split did not take effect and BUG-093 will reappear.
