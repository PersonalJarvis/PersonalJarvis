# Cross-Platform Fix Tracker — execution of DEEP-DIVE-AUDIT-2026-06-19.md

> Autonomous /loop work log. Each target: TDD (RED→GREEN), ruff on touched files,
> `scripts/ci/check_import_clean.py` green, a code-reviewer pass, then a hunk-isolated
> LOCAL commit (no push). Tick only with test evidence. Test python:
> `C:\Program Files\Python311\python.exe`. **Scope:** B1, H1, H2, H3, H4+M2, M1 docs, M5.
> **Out of scope (do NOT build here):** B2 browser-audio bridge, real-hardware sign-off.
>
> Honesty: every fix is proven at the SEAM level on a Windows host (win32/mss/platform
> forced via fakes), NOT on real macOS/Linux hardware. The remaining gap is the live
> sign-off owed in SIGNOFF-LOG.md.

## Targets

- [x] **B1 — Computer-Use pixel-click coordinate fallback** (`jarvis/harness/screenshot_only_loop.py`)
  - Non-win32 `_capture_monitor_geometry()` now falls back to mss geometry via a new
    `_mss_monitor_geometry()` helper, so `_resolve_click_pixel` scales the model's 0-1000
    coords to real pixels instead of using them as raw pixels (top-left 1000×1000 trap).
  - Windows path UNTOUCHED (AD-7): only the win32-import-except branch changed.
  - **Evidence:** `tests/unit/harness/test_screenshot_only_loop.py::test_capture_monitor_geometry_mss_fallback_off_windows` (RED→GREEN) + `::test_capture_monitor_geometry_headless_returns_zero`. Full file + click-refine suites: **67 passed**. ruff clean on touched files (6 remaining errors in screenshot_only_loop.py are pre-existing backlog, not on touched lines). import-clean gate: PASS. code-reviewer: **APPROVE_WITH_NITS** (0 BLOCKER/0 HIGH; 1 MEDIUM single-monitor edge = safe-not-a-bug, 2 MINOR nits — not gating). **Commit: `e6e3e527`.**
  - ⚠️ Shared-tree lesson: the first commit attempt swept a parallel session's uncommitted `screenshot_only_loop.py` work (309 insertions). Recovered by reset + base-restore + re-apply + restore-their-work-as-unstaged. **For every remaining target: `git diff <file>` BEFORE `git add` and stage only own hunks.**
- [x] **H1 — macOS Screen-Recording permission probe + onboarding** (`jarvis/platform/probes.py`, `jarvis/vision/screenshot.py`)
  - New tri-state `probes.screen_recording_granted()` (mirrors `ax_permission_granted`; CGPreflightScreenCaptureAccess via lazy Quartz; non-darwin→True, denied→False, pyobjc-absent→None). New `_SCREEN_RECORDING_MSG` + one-shot `warn_if_screen_recording_denied()` wired into `ScreenshotSource._capture` before the mss grab, so a denied grant surfaces a clear English message instead of silently capturing the wallpaper. AD-7: Windows unchanged.
  - **Evidence:** `tests/unit/platform/test_probes.py` (3 new + smoke line) + `tests/unit/vision/test_screen_recording_warning.py` (3 new) → **25 passed**; bitblt capture regression green. ruff clean on touched lines (2 remaining E501 in screenshot.py are pre-existing German log strings, not mine). import-clean gate: PASS (653). code-reviewer: **APPROVE-WITH-NITS** (0 BLOCKER/0 HIGH; applied the MEDIUM per-frame-probe one-shot guard + 2 nits). **Commit: `42f94014`.**
  - Seam-verified only (fake Quartz on a Windows host); real CGPreflightScreenCaptureAccess on macOS is `unverified-on-real-desktop`. Known LOW gap noted by review: a WARNING log is lost on a headless VPS — a future EventBus→onboarding-UI surface would be more durable (out of scope for H1).
- [x] **H2 — `switch_window` macOS (osascript) + Linux (wmctrl/X11) impls** (`jarvis/plugins/tool/switch_window.py`) + English strings
  - `execute()` dispatches by `detect_platform`: Windows ctypes path byte-identical (AD-7, asserted by a test); new `_find_and_focus_macos` (osascript/System Events) + `_find_and_focus_linux` (wmctrl); `_execute_non_windows` degrades on Wayland (`is_wayland`) + headless (`display_present`) with clear English messages (AD-13). Subprocess calls carry `creationflags=NO_WINDOW_CREATIONFLAGS` (0 on POSIX) + 10s timeout.
  - **Evidence:** `tests/unit/plugins/tool/test_switch_window.py` (13 passed) → macOS success/denied/missing/no-match, Linux success/missing/no-match, dispatch routing + Wayland/headless degrade + **Windows-unchanged (German readback preserved)**, plus injection-escape + case-insensitive parity. ruff clean on both files. import-clean: PASS. code-reviewer: **CHANGES-REQUIRED → fixed**: HIGH AppleScript newline-injection (now lowercased + fully escaped + control-char strip, test-pinned), MEDIUM macOS/Linux case-sensitivity parity (AppleScript `lowercase of`), LOW wmctrl error-detail string. **Commit: `a4d69e35`.**
  - Seam-verified only (faked osascript/wmctrl on a Windows host); real osascript/wmctrl on macOS/Linux is `unverified-on-real-desktop`.
- [x] **H3 — Gemini/Codex worker spawns via `create_worker_subprocess`** (`jarvis/missions/workers/{gemini_worker,codex_worker}.py`)
  - Both workers spawned via a raw `asyncio.create_subprocess_exec`, skipping `start_new_session=True` on POSIX → worker shared the orchestrator's process group → the Job Object's `os.killpg` reaper could signal the orchestrator itself. Both now route through `create_worker_subprocess` (POSIX session/group + Win32 creationflags + WinError-5 breakaway retry), matching the Direct workers. CodexWorker is legacy-but-exported, so FIXED (not deleted) to avoid breaking `workers/__init__.py` + leaving a copyable bad template. Stale personal path dropped from the GeminiWorker docstring (privacy).
  - **Evidence:** `tests/missions/test_posix_containment.py` (14 passed incl. 2 new source-level spawn-discipline tests; the helper's start_new_session-on-POSIX property is pinned in the same file). Both modules + `CodexWorker` import cleanly; import-clean gate PASS; foreign `tests/missions/critic/test_capability_honesty.py` (11) green. ruff: no new errors on touched lines (UP035/UP041/ASYNC240 are pre-existing backlog). code-reviewer: **APPROVE-WITH-NITS** (0 BLOCKER/0 MAJOR; fixed the 2 MINOR docstring issues incl. the personal path + the test-comment NIT). **Commit: `456d39e9`.**
  - NOTE: a hard `kill -9` of the orchestrator still leaks POSIX workers (userspace reaping can't match the kernel Job Object) — that honesty gap is M1 (docs), below.
- [x] **H4+M2 — lazy-guard `sounddevice` + harden import gate** (`jarvis/audio/{player,capture}.py`, `jarvis/speech/{diagnose,voice_compare}.py`, `scripts/ci/check_import_clean.py`)
  - All 4 module-scope `import sounddevice as sd` (not just player/capture — diagnose + voice_compare too, required to add sounddevice to the gate) now `try/except → sd=None`, so the modules import on a headless/slim box without libportaudio2. Behavior byte-identical when sounddevice is present (AD-7). Gate `FORBIDDEN_MODULE_SCOPE` += sounddevice,torch,mss,pyautogui,pynput,ptyprocess (the gate skips try/except-guarded imports, so the wrapped ones pass; verified no other bare module-scope import of these 6 exists in jarvis/).
  - **Evidence:** `tests/unit/audio/test_headless_import.py` (6: 4 subprocess-isolated import-safety + `_PortAudioError` sentinel + gate forbidden-set). The REAL `scripts/ci/check_import_clean.py` PASSES (653 files); full `tests/unit/audio/` = **96 passed** (no regression). ruff clean on touched lines (E402 I introduced by sentinel placement → fixed by moving it after imports; remaining S110/E501 etc. are pre-existing backlog). code-reviewer: **APPROVE-WITH-NITS → HIGH fixed**: `except sd.PortAudioError` was a runtime expr that AttributeErrors when sd=None → resolved a module-scope `_PortAudioError` sentinel (test-pinned). MEDIUM (TYPE_CHECKING import) + LOW (`_require_sd` guard) noted as low-priority follow-ups (non-gating, not on the headless path). **Commit: `26d0d30e`.**
  - Test trap caught + fixed: an in-process re-import test polluted sibling audio tests → switched to a fresh-subprocess import check (zero pollution).
- [x] **M1 — honesty docs** — softened the absolute "No zombies on orchestrator crash" claim in README.md + CLAUDE.md: Windows Job Object = kernel guarantee; macOS/Linux = POSIX process-group reaper (reaps on clean shutdown/cancel/timeout/exception, H3) but userspace, so leaks on a hard `kill -9` of the orchestrator (PR_SET_PDEATHSIG = Linux follow-up; no macOS equivalent).
  - **Evidence:** `git diff` showed exactly 2 lines changed (one per file); both carry "POSIX process-group reaper" (grep-verified); English (language gate clean). CLAUDE.md had a parallel session's uncommitted "one-repo doctrine" rewrite (28+/10-) — isolated via backup/checkout-base/re-apply/restore so my commit holds only my worker-bullet hunk and their work is preserved as an unstaged diff. **Commit: `cb34d3b4`.**
- [x] **M5 — tray display_present gate + English strings** (`jarvis/ui/tray.py`)
  - `JarvisTray.start()` early-returns with a logged English no-op when `display_present()` is False (headless / Wayland-without-AppIndicator) instead of spawning a daemon thread that dies on the first draw (AD-6); `_run()` wraps the pystray Icon start in try/except so a missing tray host logs a warning instead of dying silently. German MenuItem labels + the `set_error` title translated to English (Output-Language Policy). Windows/macOS `display_present()`=True, so the tray still starts normally (AD-7).
  - **Evidence:** `tests/unit/ui/test_tray.py` (3 passed): display-absent → no thread + "Tray not started" log; display-present → thread spawns/_run called (AD-7 guard); menu strings English (German absent). ruff clean on touched lines (UP042/S110 are pre-existing backlog). import-clean gate PASS. code-reviewer: **APPROVE-WITH-NITS** (0 BLOCKER/0 MAJOR; applied LOW = +2 English-string assertions, MEDIUM = clarifying comment; German module docstring/comments left as out-of-scope pre-existing). **Commit: `ab230a99`.**

## STOP CONDITION (all must hold before ending the loop)
- [x] every target above checked with test evidence
- [x] ruff clean on all touched files (pre-existing backlog left untouched; no new errors on touched lines)
- [x] `check_import_clean.py` green with the extended forbidden set (653 files)
- [x] every targeted test suite green
- [x] final code-reviewer pass: zero unaddressed BLOCKER/HIGH (every BLOCKER/HIGH from the per-target reviews was fixed)
- [x] README + CLAUDE.md honesty edits done (M1)
- [x] FINAL SUMMARY appended below

## FINAL SUMMARY (loop complete 2026-06-19)

All seven targets implemented test-first, each code-reviewed (per-target subagent),
hunk-isolated and committed to **local `main`** (NOT pushed). Every fix was proven
at the SEAM level on this Windows host (forced `detect_platform`/`sys.platform`/
module-presence via fakes); none was run on real macOS/Linux hardware.

| # | Fix | Commit | Proof (one line) |
|---|---|---|---|
| B1 | Computer-Use pixel-click works off-Windows | `e6e3e527` | `_capture_monitor_geometry` falls back to mss geometry on non-win32 → 0-1000 coords scale to real pixels instead of the top-left 1000×1000 trap (`test_capture_monitor_geometry_mss_fallback_off_windows`) |
| H1 | macOS Screen-Recording permission surfaced | `42f94014` | new tri-state `probes.screen_recording_granted()` + one-shot warning at the screenshot capture site so a denied grant degrades with a clear message instead of clicking the wallpaper (25 tests) |
| H2 | `switch_window` on macOS + Linux | `a4d69e35` | osascript (macOS) + wmctrl (Linux/X11) behind the platform seam; Wayland/headless degrade in English; AppleScript injection escaped + case-insensitive parity (13 tests; Windows path byte-identical) |
| H3 | worker POSIX kill-on-crash isolation | `456d39e9` | Gemini/Codex workers spawn via `create_worker_subprocess` (`start_new_session` on POSIX) so the killpg reaper can't hit the orchestrator itself (14 containment tests) |
| H4+M2 | lazy `sounddevice` + hardened import gate | `26d0d30e` | 4 audio modules import on a slim Linux box without libportaudio2; gate forbids eager sounddevice/torch/mss/pyautogui/pynput/ptyprocess; `_PortAudioError` sentinel (96 audio tests + gate green) |
| M1 | honest cross-platform kill-on-crash docs | `cb34d3b4` | README + CLAUDE.md no longer claim absolute "no zombies on crash" — Windows = kernel guarantee, POSIX = reaped on clean teardown but leaks on a hard `kill -9` (PR_SET_PDEATHSIG = Linux follow-up) |
| M5 | tray display gate + English strings | `ab230a99` | `JarvisTray.start()` gated on `display_present()` (logged no-op headless) + `_run` try/except (no silent daemon-thread death) + English menu strings (3 tests) |

**The single remaining gap:** `unverified-on-real-desktop` — needs a live sign-off
on a real Mac and a real Linux desktop + a green CI matrix run after push. The audit
and every fix here were proven at the seam level on a Windows host via platform
fakes / forced `detect_platform`; none was run on real macOS/Linux hardware. Nothing
is pushed (local `main` only). Out of scope for this loop: the B2 browser-audio
bridge (a feature, not a fix) and the real-hardware sign-off.

## POST-LOOP ADDENDUM (2026-07-14) — first real-Mac boot falsified M5's macOS assumption

The first fresh-install boot on real Mac hardware (the live sign-off owed above)
aborted natively: pystray's darwin backend creates an `NSStatusItem` in
`Icon.__init__`, and AppKit is main-thread-only — from the `jarvis-tray` worker
thread that is an uncatchable C-level abort ("Python quit unexpectedly"), not a
Python exception, so M5's `_run` try/except never helps. M5's line
"Windows/macOS `display_present()`=True, so the tray still starts normally" was
wrong for macOS: the tray *started* and killed the whole app. Full forensics:
`docs/BUGS.md` **BUG-056**.

- **Fix (committed):** `JarvisTray.start()` now gates on `sys.platform ==
  "darwin"` → logged English no-op (AD-6/AD-11); desktop window + Dock icon
  remain the macOS surface. Guard:
  `tests/unit/ui/test_tray.py::test_tray_start_is_noop_on_macos`.
- [ ] **Follow-up (needs real Mac hardware):** real menu-bar icon via
  main-thread hosting — construct the icon on the main thread and
  `run_detached(darwin_nsapplication=<pywebview's NSApplication>)`; verify
  against a live pywebview run loop before lifting the gate.

**Second real-Mac boot (2026-07-14, later): BUG-057.** With the tray gated,
the next boot died identically one layer further in: the default JarvisBar
builds its Tk root on the `jarvis-backend` worker thread — Aqua-Tk is
main-thread-only on macOS. Full boot-path audit gated ALL off-main-thread Tk
creators on darwin (bar, mascot orb, virtual cursor, `make_overlay_surface`
→ tray floor); Dock icon + pywebview window confirmed main-thread-safe.
Commit `b82d821a`, shipped public same day. The follow-up above therefore
grows: main-thread/own-process hosting is needed for the menu-bar icon AND
the bar/orb before macOS gets any on-screen overlay.

**Own-process bar host (2026-07-14, this session).** The BUG-057 follow-up
for the bar landed: `jarvis.ui.jarvisbar.host` is a companion process whose
MAIN thread runs the bar's Tk mainloop (legal on Aqua-Tk), remote-driven by
`SubprocessBarOverlay` over a JSON-per-line stdio protocol (stdin EOF =
parent died → host exits; events like mute-toggle stream back on stdout).
`_build_overlay_surface` on darwin now returns the proxy for
`style == "jarvis_bar"` (NullOverlay stays the fallback + the mascot's
surface). macOS transparency: no color key there — the root sets
`-transparent` + `systemTransparent` and frames carry a real alpha channel
(`renderer.key_to_alpha`).
- [ ] **Verify on real Mac hardware:** bar visible, transparent (no magenta
  rectangle), draggable, mute/hang-up clicks round-trip, no Dock icon for
  the host (pyobjc accessory policy is best-effort), clean exit on quit.
