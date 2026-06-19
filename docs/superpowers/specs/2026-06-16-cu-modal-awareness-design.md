# Computer-Use modal-window awareness

**Date:** 2026-06-16
**Status:** Design approved, pending implementation
**Area:** `jarvis/harness/` (Computer-Use screenshot loop)

## Problem

When a blocking **modal dialog** owns the foreground (a native Win32 dialog that
disables its owner window and beeps on any click outside it), the Computer-Use
loop does not recognize it. It keeps planning and executing clicks on the
underlying — now click-blocked — window, which the OS silently rejects, and the
mission dies in the circling-guard cap.

### Evidence (transcription log, 2026-06-16 21:41–21:42)

User voice command (German, confidence 0.965):

> "Ich bin verwirrt in ShareX … navigiere durch die Einstellungen, damit die
> Screenshots direkt angezeigt werden."

The CU loop ran:

```
[cu] plan: 2 steps -> click on 'Aufgaben nach dem Aufnehmen' | click on 'Screenshot anzeigen'
[cu] step 1.1 action=click ... target='Aufgaben nach dem Aufnehmen'
[cu] click at (1325,842) produced no local change — refining for a corrected retry (1/3)
[cu] click refine: (1325,842) -> (1282,842)
[cu] click at (1282,842) produced no local change — refining for a corrected retry (2/3)
... (steps 2–7) ...
[cu] step 3.1 repeated click ~(338,390) x2 — toggle-stop (not executed)
[cu] step 7.1 repeated click ~(338,390) x6 — toggle-stop (not executed)
Announcement: 'Das am Bildschirm hat nicht geklappt: 5 guard-blocked actions this mission ...'
```

### Root cause

1. `_foreground_clickable_labels()` (`screenshot_only_loop.py:1689`) already
   enumerates the **foreground** window's UIA controls every step, but the loop
   only uses the *names*. It never checks whether that foreground window is a
   **modal** blocking everything beneath it.
2. The "click produced no local change" signal (`_click_with_refine`,
   `screenshot_only_loop.py:2076`) is interpreted **only** as a missed click →
   refine coordinate → retry. It is never interpreted as "the surface is
   blocked."
3. The `_STUCK_LIMIT = 3` identical-screenshot guard does **not** fire, because
   the modal repaints/refocuses globally (the per-step screenshot hashes differ:
   `70217dcf … 2e86d910`) while only the *local* region around the click stays
   inert. So the run falls into the repeated-click toggle-stop guard and aborts
   at `_MAX_GUARD_HITS = 5` (`_FAIL_EXIT_CODE = 5`).

The loop has **no concept of a modal window**.

## Goal

Detect a blocking modal owning the foreground and let the planning model deal
with it **first**, instead of clicking through it. Chosen resolution policy
(user-approved): **surface the modal to the model** — never auto-confirm a
dialog blindly, because a modal can be a destructive "Really delete?" prompt.
This respects the existing risk-tier safety posture.

## Non-goals

- No auto-confirm / auto-dismiss of dialogs (rejected: a blind Enter/Escape can
  confirm a destructive action or discard the user's in-progress work).
- No change to the `ToolExecutor` / risk-tier layer (AP-3 untouched).
- No detection of **in-window** modals that have no separate HWND (Electron /
  web overlays). Out of scope; see "Known limitation".

## Detection mechanism

The canonical, **precise** Win32 signal for a modal dialog: a modal disables its
owner window. So:

```
modal  ⟺  GetWindow(hwnd, GW_OWNER) != 0  AND  IsWindowEnabled(owner) == False
```

A normal popup / dropdown / autocomplete does **not** disable its owner, so it is
correctly *not* flagged — the model should still click those. This keeps false
positives near zero. Window class `#32770` and UIA `IsModal` are recorded for
diagnostics but do **not** trigger detection on their own.

### New module `jarvis/harness/modal_guard.py`

A small, isolated, unit-testable helper (CU-loop-local, hunk-isolated — matching
the established pattern of focused CU fixes).

```python
@dataclass(frozen=True)
class BlockingModal:
    title: str            # foreground window title — shown to the model
    window_class: str     # e.g. "#32770" — diagnostic / logging only
    signal: str           # why flagged; current impl emits ONLY "owner_disabled"
    bounds: tuple[int, int, int, int]  # GetWindowRect: (left, top, right, bottom)

def detect_foreground_modal() -> BlockingModal | None: ...
```

The `signal` field exists for diagnostics and forward-compat. The current
implementation has a **single** trigger and therefore emits only
`"owner_disabled"`; do **not** add untriggered branches. Any future corroborating
or cross-platform detector (e.g. a UIA `IsModal` path) would introduce a new
literal here, gated behind its own condition. The pixel/name click-through block
treats `bounds` as a `GetWindowRect` rectangle: a point is "inside" iff
`left <= x < right and top <= y < bottom`.

- Pure Win32 `ctypes` (`GetForegroundWindow`, `GetWindow`, `IsWindowEnabled`,
  `GetClassNameW`, `GetWindowRect`). Sub-millisecond, no COM, no UIA traversal,
  no new dependency.
- **Never raises.** non-Windows, no foreground window, or any error → `None`.
- **Cross-platform seam (doctrine):** Windows-only now; returns `None` on
  macOS/Linux. Computer-Use is itself a `[desktop]` extra, so a graceful no-op
  on other platforms is doctrine-compliant. Future ports (macOS `AXModal`/sheet,
  Linux AT-SPI modal state) live behind this same function signature.
- Allow dependency injection of the `user32` accessor for tests (so the unit
  test runs cross-platform without a real Win32 desktop).

## Integration into `run_cu_loop`

The probe runs **inline beside** the existing per-step foreground UIA
enumeration (both are foreground-window probes) — no extra COM round-trip, no
extra step latency. The CU loop is the background worker, not the voice critical
path, and the probe is pure `ctypes`, so AP-9 latency concerns do not apply.

When a modal is detected this step, two things happen.

### 1. Context hint to the model (before plan/think)

Prepend a strong block to the existing `controls_hint`. The modal's buttons are
**already** in `control_labels` (the foreground enumeration enumerates the modal
itself when it owns the foreground), so no second enumeration is needed:

```
⚠ A modal dialog '<title>' is blocking the rest of the screen. Windows will
REJECT (error-beep) any click outside it. You MUST deal with this dialog FIRST.
Its buttons are: <control_labels>. Use click_element on one of them (or hotkey
Enter/Escape) before anything else.
```

### 2. Click-through block (the core fix)

Immediately before `_execute_action`, while a modal is active:

- **`click` (pixel):** resolve the planned absolute pixel via the existing
  `_resolve_click_pixel`. If it lands **outside** the modal `bounds`
  (`GetWindowRect`) → **do not execute**; append a constructive re-plan note to
  `history` and `continue` to the next step (re-observe + re-plan).
- **`click_element` (name):** if the target name is **not** among the modal's
  controls (`control_labels`) → same block + re-plan note.
- Actions that target the modal (pixel inside `bounds`, or name in
  `control_labels`, or a `hotkey` Enter/Escape) → execute normally. The model
  closes/confirms the modal → the next `observe` finds no modal → the loop
  resumes the real task.

This block fires **before** the "no local change" verifier, replacing the
observed death spiral (`no local change` → refine → repeated-click →
`guard_hits >= 5` → abort) with a single constructive "handle the dialog first"
redirect.

**No new guard counter.** The click-through block is a redirect, not a
`guard_hits` increment — it does not push the mission toward the circling-abort
cap, and it carries no per-unit-counter reset hazard (BUG-032 class avoided).

## Error handling & no-regression

- `detect_foreground_modal()` is fully defensive: any failure → `None` → the
  loop behaves exactly as today.
- When no modal is detected (the overwhelming common case), the added code path
  is a single `None` check — behavior is byte-for-byte unchanged.
- No change to exit codes, the `ToolExecutor`, the risk-tier policy, or the
  voice path.

## Known limitation

In-window modals with no separate top-level HWND (Electron / web overlays,
custom-painted in-process dialogs) are **not** detected by HWND-level probing.
The ShareX case is a native Win32 dialog and is covered. For web/Electron
overlays the behavior is unchanged from today (no regression) — the model still
sees them in the screenshot and can interact with them. Documented, not fixed.

## Testing (TDD, RED → GREEN)

**`tests/unit/harness/test_modal_guard.py`** — unit, cross-platform via injected
`user32` shim:

- owner present + owner disabled → returns `BlockingModal` with
  `signal="owner_disabled"` and the window rect.
- owner present + owner **enabled** (normal popup) → `None`.
- no owner → `None`.
- no foreground window (`GetForegroundWindow == 0`) → `None`.
- injected accessor raising → `None` (never propagates).
- non-Windows platform path → `None`.

**`tests/unit/harness/` CU-loop integration** (existing CU fakes):

- modal active + planned `click` outside `bounds` → action **not** executed; a
  re-plan note is appended to history; loop continues.
- modal active + planned `click_element` whose name is a modal control →
  executed normally.
- modal active + planned `click` inside `bounds` → executed normally.
- no modal → identical behavior to the pre-change loop (regression guard).

## Files touched

- **New:** `jarvis/harness/modal_guard.py`
- **New:** `tests/unit/harness/test_modal_guard.py`
- **Edit:** `jarvis/harness/screenshot_only_loop.py` — inline probe in the
  observe phase, `controls_hint` prefix, and the pre-`_execute_action`
  click-through block.
- **Edit (tests):** CU-loop integration test file under `tests/unit/harness/`.

## Anti-pattern alignment

- AP-3 (no direct `Tool.execute`): untouched — the block sits above the existing
  executor call.
- AP-9 (no latency on the voice critical path): N/A — CU loop is the background
  worker; probe is sub-ms `ctypes`.
- BUG-032 (stale cross-unit counter): avoided — the redirect adds no new
  per-unit counter and does not increment `guard_hits`.
- Cloud-first doctrine: Windows-only detection behind a graceful no-op seam; no
  new hard dependency; Computer-Use is a `[desktop]` extra.
