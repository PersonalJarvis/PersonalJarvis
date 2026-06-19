# Orb Drag-and-Pin — Design Spec

**Date:** 2026-05-17
**Status:** Approved (autonomous shipping run)
**Owner:** Ruben (product) · Claude (implementation)
**Tracking issue / branch:** TBD on first commit

---

## 1. Purpose

Today the live Jarvis orb (Tkinter mascot in [`ui/orb/overlay.py`](../../../ui/orb/overlay.py)) is **pinned to the bottom-right Windows-taskbar tray edge** by an auto-anchor that re-fires every 1500 ms. This spec adds a **manual drag-and-pin mode**: left-mouse-button down on the orb, drag across the screen (and across monitors), release — the orb stays at the release point and that position survives Jarvis restarts.

A **double-click on the orb** reverts to the default tray-edge anchor and re-enables the auto-anchor loop.

---

## 2. User-facing behavior

| Gesture | Effect |
|---|---|
| LMB-press on orb canvas, drag ≥ 5 px, release | Orb moves with cursor; on release stays at release-point. Auto-anchor disabled. Position persisted to `jarvis.toml`. |
| LMB-press on orb canvas, drag < 5 px, release | Treated as a click. No movement, no persistence, no auto-anchor change. (Future use for "open chat" — out of scope here.) |
| Double-LMB-click on orb canvas | Auto-anchor re-enabled. `[overlay.mascot]` position fields cleared from `jarvis.toml`. On next 1500 ms tick the orb snaps back to the tray-edge. |
| Restart Jarvis with a manually-pinned orb | Orb appears at the pinned position. If the persisted monitor is gone (laptop undocked, port-swap, monitor unplugged), fall back to default tray-edge anchor and clear the stale `[overlay.mascot]` entry. |
| Drag orb to a position that would be off-screen (negative coords / past monitor right/bottom edge) | Position is **clamped** into the visible work-area minus a 16 px safety margin. Snap-to-edge is **off** — orb stops exactly where released (within the clamp). |
| Resolution change / DPI change / taskbar re-size **while manually pinned** | Orb stays put. We only re-clamp into the new work-area if the old position is now invalid; otherwise no movement. |

**Cursor feedback during drag:** Tk cursor `fleur` (4-arrow move cursor) while LMB is held; restored to default on release. Telegraphs "I am movable right now."

**Drag scope:** Only the 108 × 108 orb canvas, **not** the comment bubble (`OrbCommentBubble`) that floats next to it.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  OrbOverlay  (ui/orb/overlay.py — Tk main thread)           │
│                                                              │
│  state:                                                      │
│    _mascot_x, _mascot_y         (int)  ← already exists      │
│    _manual_pinned: bool         NEW                          │
│    _drag_state: _DragState|None NEW (offset, start xy, etc.) │
│                                                              │
│  bindings (NEW on self._canvas):                             │
│    <ButtonPress-1>     → _on_drag_press                      │
│    <B1-Motion>         → _on_drag_motion                     │
│    <ButtonRelease-1>   → _on_drag_release                    │
│    <Double-Button-1>   → _on_reset_double_click              │
│                                                              │
│  _schedule_position_recheck():                               │
│    if self._manual_pinned:                                   │
│      → only re-clamp into work-area if off-screen            │
│    else:                                                     │
│      → existing taskbar-anchor logic (unchanged)             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │ persistence
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  ui/orb/drag_persistence.py   NEW thin wrapper module        │
│  ─────────────────────────────────────────────────────────   │
│  Reuses:                                                     │
│    OS-Level/src/overlay/mascot_position.py                   │
│      • MascotPosition  (dataclass)                           │
│      • load_position_from_toml(path) -> MascotPosition|None  │
│      • save_position_to_toml(path, pos)  (atomic, tempfile)  │
│      • clamp_to_work_area(x, y, geo, mascot_size_px=…)       │
│                                                              │
│  Adds (Tk-flavoured, no Qt import):                          │
│    • screens_from_tk(root) -> list[_ScreenSnapshot]          │
│        Win32 EnumDisplayMonitors-based; ctypes only.         │
│    • clear_position_in_toml(path)                            │
│        removes the three position_* keys from [overlay.mascot]│
│        (atomic, comment-preserving — same regex pattern).    │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Why a thin wrapper instead of importing `OS-Level.overlay.mascot_position` directly?

Two reasons:

1. **Qt-free import path.** `mascot_position.screens_from_qt` lazy-imports PySide6. The Tk orb must never trigger that import. The wrapper exposes a `screens_from_tk` shim and re-exports the math helpers, so `ui/orb/overlay.py` imports from `ui.orb.drag_persistence`, not from `OS-Level/...`.

2. **Path-discipline.** `OS-Level/src/overlay/` is an alternate, dormant implementation (PySide6 mascot retired due to Win11 DWM transparency bugs — see `ui/orb/overlay.py` header). The live Tk overlay shouldn't reach across module boundaries into a retired sibling. A wrapper module documents the intentional re-use of pure-math helpers without coupling lifecycles.

### 3.2 Config schema

In `jarvis.toml`:

```toml
[overlay.mascot]
# Set when the user has manually dragged the orb. Absent / empty = auto-anchor.
position_monitor      = "\\\\.\\DISPLAY1"   # Win32 device name
position_x_relative   = 1340                 # px, relative to work-area top-left
position_y_relative   = 720                  # px, relative to work-area top-left
```

`[overlay.mascot]` schema is already established by `OS-Level/src/overlay/mascot_position.py` — we reuse it byte-for-byte to keep one source of truth and to make the existing tests in `tests/overlay/test_mascot_position.py` cover our persistence path.

When the user double-clicks to reset, **all three keys are removed** (not zeroed) — absence is the "auto-anchor" signal.

---

## 4. State machine

```
                  ┌──────────────────────────┐
                  │ AUTO_ANCHOR              │
                  │ _manual_pinned=False     │
                  │ recheck timer reposit.   │
                  └──┬─────────────────────┬─┘
                     │ LMB-press           │
                     │ + drag ≥ 5 px       │ double-LMB-click
                     │ + release           │ (and from boot if toml empty)
                     ▼                     │
                  ┌──────────────────────────┐
                  │ MANUAL_PINNED            │
                  │ _manual_pinned=True      │
                  │ recheck timer no-op      │
                  │ (clamp-only on resize)   │
                  └──┬─────────────────────┬─┘
                     │ LMB-press           │
                     │ + drag ≥ 5 px       │ double-LMB-click
                     │ + release           │
                     ▼                     │
                  (stays MANUAL_PINNED — just new coords)
```

State transitions are triggered only by user gestures **or** by boot resolution:

- **Boot:** load `jarvis.toml`. If the three `position_*` keys are present **and** the monitor exists, enter `MANUAL_PINNED`. Otherwise enter `AUTO_ANCHOR`.

---

## 5. Event flow detail

### 5.1 LMB press

```
_on_drag_press(event):
    self._drag_state = _DragState(
        start_root_x=event.x_root,
        start_root_y=event.y_root,
        offset_x=event.x_root - self._mascot_x,
        offset_y=event.y_root - self._mascot_y,
        moved=False,
    )
    self._root.configure(cursor="fleur")
```

### 5.2 LMB motion (only fires while LMB held)

```
_on_drag_motion(event):
    if self._drag_state is None: return
    dx = event.x_root - self._drag_state.start_root_x
    dy = event.y_root - self._drag_state.start_root_y
    if not self._drag_state.moved and (abs(dx) + abs(dy)) < DRAG_THRESHOLD_PX:
        return                              # below threshold; still a click
    self._drag_state.moved = True
    new_x = event.x_root - self._drag_state.offset_x
    new_y = event.y_root - self._drag_state.offset_y
    self._mascot_x, self._mascot_y = new_x, new_y
    self._root.geometry(f"{WIN_W}x{WIN_H}+{new_x}+{new_y}")
    if self._comment_bubble is not None:
        self._comment_bubble.update_anchor(new_x, new_y, self._root.winfo_screenwidth())
```

`DRAG_THRESHOLD_PX = 5` (manhattan distance — `abs(dx)+abs(dy)` keeps it cheap and matches Tk's typical drag-recognition heuristic).

### 5.3 LMB release

```
_on_drag_release(event):
    self._root.configure(cursor="")
    state = self._drag_state
    self._drag_state = None
    if state is None or not state.moved:
        return                              # was a click, not a drag
    # Persist.
    monitor_geo, monitor_name = _monitor_at(self._mascot_x, self._mascot_y)
    clamped = clamp_to_work_area(
        self._mascot_x, self._mascot_y, monitor_geo, mascot_size_px=WIN_W
    )
    if clamped != (self._mascot_x, self._mascot_y):
        self._mascot_x, self._mascot_y = clamped
        self._root.geometry(f"{WIN_W}x{WIN_H}+{clamped[0]}+{clamped[1]}")
    self._manual_pinned = True
    save_position_to_toml(
        TOML_PATH,
        MascotPosition(
            monitor=monitor_name,
            x_relative=self._mascot_x - monitor_geo[0],
            y_relative=self._mascot_y - monitor_geo[1],
        ),
    )
```

### 5.4 Double-click

```
_on_reset_double_click(event):
    self._manual_pinned = False
    clear_position_in_toml(TOML_PATH)
    # Next recheck tick (≤ 1500 ms) will re-anchor; force immediate.
    screen_w = self._root.winfo_screenwidth()
    screen_h = self._root.winfo_screenheight()
    anchor = self._resolve_anchor(screen_w, screen_h)
    self._mascot_x, self._mascot_y = anchor.x, anchor.y
    self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
```

**Note:** Tk fires `<Button-1>` **before** `<Double-Button-1>`. We must guard `_on_drag_press` against a follow-up double-click by not starting a "real drag" until movement exceeds the threshold — that guard is already part of the threshold logic in 5.2: a press-release-press cycle within ~500 ms with < 5 px movement leaves `_drag_state.moved = False`, so the release in 5.3 is a no-op, and the second click's `<Double-Button-1>` fires the reset. Tested in the unit tests below.

### 5.5 Auto-anchor recheck

```
_schedule_position_recheck():
    if not self._running or not self._root: return
    if self._manual_pinned:
        # Clamp-only path: protect against the user pinning at (1900, 600)
        # and then unplugging the right-hand monitor.
        screens = screens_from_tk(self._root)
        monitor_geo, monitor_name = _monitor_at(self._mascot_x, self._mascot_y, screens=screens)
        clamped = clamp_to_work_area(
            self._mascot_x, self._mascot_y, monitor_geo, mascot_size_px=WIN_W
        )
        if clamped != (self._mascot_x, self._mascot_y):
            self._mascot_x, self._mascot_y = clamped
            self._root.geometry(f"{WIN_W}x{WIN_H}+{clamped[0]}+{clamped[1]}")
            # Re-persist clamped position.
            save_position_to_toml(TOML_PATH, MascotPosition(
                monitor=monitor_name,
                x_relative=clamped[0] - monitor_geo[0],
                y_relative=clamped[1] - monitor_geo[1],
            ))
        self._root.after(POSITION_RECHECK_MS, self._schedule_position_recheck)
        return
    # AUTO_ANCHOR branch — current logic, unchanged.
    ...existing code...
```

### 5.6 Boot

In `OrbOverlay.start()`, immediately before computing the default anchor (line 1561 today):

```
persisted = load_position_from_toml(TOML_PATH)
if persisted and persisted.monitor:
    screens = screens_from_tk(self._root)
    placement = resolve_placement(persisted, screens, mascot_size_px=WIN_W)
    if not placement.recovered:
        # Monitor still present — use persisted position.
        self._manual_pinned = True
        self._mascot_x, self._mascot_y = placement.abs_x, placement.abs_y
        self._root.geometry(f"{WIN_W}x{WIN_H}+{placement.abs_x}+{placement.abs_y}")
        # ... skip the existing anchor block ...
        # comment_bubble init uses (mascot_x, mascot_y) — same flow.
    else:
        # Persisted monitor missing — fall back to default, clear stale entry.
        clear_position_in_toml(TOML_PATH)
        # continue with existing default-anchor code
```

---

## 6. Edge cases

| Case | Behavior |
|---|---|
| User drags orb to a non-existent location (e.g., negative coords by holding past monitor edge) | `clamp_to_work_area` on release puts orb back inside visible area on its current monitor. Same on next recheck if state drifts. |
| User drags orb across monitor boundary onto a second monitor | `_monitor_at()` resolves the monitor containing the orb's center after the drag; we persist with that monitor's device name. |
| User unplugs the monitor where the orb was pinned | Next boot: `resolve_placement` returns `recovered=True`; we fall back to default tray-edge and call `clear_position_in_toml`. Mid-session: the next recheck tick re-clamps onto the now-primary monitor's work area. |
| Tk fires `<Button-1>` then `<Double-Button-1>` rapidly | The single-click handler sets `_drag_state` but does no geometry write; the release with `moved=False` is a no-op; the double-click fires and resets. |
| Boot when `jarvis.toml` doesn't exist | `load_position_from_toml` returns `None` → AUTO_ANCHOR mode, no I/O until the user drags. |
| `jarvis.toml` exists but `[overlay.mascot]` doesn't | Same as above. `load_position_from_toml` returns `None`. |
| `[overlay.mascot]` is present but partially malformed (e.g. non-int x) | `load_position_from_toml` falls back to defaults — caller checks `persisted.monitor` truthiness before trusting it. Spec uses `if persisted and persisted.monitor:` exactly to gate on this. |
| Drag-Threshold is 5 px but user's hand shakes ±2 px before releasing | `moved` stays `False`; no persistence; no auto-anchor change. Click-vs-drag stays clean. |
| Two orb instances (paranoid case — `--sticky` preview + production) | Both write to the same `jarvis.toml`. Atomic writer (`os.replace`) guarantees no torn TOML; last write wins. Production preview mode is dev-only — acceptable. |
| Position would write outside `[overlay.mascot]` keys | `_replace_or_append_field` regex is scoped to the section header; impossible to spill into other sections. (Already tested in `mascot_position.py` tests.) |

---

## 7. Files affected

### New

- `ui/orb/drag_persistence.py` — ~120 lines. Re-exports + Tk shim + `clear_position_in_toml`.
- `tests/unit/ui/test_orb_drag.py` — ~200 lines. Threshold logic, state machine, double-click reset, clamp-on-monitor-loss.

### Modified

- `ui/orb/overlay.py`
  - Add module constants: `DRAG_THRESHOLD_PX = 5`, `TOML_PATH = …` (resolved via existing `jarvis.core.config` helper).
  - Add `_DragState` dataclass.
  - Add `_manual_pinned: bool = False`, `_drag_state: _DragState|None = None` to `__init__` slots.
  - Hook canvas bindings inside `start()` after `self._canvas.pack(...)`.
  - Branch `_schedule_position_recheck()` on `_manual_pinned`.
  - Branch the boot anchor block on persisted-toml-presence.

### Untouched (but reused)

- `OS-Level/src/overlay/mascot_position.py` — pure-math helpers + atomic-TOML writer. **Not edited.** Only consumed.

---

## 8. Testing

### Unit (no Tk required, pure logic)

- `test_drag_threshold_below_5px_is_click` — synthetic event seq with 4 px movement → `moved` stays False → no geometry call, no toml call.
- `test_drag_threshold_above_5px_is_drag` — 6 px movement → geometry call + toml write on release.
- `test_double_click_clears_toml_and_resets_state` — call `_on_reset_double_click` after manual pin → `_manual_pinned=False`, `clear_position_in_toml` invoked.
- `test_boot_with_persisted_position_skips_auto_anchor` — patch `load_position_from_toml` → `_manual_pinned=True`, default anchor not called.
- `test_boot_with_missing_monitor_clears_stale_toml` — patch screens so persisted monitor isn't in list → `clear_position_in_toml` invoked, fall back to default.
- `test_recheck_clamps_when_manual_pinned_and_monitor_shrunk` — simulate monitor going from 1920 → 1280 wide → clamp pulls orb back into work area, re-persists.

### Existing tests reused

- `tests/overlay/test_mascot_position.py` — already covers `resolve_placement`, `snap_to_edges`, `clamp_to_work_area`, `save_position_to_toml`, `load_position_from_toml`. We pile our usage on top; existing tests guard the foundation.

### Manual smoke (golden-path verification, required before claiming "done")

1. Start Jarvis with `run.bat`. Wait for orb to appear bottom-right.
2. Press-hold LMB on orb → drag to top-left quadrant → release. Orb stays.
3. Check `jarvis.toml` → `[overlay.mascot]` section now has `position_monitor`, `position_x_relative`, `position_y_relative`.
4. Wait 5 s. Orb does NOT snap back. (Auto-anchor disabled.)
5. Restart Jarvis. Orb reappears at top-left.
6. Double-click orb. Orb returns to bottom-right tray edge.
7. Check `jarvis.toml` → three position keys are gone.
8. Restart Jarvis. Orb appears bottom-right. (Default behavior restored.)

Capture step-by-step output in the verification log entry (per CLAUDE.md autonomy rule 2: "Verifikation ist Pflichtteil — nicht optional" / "Verification is a mandatory part — not optional").

---

## 9. Anti-pattern check (CLAUDE.md AP-1 … AP-18)

| AP | Risk | Mitigation |
|---|---|---|
| AP-1 (subprocess flicker) | None — no new subprocess | ✅ |
| AP-3 (Tool.execute bypass) | N/A — no tool surface | ✅ |
| AP-4 (multi-layer enum drift) | None — no new wire-format enum | ✅ |
| AP-5 (worker tool-set pollution) | N/A | ✅ |
| AP-7 (TOML write without lock + tempfile + BOM) | Reuses existing `_atomic_write_text` which already handles tempfile + `os.replace`. BOM: file is read/written `encoding="utf-8"` (no BOM). | ✅ |
| AP-8 (preflight skip) | New code in main worktree only; standard preflight before commit. | ✅ |
| AP-9 (awareness/wiki on voice path) | Drag handlers run in Tk main loop, completely off voice path. | ✅ |
| AP-11 (LLM call in scrubber) | No LLM. | ✅ |
| AP-13 (block on watchdog reload) | Not applicable — `[overlay.mascot]` is not watchdog-reloaded; the orb is the only writer & reader. | ✅ |
| AP-14 (resurrect Sub-Jarvis) | N/A | ✅ |
| AP-15 (auto-activate skills) | N/A | ✅ |
| AP-16 (config section without `extra="allow"`) | `[overlay.mascot]` already exists implicitly under `[overlay]`; we add no new top-level section. **Verify:** spec's pre-implementation step 1 is to grep `JarvisConfig` for the overlay model and confirm extra-tolerance. If extra-tolerance is missing, add `model_config = ConfigDict(extra="allow")` to the overlay sub-model in the same change. | ⚙ verify-first |

---

## 10. Rollout

Single commit on a new branch `feature/orb-drag`. No feature flag — the behavior is additive (drag is purely opt-in by user gesture); a user who never drags sees zero change. If a regression surfaces, revert is one `git revert`.

---

## 11. Out of scope

- Right-click context menu (saved for a future change — Q2 picked double-click).
- Snap-to-edge while dragging (off — user said "bleibt wo man loslässt" / "stays where you let go").
- Hotkey to summon orb to cursor (different feature, future).
- Per-monitor preferred positions (one persisted position; multi-monitor handled by stick-to-current-monitor on drag-release).
- Editing the comment bubble's behavior — bubble follows the orb's `_mascot_x/y` as today.
- The dormant PySide6 `MascotWindow` in `OS-Level/src/overlay/window_mascot.py` — not touched.

---

## 12. Open questions

None. All decisions captured above.
