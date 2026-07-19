# OS Feature Parity — macOS / Linux Gap Register

**Binding rule:** [`CLAUDE.md`](../CLAUDE.md) §3 *"OS feature parity — macOS
and Linux are first-class"*. Every feature ships working on Windows, macOS,
and Linux (desktop AND headless) in the same change. A Windows-only
implementation may land only with a capability gate, honest degradation, and
an entry in this register.

**Last full audit:** 2026-07-16 — five-agent sweep across the entire feature
surface (Computer-Use/desktop actions, voice/audio stack, core/launcher/infra,
data/knowledge features + agent system, full feature inventory).

**Fix pass 2026-07-16 (same day):** P-06/P-08/P-09/P-11 fixed and removed;
P-02/P-03 implemented for macOS + X11 Linux (rows narrowed to the Wayland
residual); P-10 fixed on Linux via `PR_SET_PDEATHSIG` (row narrowed to
macOS). Git history of this file keeps the original entries.

**Fix pass 2026-07-19:** P-01 fixed and removed. Both the Jarvis Bar and the
mascot now use a main-thread companion-process host on macOS; rendered images
and bubble fonts are explicitly bound to the overlay's Tcl interpreter so the
host's Tk bootstrap root cannot steal them.

**Desktop download follow-up 2026-07-19:** saved-file drag-out now has native
Windows (OLE/WebView2) and macOS (AppKit/WKWebView) sources. P-15 records the
remaining GTK source gap; reveal/open actions remain available on Linux.

## Audit verdict summary

**No hard breakers found.** No feature crashes on macOS or headless Linux;
no ungated Windows module-level import exists anywhere in `jarvis/`; no
runtime code path hardcodes a Windows path. The platform seams
(`jarvis/cu/actuate/`, `jarvis/vision/tree_factory.py`,
`jarvis/platform/probes.py`, `jarvis/missions/isolation/job_object.py`,
`config._ensure_keyring_backend`) all carry real macOS and Linux
implementations, not stubs.

| Area | Verdict |
|---|---|
| Computer-Use / desktop actions (click, type, hotkey, scroll, drag, windows, apps, screenshots, UI trees) | Full per-OS backends (Win32/UIA, Quartz/AX, xdotool/AT-SPI); honest degradation on Wayland/headless/missing TCC grants |
| Voice / audio (capture, playback, VAD, wake, STT, TTS, realtime) | Clean; headless disables voice honestly; WASAPI logic is inert-by-data off Windows |
| Core (launcher, config, keyring, restart, autostart, tray, elevation, paths) | Clean; per-OS autostart (Registry / LaunchAgent / XDG `.desktop`), keyring falls back to a 0600 file on headless hosts |
| Data / agents (wiki, contacts, telephony, sessions, missions, skills, self-mod, channels, MCP) | Clean; mission workers run on POSIX with a real process-group reaper |

## Open parity gaps

Ordered by user impact. "Behavior" describes what a macOS/Linux user actually
experiences today.

| # | Impact | Area | Gap | Evidence | Behavior off-Windows |
|---|---|---|---|---|---|
| P-02 | Low | Awareness | Idle detection has no Wayland backend (Windows GetLastInputInfo, macOS Quartz, Linux X11 `xprintidle` all exist since 2026-07-16); Wayland exposes no global idle time without portal support | `jarvis/awareness/watchers/idle.py` | Wayland: one honest log line, watcher does not start |
| P-03 | Low | Awareness | Window-focus watcher has no Wayland backend (Windows event hook, macOS NSWorkspace, Linux X11 polling all exist since 2026-07-16); Wayland hides the foreground window by design | `jarvis/awareness/watchers/window.py` | Wayland: one honest log line, watcher does not start |
| P-04 | Medium | CU typing | Linux non-ASCII typing (umlauts, CJK, emoji) requires `xdotool`; pyautogui silently drops those chars without it | `jarvis/cu/actuate/posix.py:387-438` | With `xdotool` (installer provisions it since 2026-07-15): fine. Without: warning + silent char loss |
| P-05 | Low | Wiki | Wiki search hard-fails (RuntimeError with actionable apt/pysqlite3 remediation) on distros whose system SQLite lacks FTS5 | `jarvis/memory/wiki/fts_index.py:279` | `python:3.11-slim` and macOS ship FTS5 — only exotic/old distros affected; message is honest. Decision 2026-07-16: kept as honest hard error — a pysqlite3 shim would rewire seven wiki modules for an exotic audience |
| P-07 | Low | Audio | No macOS/Linux host-API preference exists (the Windows-name-driven tables are intentionally inert off Windows — documented in-code since 2026-07-16), and headset-name heuristics are Windows-centric | `jarvis/audio/player.py`, `jarvis/audio/capture.py` | Device auto-pick falls back to OS default order — works, less clever than on Windows |
| P-10 | Low | Missions | macOS worker reaper: a hard SIGKILL of the orchestrator reparents the worker tree to init (Linux covered via `PR_SET_PDEATHSIG` since 2026-07-16; Windows covered by the kernel Job Object; macOS needs a kqueue `EVFILT_PROC` watcher) | `jarvis/missions/isolation/job_object.py:327-350` | macOS only, and only on orchestrator SIGKILL; normal cancel/kill paths reap correctly |
| P-12 | Info | CU legacy | Frozen legacy CU loops are Windows-only, but NOT on the live path (harness force-routes to v2); imports are lazy | `jarvis/cu/loops/screenshot_only_loop.py` et al. | None at runtime |
| P-13 | Info | Wiki | Wiki DB/vault anchor at `repo_root()` — read-only *wheel* installs would fail writes (not OS-specific; `JARVIS_DATA_DIR` override exists) | `jarvis/memory/wiki/db_path.py:9`, `vault_root.py:59` | None on the advertised install paths |
| P-14 | Info | CU extras | macOS/Linux actuation and UI trees depend on optional extras (pynput, pyobjc, pyatspi); without them everything degrades honestly to screenshot + pixel-click | `jarvis/cu/actuate/posix.py`, `jarvis/vision/tree_factory.py` | By design (§3); bare install keeps the CU loop functional |
| P-15 | Low | Desktop downloads | Native drag-out has Windows OLE and macOS AppKit sources but no GTK/WebKitGTK source yet | `jarvis/ui/native_drag.py` | Linux desktop: the saved-file toast keeps reliable **Show in folder** and **Open** actions but is not itself a drag handle; headless: the normal browser download path remains available |

## Maintenance

- Fixing a gap: remove its row (git history keeps the record).
- Landing a new Windows-only implementation: add a row (required by
  CLAUDE.md §3) with impact, evidence, and off-Windows behavior.
- Re-audit cadence: rerun the five-area sweep after any release that touches
  platform seams (`jarvis/platform/`, `jarvis/cu/actuate/`, `jarvis/vision/`,
  `jarvis/audio/`, `jarvis/missions/isolation/`).
