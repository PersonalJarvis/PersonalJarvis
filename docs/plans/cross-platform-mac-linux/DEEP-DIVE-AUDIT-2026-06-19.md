# Cross-Platform Deep-Dive Audit — does Personal Jarvis really run on macOS + Linux?

> **Date:** 2026-06-19 · **Method:** adversarial *code* audit by 5 parallel deep-dive
> sub-agents (Boot/Packaging, Speech/Audio, Computer-Use/Vision, Admin/Permissions/Worker-Isolation,
> Desktop-UI/Runtime) reading the actual `jarvis/` source — **not** the migration docs' claims.
> **Environment:** Windows-only host; **no macOS or Linux hardware was available**, so every
> finding is from *static code analysis*, never a real-OS run. This audit complements, and does
> not replace, the live sign-off still owed in [`SIGNOFF-LOG.md`](SIGNOFF-LOG.md).

---

## TL;DR — the honest answer

**The foundation is genuinely well-built; the two things the maintainer cares about most are the two weakest.**

The base install, headless boot, import discipline, path/encoding handling, subprocess hygiene,
the `jarvis/platform/` capability seam, the elevation/permission port, and the macOS-AX / Linux-AT-SPI
accessibility trees are all **real, cross-platform, and not stubs** — verified by reading the code
(one sub-agent even ran a recursive import scan over all 652 `jarvis.*` modules with every
Windows/GPU/audio package force-absent: only 10 modules broke, every one of them on `sounddevice`,
none on the boot chain).

But two flagship capabilities fall down off Windows:

1. **Computer-Use is Windows-only in practice** — a single hard-`win32` coordinate source silently
   turns every pixel-click into garbage on macOS/Linux, and the macOS screen-capture permission is
   never handled. (SERIOUS-RISK)
2. **The headless-VPS "talk to Jarvis in the browser" experience does not exist** — the WebSocket
   carries only text; there is no browser-microphone ingest and no browser-speaker return path.
   (BLOCKER for the cloud-first voice claim)

And the over-arching caveat that the migration docs are already honest about: **nothing has ever
run on real macOS/Linux hardware, and the CI matrix has never executed** (nothing is pushed). The
code *says* it works; no real session has confirmed it.

### Per-domain verdicts

| Domain | Verdict | Headline |
|---|---|---|
| Boot / Packaging / Imports / Paths / Encoding / Subprocess | **MOSTLY-OK-WITH-GAPS** | Headless boot proven clean; `sounddevice` eager-import is the one crack |
| Speech & Audio (listen + speak) | **MOSTLY-OK-WITH-GAPS** (1 architectural BLOCKER) | Providers cloud-clean; **no browser-mic path** → headless = text-only |
| **Computer-Use & Vision** | **SERIOUS-RISK** | Windows-only in practice: pixel-click coords broken off-Windows + macOS capture permission unhandled |
| Admin / Permissions / Worker-Isolation | **MOSTLY-OK-WITH-GAPS** | Real per-OS elevation + working POSIX worker-reaper; `GeminiWorker` bypasses it |
| Desktop UI & Runtime glue | **MOSTLY-OK-WITH-GAPS** | Seams clean, lock portable; 3 MEDIUM desktop-UX defects |

---

## Master finding list — ranked by severity

### 🔴 BLOCKER-class

**B1 — Computer-Use pixel-click is broken on every non-Windows desktop, silently.**
`jarvis/harness/screenshot_only_loop.py:815` `_capture_monitor_geometry()` imports `win32api` and
returns `(0,0,0,0)` on any non-win32 host. `_resolve_click_pixel()` (`:2077`) then treats the model's
**normalized 0–1000 coordinate as a raw pixel**, so every click lands inside the top-left 1000×1000 px
square. The zoom-refine + post-click verify accuracy passes are disabled by the same zero geometry
(`:2323`). No log, no degrade, no error — the agent just clicks the wrong place.
*Fix:* wire the mss monitor geometry (`select_capture_monitor(sct.monitors)` already returns
left/top/width/height) into `_capture_monitor_geometry()` for non-win32. Small change; re-enables refine+verify automatically.

**B2 — No browser-microphone / browser-speaker path; the headless-VPS voice experience does not exist.**
`_run_headless` (`jarvis/ui/web/launcher.py:168`) never constructs `SpeechPipeline`/`MicrophoneCapture`/`AudioPlayer`.
The web channel `jarvis/channels/web.py:114` accepts only `kind ∈ {text,voice,system,action,event_mirror}`
where `content` is `str(...)` — **"voice" carries text, not PCM**. There is no `receive_bytes`/Opus/webm
ingest anywhere; the frontend `getUserMedia` is used only by the onboarding mic *test* (`MicTestStep.tsx:19`)
which discards the stream. TTS always plays through the local `sounddevice` `AudioPlayer`
(`pipeline.py:1189`, `:2385`) with no WS return path. This directly contradicts the CLAUDE.md doctrine
("browser's microphone and speakers … the full experience"). The Twilio telephony bridge
(`jarvis/telephony/session.py`, stdlib `audioop`, zero sounddevice) **proves the STT→Brain→TTS core can
run on socket-supplied audio** — that exact pattern was simply never built for the browser.
*Fix:* add a WS binary-audio ingest → STT and stream TTS bytes back over WS for browser playback; the Twilio bridge is the working template. (This is a real feature build, not a one-liner.)

### 🟠 HIGH

**H1 — macOS Screen-Recording permission is never probed or surfaced (Computer-Use).**
`jarvis/platform/probes.py` has `ax_permission_granted()` (the Accessibility grant for the *tree*) but
**no `screen_recording_granted()`**; `display_present()` returns `True` for darwin unconditionally.
Without the Screen-Recording TCC grant, `mss` returns the **bare wallpaper with no error** and the agent
clicks blind. *Fix:* add a `CGPreflightScreenCaptureAccess` probe + an English onboarding message mirroring `_AX_PERMISSION_MSG`.

**H2 — `switch_window` hard-fails off Windows.**
`jarvis/plugins/tool/switch_window.py:108` `if os.name != "nt": return ToolResult(success=False,
error="…nur auf Windows verfuegbar…")`. No `osascript` (macOS) / `wmctrl` (X11) implementation.
It is a live router tool; off Windows the only way to focus an app is re-launch via `open_app`.
*Fix:* add osascript/wmctrl impls. (Also: the error string is German — Output-Language-Policy violation.)

**H3 — `GeminiWorker` bypasses the POSIX worker-isolation reaper.**
`jarvis/missions/workers/gemini_worker.py:314` spawns via `asyncio.create_subprocess_exec(... creationflags=...)`
directly, skipping `create_worker_subprocess` and therefore **`start_new_session=True`**. It is live-routable
(`init.py:526` returns `GeminiWorker()` when `[brain.sub_jarvis].provider == "gemini"`). On POSIX the worker
then shares the orchestrator's process group, so the kill-on-close reaper (`os.killpg`,
`jarvis/missions/isolation/job_object.py:251`) can signal the **orchestrator's own group** rather than
isolate the worker. *Fix:* route through `create_worker_subprocess` like `ClaudeDirectWorker`/`CodexDirectWorker` do (one-line change). The dead `CodexWorker` (`codex_worker.py:118`) has the same bug but is not in the live factory — clean it up, don't copy it.

**H4 — `sounddevice` is an eager module-scope import with no graceful fallback.**
`jarvis/audio/player.py:18` and `jarvis/audio/capture.py:17` do a bare `import sounddevice as sd`
(no try/except), and the installed `sounddevice` loads PortAudio **eagerly** — on Linux without
`libportaudio2` it `raise OSError("PortAudio library not found")` at import time (the Linux branch has
no bundled binary, unlike macOS/Windows). 10 audio/speech modules transitively share this. It is **not**
a base-boot blocker (these modules are off the headless chain — proven), but it is a hard import failure
for any Linux *desktop voice* path or any test/code that does `import jarvis.speech.pipeline`.
*Fix:* lazy-import sounddevice behind a guarded no-op (mirror `device_init.py:36`'s `_get_sd()` pattern) or move the audio modules behind a capability probe; document `libportaudio2` as a base system prerequisite.

### 🟡 MEDIUM

**M1 — Hard-`kill -9` of the orchestrator leaks every POSIX worker.** Inherent to userspace reaping:
`close()` never runs, process groups reparent to init/launchd and survive. This is the one guarantee only
the Windows kernel Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) provides. The code documents this
honestly (`job_object.py:184`), but README.md / CLAUDE.md oversell "No zombies on orchestrator crash" as
cross-platform-absolute. *Fix:* add a Linux `PR_SET_PDEATHSIG` preexec; document the macOS gap; soften the docs.

**M2 — The import-cleanliness CI gate is blind to non-win32 eager imports.**
`scripts/ci/check_import_clean.py:24` `FORBIDDEN_MODULE_SCOPE` lists only `win32*`/`winreg`/`global_hotkeys`/
`pywinauto`/`winpty`. It does **not** include `sounddevice`/`torch`/`mss`/`pyautogui`/`pynput`/`ptyprocess`,
and Check 1 only imports the empty top-level `jarvis/__init__.py`. So it cannot catch H4 or any future eager
GPU/audio import landing on the boot chain; its green is narrower than the CI comment claims.
*Fix:* extend the forbidden set; add a clean `pip install .` (no extras) leg on `python:3.11-slim` that runs the gate.

**M3 — `requirements.txt` is hash-locked and may not resolve on `python:3.11-slim` (unverified).**
It is a fully-pinned `--generate-hashes` lockfile; if any pinned transitive wheel lacks a cp311 manylinux
build, `pip install -r requirements.txt` fails closed on slim. `pip install -e .` (PEP 508 ranges) is safer.
Could not be tested without a Linux host. *Fix:* a clean-slim CI leg closes it.

**M4 — Data dirs ignore OS conventions.** `jarvis/core/paths.py:18` uses `LOCALAPPDATA` → `~/.jarvis`
fallback; on macOS/Linux everything lands in `~/.jarvis` instead of `~/Library/Application Support` / XDG.
Works, but unconventional for an open-source release. `platformdirs` is already a base dep. *Fix:* route `user_data_dir()` through `platformdirs.user_data_dir("Jarvis")`, keep `~/.jarvis` as legacy fallback.

**M5 — Desktop tray starts pystray without a display pre-check.** `jarvis/ui/desktop_app.py:1940` starts
`JarvisTray()` ungated; on a Linux desktop without an AppIndicator/notification-area host the pystray daemon
thread dies with no degrade log (unlike `TrayOnlySurface.start()` which try/excepts). The tray menu strings
are also German (`tray.py:110` `MenuItem("Öffnen",…)`) — Output-Language-Policy violation. *Fix:* `display_present` pre-check + logged no-op; translate the strings.

**M6 — PySide6 mascot crashloops instead of degrading.** `OS-Level/src/overlay/window_mascot.py:20`
imports PySide6 at module scope. It lives in a **separate top-level package spawned as a subprocess**
(`overlay/supervisor.py:56`), so it cannot break the main `jarvis` import — but if enabled on a PySide6-less
host it crashloops to the 5-restarts/5-min cap instead of a clean no-op. Defused today by `enabled=false`
default (`jarvis.toml:1013`, BUG-013). *Fix:* PySide6 probe before spawn → no-op if absent.

**M7 — Audio input device/host-API logic is Windows-shaped + macOS mic-permission UX missing.**
`capture.py:66` host-API preference is MME/DirectSound/WASAPI only; there is no input-side samplerate-fallback
cascade (the output side has one), and a macOS Microphone-TCC denial degrades to "no mic" with **no** "grant
permission" hint (`capture.py:340` raises `RuntimeError("Kein Mikrofon-Device verfuegbar.")`). On a real
Mac/Linux desktop this is saved only by the system-default fallback. *Fix:* cross-platform host-API ranks + input samplerate cascade + a permission-denied hint.

### 🟢 LOW (cleanups, not risks)

- **L1** Output host-API preference maps have no Core Audio/ALSA/PulseAudio entries (`player.py:64`); harmless — every device falls to system default. (speech)
- **L2** `device="cuda"` literal in the STT fallback (`plugins/stt/__init__.py:102`, `fwhisper.py:31`) — a latent footgun, unreachable on the wired paths; change to `"cpu"`.
- **L3** 200-char worktree path cap (`missions/isolation/worktree.py:43`) — MAX_PATH heritage, needlessly strict on POSIX but cleanly handled as a worktree failure.
- **L4** `mklink /J` desktop mirror (`core/paths.py:148`) silently fails off Windows — no per-OS symlink alternative; Mac/Linux users don't get the desktop output shortcut.
- **L5** Bare `open()` text-mode calls without explicit `encoding=` exist tree-wide — UTF-8 on Linux vs cp1252 on Windows is a latent data-fidelity issue, never a boot failure (the high-value config/TOML/JSON readers already pin encoding / use the BOM-safe writer).

---

## What is genuinely solid (don't re-litigate these)

- **Headless base boot on `python:3.11-slim`:** proven clean by a 652-module recursive import scan with all Windows/GPU/audio/desktop packages force-absent. Only the 10 `sounddevice`-dependent audio modules break, none on the launcher→server→brain→mission→channel chain.
- **Win32 import discipline:** every `import win32*`/`winreg`/`ctypes.windll` is function-scoped or `sys.platform`-guarded; zero module-scope failures.
- **`jarvis/platform/` seam:** `detect_platform()` never raises; every probe lazy-imports via `find_spec` and swallows to False/None.
- **Subprocess `creationflags`:** `NO_WINDOW_CREATIONFLAGS = 0` off-Windows (`process_utils.py:34`); no spawn raises on POSIX.
- **Paths/encoding:** `platformdirs` available, `Path.home()` fallbacks, explicit `utf-8` + BOM-aware reads/writes; `sys.stdout.reconfigure` is win32-guarded.
- **Elevation port (real, not theater):** `make_elevator()` selects UAC/polkit/sudo/osascript/Null per OS; the HMAC security core is transport-agnostic; `UnixSocketTransport` has a genuine fail-closed `SO_PEERCRED`/`getpeereid` peer-credential check; the Linux/macOS op vocabulary (apt/systemctl/ufw/brew/launchctl) is pattern-validated and wired all the way into the argv-building executor.
- **POSIX worker reaper:** `start_new_session=True` + `os.killpg`-on-close (`job_object.py:171`) is a real functional Job-Object equivalent, fires correctly for the two LIVE workers (`ClaudeDirectWorker`, `CodexDirectWorker`) through the `async with job:` lifecycle even on exceptions.
- **macOS AX tree + Linux AT-SPI tree:** both are **complete, plausible, permission-gated implementations — not stubs** (`ax_tree.py`, `atspi_tree.py`), normalizing native roles onto the same canonical UIA vocabulary (`role_map.py`) — the strongest part of the Computer-Use stack.
- **App-launch + Terminal/PTY:** fully ported (`open -a`/`xdg-open`, `ptyprocess` + `$SHELL`/`/etc/shells`/`which` discovery); CI-provable.
- **Autostart:** fully cross-platform (Windows Run-key / macOS LaunchAgent plist / Linux XDG `.desktop`), never raises, headless no-op via `display_present`.
- **Single-instance lock:** portable `filelock` (fcntl/LockFileEx), not a Windows-mutex-only path.
- **Orb framework conflict resolved:** the live Orb is **Tk** (`-transparentcolor`, works on Win+macOS); PySide6 lives only in the separately-spawned subprocess package and cannot break the main import.
- **Provider layer (Brain/STT/TTS/Wake/VAD):** genuinely cloud-first — Groq STT + Gemini TTS are the defaults, wake/VAD/local-Whisper are lazy + `[local-voice]`-gated, SAPI5 is a correctly-gated silent opt-in.

---

## Recommended fix order (highest leverage first)

1. **B1** — wire mss geometry into `_capture_monitor_geometry()` (small, unblocks Computer-Use pixel-click on all non-Windows desktops, re-enables refine+verify).
2. **H1 + H2** — add the macOS Screen-Recording probe/onboarding and an `osascript`/`wmctrl` `switch_window` impl (completes Computer-Use on macOS/Linux).
3. **H3** — route `GeminiWorker` through `create_worker_subprocess` (one line; closes the live POSIX worker-isolation hole) and delete dead `CodexWorker`.
4. **H4 + M2** — lazy-guard the `sounddevice` import and extend the import-clean gate's forbidden set + a clean-slim CI leg (closes the one base-import crack and the gate blind spot together).
5. **B2** — design + build the browser-audio WS bridge (real feature; Twilio is the template) — this is what makes the cloud-first "talk in the browser" claim true.
6. **M1, M3, M4, M5, M6, M7** then the LOW cleanups — and align README/CLAUDE.md prose with the honest reaping + voice limits.
7. **Above all: push, let the CI matrix run, and get one live sign-off each on a borrowed/rented Mac and a Linux desktop.** Every macOS/Linux GUI/permission verdict here is from code-reading; none is confirmed on real hardware.

---

## Methodology + honesty note

Five `general-purpose` sub-agents ran in parallel, each scoped to one disjoint domain, each instructed to
audit the **actual code** adversarially rather than trust the migration docs, and to label every finding
`✅ / ⚠️ / ❌ / ❓` with `file:line` evidence and a BLOCKER/HIGH/MEDIUM/LOW severity. Several spawned their
own sub-agents (import-scan, subprocess-sweep, path/encoding sweep, pipeline-wiring trace). All findings are
from **static code analysis on a Windows host** — no macOS or Linux machine was available, so no runtime
behavior was observed. Where a sub-agent could only reason (not run), it is marked LIKELY/unverified. This
audit's value is finding the gaps between "the cross-platform code exists and looks plausible" and "it will
actually work" — it does not substitute for the real-hardware sign-off owed in `SIGNOFF-LOG.md`.
