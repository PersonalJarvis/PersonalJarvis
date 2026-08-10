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

**Fix pass 2026-07-31:** P-28 fixed and removed. Codex subscription voice now
uses a parent-lifeline process-group supervisor on macOS and Linux, while
Windows retains kernel Job Object containment.

**Fix pass 2026-08-03 (subscription realtime voice).** Four defects of the
same shape as P-29 — the feature was reachable only on the maintainer's OS:

- **Linux login terminals.** The visible `codex login` accepted exactly
  `gnome-terminal`, `konsole` and literal `xterm`, so XFCE, MATE, Cinnamon and
  anyone on kitty/alacritty/foot/wezterm could not connect subscription voice
  AT ALL — while the Providers card still offered an enabled Connect button.
  `jarvis/codex_auth.py::_LINUX_LOGIN_TERMINALS` now carries fourteen entries
  with their documented foreground/no-fork forms, and
  `linux_login_terminal_available()` is the pre-click capability probe so a
  desktop that genuinely has none reports `lifecycle_unavailable` with an
  actionable reason instead of an error toast after the click.
- **Linux browser hand-off.** Windows (ShellExecute) and macOS (`open`) opened
  the OAuth page themselves; the Linux login child was handed an environment
  with no `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY`, so the user had to copy a
  device-code URL out of the terminal. Those session handles now reach the
  child through both allowlists. Deliberately partial: the forced file
  credential store still strips `DBUS_SESSION_BUS_ADDRESS` and
  `XDG_RUNTIME_DIR`, so a pure-Wayland session without XWayland keeps the
  printed URL — the keyring-isolation guarantee outranks the convenience.
- **POSIX login containment.** Process-tree containment for the login guardian
  was Windows-only, so a Jarvis crash on macOS/Linux left terminal → guardian
  → `codex login` alive with the profile lock still held and every later
  connect reporting a permanent "busy". `make_process_tree` already returns a
  real POSIX process-group reaper; the login path now uses it, and only the
  Windows breakaway flag remains Windows-shaped.
- **POSIX delegate cleanup + macOS PATH.** The Codex CLI that executes
  subscription-voice actions was tree-killed only on Windows (`taskkill /T`),
  leaking the real `codex` child on every capped or cancelled turn off
  Windows; it now leads its own process group and gets the SIGTERM/SIGKILL
  sibling. It also resolved its binary with a bare `shutil.which`, so a
  GUI-launched macOS app could show the subscription as connected while every
  action failed with "Codex CLI not found" — both sites now share
  `CodexAuthService._resolve_binary` and its `ensure_cli_paths()` repair.

**Fix pass 2026-08-03 (subscription voice, second round).** The 2026-08-03 pass
above fixed which terminals are offered; this one fixes what happens after one
is launched. Same shape again — a guarantee that held only on Windows.

- **Login containment now binds to the guardian, not to the launcher.** The
  previous pass gave the login a POSIX process-group reaper, but on macOS the
  spawned process is `osascript`, which asks the ALREADY-RUNNING Terminal.app to
  `do script` — so the guardian is a grandchild of Terminal, in no process group
  Jarvis owns. GNOME is the same story through `gnome-terminal-server`. The
  reaper was signalling a group that contained only the launcher, and a Jarvis
  crash still left the guardian holding the profile lock with every later
  Connect reporting a permanent "busy". A process tree cannot cross those
  boundaries and neither can an inherited pipe, so the lifeline is now a
  **parent-liveness lock file**: Jarvis holds it for the whole login, the kernel
  drops it on any kind of death, and the guardian polls it and ends the login if
  it can take it (`jarvis/codex_auth.py::_hold_parent_liveness_lock`,
  `jarvis/codex_login_guard.py::_parent_liveness_lost`, exit code
  `EXIT_PARENT_GONE`). The probe is deliberately conservative — anything it
  cannot read counts as "Jarvis is alive", because ending a healthy login is
  worse than one stale lock.
- **`wezterm` released the profile lock mid-write.** Plain `wezterm start` hands
  the window to a running `wezterm-gui` and returns at once, so `cleanup_login`
  ran its post-check and released the lock while `codex` was still writing
  `auth.json` — exactly the hazard the terminal table's own docstring describes.
  Now `--always-new-process`. Every entry additionally carries the REASON its
  flags keep it in the foreground, pinned per entry by a test.
- **Terminal matching is exact, not prefix.** `startswith` accepted Debian's
  `gnome-terminal.wrapper` for the `gnome-terminal` entry (and handed it flags
  that wrapper rejects) and accepted anything merely beginning with `st`. Both
  launched something that could not host the login, and the failure then
  surfaced as a guardian handshake error. Matching is now exact with a tiny
  justified alias map, and a login whose guardian never wrote its first
  acknowledgement names the TERMINAL as the cause instead of the guardian.
- **The POSIX lifeline no longer swallows a forwarded descriptor.**
  `child_lifeline.py` re-spawns the real child with `close_fds=True`, so the
  profile-lock descriptor the caller passes in was dropped at exec and the lock
  was held by the supervisor rather than by the app-server child it was meant
  for. The supervisor now accepts `--keep-fd N` and forwards it. Harmless today
  because the two processes die together — but the caller's guarantee was simply
  not true, and the first change that lets the supervisor exit first would have
  turned it into two processes writing one profile.
- **Checked, not a defect: the macOS Apple-Silicon PyAV pin.** Base pins
  `av==15.1.0` for that cell with no `python_version` guard while `[local-voice]`
  and `[tts-eval]` carried `python_version < '3.14'` and a comment claiming
  wheels only up to 3.13 — which reads as a broken install on macOS arm64 +
  CPython 3.14. It is not one: the PyPI file list for `av 15.1.0` carries
  `cp311/cp312/cp313/cp314` `macosx_13_0_arm64` wheels. The base pin was right
  and the comment was wrong; the comment is corrected and the redundant guard
  removed. The pin itself stays — `av 16+` raises the Apple-Silicon floor to
  macOS 14, and 15.1 is what keeps the supported macOS 13 floor.

**Fix pass 2026-08-03 (keyboard + pointer, from live Mac reports).** Three
defects of one shape — a surface OFFERS something on every OS and only one OS
can actually deliver it, with nothing raising in between:

- **Keybind picker.** The Quartz keycode table covered letters, digits,
  F1-F12 and the arrows. The picker also offers the whole nav cluster, the
  entire numpad and F13-F20, and the Windows backend registers all of them —
  so on macOS those shortcuts recorded, validated, saved and rendered as
  bound, then never fired. Fixed in `backends/quartz.py`; a parity test now
  reads the bindable tokens out of the frontend source, so adding a cap
  without its keycode fails instead of shipping a dead shortcut.
- **Event-tap permission probe.** The TCC grant check ran on EVERY reconcile,
  i.e. two native ObjC calls per keystroke the machine sees, inside the tap
  callback. macOS DISABLES a tap whose callback overruns its deadline — the
  "works sometimes, or not at all" report. Now throttled (1 s TTL) while
  staying fail-closed.
- **Computer-Use numpad.** `base._NAMED_KEYS` accepts `numpad0`-`numpad9`
  plus the five operators and Windows maps every one; the POSIX table mapped
  none, so the identical action died off-Windows with
  `ValueError: Unknown key: 'numpad5'`. Addressed by raw virtual key
  (Carbon `kVK_ANSI_Keypad*` / `XK_KP_*`).
- **Sidebar pointer offset** (macOS only, not previously registered): the
  `<aside>` carried `backdrop-blur`, making the `backdrop-filter` element an
  ANCESTOR of the scrolling `<nav>`. WebKit does not reliably invalidate that
  backdrop snapshot when a descendant scrolls, so the sidebar painted rows at
  their old offsets while hit-testing them at the new ones. The frosted
  backing is now its own non-scrolling layer. **Awaiting confirmation on real
  Mac hardware** — it cannot be reproduced on Windows, where Chromium
  composites the case eagerly.

P-26 is removed: a Mac user CAN now record a ⌘ shortcut (`metaToken` emits
`cmd` on darwin, and `KeyboardMap` no longer draws the Meta cap reserved).

**Known, deliberate, and NOT a macOS gap:** the punctuation keys (`- = [ ] \
; ' , . /` and backtick) plus CapsLock are drawn `dead` in the picker on
every OS. `event.code` is keyed to US-layout positions, so binding by
position would record "BracketLeft" while the keycap the user actually
pressed prints something else entirely on any non-US layout. Reported from a
Mac as "you cannot pick all the keys"; it is cross-platform by design, not a
parity defect. Changing it means choosing position- over label-fidelity for
all three OSes at once.

**Fix pass 2026-08-09 (stable subscription voice).** P-30 is resolved and
removed. The ChatGPT-subscription card is now a compatibility alias for one
OS-neutral composition: Jarvis microphone/VAD/STT, streamed Codex App Server
text, then Jarvis sentence TTS and receipt-backed playback. The selected
profile therefore no longer depends on WebRTC, `aiortc`, PyAV, ChatGPT-Live
audio boundaries, or provider-side VAD. Windows, macOS, and graphical Linux
run the same pipeline; headless hosts report `headless_audio_unavailable`
instead of advertising a call they cannot play. The capability response also
reports subscription login, STT, TTS, desktop runtime, and platform readiness
separately. The old provider id migrates at read time, so existing selections
enter the stable pipeline without a destructive config rewrite.

**Performance pass 2026-08-10 (Computer-Use).** Stable-frame acquisition and
visual-effect verification now use the same OS-neutral MSS/Pillow path on
Windows, macOS and Linux/X11: a call-scoped capture session, deferred BGRX
conversion, area-filtered thumbnails, and bounded polling with persistent
effect confirmation. Windows' native window capture remains a capability-
gated first choice with the shared rectangle path as its fallback. The
existing macOS Screen Recording gate, Wayland refusal and headless refusal are
unchanged, so an unavailable capture backend never becomes permission to act
blindly. The cross-platform tkinter engine rig remains the release oracle.

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
| On-demand Screen Context | One-shot capture is wired into the production brain on Windows, macOS, and Linux/X11; UIA/AX/AT-SPI text is source-filtered, the indicator precedes capture, and Wayland/headless/missing grants refuse honestly |
| Voice / audio (capture, playback, VAD, wake, STT, TTS, realtime) | Clean; headless disables voice honestly; WASAPI logic is inert-by-data off Windows |
| Core (launcher, config, keyring, restart, autostart, tray, elevation, paths) | Clean; per-OS autostart (Registry / LaunchAgent / XDG `.desktop`), keyring falls back to a 0600 file on headless hosts |
| Data / agents (wiki, contacts, telephony, sessions, missions, skills, self-mod, channels, MCP) | Clean; mission workers run on POSIX with a real process-group reaper |

## Open parity gaps

Ordered by user impact. "Behavior" describes what a macOS/Linux user actually
experiences today.

| # | Impact | Area | Gap | Evidence | Behavior off-Windows |
|---|---|---|---|---|---|
| P-29 | Low | Subscription voice | The dedicated ChatGPT-subscription voice login is an interactive browser flow, so a headless Linux host — and a graphical Linux desktop that ships no terminal emulator able to host the login for its full lifetime — can never CONNECT the profile there (an existing login still reports ready and calls work through the browser voice bridge) | `jarvis/codex_app_server.py::_login_required_state`, `_linux_login_terminal_missing`, `start_codex_subscription_login`, `jarvis/codex_auth.py::_LINUX_LOGIN_TERMINALS` | Both cases report the same `lifecycle_unavailable` truth on every surface (card, activation, voice-mode, Test), each with its own actionable reason — "run Jarvis on a desktop" or "install one of these terminals" — and never an enabled Connect button that can only produce an error toast |
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
| P-22 | Low | Orb window | The floating orb window (both looks: the Gigi mascot and the procedural **voice orb**) is a Tk window whose transparency comes from a colour key. Windows keys it out natively; macOS uses Aqua-Tk's `-transparent` in the companion host; on Linux the attribute is accepted only under a **compositing** window manager, and not at all on Wayland | `ui/orb/overlay.py::_apply_color_key`, `_build_renderer`, `jarvis/ui/jarvisbar/host.py::_build_surface` | Windows/macOS: full parity, including drag-to-any-monitor and the live mascot↔voice-orb switch. Linux with a compositor (GNOME/KDE/picom): works. Linux without one, and Wayland: the window would be an opaque magenta square, so it is NOT shown — the surface logs one actionable English line and stays hidden; voice, tray and the app window are unaffected. Note the Jarvis Bar degrades DIFFERENTLY on such a session (it keeps drawing and shows its key colour — pre-existing, `jarvis/ui/jarvisbar/overlay.py:738`), so "None (hidden)" is the honest display style on a non-compositing Linux desktop until the bar adopts the same gate |
| P-21 | Low | Coding-CLI panes | OpenCode panes ship single-login for the same class of reason: the only variable that moves its credentials and session database is `XDG_DATA_HOME`, which is a SHARED variable rather than a dedicated override — redirecting it per pane would also redirect any other XDG-aware tool the agent spawns inside that pane | `jarvis/workspace/agents.py` (the `opencode` entry) | All OSes: one OpenCode login. Verified on Windows that `XDG_DATA_HOME` does move `auth.json` and the session database; the blast radius on macOS and Linux has not been measured, which is why it is not wired up |
| P-22 | Low | Coding-CLI panes | Kimi Code uses the bundled Git Bash as its shell environment on Windows, so without Git for Windows installed the binary answers `--version` correctly and the agent then cannot run a single shell command | Kimi vendor docs; `jarvis/workspace/agents.py` (the `kimi` entry) | Windows without Git for Windows: the pane opens, the CLI reports a healthy version, and shell commands fail inside it. macOS/Linux unaffected. `KIMI_SHELL_PATH` points at a non-standard `bash.exe`. An install check that only runs `--version` cannot see this |
| P-23 | Info | Coding-CLI panes | Kimi Code's alternate screen cannot be disabled (an open upstream request notes it is the outlier versus Claude Code, Codex and the Gemini CLI), so it may conflict with the pane's own scrollback the way a Claude Code pane once did | Upstream issue; `jarvis/agentic_ide/screen.py` | All OSes equally — not an OS gap, recorded here because it is the same class of pane defect and is expected to need the same kind of fix |
| P-27 | Low | Mouse-button shortcuts | A shortcut may now be a MOUSE BUTTON (middle, and the two side buttons — `mouse_middle` / `mouse_x1` / `mouse_x2`). All three OSes are implemented in the same change and share one token vocabulary, but the delivery is not uniform: Windows needs nothing extra (the backend polls `GetAsyncKeyState`, which reports mouse buttons); macOS needs pyobjc `Quartz` plus the Accessibility + Input Monitoring grants the hotkey tap already requires; Linux/X11 needs `pynput`, which is the opt-in `[desktop-linux]` extra for the reason recorded in P-24 (`evdev` is source-only). Wayland cannot do it at all — no global button grab exists, the same design reason keyboard shortcuts degrade there. The left and right buttons are deliberately not bindable on any OS: their meaning follows the system "swap mouse buttons" setting, so a shortcut recorded as "left" would fire on the physical right button for a left-handed user | `jarvis/trigger/hotkey.py::mouse_hotkeys_available`, `backends/global_hotkeys.py::_MOUSE_TOKEN_TO_VK`, `backends/pynput.py::_start_mouse_listener`, `backends/quartz.py::_MOUSE_BUTTON_TO_TOKEN` | Every host answers the capability question BEFORE offering the control: `mouse_hotkeys_available()` returns an English sentence naming what is missing and what still works, and a backend that cannot start its mouse hook logs the same thing and keeps the KEYBOARD shortcuts alive rather than failing the whole binding. macOS/Linux desktop with the extras: full parity with Windows. Wayland and headless: key combinations only |

| P-28 | Low | Local realtime | The one-click managed install AND the 2026-08-08 supervisor (prewarm, pidfile ownership, start/stop routes, Ollama keep-alive warm ping) are built cross-platform — pathlib, `os.name` venv layout with a POSIX `lib/python*/site-packages` glob, per-hardware torch flavor, `start_new_session` + `killpg` SIGTERM→SIGKILL escalation on POSIX, `HF_HUB_DISABLE_SYMLINKS` Windows-only — but have only been RUN on the Windows dev box. The preflight's accelerator probe knows two sources: NVIDIA VRAM (nvidia-smi) and Apple-Silicon unified memory (total RAM); the derived launch command maps them to `cuda`/`mps`; an AMD/Intel/no-nvidia-smi host gets the honest "no supported accelerator" blocker (unit-tested) | `jarvis/realtime/local_server/{preflight,install,supervisor}.py` | Any host under 12 GB usable accelerator memory (including every GPU-less/headless box) gets the honest blocker with a cloud pointer instead of an install — verified by unit tests. macOS Apple-Silicon: preflight and install should work but the smoke boot (`--qwen3_tts_device mps`) is UNVERIFIED on real hardware; a failure is honest (install ends in an error state naming the smoke log, readiness stays fail-closed). Linux+NVIDIA: expected to work via the same cu130 wheel index, unverified; the supervisor's POSIX kill/spawn branches are unit-tested but not live-run. The bring-your-own-server URL socket keeps full parity everywhere |

| P-32 | Low | Local models | The in-app Ollama runtime install (the plug-and-go path for local models: detect → install → start → pull, no terminal) has one silent path per OS and an honest refusal elsewhere: Windows uses winget or the official per-user OllamaSetup.exe (built, not yet live-run on a machine WITHOUT Ollama); macOS automates only Homebrew — a dmg drag cannot be scripted honestly, so brew-less Macs get the download pointer; Linux runs the official install.sh only when non-interactive sudo exists (the script escalates internally; without it the refusal names the one terminal command instead of hanging on an invisible password prompt). Detection and start are cross-platform | `jarvis/brain/ollama_runtime.py`, `jarvis/ui/web/provider_routes.py` (ollama-runtime routes), `jarvis/realtime/local_server/install.py::_setup_local_brain` | Every OS gets the same three-state truth (not installed / stopped / running) and the same buttons; only the INSTALL leg differs. Refusals are one honest sentence with the exact fixing action. All install legs are unit-tested with fakes; no leg has been live-run on a machine without Ollama yet — the cold-machine drill is the recorded release gate |
| P-31 | Low | Detached views | The "own window" detach (Agentic IDE / Voice into a second pywebview window) creates the window at RUNTIME, after `webview.start()`. pywebview documents runtime multi-window, and the code path is OS-neutral (worker-thread create, distinct title, shared backend), but it has only been RUN against the Windows/WebView2 backend; whether the cocoa and GTK backends accept a runtime `create_window` is unverified from this machine. Browser-lock auth in the second window additionally relies on a shared cookie jar, which is verified for WebView2 only | `jarvis/ui/desktop_app.py::open_detached_window`, `jarvis/platform/probes.py::webview_backend_available`, `jarvis/ui/web/desktop_routes.py` | Every failure is honest and keeps the feature usable: a shell whose backend refuses the runtime create answers `ok: false` with the solo URL and the frontend opens it in the user's real browser tab (via open-external); plain-browser clients open the tab directly; headless answers `no_desktop_shell` the same way. macOS/Linux desktop live check pending: detach, close main, reattach, tray-reopen — on success this row shrinks to the cookie-jar note or disappears |

## Maintenance

- Fixing a gap: remove its row (git history keeps the record).
- Landing a new Windows-only implementation: add a row (required by
  CLAUDE.md §3) with impact, evidence, and off-Windows behavior.
- Re-audit cadence: rerun the five-area sweep after any release that touches
  platform seams (`jarvis/platform/`, `jarvis/cu/actuate/`, `jarvis/vision/`,
  `jarvis/audio/`, `jarvis/missions/isolation/`).
