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
| P-24 | Medium | Dictation shortcut | The global dictation/call shortcut needs `pynput` on Linux/X11, and `pynput` hard-requires `evdev` — which is published **source-only** (verified on PyPI 2026-07-28: evdev 1.9.3 ships an sdist and no wheels) and compiles against the kernel headers. Putting it in `[full]` would break the one advertised install path on a stock `python:3.11-slim`, so it is the opt-in `[desktop-linux]` extra instead. Wayland is a separate, unfixable-by-install case: the compositor owns global shortcuts by design (the XDG `GlobalShortcuts` portal lets the *compositor* assign the keys, and no wlroots compositor implements it at all) | `pyproject.toml` (`desktop-linux`), `jarvis/platform/probes.py::has_hotkey`, `jarvis/trigger/backends/noop.py::explain_unavailable` | X11 without the extra: no global shortcut, and the log/UI now names the actual cause and the exact `pip install` that fixes it (it used to blame Wayland unconditionally). Wayland: no global shortcut at all — bind a compositor shortcut to `jarvis api dictation start`. On both, dictation still works from the Jarvis Bar, the Dictation view and the CLI, and voice still works via the wake word |
| P-25 | Medium | Dictation insertion | Pasting the transcript into another application is blocked, silently, in three OS-specific situations: Windows UIPI when the foreground window is elevated and Jarvis is not (`SendInput` reports success and the input is discarded), macOS Secure Input while a password field is focused, and Wayland outright (no synthetic input). Detection exists for the first two; Wayland is refused up front | `jarvis/dictation/insert.py::describe_target`, `jarvis/platform/input_isolation.py::windows_foreground_window_is_elevated`, `macos_secure_input_enabled` | All three degrade to the SAME honest outcome instead of silence: the transcript is left on the clipboard, the result is reported as `clipboard_only`, and the bar plus the Dictation view say why and that Ctrl+V will paste it. macOS Secure Input detection is implemented but has not been verified on real hardware from this machine |
| P-02 | Low | Awareness | Idle detection has no Wayland backend (Windows GetLastInputInfo, macOS Quartz, Linux X11 `xprintidle` all exist since 2026-07-16); Wayland exposes no global idle time without portal support | `jarvis/awareness/watchers/idle.py` | Wayland: one honest log line, watcher does not start |
| P-03 | Low | Awareness | Window-focus watcher has no Wayland backend (Windows event hook, macOS NSWorkspace, Linux X11 polling all exist since 2026-07-16); Wayland hides the foreground window by design | `jarvis/awareness/watchers/window.py` | Wayland: one honest log line, watcher does not start |
| P-04 | Medium | CU typing | Linux desktop Unicode text input needs the system `xdotool` binary (pip cannot install it); the pyautogui fallback used on Linux drops non-ASCII chars (umlauts, CJK, emoji) without it | `jarvis/cu/actuate/posix.py::type_text`, `jarvis/plugins/tool/type_text.py` | With `xdotool` (installer provisions it since 2026-07-15): fine. Without, the drop is now reported HONESTLY (2026-07-23): an all-non-ASCII text fails with an actionable "install xdotool" error, and a mixed text types its ASCII portion and warns that the rest was dropped — no more silent success |
| P-05 | Low | Wiki | Wiki search hard-fails (RuntimeError with actionable apt/pysqlite3 remediation) on distros whose system SQLite lacks FTS5 | `jarvis/memory/wiki/fts_index.py:279` | `python:3.11-slim` and macOS ship FTS5 — only exotic/old distros affected; message is honest. Decision 2026-07-16: kept as honest hard error — a pysqlite3 shim would rewire seven wiki modules for an exotic audience |
| P-07 | Low | Audio | No macOS/Linux host-API preference exists (the Windows-name-driven tables are intentionally inert off Windows — documented in-code since 2026-07-16), and headset-name heuristics are Windows-centric | `jarvis/audio/player.py`, `jarvis/audio/capture.py` | Device auto-pick falls back to OS default order — works, less clever than on Windows |
| P-10 | Low | Missions | macOS worker reaper: a hard SIGKILL of the orchestrator reparents the worker tree to init (Linux covered via `PR_SET_PDEATHSIG` since 2026-07-16; Windows covered by the kernel Job Object; macOS needs a kqueue `EVFILT_PROC` watcher) | `jarvis/missions/isolation/job_object.py:327-350` | macOS only, and only on orchestrator SIGKILL; normal cancel/kill paths reap correctly |
| P-12 | Info | CU legacy | Frozen legacy CU loops are Windows-only, but NOT on the live path (harness force-routes to v2); imports are lazy | `jarvis/cu/loops/screenshot_only_loop.py` et al. | None at runtime |
| P-13 | Info | Wiki | Wiki DB/vault anchor at `repo_root()` — read-only *wheel* installs would fail writes (not OS-specific; `JARVIS_DATA_DIR` override exists) | `jarvis/memory/wiki/db_path.py:9`, `vault_root.py:59` | None on the advertised install paths |
| P-14 | Info | CU extras | macOS/Linux actuation and UI trees depend on optional extras (pynput, pyobjc, pyatspi); without them everything degrades honestly to screenshot + pixel-click | `jarvis/cu/actuate/posix.py`, `jarvis/vision/tree_factory.py` | By design (§3); bare install keeps the CU loop functional |
| P-15 | Low | Desktop downloads | Native drag-out has Windows OLE and macOS AppKit sources but no GTK/WebKitGTK source yet | `jarvis/ui/native_drag.py` | Linux desktop: the saved-file toast keeps reliable **Show in folder** and **Open** actions but is not itself a drag handle; headless: the normal browser download path remains available |
| P-18 | Low | Overlay drop | Dropping a file ONTO the floating bar/mascot uses two backends: tkdnd on the Tk surfaces (Windows/Linux) and native Qt drag events on the macOS Qt bar (added 2026-07-27 — before that, dropping on the bar did nothing at all on a Mac). The bundled `libtkdnd*.so` links against X11 libs, so a Linux host without them registers no drop target | `jarvis/overlay/drop_target.py`, `jarvis/ui/jarvisbar/qt_overlay.py::dropEvent` | macOS and Windows: full parity. Linux desktop: needs `libxcursor1 libxrender1 libxext6` + `python3-tk` (otherwise `register()` returns False and it is a logged no-op). Headless: no overlay exists — the in-app dock (`POST /api/chat/drop`) carries the feature on every OS |
| P-19 | — | Overlay drop | RESOLVED 2026-07-27. The macOS bar runs in a companion process, whose drop bridge had no handler — the parent's is the real one. A file dropped on the macOS bar was accepted by the window (the OS even showed the "copy" cursor) and then silently discarded; it never became conversation context. Windows/Linux were unaffected (their bar is in-process). Fixed by forwarding the drop over the existing host protocol and returning the intake's verdict as `drop_result` | `jarvis/ui/jarvisbar/host.py::_wire_drop_forwarding`, `jarvis/ui/jarvisbar/subprocess_overlay.py::_dispatch_drop_event` | All three OSes deliver a dropped file into the conversation context and confirm it on the bar. Guards: `tests/unit/ui/jarvisbar/test_host_drop_roundtrip.py` |
| P-16 | Low | Wiki | `VaultLock` dead-owner fast-steal is POSIX-only (`os.kill(pid, 0)` liveness probe; on Windows `os.kill` cannot probe — a non-CTRL signal terminates the target) | `jarvis/memory/wiki/lock.py::_pid_alive` | Windows: a lock left by a crashed/restarted process is stolen only after the `stale_after_seconds` wall-clock window (300 s) — the pre-fix behavior everywhere; a Win32 `OpenProcess` probe could close this |
| P-17 | Low | JarvisBar | "Follow the mouse to the active monitor" has per-OS monitor backends (Windows `MonitorFromPoint`+`rcWork`, macOS Qt available-geometry / Quartz, Linux X11 `xrandr`) but no Wayland backend — Wayland exposes no reliable global monitor geometry without portal support | `jarvis/platform/monitors.py::work_area_at`, `jarvis/ui/jarvisbar/overlay.py`, `qt_overlay.py` | Wayland: `work_area_at` returns `None`, so the bar keeps the single-monitor behaviour (it does not migrate; a cross-monitor drag pins to the primary work area). The feature is a graceful no-op there, never a crash |
| P-18 | Low | Agent accounts | Multi-subscription switching gives each account its own CLI config directory (`CLAUDE_CONFIG_DIR` / `CODEX_HOME`) — the CLIs' own documented override. On macOS, Claude Code keeps its credentials in the **Keychain** rather than in that directory, and whether a second config dir earns a second Keychain entry is UNVERIFIED on this hardware (everything here was measured on Windows) | `jarvis/agent_accounts.py::describe`, `env_overrides` | Windows/Linux: a second Claude seat works as designed (its `.credentials.json` lives in its own folder). macOS: the added account may come back reporting **"Not signed in"** after a completed sign-in — which is the honest outcome, not a crash: the switcher never claims a login it cannot read, so a pane is never silently routed to the first account's credentials. Codex is unaffected on all three OSes (`auth.json` is a plain file). Next Mac session: add a second Claude account, sign in, and check whether `describe()` reports it connected |

| P-20 | Low | Coding-CLI panes | Kimi Code panes deliberately ship WITHOUT multi-subscription switching, unlike Claude Code and Codex. Three independent reasons, all recorded on the registry entry: the wound-down Python generation ignores `KIMI_CODE_HOME` entirely, so seats created on a machine that has it would all silently resolve to one login; its configuration and its credentials share a single `config.toml`, so no setup can be carried to a new seat without carrying the key with it; and its credential layout is unverified against a live install of the current generation | `jarvis/workspace/agents.py` (the `kimi` entry), `jarvis/agent_accounts.py::platforms` | All OSes: one Kimi login, and the account switcher honestly does not offer the CLI at all rather than showing a switch that does nothing. Unblocked by verifying the current generation's credential layout and gating the override on the generation probe |
| P-21 | Low | Coding-CLI panes | OpenCode panes ship single-login for the same class of reason: the only variable that moves its credentials and session database is `XDG_DATA_HOME`, which is a SHARED variable rather than a dedicated override — redirecting it per pane would also redirect any other XDG-aware tool the agent spawns inside that pane | `jarvis/workspace/agents.py` (the `opencode` entry) | All OSes: one OpenCode login. Verified on Windows that `XDG_DATA_HOME` does move `auth.json` and the session database; the blast radius on macOS and Linux has not been measured, which is why it is not wired up |
| P-22 | Low | Coding-CLI panes | Kimi Code uses the bundled Git Bash as its shell environment on Windows, so without Git for Windows installed the binary answers `--version` correctly and the agent then cannot run a single shell command | Kimi vendor docs; `jarvis/workspace/agents.py` (the `kimi` entry) | Windows without Git for Windows: the pane opens, the CLI reports a healthy version, and shell commands fail inside it. macOS/Linux unaffected. `KIMI_SHELL_PATH` points at a non-standard `bash.exe`. An install check that only runs `--version` cannot see this |
| P-23 | Info | Coding-CLI panes | Kimi Code's alternate screen cannot be disabled (an open upstream request notes it is the outlier versus Claude Code, Codex and the Gemini CLI), so it may conflict with the pane's own scrollback the way a Claude Code pane once did | Upstream issue; `jarvis/agentic_ide/screen.py` | All OSes equally — not an OS gap, recorded here because it is the same class of pane defect and is expected to need the same kind of fix |
| P-26 | Low | Keybind recorder | A Mac user cannot RECORD a Command (⌘) shortcut, even though the backend validator accepts `cmd+…` on darwin. Two frontend layers close the door: `modifierTokens` maps `metaKey` to the `win` token (there is one token for "the Meta key", and it is named after the Windows key), and `KeyboardMap` then renders that cap disabled with a "Reserved by the system" tooltip — correct on a PC, wrong on a Mac, where ⌘ is the natural modifier for exactly this kind of shortcut. Deliberately deferred, not overlooked: separating the two Meta keys means a new token that the hotkey backends (`global_hotkeys._KEY_MAP`, `pynput._GENERIC_MODIFIER_ALIASES`) must also learn, and the shipped defaults (`ctrl+right_alt+j` push-to-talk, `ctrl+right_alt+space` hands-free) need no ⌘ on any OS | `jarvis/ui/web/frontend/src/hooks/useHotkey.ts::modifierTokens`, `src/views/settings/KeyboardMap.tsx` (`reserved = token === "win"`), `jarvis/trigger/hotkey.py::validate_hotkey` | macOS: every shipped default works, and Ctrl/Option/Shift combos record and save normally — only ⌘-based combos are unreachable from the UI (a `cmd+…` combo already present in `jarvis.toml` keeps working). Windows/Linux: correct as-is, the Windows/Super key genuinely is OS-reserved |

## Maintenance

- Fixing a gap: remove its row (git history keeps the record).
- Landing a new Windows-only implementation: add a row (required by
  CLAUDE.md §3) with impact, evidence, and off-Windows behavior.
- Re-audit cadence: rerun the five-area sweep after any release that touches
  platform seams (`jarvis/platform/`, `jarvis/cu/actuate/`, `jarvis/vision/`,
  `jarvis/audio/`, `jarvis/missions/isolation/`).
