# Personal Jarvis — Phase 9 Overlay Quickstart

Voice-driven Personal Jarvis gets visual feedback: a yellow
**edge glow** when Jarvis types/clicks, a **mascot** that shows the status,
**cursor trail** and **click ripple** during actions, and a
**privacy default** (invisible in OBS / screen recording / Teams share).

This document walks you through testing the feature in 4 steps.

---

## TL;DR — Three commands

```powershell
# 1. Install the overlay package once
cd "<USER_HOME>\Desktop\Personal Jarvis-main\OS-Level"
pip install -e .

# 2. Smoke test (5-second sign of life)
python -m overlay --smoke

# 3. Production test with the main Jarvis
cd "<USER_HOME>\Desktop\Personal Jarvis-main"
run.bat --debug
```

If, after step 2, the **mascot appears in the top-left of your primary monitor**
(a black rounded square with yellow eyes), the setup is working.

---

## Step 1 — Prerequisites

### a) Make the overlay package installable

Run once:

```powershell
cd "<USER_HOME>\Desktop\Personal Jarvis-main\OS-Level"
pip install -e .
```

This makes the `overlay` Python namespace globally available. Dependencies
(PySide6, websockets, pydantic, psutil, pywin32, python-ulid) are installed
along with it.

### b) Enable `jarvis.toml`

Open `<USER_HOME>\Desktop\Personal Jarvis-main\jarvis.toml`
and make sure the `[overlay]` section looks like this (or add it):

```toml
[overlay]
enabled = true                # Master switch
edge_glow_enabled = true      # Yellow border on typing/clicking
mascot_enabled = true         # Status-indicator mascot
hide_from_capture = true      # Privacy: invisible in OBS/Teams
respect_reduced_motion = true # Honor the OS animation setting
```

All defaults are `true` — if the section is missing entirely, the
overlay should still start (see plan §21.4).

### c) Check the frontend build

The renderer HTML/JS/CSS must be built:

```powershell
cd "<USER_HOME>\Desktop\Personal Jarvis-main\OS-Level\overlay-ui"
npm install                   # once
npm run build                 # produces dist/edge-glow.html + dist/mascot.html
```

The output should contain: `dist/edge-glow.html`, `dist/mascot.html`, plus
asset bundles in the `dist/assets/` folder. **0 warnings** is expected.

---

## Step 2 — Standalone smoke

Test 1 checks whether the overlay starts on its own (without the main Jarvis):

```powershell
cd "<USER_HOME>\Desktop\Personal Jarvis-main\OS-Level"
python -m overlay --smoke
```

**What you should see:**

- After ~1 s the **mascot** appears in the top-left of the primary monitor
  (position: ~200, 80 px from the desktop corner)
- Mascot look: a black rounded square with two yellow round eyes,
  black pupils, and a yellow mouth arc at the bottom
- The edge glow is **NOT** visible (no main Jarvis = no action =
  no glow — by design, plan §6.1)
- After **5 seconds** everything closes automatically

**If nothing appears:**

1. Read the console output — `python -m overlay --smoke` prints logs
2. Likely causes:
   - `ModuleNotFoundError: No module named 'overlay'` → step 1.a
     was skipped
   - `ImportError: PySide6` → run `pip install PySide6` manually
   - `FileNotFoundError: ...overlay-ui/dist/edge-glow.html` → step
     1.c was skipped (`npm run build`)

---

## Step 3 — Production test with the main Jarvis

Test 2 checks whether the main Jarvis spawns + controls the overlay correctly:

```powershell
cd "<USER_HOME>\Desktop\Personal Jarvis-main"
run.bat --debug
```

`--debug` opens a console with live logs. Look for:

- `Supervisor: spawned PID=...` — the overlay subprocess was started
- `overlay WS-Server listening on ws://127.0.0.1:7842` — IPC connected

**Visual tests per state:**

| State | What is visible? | How to trigger? |
|---|---|---|
| **idle** | Mascot only, no glow | Default after boot |
| **listening** | Mascot pulses slightly brighter | Say "Jarvis" (wake word) |
| **thinking** | Mascot pulses faster | Ask a question |
| **typing** | Yellow rotating edge stroke + bottom sweep per keystroke | "Jarvis, tippe Hallo Welt in Notepad" |
| **clicking** | Faster edge stroke + yellow ripple on every click | "Jarvis, klick auf den OK-Button" |
| **error** | Brief red flash | When an action fails |

---

## Step 4 — Mascot interactions

- **Drag**: hold the left mouse button on the mascot and drag → the position
  is saved to `jarvis.toml` `[overlay.mascot]` after release
- **Snap**: within 16 px of a screen edge → it snaps automatically
- **Right-click**: context menu with "Hide for session" / "Reset
  position" / "Settings..."
- **Restart test**: drag the mascot → fully close the app → restart it →
  the mascot should reappear at the same position
- **Monitor recovery**: drag the mascot onto a secondary monitor → unplug
  that monitor → restart the app → the mascot lands at the primary
  default position (200, 80) (deterministic plan §13.4 fallback)

---

## Step 5 — Privacy check

Plan §18.1 requires: the overlay is invisible in screen captures
(`WDA_EXCLUDEFROMCAPTURE`).

**Test:**

1. Open the **Snipping Tool** → take a screen clip → the mascot
   should be **absent** from the screenshot
2. **OBS Studio** with display capture → the mascot should be **absent**
   from the stream preview
3. Start a **Teams / Zoom** screen share → the mascot should be
   **invisible** to recipients

If the overlay is visible: `hide_from_capture = false` in the TOML
disables the privacy function (for tutorial recordings).

---

## Architecture in 5 sentences

1. **The main Jarvis** (FastAPI, existing) spawns the overlay as a
   **separate subprocess** under a Win32 Job Object (plan AD-9, guarantees
   kill-on-parent-crash within 1 s).
2. The overlay process uses **PySide6 + QtWebEngine** — edge glow and
   mascot are two separate transparent top-level HWNDs (plan AD-1).
3. **IPC** goes over **WebSocket** (state, click, action), **shared
   memory** (60 Hz cursor stream — avoids voice-loop contention),
   and a **named pipe** as a fallback (AD-5).
4. The **state machine** lives in the overlay process (AD-8); the main Jarvis
   only sends events, and the overlay translates them into state transitions.
5. **Sub-agents** (`JARVIS_DEPTH > 0` env) do **not** trigger the overlay
   (plan §8.7) — structurally, not by convention.

---

## When something doesn't work

A detailed list of 10 scenarios:
**`docs/overlay-troubleshooting.md`** (286 lines, all plan-§ references).

A few quick checks:

- Console log line `Overlay: disabled via config` → check `[overlay]
  enabled = true` in jarvis.toml
- Console log line `Overlay: Sub-Agent-Process` → the `JARVIS_DEPTH`
  env var is set by accident (`echo %JARVIS_DEPTH%` must be empty or
  0)
- Task Manager → `python.exe` with command line `... -m overlay
  --ws-port=7842` must be running when the main Jarvis is started

---

## Further documentation

- `OS-Level/OS-LEVEL_PLAN.md` — the master plan (~2000 lines,
  architecture decisions, edge cases, performance budgets)
- `docs/overlay-ipc-protocol.md` — IPC wire format (all 9 envelope
  types, backpressure, reconnection)
- `docs/overlay-state-machine.md` — 8 states + transition diagram +
  latency budget
- `docs/overlay-troubleshooting.md` — end-user troubleshooting

---

**Current state (post Phase 9.10):**
- 250+ tests green (`pytest tests/overlay/ -q`)
- Build clean (`npm run build`, 0 warnings)
- Phase 9.9 SHIP audit + all 12 audit findings fixed
- Production default path works (hook in DesktopApp + headless)
