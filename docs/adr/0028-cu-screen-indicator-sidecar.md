# ADR-0028: Computer-Use screen indicator as a minimal Qt sidecar (replaces the OS-Level overlay)

Date: 2026-07-15
Status: Accepted

## Context

Users need an unmistakable, always-visible signal that Jarvis is
controlling their mouse and keyboard: a colored glow around the monitor
edges plus an "Esc to cancel" hint — the screen-edge affordance modern
agent desktops have made familiar. Two prior states existed:

1. The **Phase-9 OS-Level overlay** contained a finished `EdgeGlowWindow`
   (PySide6 + QtWebEngine rendering a Vite/TypeScript edge-glow page),
   but it was disabled by default, never wired to the Computer-Use loop,
   scoped Windows-only by its own plan, and its import chain once broke
   the CU mouse tools on hosts without its manual editable install
   (fix eda6b318). Dead weight with real breakage risk.
2. **No cancel affordance**: the engine polled its cancel token per step,
   and the voice hangup / kill hotkey / tray stop could cancel missions,
   but nothing on the screen told the user a mission was running or how
   to stop it with one key.

## Decision

- **Delete the OS-Level subsystem entirely** (`OS-Level/`,
  `tests/overlay/`, and its glue in `jarvis/overlay/` — commit
  eb91e393). The live parts of `jarvis/overlay/` (surface ladder,
  drop targets, virtual/system cursor) stay.
- **Rebuild the indicator from scratch as `jarvis/cu/indicator`**, a
  minimal PySide6 sidecar process (`python -m jarvis.cu.indicator`):
  QPainter gradients only — no QtWebEngine, no web frontend, no IPC
  server stack. One frameless, click-through, always-on-top,
  never-activating window per monitor; soft breathing Jarvis-gold glow;
  localized "Esc to cancel" pill on the primary screen; JSON-lines
  stdin/stdout protocol; exits on parent EOF.
- **Lifecycle events, not polling**: `CUControlStarted`/`CUControlEnded`
  are published at the `ComputerUseHarness.invoke()` boundary — exactly
  where mouse/keyboard control begins and ends on every exit path. A
  main-process controller refcounts them (missions overlap), spawns the
  sidecar lazily on the first Started and kills it on the last Ended
  (AP-26: nothing on the boot path; PySide6 never imported in the main
  process).
- **Escape cancels everything**: while ≥1 mission runs, the controller
  arms a global Escape binding through the existing cross-platform
  `HotkeyTrigger` backends and cancels ALL active missions via the same
  CU-scoped token registry the voice hangup uses (engine exit 130,
  honest "cancelled"). A Jarvis-synthesized Esc is stamped by the
  actuation layer and ignored (`self_input`). Escape stays armed even
  when the visual is disabled — it is a safety affordance.
- **Capture hygiene**: on Windows the sidecar windows carry
  `WDA_EXCLUDEFROMCAPTURE`, so neither CU's own perception frames nor
  user screenshots/recordings contain the border
  (`JARVIS_CU_INDICATOR_CAPTURABLE=1` re-enables capture for visual
  verification). On macOS/Linux, where no such API exists, a fail-open
  capture guard blanks the border for the split second of each CU frame
  grab.
- **Universality**: `pyside6-essentials` lives in the `[desktop]` extra.
  Headless hosts, Wayland sessions, and base installs degrade to one
  logged English line — never a crash, never a blocked mission. The
  pill text resolves through the one runtime language resolver with a
  full de/en/es phrase table.

## Consequences

- A user always sees when Jarvis drives their machine and can stop it
  with one key — on Windows, macOS, and Linux-X11 alike.
- The dormant overlay tech debt (87 tracked files + glue) is gone; the
  mascot/cursor-trail features it bundled would need a fresh design if
  ever wanted again.
- Wayland gets no border and no global Escape (no always-on-top surface,
  no global hotkeys there); cancel remains available via voice, tray,
  and the kill hotkey inside focused apps. Documented degradation.
- The sidecar is fire-and-forget: a crash or broken pipe disables the
  visual for the mission and logs once; CU never waits on it beyond the
  150 ms capture-guard ack timeout.

## Alternatives considered

- **Reuse the OS-Level `EdgeGlowWindow`**: functionally closest, but it
  dragged in QtWebEngine + a TS build chain + a WS/SHM IPC stack for
  what a single QPainter paint routine does, was Windows-scoped by
  design, and the maintainer explicitly chose a from-scratch rebuild.
- **Tk (like the orb/JarvisBar)**: no per-pixel alpha (color-key only →
  jagged glow), macOS Tk is main-thread-only (BUG-057), and click-through
  needs per-OS hacks — three fragile paths instead of one.
- **Per-OS native windows (ctypes/pyobjc/X11)**: zero new dependencies
  but three implementations to build and verify; rejected on risk and
  maintenance grounds.
