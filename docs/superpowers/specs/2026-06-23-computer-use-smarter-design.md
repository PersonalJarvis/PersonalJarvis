# Computer-Use: Situational Awareness, Click Precision & Selectable Model

Date: 2026-06-23
Status: approved (maintainer), implementation in progress

## Problem

Computer-Use (CU) makes "obvious" mistakes that cost extra steps and time. Example:
OBS is already running and visible in the taskbar; the user says "open OBS and go to
settings", and CU still plans an `open_app` step (re-launching), because it only sees
the current screenshot and the single foreground window — it has no knowledge of what
is already running but minimized/backgrounded.

Root cause: **state blindness, not model weakness.** A minimized OBS is invisible to the
planning model regardless of model size. The highest-leverage fix is to give CU cheap,
real state — plus a user-selectable model so power users can trade latency for quality on
their own terms.

CU runs on the *active brain provider* and uses that provider's `model` (the user's
configured main model). On the maintainer's box that is `gemini-3.5-flash`. The strong
tier (`deep_model`) is `gemini-3.1-pro-preview`. Selection is provider-agnostic
(`manager._fast_model(active_provider)` → `cfg.model`), AP-21-compliant.

## Goals

1. CU stops re-launching apps that are already running; it focuses the existing window.
2. CU plans with awareness of what is currently open.
3. CU clicks more precisely (UIA fallback when a pixel click misses).
4. The CU model is user-selectable per provider, defaulting to the configured `model`.
5. No existing feature breaks. Cross-platform correct (Win/macOS/Linux + headless).

## Non-goals

- No automatic mid-mission model escalation (explicitly rejected by maintainer — wants
  explicit control, no surprise latency).
- No new hard dependency; the headless `python:3.11-slim` base install must still boot.

## Architecture

Four phases, sequential, each independently testable and useful. Lowest risk first.

### Phase 0 — Foundation: reusable window-state utility

New module `jarvis/platform/window_state.py` behind the existing platform seam
(`detect_platform()` + probes). Public API:

```python
@dataclass(frozen=True)
class WindowInfo:
    title: str
    minimized: bool = False
    handle: int | None = None   # opaque platform handle (hwnd on Windows; None elsewhere)

def list_windows() -> list[WindowInfo]          # all visible top-level windows
def get_foreground_title() -> str               # current foreground window title ("" if unknown)
def focus_window(title_contains: str) -> tuple[bool, str]
def is_app_running(app_name: str) -> WindowInfo | None   # conservative title match; None = not found
```

Per-OS implementation:
- Windows: ctypes `EnumWindows` + `GetWindowTextW` + `IsWindowVisible` (+ `IsIconic` for
  minimized, `GetForegroundWindow` for foreground). `focus_window` reuses the exact
  battle-tested logic currently in `switch_window._find_and_focus_windows` (AD-7: behavior
  unchanged — code is moved, not rewritten; old names re-exported so existing tests pass).
- macOS: `osascript` / System Events (list + raise), reusing the existing escaped/
  injection-safe AppleScript path.
- Linux/X11: `wmctrl -l` (list) + `wmctrl -i -a` (focus). Wayland/headless degrade to an
  empty list / clear English message (AD-13), never a hard failure.
- Anything unknown / headless: empty list, `(False, message)`.

`switch_window` is refactored to delegate its focus to `window_state.focus_window`.

`is_app_running` is **conservative**: it only reports a match when an app token clearly
appears in a window title (case-insensitive substring, with a small alias map for known
mismatches like `calc`→`calculator`). When uncertain it returns `None`, so the caller
falls through to the normal launch — a false negative just reproduces today's behavior
(no regression); a false positive (wrongly focusing instead of launching) is the worse
error, so we bias against it.

Error handling: every probe swallows its own exceptions to a logged empty result — the
utility never raises into a caller.

### Phase 1 — Awareness

1a. **State in the prompt.** At each CU step, gather `list_windows()` + `get_foreground_title()`
and inject a compact line alongside the screenshot, e.g.
`Currently open windows: OBS Studio (minimized), Chrome — YouTube, Discord. Foreground: Chrome.`
Gathered cheaply (microseconds), wrapped in try/except → empty string on failure or on a
platform that returns nothing. Off the critical path concerns do not apply: this is a
single EnumWindows call, not the heavy awareness layer.

1b. **`open_app` already-running short-circuit.** Before launching, call
`window_state.is_app_running(app_name)`. If it matches AND the app is not in a small
multi-instance allowlist (`explorer`, `cmd`, `powershell`, `pwsh`, `wt`, `terminal`,
`conhost`) AND `reuse_existing` is true → focus the existing window and return
`"<app> is already running — brought it to the front."` instead of launching. New schema
field `reuse_existing: bool = True`; an explicit "new window" intent sets it false.

1c. **`switch_window` becomes a CU action.** Add `"switch_window"` to `_VALID_ACTIONS`, add
a dispatch branch in the action executor, and add it to the planner action schema + the
system prompt so the model can emit "switch to the OBS window" directly when it sees from
the window list that OBS is already open.

### Phase 2 — Click precision: UIA snap on a missed pixel click

In `_click_with_refine`, when the existing post-click verification (regional screenshot
diff) reports "no visible change", attempt a UIA snap **before** the expensive LLM refine
retry: query `make_ui_tree_source()`, find the clickable element whose bounding box
contains the guessed `(x, y)` (or is nearest within a small radius), and click its center.
If UIA finds nothing (or no tree backend is available — headless/Null), fall through to the
existing refine retry. Purely additive; no existing path is removed.

### Phase 3 — Selectable Computer-Use model (per provider)

- Config: new optional field `cu_model: str | None = None` on the brain provider config
  model (sibling of `deep_model`).
- Selection: `BrainManager._cu_model(provider)` = `cfg.cu_model or cfg.model or
  get_tier_default_model("router", provider)`. The CU loop uses this (via a
  `_select_cu_model` helper that prefers `_cu_model`, falling back to the existing
  `_select_fast_model` chain for stubs). Provider-agnostic, AP-21-compliant.
- Persistence: `config_writer.set_cu_model(provider, model)` (lock + tempfile + BOM-safe).
- REST: an endpoint to read/write the per-provider CU model (mirrors the existing
  per-provider model endpoints).
- UI: a "Computer-Use model" picker per provider in the API-Keys/provider view, reusing
  the existing searchable per-provider model combobox. Empty = "use my main model".

## Data flow

```
CU step → ComputerUseContext → screenshot + goal + history + [window-state line]
        → brain (provider's cu_model) → action JSON
        → execute: open_app (is_app_running? → focus) | switch_window | click (→ UIA snap on miss) | …
```

## Testing

Per phase, TDD (RED→GREEN). All cross-platform branches exercised via `detect_platform`/
probe monkeypatching + faked ctypes/subprocess (seam-level — proves dispatch + parsing,
NOT real osascript/wmctrl/UIA behavior on real hardware, per SIGNOFF-LOG honesty).

- Phase 0: `tests/unit/platform/test_window_state.py` — list/focus/is_app_running per OS,
  headless/Wayland degrade, conservative-match guard. Existing `test_switch_window.py`
  must stay green (regression guard for the delegation).
- Phase 1: open_app short-circuit (running → focus, multi-instance → still launch, uncertain
  → still launch, force-new → still launch); awareness-line formatting; switch_window action
  dispatch + valid-actions parity.
- Phase 2: missed-click → UIA snap clicks the element; UIA-empty → falls through to refine;
  no-tree → falls through. Existing click tests stay green.
- Phase 3: `_cu_model` resolution precedence; config round-trip; route; frontend tsc+build.
- Full suite + `ruff check` + `mypy` on touched files. No newly-added German lines
  (language-policy gate).

## Risks / guardrails

- AP-21: never branch on provider name/model id — gate on capability / use provider-agnostic
  tier lookups.
- AD-7: Windows window logic moved verbatim, guarded by existing + new tests.
- AP-1: every subprocess keeps `NO_WINDOW_CREATIONFLAGS`.
- AP-7: config writes via `config_writer` only.
- Latency: `EnumWindows` is microseconds; awareness gathering is wrapped + best-effort.
- Headless VPS base install still boots (window_state degrades to empty; no new hard dep).
- open_app reuse biases against false positives (uncertain → launch as before).
