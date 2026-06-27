# Computer-Use: opt-in proactive zoom-before-click

- **Date:** 2026-06-27
- **Status:** Design (approved for spec)
- **Area:** OS-level Computer-Use (`jarvis/harness/screenshot_only_loop.py`)
- **Volume:** small, opt-in, non-invasive (first iteration)

## Problem

The screenshot-only Computer-Use loop sometimes clicks the **wrong UI element**.
The current accuracy machinery is **reactive**: it clicks first, then checks
whether the click landed, and only repairs a *detected* miss.

The detection itself is the gap. After a click, `_click_with_refine`
(`screenshot_only_loop.py:2937-2958`) captures a small crop around the clicked
point and compares it pixel-for-pixel before/after a settle delay
(`_VERIFY_CROP_RADIUS_PX = 110`, `_CLICK_VERIFY_SETTLE_S = 0.6`). The accept
condition is `post != pre` — i.e. *"something near the click visibly reacted"*.
A click that lands on the **wrong but reactive** control (a neighbouring button
that highlights, a different list row that selects) passes this check. The loop
then moves on believing it succeeded.

So the existing reactive path handles **near-misses** (off by a few pixels)
well — via UIA element-snap (`_uia_snap_click`, `:2818`) and an LLM zoom-refine
retry (`_refine_click_point`, `:2680`) — but it does **not** catch
*wrong-element* selection, because the verification only asks "did anything
react", never "did the *intended* thing react".

## Current behaviour (what already exists — do not rebuild)

The loop already contains a full screenshot-crop zoom-refine mechanism, but it
is **gated to retries only**:

- `_click_with_refine` (`:2862`) runs `1 + 2` attempts (`_CLICK_MAX_ATTEMPTS = 3`).
- On attempt 0 it clicks the **coarse** model point directly. The refine pass is
  guarded by `if clicked:` (`:2894`) — it runs only **after** a click already
  happened. The inline comment (`:2895-2900`) records *why*: in live runs the
  first-attempt refine corrected the point by ≤5 px — "pure cost" (one extra LLM
  round-trip) for a near-zero accuracy gain on the *easy* case.
- `_resolve_click_pixel` (`:2510`) maps the model's `0..1000` normalized
  coordinate to an absolute screen pixel against the captured monitor geometry.
- `_refine_click_point` (`:2680`): grabs a live crop around `(x, y)`
  (`_refine_crop_bbox`, radius `max(180px, 14% of monitor width)`), sends it to
  the brain with `_REFINE_SYSTEM_PROMPT`, and parses a strict
  `{"found": bool, "x": 0-1000, "y": 0-1000}` verdict
  (`_parse_refine_verdict`, `:2646`). It returns `(True, ax, ay)` (relocated),
  `(False, 0, 0)` (target genuinely not in the crop), or `None` (any failure →
  keep coarse estimate).
- The crop is a **digital screenshot crop** captured live via
  `capture_region` / mss (`_grab_region_jpeg`, `:2621`). It does **not** change
  the application or OS zoom level and renders nothing on screen.

Key realization: the building blocks for proactive zoom-before-click **already
exist**. The first iteration is mostly a gated *re-enable* plus safe framing, not
new machinery.

## Goal

When explicitly enabled, perform the zoom-refine **before** the first click so
the model confirms or relocates the named target inside a magnified live crop,
and **refuses to click** when the target is not actually in the crop — turning
the silent wrong-element click into a re-plan.

### Non-goals (this iteration)

- No confidence/risk signal and no adaptive "zoom only when risky" heuristic
  (that is the deferred Approach B).
- No revival of the orphaned numbered Set-of-Marks overlay and no browser-DOM /
  accessibility-first targeting path (deferred Approach C — `set_of_marks.py`
  stays orphaned).
- No change to the default fast path. With the flag off, behaviour is byte-for-byte
  the current behaviour.

## Hard requirements

1. **Invisible to the user (maintainer requirement, 2026-06-27).** The zoom is an
   **internal screenshot crop only**. There is no on-screen magnification, no
   lens/zoom overlay, no zoom animation, and the cursor does not move for the
   zoom step (it moves only at the real click, exactly as today). Nothing about
   the proactive zoom is observable on the monitor.
2. **Opt-in, default off.** The current latency profile must not regress for any
   user who does not turn it on.
3. **Fail-safe.** Any failure of the zoom step degrades to today's plain coarse
   click — never a hard error, never a skipped click that the user asked for.
4. **No application-visible side effects.** The target application must observe
   nothing (no synthetic zoom keystroke, no DOM mutation, no accessibility-tree
   write) — only a passive screen read.

## Design

### Configuration

Add to `ComputerUseConfig` (`jarvis/core/config.py`):

```python
zoom_before_click: bool = False
```

- Default `False` (requirement 2).
- Inherits the existing whole-config voice-mutability — no extra UI/route work
  (the entire `JarvisConfig` is already voice-editable with honest readback).
- Documented in the field as: *"Proactively zoom-refine each click target before
  clicking (one extra model call per click; default off). Internal screenshot
  crop only — invisible on screen."*

### Threading into the run context

`zoom_before_click` is read off `ComputerUseContext`, the same way
`verify_after_each_step` and `uia_click_fallback` already are
(`getattr(ctx, "zoom_before_click", False)`). It is populated from config where
the context is built, so a single resolver owns the value and no layer
re-derives it.

### The one behavioural change

In `_click_with_refine` (`:2892-2904`), the refine pass is currently:

```python
if clicked:
    refined = await _refine_click_point(...)
```

It becomes (conceptually):

```python
proactive = (
    getattr(ctx, "zoom_before_click", False)
    and not clicked          # only the first, pre-click pass
    and bool(target)         # needs a named target to confirm against
)
if clicked or proactive:
    refined = await _refine_click_point(...)
```

Nothing else in the loop changes. The existing handling of the refine verdict
already produces the right outcomes:

- **found + relocated** → `x, y` updated, then clicked (`:2924-2925`).
- **found + unchanged within `_REFINE_TOL_PX`** → confirmed, clicked.
- **not found, no prior click, target present** → returns
  `False, "refine: target ... not found near (x,y) — re-plan from a fresh
  screenshot"` (`:2915-2922`). **This is the wrong-element guard** and it is
  already implemented; the flag simply lets it run before the first click.
- **refine returns `None`** (crop grab failed, brain timeout, malformed verdict)
  → falls through to the coarse click (requirement 3), already the behaviour.

### Why target-gated even when the flag is on

The refine model confirms against a *named* target. Click actions already carry a
`target` field (`obj.get("target")`, `:2876`). When the model emits no target,
the proactive zoom has nothing to verify against and would only add a round-trip,
so it is skipped and the coarse click runs. This keeps the cost proportional to
the cases where zoom actually helps.

### Interaction with the existing reactive path

Proactive zoom is **additive**. If the (now refined) first click still produces a
verified miss, the existing retry path — UIA-snap then reactive refine — still
runs, bounded by the unchanged `_CLICK_MAX_ATTEMPTS = 3`. The proactive refine
happens *inside* attempt 0 and does not add an attempt.

## Safety analysis (against the stated risks)

- **Layout shifts:** impossible. The zoom is a crop of an already-captured
  screenshot; the application/browser zoom level is never touched. No keystroke
  (`Ctrl`+`+`), no resize.
- **Coordinate mismatch:** the crop is captured live at native resolution around
  the coarse point; the model answers in crop-relative `0..1000`, mapped back by
  `_crop_norm_to_abs` (`:2634`) which clamps the result inside the crop. A wildly
  off coarse estimate (target outside the ~14%-of-width crop) yields *not found*
  → re-plan, never a wild click.
- **Accessibility side effects:** none. The proactive zoom reads only a screen
  image; it does not query or mutate the accessibility tree. (The separate,
  already-existing UIA-snap on a verified miss only *reads* the tree.)
- **Automation detection:** zero added surface. A screenshot crop produces no
  events in the target application — strictly less observable than an actual zoom
  gesture would be.
- **On-screen visibility:** none — see hard requirement 1.

## Cross-platform & browser compatibility

- **Capture path** (`capture_region` via mss) is identical on Windows, macOS and
  Linux, so the proactive crop works on all three.
- **macOS** needs the Screen-Recording TCC permission, which is already probed
  (`screen_recording_granted`); without it, capture returns and the step
  degrades to the coarse click.
- **Linux/Wayland** screenshot restrictions are already handled by the graceful
  `None` return from `_grab_region_jpeg` → coarse click.
- **Browsers** need no special handling: the crop is application-agnostic. A
  browser-/DOM-aware targeting path is explicitly out of scope (Approach C).
- The verified-miss **UIA-snap** remains Windows-only and degrades to a no-op via
  `make_ui_tree_source` (AX / AT-SPI / Null) elsewhere — unchanged by this work.

## Latency

One extra brain round-trip per *targeted* click, only when the flag is on. The
call reuses `_think_timeout_s(ctx)` and sits inside the per-step budget
(`per_step_timeout_s = 30`, `think_timeout_cap_s = 10`). On a 1–2 Hz mission loop
this is acceptable; default-off keeps the tuned-fast default untouched.

## Testing

Unit tests around `_click_with_refine` using the repo's fakes (no `unittest.mock`):

1. flag **off** → no proactive refine call; attempt 0 clicks the coarse point
   (regression guard for the default path).
2. flag **on**, target present, refine **relocates** → clicks the refined point.
3. flag **on**, target present, refine **not found** → returns the re-plan signal,
   **no click dispatched** (the wrong-element guard).
4. flag **on**, **no target** → coarse click, no refine call.
5. flag **on**, refine returns **`None`** (crop/brain failure) → coarse click
   (fail-safe).
6. config round-trip: `zoom_before_click` parses, defaults to `False`, and reaches
   `ComputerUseContext`.

## Files touched (anticipated)

- `jarvis/core/config.py` — new `ComputerUseConfig.zoom_before_click` field.
- the Computer-Use context construction site — thread the flag onto
  `ComputerUseContext`.
- `jarvis/harness/screenshot_only_loop.py` — the gate change in
  `_click_with_refine`.
- `tests/unit/...` — the six tests above.

## Out of scope / future iterations

- **Approach B (adaptive):** zoom only on risky clicks (small target / dense
  region / low model confidence), once a confidence signal exists.
- **Approach C (accessibility/browser-first):** lean on the UIA/AX tree or revive
  numbered Set-of-Marks for the highest accuracy.
