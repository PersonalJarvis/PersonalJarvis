# ADR (plan-local): Which overlay framework does the Orb port target?

> Wave 0, sub-task 0.6 — resolves PC-6. This is a decision note, not the build;
> the `OverlaySurface` abstraction itself is Wave 2. Feeds AD-11.

## Context

Two overlay code paths exist in the tree, and the research agents disagreed on
which is live. Ground truth, verified on the working tree (2026-05-29):

| Path | Framework | Status | Evidence |
|---|---|---|---|
| `ui/orb/overlay.py` (top-level `ui/` package) | **Tkinter** | **LIVE** | `ui/orb/overlay.py` + `ui/orb/__init__.py` exist; uses `wm_attributes("-transparentcolor", COLOR_KEY_HEX)`; imported by the live supervisor via `ui/orb/bus_bridge.py`, `ui/orb/mic_listener.py` |
| `jarvis/overlay/` (`supervisor.py`, `bridge.py`) | process mgmt | **LIVE** | spawns the orb as a separate `pythonw` subprocess + Win32 Job-Object kill-on-close; references `ui.orb` |
| `OS-Level/src/overlay/` (19 modules) | **PySide6 / Qt** | **ABANDONED** | exists but is the rejected approach; only `tests/overlay/conftest.py` adds it to `sys.path` |

Note the earlier-research path `jarvis/ui/orb/overlay.py` **does not exist** — the
live Tk orb is the top-level `ui/orb/` package (the repo-root `conftest.py:7`
adds the repo root to `sys.path` precisely so `import ui.orb` resolves).

## Decision

**The `OverlaySurface` abstraction (Wave 2) wraps the live Tk orb
(`ui/orb/overlay.py`) as `TkColorKeyOverlay`, not the PySide6 tree.**

Rationale:
- Tk's `-transparentcolor` (color-key transparency → Win32 `LWA_COLORKEY`) works
  on **Windows and macOS**, matching AD-11's cross-platform default.
- The PySide6 tree under `OS-Level/src/overlay/` is the abandoned approach and
  must **not** be re-imported as the live overlay.
- On Linux the color-key trick is unavailable on X11 Tk and unreliable under
  Wayland, so the Wave-2 ladder degrades there to the already-cross-platform
  pystray tray (`jarvis/ui/tray.py`) — `TrayOnlySurface`.

## Consequences / scope guard

- `OS-Level/src/overlay/` is **not deleted** by this plan (grandfathered, out of
  scope). It is simply not the live overlay and gains no new callers.
- The Windows system-cursor swap (`jarvis/overlay/system_cursor.py`,
  `SetSystemCursor`) stays Windows-only — there is no macOS/Linux API to swap the
  global cursor handle; it is already a no-op off-Windows.
- Wave-0 sub-task 0.7 keeps the live `import ui.orb` path working and gates the
  abandoned PySide6 overlay tests so they skip cleanly on a box without PySide6
  (and never pollute the min-passed floor).
