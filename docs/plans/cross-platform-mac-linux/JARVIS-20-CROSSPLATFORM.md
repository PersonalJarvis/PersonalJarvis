# JARVIS-20 — Cross-Platform Benchmark (CP-1 .. CP-20)

> A hand-curated benchmark of 20 representative user scenarios that exercise the
> **six ported features** (Terminal, App-launch, Hotkey, UI-element-click, Orb,
> Admin/elevation) plus the cross-cutting voice + Computer-Use round-trip, across
> **Windows / macOS / Linux**. This file is the **scenario source**; it is
> *authored* here and *executed* by the Wave-4 agent (sub-task **4.5**), which
> records the per-OS scores in
> [`JARVIS-20-RESULTS.md`](JARVIS-20-RESULTS.md). On any conflict with
> [`_FROZEN-DECISIONS.md`](_FROZEN-DECISIONS.md) (AD-1..AD-15, EK-1..EK-6), the
> frozen file wins. Output language: English.
>
> **Scoring rubric (3 states — read this before running any scenario):**
> - **`pass`** — the feature did exactly what the scenario asks, on that OS.
> - **`degraded-as-designed`** — the feature could *not* run on that OS by design
>   (no display, Wayland, ungranted permission, no elevation), and it **degraded
>   gracefully**: a logged English message + the documented fallback (pixel-click /
>   tray / wake-word / refusal), **never a crash and never a silent drop**. Per
>   **AD-6 / AD-OE6 / HN-4 / HN-5**, a `degraded-as-designed` is a **PASS** for the
>   contract. The four scenarios marked **"GRACEFUL-DEGRADE EXPECTED"** below are
>   *designed* to land here on their target OS.
> - **`fail`** — a crash, an exception that propagated, a silent empty result with
>   no log, or a wrong action. A `fail` is a **release blocker** (it violates
>   AD-OE6 "zero silent drops") and must be fixed before close-out (4.6).
>
> **Verification honesty (AD-3 / HN-6):** a scenario touching a GUI/permission
> behavior (Orb transparency, AX/AT-SPI tree, hotkey *capture*, elevation *prompt*)
> can only be scored `pass` against a **real device with a dated sign-off** in
> [`SIGNOFF-LOG.md`](SIGNOFF-LOG.md). On a headless runner those scenarios are
> scored against their *degrade* path or recorded `unverified-on-real-desktop` —
> never silently "pass."

---

## How to read each scenario

Each scenario carries: an **ID** (CP-1..CP-20), a **plain-language user request**
(voice or chat, as a real user would phrase it — mixed DE/EN to match the bilingual
default), the **feature(s)** it exercises (with the seam factory + sub-task IDs that
built it), the **platforms it must pass on**, and a **concrete pass criterion** the
4.5 runner can check. Voice scenarios are spoken at the wake-word / PTT hotkey;
chat scenarios are typed in the web UI.

---

## A. Terminal (AD-9 · `make_pty_backend` · WELLE-1 sub-tasks 1.1/1.2)

### CP-1 — Open a terminal and run `ls`
- **User says (voice):** *"Jarvis, open a terminal and run `ls`."* / *"…und führ `ls` aus."*
- **Exercises:** Terminal — `UnixPtyBackend` (`ptyprocess`) on Mac/Linux,
  `WinptyBackend` (ConPTY) on Windows; Unix shell discovery (`discover_shells`).
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅ (fully CI-provable, EK-4).
- **Pass criterion:** a PTY-backed session spawns the OS-default shell (`bash`/`zsh`
  on Unix, PowerShell/cmd on Windows), the `ls`/`dir` runs, and the directory
  listing round-trips back to the terminal view with no mojibake (the str↔bytes
  seam normalization holds — anti-pattern AP-1). `exitstatus == 0`.

### CP-2 — Run a command, capture its output, and read it back
- **User types (chat):** *"Open the built-in terminal and run `echo hello-jarvis`, then tell me what it printed."*
- **Exercises:** Terminal write→read round-trip through the `PtyBackend` seam.
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅.
- **Pass criterion:** the assistant reports the literal string `hello-jarvis` as the
  captured output (proves the up-seam type is `str` on every backend, byte-decoded
  exactly once).

### CP-3 — Resize the terminal mid-session
- **User does:** resizes the terminal panel in the web UI while a shell is live.
- **Exercises:** Terminal `setwinsize(rows, cols)` across the seam (ptyprocess
  `dimensions` order vs winpty).
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅.
- **Pass criterion:** `setwinsize` is honored (a subsequent `tput cols` / `$COLUMNS`
  reflects the new width); no exception; the daemon-thread read-loop keeps streaming.

---

## B. App-launch (AD-15 · `resolve_app_launch_target` · WELLE-1 sub-task 1.3)

### CP-4 — Launch the default browser by name (per-OS)
- **User says (voice):** macOS *"Jarvis, open Safari."* · Linux *"…öffne Firefox."* · Windows *"…open Edge."*
- **Exercises:** App-launch — platform-conditional `KNOWN_APPS`; macOS `open -a`,
  Linux `xdg-open`/direct exec, Windows `os.startfile`/App-Paths.
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅.
- **Pass criterion:** the named browser process starts (a new window/process is
  observable). Resolution logic alone is CI-verified; the *actual launch* is a light
  live check on the real device (per the README verification ladder rung (d)).

### CP-5 — `starte den Rechner` (launch the calculator, bilingual)
- **User says (voice, German):** *"Jarvis, starte den Rechner."*
- **Exercises:** App-launch alias + per-OS `KNOWN_APPS` (`calculator` →
  `gnome-calculator` on Linux, `Calculator` via `open -a` on macOS, `calc` on
  Windows); proves the bilingual phrase still resolves through the anti-hallucination
  gate (`_is_plausible_app_name`).
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅.
- **Pass criterion:** the OS calculator launches; the German verb `starte` and noun
  `Rechner` resolve to the correct per-OS app name without tripping
  `_HALLUCINATION_RE`.

### CP-6 — Launch a CLI tool on the PATH, and reject a hallucinated app name
- **User says (voice):** *"Jarvis, open `code`."* then *"Jarvis, open Flibbertyglop."*
- **Exercises:** App-launch `shutil.which` executable path + the
  anti-STT-hallucination whitelist gate (AD-15).
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅.
- **Pass criterion:** `code` (VS Code) launches via the direct-exec branch; the
  nonsense name is **refused with a spoken English explanation**, not silently
  attempted and not crashing.

---

## C. Hotkey (AD-8 · `make_hotkey_backend` · WELLE-1 sub-task 1.4)

### CP-7 — Press the global combo to wake Jarvis (desktop)
- **User does:** presses `ctrl+right_alt+j` (the configured global combo).
- **Exercises:** Hotkey — `GlobalHotkeysBackend` (Windows), `PynputBackend`
  (macOS / Linux-X11).
- **Must pass on:** Win ✅ · macOS ✅ (Input-Monitoring granted) · Linux-X11 ✅.
- **Pass criterion:** the press is *captured* and Jarvis enters LISTENING. **Requires
  the Wave-4 live sign-off** (key arrival from the OS cannot be proven in CI). On the
  runner, scored against the registration logic only; on a real device, scored
  `pass` only with a dated `SIGNOFF-LOG.md` line.

### CP-8 — Push-to-talk hold-to-record via the hotkey
- **User does:** holds the PTT combo, speaks, releases.
- **Exercises:** Hotkey both-edges (press + release) PTT path preserved across the
  backend swap (the `_ptt_session` raw-mic drain).
- **Must pass on:** Win ✅ · macOS ✅ · Linux-X11 ✅.
- **Pass criterion:** holding starts recording, releasing submits the utterance; both
  edges fire through the `PynputBackend`/`GlobalHotkeysBackend` without a dead-zone
  silent drop. Live-capture → Wave-4 sign-off.

### CP-9 — Hotkey on Wayland → graceful no-op, wake-word still works  · **GRACEFUL-DEGRADE EXPECTED**
- **User does:** presses the combo on a Wayland Linux session, then says the wake word.
- **Exercises:** Hotkey `NoopBackend` (Wayland forbids global capture by OS design,
  folded into `capabilities.has_hotkey`); the wake-word as the universal fallback.
- **Must pass on:** Linux-Wayland → **`degraded-as-designed`** (this *is* the pass).
- **Expected graceful behavior (= the pass criterion):** the combo does **nothing**
  but logs **once** the English message "global hotkey unavailable on Wayland by OS
  design; lean on the wake word" — no crash, no repeated spam — **and** the wake word
  still summons Jarvis. A crash or a silent dead hotkey with no log is a `fail`
  (anti-pattern AP-4).

---

## D. UI-element-click (AD-10 · `make_ui_tree_source` · WELLE-2 sub-tasks 2.1/2.2/2.3/2.4)

### CP-10 — Click the "Save" button by name (real accessibility tree)
- **User says (voice):** *"Jarvis, click the Save button."*
- **Exercises:** UI-element-click — `AXTreeSource` (macOS `pyobjc`) /
  `AtspiTreeSource` (Linux `pyatspi`) → canonical-UIA role normalization
  (`role_map.py`: `AXButton`/`ROLE_PUSH_BUTTON` → `Button`).
- **Must pass on:** Win ✅ · macOS ✅ (Accessibility granted) · Linux ✅ (AT-SPI bus up).
- **Pass criterion:** the named "Save" control is found in the native tree, normalized
  to a `Button` `UIANode`, and clicked at its bounds. **Requires Wave-4 live sign-off**
  (a real tree from a real app). On the runner, scored against the role-normalization
  logic only.

### CP-11 — Read the visible UI state and act on a named field
- **User types (chat):** *"What text fields are on screen right now? Then type my email into the address field."*
- **Exercises:** UI-element-click — tree capture → `Edit` role normalization
  (`AXTextField`/`ROLE_ENTRY` → `Edit`) → type into the named field.
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅.
- **Pass criterion:** the assistant enumerates the on-screen `Edit` fields by name and
  types into the addressed one. Live tree → Wave-4 sign-off; role mapping CI-verified.

### CP-12 — AX/AT-SPI permission absent → pixel-click fallback  · **GRACEFUL-DEGRADE EXPECTED**
- **User says (voice):** *"Jarvis, click the OK button"* — on macOS with Accessibility
  **not** granted, or Linux with the AT-SPI bus **down** / `pyatspi` not installed.
- **Exercises:** UI-element-click detect-and-degrade (AD-13): empty native tree →
  the already-working pixel-click path (the universal fallback).
- **Must pass on:** macOS (no grant) → **`degraded-as-designed`** · Linux (no bus) →
  **`degraded-as-designed`**.
- **Expected graceful behavior (= the pass criterion):** the source returns an empty
  `Observation` (`source="screenshot_only"`), logs **one** English onboarding line
  ("Accessibility permission not granted — enable in System Settings › Privacy &
  Security › Accessibility …" / "AT-SPI bus unavailable — install python3-pyatspi …"),
  and the click loop **falls through to the vision/pixel-click path** and still clicks
  the OK button. A silently-empty tree with no log, or a crash, is a `fail`
  (anti-patterns AP-2 / AP-8).

---

## E. Orb overlay (AD-11 · `make_overlay_surface` · WELLE-2 sub-tasks 2.5/2.6)

### CP-13 — Orb appears and shows the LISTENING state
- **User does:** wakes Jarvis; watches the orb.
- **Exercises:** Orb — `TkColorKeyOverlay` (Windows + macOS `-transparentcolor`);
  `set_state` mapping to the LISTENING visual.
- **Must pass on:** Win ✅ · macOS ✅.
- **Pass criterion:** a **transparent** orb (no opaque magenta/black backing box)
  appears and visibly changes to its LISTENING state, then back to IDLE.
  **Transparency requires the Wave-4 live sign-off** (a headless runner cannot prove
  a transparency mask, anti-pattern AP-9). Construction is CI-verified offscreen.

### CP-14 — Orb cycles IDLE → LISTENING → THINKING → SPEAKING during a voice turn
- **User does:** completes one full voice round-trip and watches the orb.
- **Exercises:** Orb `set_state` across the four `JarvisState` values driven by the
  desktop bridge.
- **Must pass on:** Win ✅ · macOS ✅ (orb) · Linux ✅ (orb *or* tray — see CP-15).
- **Pass criterion:** the orb (or, on Linux without a compositor, the tray icon)
  shows all four states in order through the turn. State-mapping logic is CI-verified;
  the visible transition on Mac/Linux → Wave-4 sign-off.

### CP-15 — Orb on headless/Wayland Linux → state-colored tray fallback  · **GRACEFUL-DEGRADE EXPECTED**
- **User does:** runs the desktop app on a Linux box with no compositor / on Wayland.
- **Exercises:** Orb `LinuxBestEffortOverlay` → `TrayOnlySurface` fallback driving the
  already-cross-platform pystray tray (`jarvis/ui/tray.py`).
- **Must pass on:** Linux-Wayland / headless → **`degraded-as-designed`**.
- **Expected graceful behavior (= the pass criterion):** the surface detects it cannot
  key out the transparent color (Wayland / `TclError` / no display), logs **one**
  English message, and **falls through to the tray** — which shows IDLE/LISTENING/
  THINKING/SPEAKING via the state-colored tray icon. **Never an opaque magenta box,
  never a crash** (anti-pattern AP-5). The user still gets state feedback.

---

## F. Admin / elevation (AD-12 · `make_admin_transport` + `make_elevator` · WELLE-3)

### CP-16 — Authorized privileged op on a desktop (install a package)
- **User says (voice):** macOS *"Jarvis, install `wget` with Homebrew."* · Linux
  *"…installiere `htop`."* · Windows *"…install 7zip."*
- **Exercises:** Admin — `BrewInstallOp`/`AptInstallOp`/`InstallWingetOp` through the
  validated-argv HMAC core; `MacAuthElevator`/`PolkitElevator`/`UacElevator`;
  `UnixSocketTransport` peer-cred (Unix) / SDDL-ACL pipe (Windows).
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅ — **all via Wave-4 live sign-off**
  (the auth prompt is interactive; never CI-testable end-to-end, AD-3 / HN-6).
- **Pass criterion:** the OS auth prompt (UAC / polkit dialog / Touch-ID-password
  sheet) appears, and on approval the package installs via an **argv list, never a
  shell string** (`shell=False`, HN-11). Schema validation + the HMAC core are
  CI-verified against a fake transport; the prompt + install → sign-off.

### CP-17 — Malicious package name is rejected by argv validation
- **User says (voice):** *"Jarvis, install `git; rm -rf ~`."*
- **Exercises:** Admin — the Pydantic `extra="forbid"` + pattern-validated-argv gate
  (e.g. the `apt` `package` regex `^[a-z0-9][a-z0-9+\-.]{0,127}$`).
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅ (pure-Python validation — **fully
  CI-provable**).
- **Pass criterion:** the op is **refused at schema validation** (the `;`/space fails
  the regex) before any subprocess is built — the injection never reaches a shell
  (HN-11/HN-12, anti-pattern AP-10). Spoken refusal, no crash.

### CP-18 — Privileged op on a headless box → graceful refusal  · **GRACEFUL-DEGRADE EXPECTED**
- **User says (voice/chat) on the €5 VPS (no pkexec, no sudo, no GUI):** *"Jarvis,
  restart the nginx service."*
- **Exercises:** Admin — `NullElevator` (the `not capabilities.has_elevation`
  fallback).
- **Must pass on:** headless Linux VPS → **`degraded-as-designed`**.
- **Expected graceful behavior (= the pass criterion):** `make_elevator()` returns
  `NullElevator`; the request returns a typed `AdminResponse(success=False, …)` with
  the spoken/logged English message "no elevation mechanism available on this host —
  privileged operations are disabled; install pkexec or run with sudo." It **never
  silently runs the op** and **never crashes** (HN-14, anti-pattern AP-7).

---

## G. Cross-cutting (voice round-trip + Computer-Use, AD-OE contract · all six seams)

### CP-19 — Full voice round-trip on each OS
- **User says (voice):** *"Jarvis, what time is it?"* (a no-tool smalltalk turn).
- **Exercises:** wake/PTT hotkey → VAD → STT → router-brain → TTS, with the orb/tray
  showing state — the cross-cutting path the six ports plug into.
- **Must pass on:** Win ✅ · macOS ✅ · Linux ✅ (browser-mic on the VPS path).
- **Pass criterion:** one uninterrupted spoken round-trip completes (wake → spoken
  answer through `scrub_for_voice`), with the orb *or* tray reflecting the state
  cycle. No silent drop on any leg (AD-OE6). Voice plumbing is cross-platform base;
  the wake hotkey on Wayland degrades to wake-word (see CP-9).

### CP-20 — Computer-Use screenshot → click → type still works everywhere
- **User types (chat):** *"Take a screenshot, find the search box, click it, and type `personal jarvis`."*
- **Exercises:** the cross-cutting Computer-Use loop — screenshot (cross-platform
  `mss`) → element resolution via `make_ui_tree_source` (AX/AT-SPI/UIA, falling to
  pixel-click) → type (cross-platform `pyautogui`). Ties the UI-element-click port
  (D) back into the universal vision loop.
- **Must pass on:** Win ✅ · macOS ✅ · Linux-X11 ✅ (with the AX/AT-SPI or pixel path).
- **Pass criterion:** the screenshot is captured, the search box is targeted (by named
  element where the accessibility tree is available, else by pixel coordinate), the
  click lands, and `personal jarvis` is typed. The screenshot→pixel path is
  cross-platform base; the named-element path follows CP-10/CP-12's sign-off status.

---

## Coverage map (every feature × the scenarios that exercise it)

| Feature | Seam factory | Scenarios | CI-provable scenarios | Sign-off-gated scenarios | Graceful-degrade scenarios |
|---|---|---|---|---|---|
| **Terminal** | `make_pty_backend` | CP-1, CP-2, CP-3 | CP-1, CP-2, CP-3 (real PTY) | — | — |
| **App-launch** | `resolve_app_launch_target` | CP-4, CP-5, CP-6 | resolution of all three | CP-4 (live launch) | — |
| **Hotkey** | `make_hotkey_backend` | CP-7, CP-8, CP-9 | registration logic | CP-7, CP-8 (capture) | **CP-9** (Wayland no-op) |
| **UI-element-click** | `make_ui_tree_source` | CP-10, CP-11, CP-12, CP-20 | role normalization | CP-10, CP-11 (live tree) | **CP-12** (pixel fallback) |
| **Orb** | `make_overlay_surface` | CP-13, CP-14, CP-15 | construction + state map | CP-13, CP-14 (transparency) | **CP-15** (tray fallback) |
| **Admin/elevation** | `make_admin_transport` / `make_elevator` | CP-16, CP-17, CP-18 | CP-17 (argv validation) | CP-16 (prompt+install) | **CP-18** (NullElevator refusal) |
| **Cross-cutting** | all of the above | CP-19, CP-20 | voice plumbing, screenshot+pixel | hotkey/named-element legs | CP-9/CP-12/CP-15 inherited |

The four **GRACEFUL-DEGRADE EXPECTED** scenarios — **CP-9, CP-12, CP-15, CP-18** —
are the ones designed to land on `degraded-as-designed` on their target OS. Scoring
them `degraded-as-designed` is a **pass** (AD-6 / HN-4 / HN-5); scoring them `fail`
means the degrade crashed or dropped silently and is a release blocker (AD-OE6).

---

## Results-table template (the Wave-4 agent fills this in → `JARVIS-20-RESULTS.md`)

> The 4.5 runner copies this table into `JARVIS-20-RESULTS.md` and fills one
> cell per (scenario × OS) with **`pass`** / **`degraded-as-designed`** / **`fail`**
> / **`unverified-on-real-desktop`** (the last when the hardware to reach a
> sign-off-gated scenario was not available, per AD-3 / HN-6). Every non-`pass`
> cell needs a one-line reason in the Notes column. Cross-link each
> sign-off-gated row to its dated line in
> [`SIGNOFF-LOG.md`](SIGNOFF-LOG.md). **Zero `fail` cells is the close-out bar
> (4.6 / EK)** — a `fail` that is a crash or silent drop blocks release.

| ID | Feature | Windows | macOS | Linux | Notes (reason for any non-pass + SIGNOFF-LOG link) |
|---|---|---|---|---|---|
| CP-1  | Terminal — `ls` |  |  |  |  |
| CP-2  | Terminal — capture output |  |  |  |  |
| CP-3  | Terminal — resize |  |  |  |  |
| CP-4  | App-launch — browser by name |  |  |  |  |
| CP-5  | App-launch — `starte den Rechner` |  |  |  |  |
| CP-6  | App-launch — PATH tool + reject hallucination |  |  |  |  |
| CP-7  | Hotkey — global combo wake |  |  |  |  |
| CP-8  | Hotkey — push-to-talk hold |  |  |  |  |
| CP-9  | Hotkey — Wayland no-op + wake-word *(degrade expected)* | N/A | N/A |  |  |
| CP-10 | UI-click — Save button by name |  |  |  |  |
| CP-11 | UI-click — read state + type into field |  |  |  |  |
| CP-12 | UI-click — permission absent → pixel fallback *(degrade expected)* | N/A |  |  |  |
| CP-13 | Orb — appears + LISTENING |  |  | (orb or tray) |  |
| CP-14 | Orb — full state cycle |  |  | (orb or tray) |  |
| CP-15 | Orb — Wayland/headless → tray fallback *(degrade expected)* | N/A | N/A |  |  |
| CP-16 | Admin — authorized install (prompt) |  |  |  |  |
| CP-17 | Admin — reject malicious package name |  |  |  |  |
| CP-18 | Admin — headless → NullElevator refusal *(degrade expected)* | N/A | N/A |  |  |
| CP-19 | Cross — full voice round-trip |  |  |  |  |
| CP-20 | Cross — Computer-Use screenshot→click→type |  |  |  |  |

**Summary rollup (filled by 4.5):**

| OS | `pass` | `degraded-as-designed` | `unverified-on-real-desktop` | `fail` (blocker) |
|---|---|---|---|---|
| Windows |  |  |  |  |
| macOS |  |  |  |  |
| Linux |  |  |  |  |

> A release-ready close-out (4.6) shows **0 in the `fail` column** for all three
> OSes; every degrade scenario sits in `degraded-as-designed`; and every
> sign-off-gated cell is either `pass` with a dated `SIGNOFF-LOG.md` line or an
> honest `unverified-on-real-desktop` with its reason. That is the AD-3 honesty
> contract made measurable.
