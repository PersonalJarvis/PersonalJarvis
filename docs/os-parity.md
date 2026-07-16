# OS Feature Parity — macOS / Linux Gap Register

**Binding rule:** [`CLAUDE.md`](../CLAUDE.md) §3 *"OS feature parity — macOS
and Linux are first-class"*. Every feature ships working on Windows, macOS,
and Linux (desktop AND headless) in the same change. A Windows-only
implementation may land only with a capability gate, honest degradation, and
an entry in this register.

**Last full audit:** 2026-07-16 — five-agent sweep across the entire feature
surface (Computer-Use/desktop actions, voice/audio stack, core/launcher/infra,
data/knowledge features + agent system, full feature inventory).

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
| P-01 | Medium | Orb/UI | macOS has no floating orb — tray-only surface (deliberate: Aqua-Tk worker-thread root natively aborts the process, BUG-057) | `jarvis/overlay/surface.py:177-229` | Tray icon color feedback instead of a floating orb; all voice features still work |
| P-02 | Medium | Awareness | Idle detection is Windows-only, and `IdleDetector.start()` lacks the platform gate its sibling watcher has — the 1 s loop spins forever reporting "never idle" | `jarvis/awareness/watchers/idle.py:58,151` | Idle-based awareness silently dead on macOS/Linux; wasted loop |
| P-03 | Medium | Awareness | Window-focus watcher is Windows-only (`SetWinEventHook` pump); clean no-op elsewhere | `jarvis/awareness/watchers/window.py:119` | No window-focus history in awareness on macOS/Linux (CU window control itself HAS per-OS paths) |
| P-04 | Medium | CU typing | Linux non-ASCII typing (umlauts, CJK, emoji) requires `xdotool`; pyautogui silently drops those chars without it | `jarvis/cu/actuate/posix.py:387-438` | With `xdotool` (installer provisions it since 2026-07-15): fine. Without: warning + silent char loss |
| P-05 | Low | Wiki | Wiki search hard-fails (RuntimeError with actionable apt/pysqlite3 remediation) on distros whose system SQLite lacks FTS5 | `jarvis/memory/wiki/fts_index.py:279` | `python:3.11-slim` and macOS ship FTS5 — only exotic/old distros affected; message is honest |
| P-06 | Low | Launcher | No POSIX launcher script parity to `run.bat` (installer bundle / `python -m jarvis.ui.web.launcher` exist) | `run.bat` | Functional via installer; convenience gap only |
| P-07 | Low | Audio | Host-API preference tables are Windows-name-driven ("Windows WASAPI", "WDM-KS"); no macOS/Linux preference exists, and headset-name heuristics are Windows-centric | `jarvis/audio/player.py:94-103`, `jarvis/audio/capture.py:154-168` | Device auto-pick falls back to OS default order — works, less clever than on Windows |
| P-08 | Low | Audio | Latent: `AudioPlayer.stop()` calls `sd.stop()` unguarded — AttributeError if sounddevice is absent AND stop() is ever reached (currently unreachable on headless) | `jarvis/audio/player.py:989` | None today; one-line guard removes the trap |
| P-09 | Low | Core | `ui/shell/single_instance.py` POSIX branch enforces nothing — dead code (the load-bearing lock `desktop_app.acquire_single_instance_lock` IS cross-platform) | `jarvis/ui/shell/single_instance.py:109-112` | None; remove or port to filelock before any future use |
| P-10 | Low | Missions | POSIX worker reaper: a hard SIGKILL of the orchestrator itself reparents the worker tree to init (Windows kernel Job Object covers this case); documented limit, `PR_SET_PDEATHSIG` noted as future fix | `jarvis/missions/isolation/job_object.py:327-340` | Only on orchestrator SIGKILL; normal cancel/kill paths reap correctly |
| P-11 | Info | Launcher | Startup log line prints `http://127.0.0.1:{port}` even when bound to `0.0.0.0` (VPS) | `jarvis/ui/web/launcher.py:716` | Cosmetic/misleading log only; bind itself is correct |
| P-12 | Info | CU legacy | Frozen legacy CU loops are Windows-only, but NOT on the live path (harness force-routes to v2); imports are lazy | `jarvis/cu/loops/screenshot_only_loop.py` et al. | None at runtime |
| P-13 | Info | Wiki | Wiki DB/vault anchor at `repo_root()` — read-only *wheel* installs would fail writes (not OS-specific; `JARVIS_DATA_DIR` override exists) | `jarvis/memory/wiki/db_path.py:9`, `vault_root.py:59` | None on the advertised install paths |
| P-14 | Info | CU extras | macOS/Linux actuation and UI trees depend on optional extras (pynput, pyobjc, pyatspi); without them everything degrades honestly to screenshot + pixel-click | `jarvis/cu/actuate/posix.py`, `jarvis/vision/tree_factory.py` | By design (§3); bare install keeps the CU loop functional |

## Maintenance

- Fixing a gap: remove its row (git history keeps the record).
- Landing a new Windows-only implementation: add a row (required by
  CLAUDE.md §3) with impact, evidence, and off-Windows behavior.
- Re-audit cadence: rerun the five-area sweep after any release that touches
  platform seams (`jarvis/platform/`, `jarvis/cu/actuate/`, `jarvis/vision/`,
  `jarvis/audio/`, `jarvis/missions/isolation/`).
