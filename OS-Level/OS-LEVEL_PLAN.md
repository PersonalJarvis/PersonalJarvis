# OS-Level Edge-Glow Overlay — Engineering Plan

**Status:** Draft 1.0 — ready for prompt-chain derivation
**Scope:** Phase 9 (sequential, after the Phase 8 review pipeline)
**Language:** English
**Intended readers:** OpenClaw (autonomous implementer) and Ruben (owner/reviewer)

> This document is an **engineering spec**, not a prompt chain. It describes **what** is built and **why** — not **when** and in what order. The prompt chain with phases, definition-of-done per phase, and commit points is generated separately in the second step.

---

## Table of contents

1. Vision & Motivation
2. Scope: In / Out
3. Pre-Conditions
4. Conceptual Architecture
5. Architecture Decisions (AD-1 through AD-18)
6. State Model
7. Visual Design System
8. Trigger Specification
9. External Interface (Hauptjarvis ↔ Overlay)
10. IPC Protocol
11. Shared Memory Layout
12. Window Management
13. Mascot Specification
14. Click Visualization
15. Cursor Trail
16. Typing Indicator
17. Performance Budgets
18. Privacy & Safety
19. Accessibility
20. Edge Cases (complete enumeration)
21. Configuration Schema
22. File Structure
23. Testing Strategy
24. Success Criteria (feature-level definition of done)
25. Risks & Mitigations
26. Implementer Discretion — what OpenClaw may decide itself
27. Glossary

---

## 1. Vision & Motivation

### 1.1 What we are building

A **separate Python process** that draws an animated glow border around the screen edge, **when — and only when — the Hauptjarvis (main Jarvis) performs interactive PC actions**. Plus a small, persistent **mascot figure** that visualizes the status (idle/listening/speaking/acting), and point-in-time **click visualizations + cursor trail + typing indicator** for the phases in which Jarvis operates the computer.

Visual reference: Gemini Live's screen-edge glow, Apple Intelligence's edge effect, Perplexity Comet's browser-extension border. Strictly **black + yellow** in color (Jarvis design system), no rainbow, no blue, no purple.

### 1.2 Why this exists

**Trust problem with autonomous AI agents that operate the computer.** When Jarvis takes over the mouse and keyboard, the human should be able to recognize at a glance at any time:
- *"Jarvis is doing something right now"* (glow active)
- *"Jarvis is doing nothing right now"* (no glow)
- *"Jarvis clicked here"* (ripple at the click coordinate)
- *"Jarvis is typing right now"* (subtle sweep at the bottom screen edge)
- *"Jarvis is even alive"* (mascot idle pulse)

The effect is not primarily decorative. It is a **status signal with perceptual latency below the human perception threshold**, so the human does not have to guess whether Jarvis is currently active or whether the system is hung.

### 1.3 Mental model for the user

> *"My screen edge glows when Jarvis is working. As soon as it stops, it's gone. I can look at the mascot and know whether it's listening, thinking, or speaking. When it clicks, I see where. When it types, I see that it's typing. It is never louder than a discreet glow. It never distracts me. I can turn it off at any time."*

### 1.4 What "good" looks like

- **Latency Hauptjarvis action → glow appearance ≤ 50 ms.** Below the perceptual threshold for visible lag in mouse-based interaction (Forch et al. 2017: ~60 ms).
- **The glow visibly "breathes."** Not static, not aggressive. A 4–8 second idle period, gentle in/out like calm breathing.
- **The click ripple is sharp but short.** ≤ 600 ms lifetime, yellow on transparent, fade-out.
- **The mascot is small, calm, charming.** 160×160 px default. Reacts to state, but does not constantly dance around.
- **On multi-monitor it works as expected.** Glow only on the primary monitor (default), or on all (user setting). No odd DPI jumps.
- **In fullscreen games and during UAC it is simply gone.** No flicker, no z-order fights, no "try to be always-on-top over UAC".
- **It is quiet when nothing is happening.** No "I'm reminding you that I'm here" pulsing. Glow off = Hauptjarvis idle.

### 1.5 What "bad" looks like (anti-patterns)

- ❌ **Rainbow glow.** Visually impressive, but off-brand. Jarvis is black-yellow monochrome.
- ❌ **Glow even when Jarvis only speaks.** Glow only for *interactive PC actions* (mouse/keyboard). Pure speaking → no glow.
- ❌ **Sub-agents triggering the glow.** Only the Hauptjarvis. Sub-agents work in subprocesses, not "on your computer".
- ❌ **Character confetti while typing.** A tacky typing-game feel. A single subtle sweep at the screen edge, nothing more.
- ❌ **A dancing/waving mascot that reacts to user actions.** The mascot reacts to **Jarvis state**, not to user input. It is not Clippy.
- ❌ **WebGL shader for the glow.** Keeps a GPU context permanently open, costs 3–5 W continuously on integrated GPUs. CSS+SVG is the answer.
- ❌ **The glow surviving a Jarvis crash.** When the Hauptjarvis dies, the glow should disappear within ≤ 1 s. Job Object with `KILL_ON_JOB_CLOSE`.
- ❌ **Latency > 100 ms between action and glow.** Forch et al. 2017: 60 ms is the perception threshold. We want `<50 ms` of margin.
- ❌ **The voice loop being affected by the overlay.** Voice-path latency is sacrosanct (`<1.5 s` time-to-first-word, see CLAUDE.md). The overlay runs in a completely separate process.

### 1.6 Concrete user stories

**US-1.** *As Ruben I use the voice command "open my mail client and reply to the last email from Anna with a polite 'Sounds good, let's do it'." The Hauptjarvis then starts Browser-Use, navigates to GMX, clicks its way through. I see the glow around the screen, see a yellow ripple on every click, and a sweep at the bottom edge when it types. At any time I can recognize whether Jarvis is still active or already finished.*

**US-2.** *As Ruben I start a fullscreen game. The overlay disappears automatically, without me having to do anything. As soon as I leave the game, it is back.*

**US-3.** *As Ruben I start a Zoom call and share my screen. The glow is visible on my screen, but not in the transmission to the call participants. I can explicitly disable this privacy default setting if I want to record the overlay.*

**US-4.** *As Ruben I say "start 5 sub-agents in parallel to research". The Hauptjarvis spawns 5 Sub-Jarvis subprocesses. The glow does NOT appear — no sub-agent is "on my computer", they run in subprocesses. When the Hauptjarvis later aggregates the results and reads them out to me, the glow stays off.*

**US-5.** *As Ruben I right-click on the mascot → "Hide". The mascot disappears. On the next Jarvis start it is still hidden (persistence). I can show it again at any time via the tray menu.*

**US-6.** *As Ruben I drag the mascot to my second monitor. On the next start it is there again. When I unplug the second monitor, the mascot cleanly jumps to the first monitor (instead of ending up off-screen) and I get no crashes.*

### 1.7 Non-goals (see also §2.2)

Explicitly **not** part of this feature:
- Speech visualization (audio waveform). That is part of the voice UI, not the overlay.
- Notification toasts. Standard OS mechanisms suffice.
- Input via the overlay (no click on the glow starts an action). Only the mascot is clickable (drag, right-click menu).
- Communication with other apps (no "Jarvis controls OBS"). Only Hauptjarvis ↔ Overlay.
- Multi-user. One instance per Windows user session.
- Mobile/macOS/Linux. Windows-only.

---

## 2. Scope: In / Out

### 2.1 In Scope

| Component | Description |
|---|---|
| **Edge-Glow window** | Per-monitor frameless transparent click-through window with animated glow at the screen edge |
| **Mascot window** | Small (~160×160 px) frameless window with a Rive-based mascot, draggable, persistent position |
| **Click-ripple layer** | Display of a short yellow ripple effect at each click coordinate |
| **Cursor-trail layer** | Subtle dot trail that marks Jarvis' cursor movements |
| **Typing indicator** | Bottom-edge sweep when Jarvis types |
| **State machine** | 8 states (idle/listening/thinking/typing/clicking/speaking/error/hidden) with defined transitions |
| **IPC layer** | WebSocket primary + SHM cursor channel + Named-Pipe fallback |
| **Trigger instrumentation** | Decorator + context-manager + direct-emit API in the Hauptjarvis |
| **Process supervisor** | Job Object, auto-restart with exponential backoff, health check |
| **Configuration schema** | `[overlay]` section in `jarvis.toml` with atomic write pipeline |
| **Privacy mode** | `WDA_EXCLUDEFROMCAPTURE` default-on, user toggle |
| **Multi-monitor support** | Per-monitor window, DPI-aware, hotplug handling |
| **Fullscreen detection** | `SHQueryUserNotificationState` polling, auto-hide |

### 2.2 Out of Scope (Non-Goals)

| What | Why not |
|---|---|
| **Linux/macOS support** | Windows-only stack (PySide6 + Win32 specifics); macOS would have completely different window-compositor semantics |
| **WebGL shader for the glow** | Keeps a GPU context permanently open, costs 3–5 W continuously on an iGPU; CSS+SVG looks just as nice and is ~10× cheaper |
| **Voice/audio code in the overlay** | The audio path is sacrosanct (see CLAUDE.md `<1.5 s TTFW`). The overlay NEVER imports audio modules |
| **Custom DirectComposition renderer** | PySide6 + QtWebEngine is enough; DComp is a potential v2 escape, should performance profiling demand it |
| **Lottie / Live2D mascot** | Rive is 5–10× smaller and has state machines built in. Lottie has worse performance, Live2D is anime-aesthetic-specific |
| **ZeroMQ / gRPC IPC** | WebSocket is enough for ~10 Hz state events; the ZMQ/gRPC advantages are invisible at this volume |
| **Game-overlay D3D hooking** | Kernel-driver territory, non-trivial signed code, a huge maintenance burden for minimal gain |
| **Multi-language UI in the overlay (V1)** | DE + EN for the tray menu and mascot tooltip is enough for now; full i18n in V2 |
| **Sub-agent status in the overlay** | Sub-agents are not "on your computer" — they are subprocesses. The overlay reflects only Hauptjarvis state |
| **Ambient idle-state glow** (e.g. "Jarvis is alive") | The glow should be **silent** when Jarvis is doing nothing. The mascot idle pulse is enough of a sign of life |
| **Decorative animations for state transitions** (e.g. sparkle effects) | Too playful; does not fit Jarvis' British-butler persona |
| **Custom mouse-cursor replacement** | We only draw a *trail* behind the system cursor, we do not replace the cursor itself |

---

## 3. Pre-Conditions

These must be met **before** the implementation starts:

- **Phase 8 (review pipeline)** completed. `HarnessManager` and `SubprocessHarness` are stable.
- **The Hauptjarvis has a clear "action-path" choke point.** There must be a single point in the code through which all PC actions (mouse, keyboard, browser click via Browser-Use, Computer-Use tool) flow, at which trigger events can be emitted. If it does not yet exist, it must be created as a **pre-phase** (see §8.4).
- **`jarvis.toml`** is parseable and has a validated Pydantic model class. A new `[overlay]` section is added in parallel; existing content stays unchanged.
- **Python ≥ 3.12.** PySide6 ≥ 6.7 (Qt 6.7+, sufficient Win11 compositor stability).
- **Windows 11** (24H2 or newer recommended). Windows 10 works in principle, but is not explicitly tested (see AD-3).
- **Test suite green.** No red tests in `pytest tests/` before starting the OS-level feature. New tests land under `tests/overlay/`.
- **`uv` or `pip` as the package manager** for the separate overlay-dependency installation (see §22).

If a precondition is not met: pause, bring in the missing component.

---

## 4. Conceptual Architecture

### 4.1 Big Picture

```
┌────────────────────────────────────────────────────────────────────┐
│                Personal Jarvis — Process Tree                       │
│                                                                      │
│  ┌──────────────────────────────────────┐                            │
│  │  Hauptjarvis (FastAPI, existing)     │                            │
│  │  - Voice loop                        │                            │
│  │  - RouterBrain (Haiku)               │                            │
│  │  - Sub-Jarvis-Manager                │                            │
│  │  - Browser-Use / Computer-Use        │                            │
│  │  - NEW: OverlayBridge (emits events) │                            │
│  └────────────────────┬─────────────────┘                            │
│                       │                                              │
│                       │ Job Object (KILL_ON_JOB_CLOSE)               │
│                       ▼                                              │
│  ┌──────────────────────────────────────┐                            │
│  │  Overlay-Process (NEW, separate)     │                            │
│  │  PySide6 + QtWebEngine               │                            │
│  │                                      │                            │
│  │  ┌──────────┐  ┌──────────┐          │                            │
│  │  │ Edge-    │  │ Mascot-  │          │                            │
│  │  │ Glow-Win │  │ Window   │          │ Same process,              │
│  │  │ (per     │  │ (1×,     │          │ separate top-level HWNDs   │
│  │  │  monitor)│  │  optional)         │                              │
│  │  └──────────┘  └──────────┘          │                            │
│  └────────────────────┬─────────────────┘                            │
│                       │                                              │
│                       │ WebSocket (state, click, config)             │
│                       │ Shared Memory (60 Hz cursor stream)          │
│                       │ Named Pipe (fallback)                        │
└────────────────────────────────────────────────────────────────────┘
```

### 4.2 Trust and process boundary

**Hard rule:** The overlay is a **separate Python process**, not a thread, not a sub-window in the existing pywebview app. Reasons:

1. **Crash isolation.** An overlay crash must not kill the voice loop. A voice-loop crash must not kill the overlay (the reverse does, via Job Object).
2. **Latency isolation.** Qt's GUI event loop must not block FastAPI's asyncio event loop. Separate processes → separate event loops.
3. **Audio-path protection.** The voice loop has a `<1.5 s` time-to-first-word latency budget. Overlay code must never access the audio path, neither reading nor writing. The process boundary makes this *structurally impossible* — the overlay simply has no import access.
4. **Memory isolation.** PySide6 + QtWebEngine + Chromium = ~110–140 MB RSS. We do not want that in the main process.
5. **Independent restartability.** The overlay can be killed and restarted without disturbing the voice loop.

### 4.3 Lifecycle relationship

| Hauptjarvis event | Overlay reaction |
|---|---|
| Hauptjarvis starts | Spawns the overlay subprocess under a Job Object. The overlay registers via WS heartbeat. |
| Hauptjarvis crashes | The Job Object closes → Windows kills the overlay automatically within 1 s. |
| Overlay crashes | The Hauptjarvis' `OverlaySupervisor` detects it via heartbeat timeout (3 s), respawns the overlay. Exponential backoff with jitter. |
| User closes the Hauptjarvis cleanly | The Hauptjarvis sends `{"type": "shutdown"}` to the overlay → the overlay cleans up and exits. Job Object cleanup as a backstop. |
| Windows shuts down | Standard WM_QUERYENDSESSION handling in Qt. The overlay cleans up. |
| User turns the overlay off completely (`overlay.enabled = false`) | The Hauptjarvis does not even spawn an overlay. Toggled at runtime → the overlay is terminated. |

---

## 5. Architecture Decisions

Format: **AD-N — Title.** Decision. Rationale. Alternatives rejected.

---

### AD-1 — Stack: PySide6 (LGPLv3) + QtWebEngine

PySide6 as the Win32 host shell. An embedded `QWebEngineView` renders the glow and mascot CSS.

**Rationale:**
- **Python-native** (fits the FastAPI stack, no Rust/Node toolchain import).
- **LGPLv3** — compatible with commercially friendly OSS distribution. PyQt6 is GPLv3 → excluded.
- **Mature transparent click-through support** via `Qt.WindowTransparentForInput` → translates to `WS_EX_TRANSPARENT | WS_EX_LAYERED` at the HWND level. This is the same OS primitive that Tauri and Electron use.
- **Per-Monitor-V2 DPI awareness** built in via manifest.
- **Chromium renderer** supports CSS `@property`, `mask-composite`, `plus-lighter`, all modern glow primitives.

**Rejected:**
- **PyQt6:** GPLv3 — license-blocking.
- **pywebview transparent:** Broken on Windows (Issue #1611, #488, #745, #1200, #1271).
- **Electron sidecar:** ~200–300 MB RSS, too heavy.
- **Tauri 2.x:** 30–80 MB RSS, technically clean, but imports a Rust toolchain into a Python codebase. The ~50 MB memory win does not disqualify that.
- **Tkinter with `LWA_COLORKEY`:** Implemented like the Phase-1 Orb overlay — good for a static point, bad for animated glow with effects.
- **WPF via pythonnet:** Needs a .NET runtime distribution, worse DX than PySide6.
- **Pure Win32 + DirectComposition via ctypes:** Best performance (~35–55 MB RSS, <1% GPU), but ~600–900 LoC for v1. An escalation path for v2 if profiling demands it.

---

### AD-2 — Renderer: CSS conic-gradient + SVG feGaussianBlur, no WebGL

The glow animation runs as CSS `@property --angle` with `conic-gradient` plus stacked SVG `feGaussianBlur` filters. Compositor-thread-driven, `transform`/`opacity`/registered-custom-property only.

**Rationale:**
- **Power.** WebGL keeps a GPU context permanently open → 3–5 W continuously on an iGPU. CSS compositor animation: <1 W active, ~0 W idle (DWM throttled).
- **Survives CPU spikes.** When the Hauptjarvis is currently inferring Sonnet 4.7 and the CPU spikes, the CSS animation keeps running because it runs on the compositor thread, not on the JS thread.
- **No own GPU-context conflict** with the mascot (Rive Canvas2D-only, no WebGL).
- **No own shader-compile path.** Cross-driver compatible.

**Rejected:**
- **WebGL2 / WebGPU:** Power cost.
- **Canvas-2D radial-gradient per frame:** Higher CPU cost than a declarative CSS animation.
- **Lottie for the glow:** Lottie is 5–10× larger than CSS, and `conic-gradient` with `@property` is *declaratively animated*, which Lottie is not.

---

### AD-3 — Window configuration: per-monitor window, not virtual-screen window

In a multi-monitor setup, **a separate transparent window instance per `QGuiApplication.screens()` entry** is created, each sized to that monitor's `geometry()`. No single window across the entire virtual desktop area.

**Rationale:**
- **DWM has heterogeneous-DPI bugs** across monitors with different DPI settings when a layered window spans both.
- **Per-Monitor-V2 DPI awareness** works cleanly per window, not across a spanning window.
- **GPU cost** scales per actual pixel — a single spanning window across 2× 4K pays for the full 8K surface, even though only edges are drawn.

**Rejected:**
- **Single virtual-screen window:** DWM compositor problems with heterogeneous DPI, higher surface-memory cost.

---

### AD-4 — The mascot is a **separate window** in the same process

Edge-Glow and Mascot are **two separate frameless transparent top-level HWNDs**, both under the same `QApplication`. Edge-Glow is click-through (`WS_EX_TRANSPARENT`), Mascot is **NOT** click-through (it must receive clicks for drag and the right-click menu).

**Rationale:**
- **`WS_EX_TRANSPARENT` is a window-level attribute**, not regional. If both were in the same window, we would have to build per-region hit-testing via a cross-window `setIgnoreMouseEvents(true, {forward: true})` (Electron) — known to be flaky (Electron issues #30808, #33281, #35030).
- **Mascot-window hit-testing via `setMask(QRegion)`**: We set a circular hit region matching the mascot silhouette (see §12.2). Clicks outside the region (transparent corners of the 160×160 box) fall through to the desktop. This is the only correct solution for the `WA_TranslucentBackground` setup — see the note in §12.2 below.
- **The mascot can be hidden / unmounted independently**, without affecting the glow (e.g. when `overlay.mascot_enabled = false` is toggled at runtime).

**Rejected:**
- **Single combined window** with per-region hit-testing.
- **Mascot in the pywebview main window**: then the mascot is only visible when the pywebview window is open — defeats the purpose.

---

### AD-5 — IPC: WebSocket primary + Shared Memory for the cursor + Named Pipe fallback

Three channels with clear separation of duties:

| Channel | Frequency | Content | Rationale |
|---|---|---|---|
| **WebSocket** (FastAPI WS endpoint) | ≤ 10 Hz | State changes, click events, config, heartbeat | Existing FastAPI stack, JSON-debuggable, Pydantic-validatable |
| **Shared Memory** (`multiprocessing.shared_memory`) | 60 Hz | Cursor-position stream | 60 wakeups/s on asyncio competing with the voice loop is not acceptable; SHM is zero-copy, single-writer/single-reader |
| **Named Pipe** (`\\.\pipe\jarvis-overlay`) | Fallback | All WS channels, if the WS port is blocked | Corporate-firewall / AV-interference mitigation |

**Rationale:**
- **WebSocket** is already in the FastAPI stack, sub-25 ms end-to-end is comfortably within the 50 ms click-ping budget.
- **SHM for the cursor:** 60 Hz × WS roundtrip (~1–2 ms each) would generate 60 wakeups/s on the asyncio loop. The voice loop runs on the same loop. SHM = zero-copy, no loop wakeup.
- **Named Pipe** as a third leg: in our experience, corporate firewalls or AV tools sometimes block localhost WS ports on 7000–9999. A named pipe is not network-based and escapes such blocks.

**Rejected:**
- **ZeroMQ / nanomsg:** ~25–150 µs latency vs. WS ~0.5–2 ms. Invisible at our volume (~10 Hz). An additional dependency.
- **gRPC localhost:** ~2–6 ms in Python, binary (harder to debug).
- **Stdin/stdout JSON streaming:** OK for one-way, but bidirectionality gets ugly.

---

### AD-6 — Trigger source: only the Hauptjarvis, never sub-agents

Sub-agents (Sub-Jarvis, dispatched harnesses) **never** trigger the Edge-Glow overlay. Rationale: they run in their own subprocesses and do not "work" *on your computer* but *in a sandbox*. The glow should only light up when Jarvis itself operates the mouse/keyboard/browser on the user session.

**Mechanics:**
- The Hauptjarvis calls `OverlayBridge.action(...)` directly. `OverlayBridge` is only instantiated in the Hauptjarvis process, not in sub-agent subprocesses.
- Sub-agents have **no access** to the WS connection to the overlay. Structurally.
- If a sub-agent should ever actually perform PC actions (e.g. via the Computer-Use tool inside the sub-agent), that is a **separate feature** (Phase 10+) with its own architecture decision.

**Rationale:** Avoids the concept "sub-agent X clicks — where is that shown?". Sub-agents are logical processing threads, not compute subjects of the user. If the user later wants to see it differently, the architectural entry point stays clear (`OverlayBridge` central).

---

### AD-7 — Trigger API: decorator + context-manager + direct-emit

Three complementary entry points, all in `jarvis/overlay/bridge.py`:

```python
# 1. Decorator (for whole functions / tool-calls)
@overlay_action(kind="click")
async def click_at(x: int, y: int) -> None: ...

# 2. Context-Manager (for in-function scopes)
async def submit_form(...):
    async with overlay_action_scope(kind="typing", duration_hint_ms=2000):
        await pyautogui.typewrite(text)

# 3. Direct-Emit (for ad-hoc events outside functions)
await overlay_bridge.emit("click", x=x, y=y, monitor=monitor_id)
```

**Rationale:**
- The **decorator** is the default for all action functions in `jarvis/control/` and `jarvis/plugins/tool/computer_use.py` etc. → minimal code-change footprint.
- The **context-manager** for cases where a single function has multiple action phases (e.g. "open browser → enter URL → submit").
- **Direct-emit** as an escape hatch (e.g. for plugin code that cannot/should not be modified).
- All three paths land in the same `OverlayBridge.emit(...)` and are treated identically.

**Implementer note (see §26):** The exact signature and helper logic of the decorator is implementer discretion, as long as the three entry points exist logically.

---

### AD-8 — The state machine is in the overlay process

The state machine (idle → listening → … see §6) lives **in the overlay process**, not in the Hauptjarvis. The Hauptjarvis sends **events** ("action_started", "action_ended"), the overlay converts those to state transitions.

**Rationale:**
- **Decoupling.** The Hauptjarvis does not need to know how the overlay organizes its states. The overlay can change its state model without the Hauptjarvis being touched.
- **Coalescing.** When two events arrive within 16 ms (e.g. a click and a follow-up click), the overlay can coalesce them into a single visualization without the Hauptjarvis needing to know.
- **Heartbeat-driven transitions.** When the Hauptjarvis crashes and no more events come, the overlay can autonomously transition to `idle` after a timeout.

**Rejected:**
- State machine in the Hauptjarvis: tighter coupling, the Hauptjarvis blocks on state render.

---

### AD-9 — Process lifetime: Job Object with `KILL_ON_JOB_CLOSE`

The overlay subprocess is assigned to a Win32 Job Object, with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The handle is held by the Hauptjarvis and is not inheritable. When the Hauptjarvis process dies (even hard), Windows closes the job handle, which kills the overlay process as a job member.

**Rationale:** Guarantees no zombies, even on `os._exit()` or a process crash. Standard pattern (Raymond Chen canonical).

**Implementer note:** Doable via `pywin32` (`win32job.CreateJobObject`, `SetInformationJobObject`, `AssignProcessToJobObject`) or directly via `ctypes`. Pywin32 is preferred because it is already a dependency.

---

### AD-10 — Auto-restart with exponential backoff

When the overlay runs stably for 60 s, the restart counter is reset. On crash:

```python
delay = min(30, 0.5 * 2**failures) * jitter(0.8, 1.2)
```

Cap at 5 restarts within a 5-minute window. When the cap fires: tray notification "Overlay could not be started", the overlay feature disabled for the running session. The user can manually re-enable.

**Rationale:** Avoids tight restart loops on structural problems (e.g. a missing DLL).

---

### AD-11 — Atomic config pipeline for `[overlay]`

Changes to `[overlay]` in `jarvis.toml` are made via a `pre-validate → backup → write → reload-test → rollback` pipeline (analogous to the Self-Mod pipeline AD-7 there). Pydantic validation pre-write, backup to `jarvis.toml.bak`, atomic write via temp-file + rename, reload-test = Pydantic-parse + minimal-instantiate of the overlay-config class, rollback = copy the backup back if the reload test fails.

**Rationale:** Consistency with the rest of the system. Prevents broken configs from half-writes.

---

### AD-12 — Constraints in the code, not in the config

Hard limits (e.g. max FPS, max glow width, max mascot size) are anchored in the Python code as constants / Pydantic validators, not in the TOML. The user cannot override them via TOML.

**Rationale:** Consistent with Self-Mod AD-7 and Review AD-12. Prevents the user from accidentally blowing the performance budgets via an inadvertent `fps_active = 240`.

---

### AD-13 — Privacy default: `WDA_EXCLUDEFROMCAPTURE` on

`SetWindowDisplayAffinity(hWnd, WDA_EXCLUDEFROMCAPTURE)` is set **default-on** for all overlay HWNDs. Visible to the user, excluded from the DWM bitstream → invisible in OBS, Teams, Zoom, Snipping Tool.

**Opt-out:** `overlay.hide_from_capture = false` for legitimate recording use cases (tutorial video).

**Rationale:** Privacy-by-default. Prevents the user from accidentally sharing overlay content.

**Implementer note:** Win11 has a quirk in some builds (Microsoft Q&A 700122) where Chromium WebView2 does not honor the flag. Workaround (see §12.4).

---

### AD-14 — Coordinate system: logical pixels, conversion at the boundary

All IPC messages carry coordinates in **physical pixels** (Win32 native). Conversion to CSS logical pixels happens in the JS bridge of the QtWebEngine renderer, with `window.devicePixelRatio` and the per-monitor DPI.

**Rationale:** The Hauptjarvis/Computer-Use works in Win32 coords (which pyautogui does too). The renderer works in CSS coords. Conversion at one point → no subtle off-by-DPI bugs spread throughout the code.

---

### AD-15 — Schemas: Pydantic v2 (Python) and Zod (TS) symmetric

Each IPC message has a Pydantic v2 model on the Python side and an identical Zod schema on the TS side. A JSON schema is exported from Pydantic and checked against Zod (CI test).

**Rationale:** Prevents schema drift between sender and receiver. JSON-schema diffing as a CI gate.

---

### AD-16 — Mascot engine: Rive

Rive (`@rive-app/canvas-lite`, MIT, ≤ 200 KB gz) for mascot animations and the state machine.

**Rationale:**
- **State machines built in.** Maps 1:1 onto the overlay state machine without frame-scrubbing logic.
- **Asset size.** ~80 KB for a .riv file with all states. Lottie would be 200–500 KB for an equivalent animation.
- **Canvas2D runtime** (`canvas-lite`), no WebGL → no GPU-context conflict with Edge-Glow.
- **The designer tool is free and good.** Rive Editor (web).

**Rejected:**
- **Lottie:** 5–10× larger, no state machine.
- **Live2D:** anime-aesthetic-specific, overkill.
- **Sprite sheets:** loop-boundary race conditions, no smooth state transitions.

**Fallback (implementer discretion):** If the Rive integration proves problematic (e.g. build-pipeline issues), OpenClaw may switch to a PNG sprite-sheet solution, documents the rationale, and the mascot is implemented as a static PNG with a CSS animation (`opacity` pulse). No deal-breaker.

---

### AD-17 — Coalescing: 16 ms window for identical event types

When two events of the same type arrive within 16 ms (e.g. a double state change due to a race condition), the later one is ignored. Click events are NOT coalesced (every click deserves a ripple).

**Rationale:** 16 ms = 1 frame @ 60 Hz. State changes within one frame are perceptually identical.

---

### AD-18 — No audit log

In contrast to Self-Mod (AD-6) and Review (AD-11), the overlay feature has **no** persistent audit log. Rationale: the overlay is *visual feedback*, not a *security-relevant code path*. A lost glow frame is not reproduction-relevant. Heartbeat telemetry (RSS, FPS, drops) is kept in memory and dumped via stack trace on crash, but not persistently logged.

**Rejected:** Per-event logging would have disk-IO cost and no clear recovery use case.

---

## 6. State Model

### 6.1 States

| State | Description | Glow visual | Mascot animation |
|---|---|---|---|
| **idle** | Nothing is running | Glow off, mascot idle pulse | gentle 8-s sine breathing |
| **listening** | Wake word detected, STT running | Glow off (no PC action), mascot listening anim | mouth open, eyes alert |
| **thinking** | LLM inference running, no PC action | Glow off (no PC action), mascot thinking anim | head-tilt, dots above head |
| **typing** | Hauptjarvis types via keyboard input | Glow on (yellow conic-sweep), bottom-edge accent sweep | hands typing |
| **clicking** | Hauptjarvis clicks via the mouse | Glow on (yellow), Ripple at click coord | finger-point |
| **speaking** | TTS output running | Glow off (no PC action), mascot mouth-anim | mouth movement synced to RMS |
| **error** | Recoverable error in the Hauptjarvis | Glow shifts to amber/red flash 1× then off | confused expression |
| **hidden** | Overlay completely hidden (fullscreen detected, manual hide, or feature disabled) | Glow off, mascot hidden | n/a |

**Important:** **The glow is active ONLY in `typing` and `clicking`.** `listening`, `thinking`, `speaking` do not activate the glow — they only change the mascot. The whole point of the glow is "Jarvis is operating my computer right now".

### 6.2 Transitions

```
                ┌─────────┐
       ┌────────│  IDLE   │◄────────┐
       │        └────┬────┘         │ action_ended
       │             │ wakeword     │ utterance_done
       │             ▼              │
       │        ┌─────────┐         │
       │        │LISTENING│─────────┤
       │        └────┬────┘         │
       │             │ utterance    │
       │             ▼              │
       │        ┌─────────┐         │
       │   ┌───►│THINKING │─────────┤
       │   │    └────┬────┘         │
       │   │         │ tool_call    │
       │   │         ▼              │
       │   │    ┌─────────┐         │
       │   │    │ TYPING  │─────────┤   ← GLOW ON
       │   │    └────┬────┘         │
       │   │         │ click_event  │
       │   │         ▼              │
       │   │    ┌─────────┐         │
       │   │    │CLICKING │─────────┤   ← GLOW ON + RIPPLE
       │   │    └────┬────┘         │
       │   │         │ response_ready│
       │   │         ▼              │
       │   │    ┌─────────┐         │
       │   └────│SPEAKING │─────────┘
       │        └────┬────┘
       │             │ recoverable error
       │             ▼
       │        ┌─────────┐
       └────────│  ERROR  │
                └─────────┘

Special transitions:
  any → HIDDEN  (fullscreen detected, manual hide, feature disabled)
  HIDDEN → idle (fullscreen ended, manual unhide, feature re-enabled)

Coalescing:
  any → same state within 16 ms : ignored
  TYPING ↔ CLICKING : free transition (no intermediate IDLE step needed)
```

### 6.3 Event sources

| Event | Source (in the Hauptjarvis) | Trigger |
|---|---|---|
| `wakeword_detected` | `jarvis.speech.wakeword` | Porcupine/openWakeWord |
| `utterance_started` | `jarvis.speech.stt` | VAD starts |
| `utterance_ended` | `jarvis.speech.stt` | VAD ends |
| `inference_started` | `jarvis.brain.router` | Brain call started |
| `inference_done` | `jarvis.brain.router` | Brain call finished |
| `action_started{kind=typing}` | `OverlayBridge.action(...)` | Decorator/context-manager fired |
| `action_ended` | `OverlayBridge` | Same |
| `click_event{x, y, monitor}` | `OverlayBridge.click(...)` | Direct emit after pyautogui.click() |
| `tts_started` | `jarvis.speech.tts` | TTS starts |
| `tts_ended` | `jarvis.speech.tts` | TTS ends |
| `tts_audio_rms{rms_db}` | `jarvis.speech.tts` | Optional, for mascot mouth-sync; 30 Hz |
| `error{recoverable, message}` | `jarvis.core.errors` | Recoverable exception caught |

**Implementer discretion:** The exact event names and signatures are left to the implementer, as long as every state transition is derivable from exactly one event source.

### 6.4 Latency budget per transition

| Transition | Budget Hauptjarvis event → overlay visual |
|---|---|
| State change (any → any) | ≤ 50 ms |
| Click event → ripple visible | ≤ 50 ms (perceptual threshold; see §1.4) |
| Cursor move → trail point visible | ≤ 33 ms (1 frame @ 30 Hz, at 60 Hz cursor stream) |
| Typing-indicator update | ≤ 100 ms (less critical, because ambient) |
| Hauptjarvis crash → overlay process killed | ≤ 1 s |
| Overlay crash → restart spawn started | ≤ 3 s (heartbeat timeout) |

Backing research: Forch et al. 2017 — mouse-based interaction latency-perception threshold ~60 ms; Pubnub blog — visual-perception ceiling ~13 ms but acute awareness only from 75–100 ms. We choose 50 ms as a safe target.

---

## 7. Visual Design System

### 7.1 Colors

| Token | Hex | Usage |
|---|---|---|
| `--jarvis-yellow-primary` | `#FFC700` | Main glow stroke. Warm, high contrast on dark + light backgrounds |
| `--jarvis-yellow-soft` | `#FFE066` | Cream highlight in the conic sweep |
| `--jarvis-yellow-amber` | `#FFB300` | Drift target on hue rotation; also error-state tint |
| `--jarvis-black` | `#0A0A0A` | Mascot body, text |
| `--jarvis-error-red` | `#FF4D4D` | Only for the `error` state, a short flash |

**Hard rule:** No other colors. No blue, no green, no purple.

### 7.2 Glow geometry

| Token | Value | Notes |
|---|---|---|
| `glow-width` | 14 px | Edge-band width @ 1× DPI |
| `inner-stroke` | 2 px solid | Sharp wire-edge line |
| `box-shadow-inset` | `inset 0 0 6px / 18px` | Gentle inner halo, two stops |
| `feGaussianBlur stdDeviation` | `6, 22` (stacked) | Two-pass bloom |
| `mask-composite` | `exclude` | Punches out interior |
| `mix-blend-mode` | `plus-lighter` | Luminous accumulation (Chrome 105+) |

### 7.3 Animation timings

| Token | Value | Easing |
|---|---|---|
| `period-conic-sweep / typing` | 1.6 s | linear (rotation, anything else throbs) |
| `period-conic-sweep / clicking` | 0.9 s | linear |
| `period-halo-breathe` | 4.2 s | `cubic-bezier(0.25, 0.46, 0.45, 0.94)` (iOS Default) |
| `easing-state-change` | 250 ms | `cubic-bezier(0.4, 0, 0.2, 1)` (Material Standard) |
| `ripple-duration` | 600 ms | `cubic-bezier(0.2, 0.7, 0.3, 1)` |
| `cursor-trail-fade` | 400 ms | `linear` |
| `typing-sweep-period` | 800 ms (single sweep on each keystroke) | linear |

### 7.4 The five components of "liveliness"

Inspired by the Apple Intelligence Glow Effect (jacobamobin/AppleIntelligenceGlowEffect, MIT, SwiftUI, ~50 LoC). Transferred to CSS:

1. **Multi-layer composition with phase offset.** 3–5 layers, each with its own period and a negative `animation-delay` per corner. Each offset by 200 ms.
2. **Easing differentiation.** Material Standard (`cubic-bezier(0.4, 0, 0.2, 1)`) for state transitions; iOS Default (`cubic-bezier(0.25, 0.46, 0.45, 0.94)`) for idle breathing; **strict `linear`** for conic-sweep rotation.
3. **Hue drift.** A small ±15° rotation around the brand yellow. We drift between `#FFC700` (warm), `#FFE066` (cream), `#FFB300` (amber). Never a wide rainbow rotation.
4. **Opacity envelopes.** Each layer breathes 0.25 → 0.65 over 4 s. Never 0 (dead) or 1 (flat).
5. **Organic randomness via simplex noise.** 12 Hz updates on three CSS custom properties (`--hue-drift`, `--halo-a`, `--halo-b`) via the `simplex-noise` JS library (~2 KB). Microsecond cost. Separates "computer animation" from "alive".

### 7.5 Reference algorithm (paraphrased from AppleIntelligenceGlowEffect)

```javascript
// Pseudocode, runs in renderer
function regenerateGradientStops() {
  return [
    { color: 'var(--jarvis-yellow-primary)', location: Math.random() },
    { color: 'var(--jarvis-yellow-soft)',    location: Math.random() },
    { color: 'var(--jarvis-yellow-amber)',   location: Math.random() },
    { color: 'var(--jarvis-yellow-primary)', location: Math.random() },
  ].sort((a, b) => a.location - b.location);
}

setInterval(() => {
  document.documentElement.style.transition = 'background 500ms ease-in-out';
  document.documentElement.style.setProperty('--gradient-stops',
    stopsToCss(regenerateGradientStops())
  );
}, 250);
```

Plus the SVG filter layer for bloom (see §7.6).

### 7.6 SVG filter (bloom layer)

```xml
<filter id="jarvis-glow" x="-15%" y="-15%" width="130%" height="130%">
  <feGaussianBlur in="SourceGraphic" stdDeviation="6"  result="b1"/>
  <feGaussianBlur in="SourceGraphic" stdDeviation="22" result="b2"/>
  <feColorMatrix in="b2" type="matrix" result="b2y"
    values="1 .2 0 0 .05
            .8 .6 0 0 .03
            0 0 0 0 0
            0 0 0 1 0"/>
  <feMerge>
    <feMergeNode in="b2y"/>
    <feMergeNode in="b1"/>
    <feMergeNode in="SourceGraphic"/>
  </feMerge>
</filter>
```

The `feColorMatrix` shifts the further-blurred pass toward Jarvis yellow, independent of the source stroke color. That is the trick that gives the Apple Intelligence edges their "color-bleed" character.

**Critical:** Extend the filter viewport with negative `x`/`y` (e.g., `x="-15%"`), otherwise the blur clips at the SVG bounds.

### 7.7 What it should **not** be

- ❌ Wide rainbow rotation (off-brand)
- ❌ Pulsing aggressively at a 0.5-second beat (tiring)
- ❌ Sparkles/particles ("AI Magic" cliché)
- ❌ Sound effects on state change (out of scope for the overlay)

---

## 8. Trigger Specification

### 8.1 Ground rules

1. **Only the Hauptjarvis triggers.** Sub-agents never. (AD-6)
2. **Only PC actions trigger the Edge-Glow.** Speaking, thinking, listening → no glow.
3. **Latency Hauptjarvis action → overlay glow ≤ 50 ms.** (§6.4)
4. **Same-type events within 16 ms are coalesced.** (AD-17)
5. **The glow appears *before* the action, not after.** If possible: emit() → wait 1 frame → action(). Not critical, but ideal.

### 8.2 Categories of PC actions

| Category | Trigger | Glow state | Source module |
|---|---|---|---|
| **Mouse Click** | `pyautogui.click()`, Browser-Use click, Computer-Use mouse_click | `clicking` + Ripple at coord | `jarvis/control/mouse.py`, plugins |
| **Mouse Move** (extended) | `pyautogui.moveTo()` with duration > 0 | (no state change, but cursor trail active) | ditto |
| **Keyboard Type** | `pyautogui.typewrite()`, Browser-Use type, Computer-Use type | `typing` + bottom-edge sweep on each key | ditto |
| **Browser Navigate** | Browser-Use navigation action | `clicking` (treated as interactive) | `jarvis/plugins/browser_use/...` |
| **Keyboard Hotkey** | `pyautogui.hotkey()`, Computer-Use key | `typing` (without sweep — single event) | ditto |
| **Scroll** | `pyautogui.scroll()` | `clicking` | ditto |

**Implementer discretion:** The exact list of tool names that count as a "PC action" is extensible. The implementer documents the list in a module docstring (`jarvis/overlay/triggers.py`).

### 8.3 Where is instrumentation done?

**Choke-point approach:** There must be a single point in the Hauptjarvis codebase through which all PC actions flow. If it does not yet exist (which is likely, because the code has grown organically over time), it is created as a **pre-phase**:

```
jarvis/control/
  __init__.py
  mouse.py       # Wrappers around pyautogui mouse calls — apply decorator HERE
  keyboard.py    # Wrappers around pyautogui keyboard calls — apply decorator HERE
  browser.py     # Wrappers around Browser-Use — apply decorator HERE
```

All other modules (plugins, tools) go through `jarvis.control.*` instead of importing `pyautogui` directly. This gives a clean single point of instrumentation.

**Implementer discretion:** If `jarvis.control.*` already exists or OpenClaw finds a better solution (e.g. instrumentation via an `import-hook` or an aspect-oriented-programming pattern), that may be adopted, as long as the "single point of instrumentation" property is preserved.

### 8.4 Decorator contract

```python
@overlay_action(kind: Literal["click", "type", "move", "navigate", "hotkey", "scroll"], duration_hint_ms: int | None = None)
async def my_action(...) -> ...
    """
    Decorator emits:
      - 'action_started' event with kind to overlay BEFORE function call
      - 'action_ended' event AFTER function call (in finally)
      - On exception: 'action_ended' + 'error' event with recoverable=True
    """
```

Synchronous variant `@overlay_action_sync` for sync functions.

### 8.5 Context-manager contract

```python
async def submit_form(...):
    async with overlay_action_scope(kind="typing", duration_hint_ms=2000):
        # pyautogui is synchronous — offload via asyncio.to_thread so the
        # event loop is not blocked (see §17.1 Performance Budgets).
        await asyncio.to_thread(pyautogui.typewrite, text)
```

Identical semantics to the decorator, but inline-applicable.

### 8.6 Direct-emit contract

```python
# Coords are resolved BEFORE the click from the call args (NOT after the
# click, because §14.3 requires the ripple visualization to beat the OS click
# in time). Monitor is derived via MonitorFromPoint(x, y) from the args.
monitor_id = monitor_from_point(x, y)
await overlay_bridge.emit_click(x=x, y=y, monitor=monitor_id)  # PRE-click (§14.3)
await overlay_bridge.emit_action_started(kind="click")
await asyncio.to_thread(pyautogui.click, x, y)
await overlay_bridge.emit_action_ended()
```

Manual, for edge cases. The implementer recommends the decorator as the preferred pattern in the module docstring.

### 8.7 Sub-agent detection

`OverlayBridge.emit(...)` must detect whether it runs in the Hauptjarvis or in a sub-agent. Mechanics:

- `OverlayBridge` is a singleton, bound to a `jarvis.config.is_main_process()` check.
- In sub-agents (`JARVIS_DEPTH > 0` env var, see Phase 5 design) `OverlayBridge` is a **no-op stub** that silently does nothing.
- Sub-agent code sees the same interface, but events are sent nowhere.

**Implementer discretion:** The exact detection mechanics (env-var check, IPC flag, other) are the implementer's choice. The "sub-agents do not trigger the overlay" property is hard.

### 8.8 Click-coordinate capture

When a click happens: the `jarvis.control.mouse.click(x, y)` function is instrumented so that the coords are passed to `OverlayBridge.emit_click(x, y, monitor)` **before** the `pyautogui.click()` call — see §14.3 (the ripple must visually beat the OS click). The monitor ID is resolved locally via `MonitorFromPoint(x, y)`; if missing, the overlay derives it. The coords are the args given in the call (pyautogui does no DPI resolution anymore — `SetProcessDpiAwareness(PerMonitorV2)` is applied at startup, see §16).

---

## 9. External Interface (Hauptjarvis ↔ Overlay)

### 9.1 Public API (in the Hauptjarvis codebase)

```python
# jarvis/overlay/__init__.py
from jarvis.overlay.bridge import OverlayBridge, overlay_action, overlay_action_scope

# Singleton accessor
def get_overlay() -> OverlayBridge:
    """Returns the singleton OverlayBridge for the current process.
    In sub-agents (JARVIS_DEPTH>0), returns a no-op stub.
    Idempotent. Safe to call from any thread."""

# Configuration / lifecycle
async def start_overlay() -> None: ...
async def stop_overlay() -> None: ...
def is_overlay_enabled() -> bool: ...

# Direct event emit (escape hatch)
async def emit_state(state: OverlayState) -> None: ...
async def emit_click(x: int, y: int, monitor: str | None = None) -> None: ...
async def emit_action_started(kind: ActionKind, duration_hint_ms: int | None = None) -> str: ...  # returns action_id
async def emit_action_ended(action_id: str) -> None: ...
async def emit_error(message: str, recoverable: bool = True) -> None: ...

# Decorator / Context-Manager
@overlay_action(kind="click")
def some_function(...): ...

async with overlay_action_scope(kind="typing"):
    ...
```

### 9.2 Required caller documentation

Every caller of `emit_*` or `@overlay_action` must document in the code:
- **What kind of action it is** (in a module/function docstring).
- **Why it counts as an interactive PC action** (if non-obvious).

Example:

```python
@overlay_action(kind="click", duration_hint_ms=100)
async def click_button(button_locator: str) -> None:
    """Click a button via Browser-Use.

    Counts as an interactive PC action because it directly drives the
    browser to perform a click that the user can see.
    """
    ...
```

### 9.3 What the Hauptjarvis should NOT do

- ❌ Directly open a WS connection to the overlay. `OverlayBridge` does that internally.
- ❌ Duplicate state logic in the Hauptjarvis ("if I'm typing right now, I know the overlay is typing"). The state machine lives in the overlay (AD-8). The Hauptjarvis sends events.
- ❌ Wait synchronously for overlay reactions. All emits are fire-and-forget. If the overlay is down, it does not matter — the Hauptjarvis must not hang.
- ❌ Send sensitive data (API keys, passwords, voice audio) to the overlay. Never.

---

## 10. IPC Protocol

### 10.1 Wire envelope (all channels)

```json
{
  "v": 1,
  "type": "state | click | action_started | action_ended | cursor | heartbeat | config | ack | error",
  "id": "01HX9...ULID",
  "ts_ns": 1714478400123456789,
  "target": "edgeglow | mascot | *",
  "payload": { /* per-type, see below */ }
}
```

| Field | Type | Notes |
|---|---|---|
| `v` | int | Schema version. Major bump = breaking. Currently `1`. |
| `type` | enum | Discriminant for payload schema |
| `id` | string (ULID) | Unique per message; for dedup and ack-correlation |
| `ts_ns` | int (ns since unix epoch) | Sender wallclock; for latency-debugging |
| `target` | enum | `*` = all overlay components |
| `payload` | object | Type-specific (see §10.2) |

### 10.2 Payload schemas

**state**
```json
{ "state": "idle | listening | thinking | typing | clicking | speaking | error | hidden",
  "intensity": 1.0,
  "since_ts_ns": 1714478400000000000,
  "reason": "wakeword | user | tool | timeout | error" }
```

`intensity` (float, range `0.0..1.0`, normalized) modulates the visual strength of the current state — consumed by §14.1's CSS `--intensity` driver (mapped: `0.0` → lower state default, `1.0` → upper bump). Per-state defaults:

| State | intensity default | Effect |
|---|---|---|
| `idle` | 0.0 | Glow off (see §6.1 — only typing/clicking are on) |
| `hidden` | 0.0 | Glow off |
| `listening` | 0.6 | medium wake-word shimmer (if enabled) |
| `thinking` | 0.7 | slightly above, gentle pulse |
| `typing` | 1.0 | full sweep visual (§14.1) |
| `clicking` | 1.0 | full ripple visibility |
| `speaking` | 0.8 | TTS-driven modulation (see §13.2 mouth_intensity) |
| `error` | 1.0 | full error tint, no sub-bump |

**click**
```json
{ "x": 1024, "y": 768,
  "monitor": "\\\\.\\DISPLAY1",
  "button": "left | right | middle",
  "modifiers": ["ctrl", "shift"],
  "wallclock_ns": 1714478400500000000 }
```

**action_started**
```json
{ "kind": "click | type | move | navigate | hotkey | scroll",
  "action_id": "01HX9...ULID",
  "duration_hint_ms": 2000 }
```

**action_ended**
```json
{ "action_id": "01HX9...ULID",
  "succeeded": true,
  "duration_actual_ms": 1953 }
```

**cursor** (only sent if SHM is unavailable; normally cursor uses SHM)
```json
{ "x": 512, "y": 384, "monitor": "\\\\.\\DISPLAY1" }
```

**heartbeat** (bidirectional, 1 Hz)
```json
{ "uptime_s": 1234,
  "rss_mb": 78.5,
  "fps_actual": 59.8,
  "fps_target": 60,
  "drops": 0,
  "ws_connected": true,
  "shm_attached": true }
```

**config** (Hauptjarvis → Overlay, on config-reload)
```json
{ "theme": { "yellow_primary": "#FFC700", ... },
  "mascot_enabled": true,
  "mascot_pos": { "monitor": "\\\\.\\DISPLAY1", "x": 200, "y": 80 },
  "fps_active": 30,
  "fps_burst": 60,
  "all_monitors": false,
  "hide_on_fullscreen": true,
  "hide_from_capture": true,
  "respect_reduced_motion": true,
  "shm_cursor_name": "jarvis-cursor-7f3e",
  "shm_cursor_hz": 60 }
```

**ack** (Overlay → Hauptjarvis, optional, only for state-changes that have caller waiting)
```json
{ "ack_id": "01HX9...ULID",  // id of the message being acked
  "received_ts_ns": 1714478400123000000,
  "rendered_ts_ns": 1714478400140000000  // optional, if measurable
}
```

**error**
```json
{ "code": "schema_invalid | render_failed | shm_unavailable | ...",
  "message": "human-readable",
  "recoverable": true,
  "context": { /* free-form */ } }
```

### 10.3 Validation

- **Python side:** Pydantic v2 models in `jarvis/overlay/schema.py`.
- **TS side:** Zod schemas in `overlay-ui/src/schema.ts`.
- **CI gate:** `pytest tests/overlay/test_schema_symmetry.py` — exports JSON Schema from Pydantic, parses the Zod schema, compares structurally. CI fails on drift.

### 10.4 Backpressure

- Outbound queue (Python → Overlay) bounded at **256 messages**. If full: drop oldest non-state messages first (cursor, ack), drop state messages last. Log a warning. **Never block sender.**
- Inbound queue (Overlay → Python) bounded at **64 messages**.
- Coalescing per AD-17 happens at send-time, before queueing.

### 10.5 Reconnection Logic

- **Heartbeat interval:** 1 s (each side sends).
- **Heartbeat timeout:** 3 s. If no heartbeat received for 3 s, assume connection broken, attempt reconnect.
- **Reconnect-backoff:** 0.5 s, 1 s, 2 s, 4 s, 8 s, 30 s (cap).
- **Reconnect on either side:** Initial connection is always Overlay → Hauptjarvis (Hauptjarvis is the WS-server). If WS drops, Overlay attempts reconnect.
- **State-resync after reconnect:** Hauptjarvis sends current state as first message after reconnect; Overlay re-renders.

### 10.6 Channel-Routing

- Overlay process listens on **one** WS connection.
- Inside the Overlay process, messages are routed by `target` field to the appropriate Window (edge-glow, mascot, *).

---

## 11. Shared Memory Layout

Cursor stream uses a fixed-layout `multiprocessing.shared_memory` block. Single-writer (Hauptjarvis), single-reader (Overlay). No locks needed — monotonic sequence counter gives lock-free read consistency.

### 11.1 Block Definition

- **Name:** `jarvis-cursor-{8 hex chars}` (random per-session, so multi-instance doesn't collide).
- **Size:** 32 bytes (fits one cache line on x86-64).

### 11.2 Byte Layout

| Offset | Size | Type | Field | Notes |
|---|---|---|---|---|
| 0 | 8 | int64 LE | `ts_ns` | Wallclock (Unix-epoch ns); written via `time.time_ns()`. Comparable across §10.1 envelope `ts_ns` and §10.2 `wallclock_ns` / `received_ts_ns` / `rendered_ts_ns` for cross-channel latency-debugging. |
| 8 | 4 | int32 LE | `x` | Physical pixel x |
| 12 | 4 | int32 LE | `y` | Physical pixel y |
| 16 | 4 | uint32 LE | `seq` | Monotonic sequence counter (writer-local; see §11.4 write-pattern) |
| 20 | 4 | uint32 LE | `monitor_idx` | Index into `QGuiApplication.screens()` |
| 24 | 8 | reserved | — | Padding to 32 |

### 11.3 Read pattern (Overlay)

Canonical seqlock pattern: an odd `seq` = writer mid-write (busy), an even `seq` = quiescent. The reader retries if busy or torn:

```python
# Pseudocode
def read_cursor():
    seq_before = read_uint32_le(buf, offset=16)
    if seq_before & 1:
        return None  # writer mid-write (odd seq) — retry next frame
    if seq_before == self._last_seq:
        return None  # no new data
    ts = read_int64_le(buf, offset=0)
    x = read_int32_le(buf, offset=8)
    y = read_int32_le(buf, offset=12)
    monitor = read_uint32_le(buf, offset=20)
    seq_after = read_uint32_le(buf, offset=16)
    if seq_before != seq_after:
        return None  # writer started a new frame during our read — torn, retry
    self._last_seq = seq_after
    return (ts, x, y, monitor)
```

Read at 60 Hz from a `requestAnimationFrame` loop in the renderer (via a WebChannel bridge to Python that reads SHM, or directly if Python exposes an SHM-reader tick to JS). **Implementer discretion:** the exact bridge mechanism is up to the implementer.

### 11.4 Write pattern (Hauptjarvis)

Canonical seqlock writer: bump `seq` to odd first (busy marker), then write data, finally bump to even (done):

```python
# Pseudocode, called at 60 Hz when cursor-stream is enabled
def write_cursor(x, y, monitor_idx):
    seq = self._seq + 1                                      # odd → BUSY
    write_uint32_le(buf, offset=16, value=seq)
    ts = time.time_ns()  # Unix-epoch ns wallclock — comparable to §10.1/§10.2 timestamps
    write_int64_le(buf, offset=0, value=ts)
    write_int32_le(buf, offset=8, value=x)
    write_int32_le(buf, offset=12, value=y)
    write_uint32_le(buf, offset=20, value=monitor_idx)
    seq += 1                                                 # even → DONE
    write_uint32_le(buf, offset=16, value=seq)
    self._seq = seq
# `_seq` starts at 0 (even), so the first published frame ends with seq=2.
```

**Critical:** The single-counter pattern without an odd-busy marker is NOT a real seqlock and allows torn reads when reader and writer interleave before the writer bumps the counter. With the odd/even pattern here, the reader can detect any mid-write state (`seq & 1 == 1`) and distinguish two complete write operations (`seq_before != seq_after`).

**Why `time.time_ns()` (not `time.monotonic_ns()`):** the SHM cursor stream is correlated cross-channel against the WS envelope `ts_ns` (§10.1) and the click payload's `wallclock_ns` (§10.2) for latency-debugging. `time.monotonic_ns()` has an arbitrary per-process epoch (anchored to system boot or process start), so cross-clock subtraction would yield meaningless deltas — typically off by ~10¹⁸ ns (≈ negative 50 years). All three channels use Unix-epoch ns from `time.time_ns()` so debugging math is well-defined. Monotonicity is provided separately by `seq`.

### 11.5 When SHM is Disabled

If SHM creation fails (rare) or `cursor_trail_enabled = false`:
- Cursor-stream falls back to WS messages (`type: "cursor"`) at reduced frequency (10 Hz instead of 60 Hz).
- Overlay automatically detects via `config` message which channel to use.

---

## 12. Window Management

### 12.1 Edge-Glow-Window

**Per-monitor.** One instance per `QGuiApplication.screens()` entry (unless `overlay.all_monitors = false`, then only `primaryScreen()`).

**Window flags:**
```python
window.setWindowFlags(
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.WindowTransparentForInput  # → WS_EX_TRANSPARENT|WS_EX_LAYERED
    | Qt.WindowType.Tool                        # excluded from taskbar/Alt+Tab
    | Qt.WindowType.NoDropShadowWindowHint
)
window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
window.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
```

**Geometry:** `screen.geometry()` (full physical monitor, in logical pixels — Qt handles DPI conversion).

**Privacy:** After `show()`, call `SetWindowDisplayAffinity(hWnd, WDA_EXCLUDEFROMCAPTURE)` via `ctypes`/`pywin32`.

**Renderer:** `QWebEngineView` with `setAttribute(Qt.WA_TranslucentBackground, True)` and a transparent background page. Page loads `overlay-ui/dist/edge-glow.html`.

### 12.2 Mascot-Window

**Single instance.** Spawned only if `overlay.mascot_enabled = true` (top-level key under `[overlay]`, NOT inside `[overlay.mascot]` — see §21.1 Schema).

**Window flags:**
```python
mascot.setWindowFlags(
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    # NOTE: NO WindowTransparentForInput — mascot needs to receive clicks
    | Qt.WindowType.Tool
    | Qt.WindowType.NoDropShadowWindowHint
)
mascot.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
mascot.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
```

Plus Win32 extension via ctypes (because Qt doesn't expose all flags):
```python
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
hwnd = int(mascot.winId())
ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
ctypes.windll.user32.SetWindowLongW(
    hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
)
```

**Hit-region via `setMask(QRegion)`:** Per-pixel-alpha hit-testing is **NOT** automatic for Qt's `WA_TranslucentBackground` path (which is DWM-composited, not the legacy `UpdateLayeredWindow(ULW_ALPHA)` path). Without an explicit hit-region, the entire 160×160 client rect would capture clicks — blocking ~21 % of the box (transparent corners around the silhouette) for click-through. Fix: install a circular `QRegion` matching the silhouette so transparent corners pass clicks through to the desktop.

```python
from PySide6.QtCore import QRect
from PySide6.QtGui import QRegion

# Approximate silhouette as inscribed circle (cheap, works for any pose).
mascot.setMask(QRegion(QRect(0, 0, 160, 160), QRegion.Ellipse))
```

For finer hit-tracking when the Rive pose changes significantly, optionally rebuild the mask from the current alpha buffer on each animation transition (not per-frame — too costly). For V1 the static circular region is sufficient.

**Drag-handling:** Implement `mousePressEvent` + `mouseMoveEvent` + `mouseReleaseEvent` for drag. Drag is initiated only inside the `setMask` region. Implementer discretion: alternative JS path `mouse-down → window.draggable = true → window.move()` if rendering inside the WebView.

**Right-click menu:** Standard `QMenu` with "Hide", "Move to default position", "Settings...".

**Privacy:** Same `WDA_EXCLUDEFROMCAPTURE` treatment.

### 12.3 Per-Monitor DPI Awareness

**Manifest** (`pyproject.toml` → `[tool.briefcase.windows]` or analogous, depending on packaging):

```xml
<asmv3:application xmlns:asmv3="urn:schemas-microsoft-com:asm.v3">
  <asmv3:windowsSettings xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">
    <dpiAwareness>PerMonitorV2</dpiAwareness>
  </asmv3:windowsSettings>
</asmv3:application>
```

**Code-side fallback:**
```python
import ctypes
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
```

**Handling DPI changes mid-session:** Listen for `screenChanged` signal on each window and recompute geometry. Qt mostly handles this transparently; implementer should test the case `monitor unplugged at runtime` explicitly.

### 12.4 WDA_EXCLUDEFROMCAPTURE Win11 Quirk

Some Win11 builds fail to honor `WDA_EXCLUDEFROMCAPTURE` on Chromium-WebView2 child windows (Microsoft Q&A 700122). Mitigation:

- The Qt window IS a top-level Win32 HWND (no quirk).
- The WebEngineView is a child of the Qt window, gets clipped by the parent, doesn't render outside parent bounds.
- Affinity flag is set on the Qt top-level HWND.
- In practice this works for the layered-host pattern.

Implementer should test with OBS, Snipping Tool, and a Teams-share to verify.

### 12.5 Multi-Monitor Hotplug

- Subscribe to `QGuiApplication.screenAdded(QScreen*)` and `screenRemoved(QScreen*)`.
- On `screenAdded`: if `all_monitors = true`, instantiate new edge-glow-window for that screen.
- On `screenRemoved`: dispose any window pointing to that screen. Mascot-recovery (see §13.4).

### 12.6 Always-on-top Limitations

Universal across all GUI stacks (not a Qt limitation):
- **D3D Exclusive Fullscreen** (e.g., some games): `HWND_TOPMOST` doesn't draw over.
- **UAC Secure Desktop**: never drawn over.
- **Lock Screen**: never drawn over.

**Mitigation:** `SHQueryUserNotificationState` polled every 2 s on a low-priority thread. On `QUNS_RUNNING_D3D_FULL_SCREEN`, `QUNS_PRESENTATION_MODE`, or `QUNS_BUSY` (configurable), transition to `hidden` state.

---

## 13. Mascot Specification

### 13.1 Visual Concept

Small, geometric, friendly — not anime, not Clippy, not corporate. **Black-and-yellow only.**

Design direction (implementer discretion):
- A simple rounded shape (could be a "J" monogram with eyes, a stylized circle with a face, a hexagon with subtle features, etc.).
- Dark body (`--jarvis-black` or near-black with yellow accents).
- Eyes (or eye-stand-in) that can change to indicate state: closed/relaxed (idle), open/wide (listening), focused (thinking), animated (speaking).
- 160×160 px default size. Scale-able via config (clamped to 80–256 px).

**The mascot is NOT a personality.** It's a status indicator with charm. No "speech bubbles", no winking, no wandering around the screen. It stays where the user puts it.

**Implementer discretion:** The exact mascot illustration is up to the implementer. Free choice as long as: black + yellow, geometric (not anime/photoreal), state-readable, ≤ 80 KB Rive asset.

### 13.2 State Animations

Each Rive State Machine input maps 1:1 to an OverlayState:

| Rive Input | Type | Triggered when state == |
|---|---|---|
| `idle` | trigger | `idle` |
| `listening` | trigger | `listening` |
| `thinking` | trigger | `thinking` |
| `acting` | trigger | `typing` or `clicking` |
| `speaking` | trigger | `speaking` |
| `error` | trigger | `error` |
| `mouth_intensity` | number 0..1 | continuously updated from `tts_audio_rms` |

### 13.3 Drag Behavior

- **Click + hold + drag** moves the mascot.
- Position persisted on `mouseRelease`.
- Snaps to monitor edges within 16 px tolerance (gentle "magnetic" feel).
- Cannot be dragged off-screen (clamped to monitor work-area minus 16 px margin).

### 13.4 Position Persistence + Monitor Recovery

Persist to `jarvis.toml` under `[overlay.mascot]`:
```toml
position_monitor = "\\\\.\\DISPLAY1"  # device name (szDevice from GetMonitorInfo)
position_x_relative = 200             # pixels from monitor's top-left work-area
position_y_relative = 80
```

**Why szDevice only (not EDID):** `EnumDisplayMonitors` + `GetMonitorInfoW` (the only enumeration APIs we use) return `szDevice` (e.g. `\\.\DISPLAY1`) but **not** EDID. Retrieving EDID would require a separate path (WMI `WmiMonitorID`, `SetupDi*`, or registry under `HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\…\Device Parameters\EDID`) — out of scope for V1. `szDevice` matching is therefore best-effort across port-swaps; the deterministic fallback in step 3 handles the case where the saved device name is gone.

**On startup:**
1. Enumerate live monitors via `EnumDisplayMonitors` + `GetMonitorInfoW`. Build a map `{szDevice → (rcWork, hMonitor)}`.
2. Look up `position_monitor` (the persisted `szDevice`) in the map.
   - **Found:** Restore mascot at `(rcWork.left + position_x_relative, rcWork.top + position_y_relative)`. Clamp into `rcWork` minus 16-px margin if the relative offset would put the mascot off the work-area (covers the case of resolution changes on the same monitor).
3. **Not found** (laptop undocked, port-swapped, monitor unplugged) — deterministic fallback:
   - Pick the **primary monitor** via `MonitorFromWindow(hwnd_desktop, MONITOR_DEFAULTTOPRIMARY)`.
   - Place mascot at the **default position** for that monitor: `(rcWork.left + 200, rcWork.top + 80)`.
   - Log `mascot.position_recovered = "primary_fallback"` to telemetry; user's customized relative offset is intentionally discarded because it was anchored to a now-missing virtual-desktop region that cannot be reconstructed from the persisted relative coords alone.
4. Listen to `WM_DISPLAYCHANGE` and `WM_DEVICECHANGE` (via Qt's `screenAdded`/`screenRemoved`); on event re-run steps 1–3.
5. Validate the final placement against `GetSystemMetrics(SM_X/Y/CX/CYVIRTUALSCREEN)` as last sanity-check; if outside virtual-screen bounds for any reason, force-clamp into primary `rcWork`.

### 13.5 Hide / Disable Toggles

- **Right-click → "Hide for this session"**: mascot hidden, re-appears on next Jarvis-start.
- **Tray-Icon-Menu → "Show Mascot"**: re-show after session-hide.
- **Config: `overlay.mascot_enabled = false`** (top-level key, see §21.1): mascot never spawns. Window-process doesn't even create the mascot HWND.

### 13.6 What Mascot Does NOT Do

- ❌ Wander around the screen (Open-LLM-VTuber's "pet mode" pattern).
- ❌ Speak in speech-bubbles ("Hi! I see you're trying to write a letter…").
- ❌ React to user mouse movements ("looking at you" pattern).
- ❌ Play sounds.
- ❌ Spawn additional windows or popups.
- ❌ Draw attention to itself when nothing is happening (idle pulse is gentle, not eye-catching).

---

## 14. Click Visualization

### 14.1 Visual

When `click` event arrives:
- Spawn a `<div>` at the click coordinate.
- Initial size: 8×8 px circle, opacity 1.
- Animate via CSS to: scale(40), opacity 0, over 600 ms with `cubic-bezier(0.2, 0.7, 0.3, 1)`.
- Color: yellow gradient with falloff:
  ```css
  background: radial-gradient(circle,
    var(--jarvis-yellow-primary) 0%,
    rgba(255, 199, 0, 0.25) 60%,
    transparent 70%);
  box-shadow: 0 0 24px 6px rgba(255, 199, 0, 0.55);
  ```

### 14.2 Performance

- **Pre-warm a div pool of 8 reusable divs** to avoid `appendChild` jank during burst clicks.
- CSS animations only on `transform: scale()` and `opacity` (compositor-thread-safe).
- After 600 ms: reset div, return to pool.

### 14.3 Latency Budget

End-to-end target ≤ 50 ms:
- pyautogui `click()` → OS event: ~5 ms
- WS send: ~1–3 ms
- WS recv + DOM/paint: ~12–17 ms
- **Total: ~20–25 ms.** Comfortably under budget.

**Critical implementation detail:** Issue WS-send-emit **first**, then `pyautogui.click()` second. Order matters because the visual should reach the WebView **before** the OS click finishes propagating, so the user sees the ripple at the moment of impact rather than after.

### 14.4 Coordinate Transformation

- Hauptjarvis sends physical pixel coords (Win32 native, what pyautogui uses).
- Overlay renderer converts to CSS-logical-pixels: `cssX = physicalX / window.devicePixelRatio`.
- Per-monitor: ripple is rendered in the monitor that owns the click coord. If `all_monitors = false`, ripples on non-primary monitors are silently dropped.

### 14.5 Edge Cases

- **Click coordinates outside any monitor** (rare; can happen with stale screen-config): drop silently, log warning.
- **Click during HIDDEN state** (e.g., fullscreen game): drop silently. We don't visualize what we can't draw.
- **Multiple clicks within 50 ms** (rapid double-click): each gets a ripple. They overlap visually — that's fine.

---

## 15. Cursor Trail

### 15.1 Visual

A subtle yellow trail follows Jarvis' cursor when Hauptjarvis is moving the mouse.

- Tail of 20 points, 16-px-radius circles.
- Each point fades over 400 ms (`linear`), so the trail length is ~20 points × (1000 / 60) ms = ~333 ms of trail.
- Color: `--jarvis-yellow-primary` with opacity 0.35 → 0.

### 15.2 Rendering

- **Canvas-2D in an OffscreenCanvas in a Web Worker.** Avoids main-thread re-rendering.
- Ring-buffer of 20 points; on each frame, draw all points with their respective opacity.
- `globalCompositeOperation = 'lighter'` for luminous accumulation.

### 15.3 Source

- **60 Hz cursor stream over SHM** (see §11).
- Hauptjarvis writes cursor position **only when** an action is in progress (i.e., `OverlayBridge` is in an active `action_started` scope). Idle = no writes.
- Overlay reads SHM at 60 Hz from the renderer.

### 15.4 When Disabled

- `cursor_trail_enabled = false` in config → SHM not allocated, renderer doesn't read.
- Mid-session toggle: `config` message updates `shm_cursor_name` to empty, renderer stops reading.

---

## 16. Typing Indicator

### 16.1 Visual

Bottom-edge sweep effect on each keystroke when Hauptjarvis is in `typing` state.

- A thin yellow line (4px tall) at the bottom edge of the screen.
- Each keystroke triggers a 200ms `transform: translateX` from -100% to +100%.
- Plus: `--intensity` CSS variable bumps from 0.85 to 1.2 for 200 ms.

### 16.2 Why Not Character-Confetti

Character-by-character confetti effects (à la some chat apps) read as gimmicky for an assistant overlay. Subtle is the right register.

### 16.3 Optional Caret-Localized Pulse (V2)

Caret-following micro-pulse (using `uiautomation` Python wrapper to query the current focused control's caret rect via `TextPattern2.GetCaretRange().GetBoundingRectangles()`) is **out of scope for V1** — uiautomation calls can block 50–200 ms in unhealthy app trees, and the bottom-edge sweep is sufficient.

V2 candidate if user wants more visual feedback.

---

## 17. Performance Budgets

### 17.1 Hard Ceilings

| Resource | Idle | Active | Hard Ceiling | Action if breached |
|---|---|---|---|---|
| **Overlay process CPU** | < 0.5% | < 3% | 5% | Throttle FPS, log warning |
| **Overlay process GPU** | < 0.5% | < 2% | 5% | Reduce glow complexity |
| **Overlay process RSS** | 60–100 MB | 80–140 MB | 200 MB | Restart process |
| **Network (WS)** | ~50 B/s | ~5 KB/s | 50 KB/s | Throttle event-emission |
| **Battery delta** | < 1%/h | < 3%/h | 5%/h | Force throttle |

### 17.2 FPS Targets per State

| State | FPS Target |
|---|---|
| `hidden` | 0 (paused) |
| `idle` (no glow) | 1 (mascot only, breath animation) |
| `listening` / `thinking` / `speaking` | 30 (mascot anim only) |
| `typing` / `clicking` (with glow) | 60 (compositor-driven) |
| `error` (red flash) | 60 for 600 ms, then back to source state |

### 17.3 Throttling Strategy

- **Idle Detection:**
  - 30 s no events → mascot drops to 1 fps, edge-glow frozen.
  - 5 min no events → set WebView `IsVisible = false` (Chromium throttles hidden views).

- **Wake on Event:**
  - Any state change or click event → bump back to target FPS within 1 frame.

- **AC vs Battery:**
  - `GetSystemPowerStatus`. On battery: halve all FPS targets (60→30, 30→15). Skip cosmetic particle effects.

- **Adaptive on perf-budget breach:**
  - If heartbeat reports CPU > 5% for 3 consecutive seconds: drop FPS by 50%, log warning. If still over budget after 30 s: force `hidden` state and surface tray-warning.

### 17.4 Telemetry

Heartbeat (1 Hz) reports `rss_mb`, `fps_actual`, `fps_target`, `drops`. Hauptjarvis-side `OverlaySupervisor` keeps a rolling 60-sample window. On budget breach, log a `WARNING`-level entry to `jarvis.log` (no separate audit log per AD-18).

---

## 18. Privacy & Safety

### 18.1 Hide-from-Capture Default

`SetWindowDisplayAffinity(hWnd, WDA_EXCLUDEFROMCAPTURE)` on every overlay HWND, every monitor, mascot included. Default-on.

User can opt-out via `overlay.hide_from_capture = false` in TOML.

**Reapply:** On `WM_DPICHANGED`, on `screenAdded`, after each `show()`.

### 18.2 No User Data Through Overlay

Hard rules:
- ❌ Voice audio NEVER through overlay.
- ❌ STT transcripts NEVER through overlay.
- ❌ LLM prompts/responses NEVER through overlay.
- ❌ API keys, credentials, OAuth tokens NEVER through overlay.
- ❌ Clipboard contents NEVER through overlay.

The only data flowing to the overlay:
- Coarse states (idle/listening/typing/...).
- Click coordinates (pixel positions, not what's at those positions).
- Cursor positions.
- TTS RMS levels (a single number; can't be reverse-engineered to audio content meaningfully).

### 18.3 Voice-Loop Isolation

Overlay process **NEVER** imports `jarvis.speech.*` or any audio module. Enforced via:
- Process-boundary (separate Python interpreter).
- Code-organization (overlay module tree has no `import jarvis.speech.*`).
- Lint-rule (CI-test that greps for forbidden imports in `os-level/`).

### 18.4 Credentials / Secrets

Overlay never reads `jarvis.toml`'s sensitive sections. It only receives the `[overlay]` section subset via the `config` IPC message, which Hauptjarvis filters before sending.

### 18.5 Network

**The Hauptjarvis** binds the WS listener on **`127.0.0.1` only** (not `0.0.0.0`) — see §10.5 (the Hauptjarvis is the WS server, the overlay is the client). Port chosen from a small range (default 7842). No auth (loopback-only is the security boundary).

**Implementer note:** The §24.4 DoD check ("WS port bound to `127.0.0.1` only — verified via `netstat`") looks at the Hauptjarvis process, not the overlay subprocess.

### 18.6 Code signing / EXE distribution

(Out of scope for V1, but flagged for future:) when packaged via Briefcase or PyInstaller, signed with the project's code-signing cert. Helps SmartScreen / AV-scanners not flag the overlay process.

---

## 19. Accessibility

### 19.1 prefers-reduced-motion

```css
@media (prefers-reduced-motion: reduce) {
  .edge-glow {
    animation: none;
    /* Replace animation with static low-intensity glow */
    opacity: 0.4;
  }
  .ripple { animation-duration: 200ms; }
  .mascot { animation-play-state: paused; }
}
```

Detected from Windows accessibility settings (Animations setting) via the OS-level media query that Chromium honors automatically.

### 19.2 Disable-Animations Toggle

User setting `overlay.respect_reduced_motion = true` (default) → CSS query honored. Setting `false` → animations always run regardless of OS setting (for users who explicitly want them despite reduced-motion preference).

Additional toggle `overlay.animations_enabled = true` → master switch. False = static low-intensity overlay only (no movement at all).

### 19.3 Screen Reader Compatibility

- All overlay HWNDs have `WS_EX_TOOLWINDOW` → screen readers usually skip them.
- Set `accessible role = "presentation"` on the renderer's root element so JAWS/NVDA don't try to announce glow animation states.
- Mascot has ARIA-label `"Jarvis status indicator"`; screen readers announce it once on focus.

### 19.4 High-Contrast Mode

- Detect via the `@media (forced-colors: active)` CSS query — that is the W3C standard query for OS-imposed forced-color schemes (Windows High Contrast Mode triggers it in Chromium). `prefers-contrast` would be wrong (it applies to user-preference modes such as macOS "Increase Contrast", not Windows HC), and `prefers-contrast: high` is not even a valid value (MQ5 allows only `no-preference | more | less | custom`).
- In high-contrast mode: drop the bloom blur (visual noise); use a solid 4-px stroke with the `CanvasText` system color (conformant to the user's HC theme). No `mix-blend-mode`, no acrylic, no gradients.
- Mascot: switch to a high-contrast variant (implementer discretion: simpler outline, fewer details).
- Example:
  ```css
  @media (forced-colors: active), (prefers-contrast: more) {
    .edge-glow { filter: none; border: 4px solid CanvasText; }
    .mascot   { /* high-contrast variant */ }
  }
  ```

### 19.5 Color-Blind Safety

Yellow on dark monitors works for all common color-blindness types (deuteranopia, protanopia, tritanopia). No additional mitigation needed for V1. Verifiable via Coblis or similar simulation tool.

---

## 20. Edge Cases (complete enumeration)

### 20.1 Display & Monitor

| Edge Case | Handling |
|---|---|
| **Multi-monitor (default)** | Per-monitor windows, primary-only by default |
| **Monitor hotplug (add)** | `screenAdded` listener; spawn new window if `all_monitors = true` |
| **Monitor hotplug (remove)** | `screenRemoved` listener; dispose window; recover mascot to nearest monitor |
| **DPI change mid-session** | Qt's `screenChanged` signal triggers recompute; verify under per-monitor-V2 |
| **Vertical monitor (90° rotated)** | Use `screen.geometry()` which respects rotation; glow follows the rotation |
| **Ultra-wide monitor (21:9, 32:9)** | Standard handling; conic-gradient adapts naturally to aspect ratio |
| **Monitor with extreme DPI (300%+)** | PerMonitorV2 ensures geometry stays right; visuals scale proportionally |
| **Monitor refresh rate >60 Hz (240, 360 Hz)** | Compositor-driven CSS animations (see AD-2) are essentially free at idle (DWM throttled). If a real FPS cap is needed: JS-driven `requestAnimationFrame` with timestamp-delta gating at ~16.6 ms (CSS `animation-iteration-count` is NOT FPS-related — it controls only the number of cycles, not the sampling rate). Default: accept the native cadence. |
| **Monitor refresh rate <60 Hz (50 Hz EU)** | Animations work fine; perceived speed slightly slower (linear easing on rotation makes this OK) |
| **Variable refresh rate (G-Sync, FreeSync)** | Standard browser handling; no special action |
| **HDR mode** | Yellow `#FFC700` is well within sRGB; renders correctly. No display-p3 / scRGB needed for V1 |
| **Mixed DPI displays (4K + 1080p)** | Per-monitor windows give correct DPI per surface |

### 20.2 Window-State

| Edge Case | Handling |
|---|---|
| **Fullscreen exclusive (D3D)** | `SHQueryUserNotificationState == QUNS_RUNNING_D3D_FULL_SCREEN` → `hidden` state |
| **Borderless fullscreen** | Foreground-window-rect equals monitor-rect heuristic + no `WS_CAPTION` → `hidden` |
| **UAC secure desktop** | Universal limitation; nothing to do |
| **Lock screen** | Universal limitation; nothing to do |
| **Remote Desktop session** | Detect via `GetSystemMetrics(SM_REMOTESESSION)`; auto-disable overlay (latency too high for visual feedback) |
| **Game Mode (Xbox Game Bar)** | Auto-detect via `QUNS_BUSY` heuristic; configurable opt-out |
| **Tablet Mode** | (Win11 has dropped Tablet Mode but for legacy: adjust mascot size, keep glow) |
| **Multi-user fast user switching** | Hauptjarvis is per-user; overlay is per-Hauptjarvis. No cross-talk possible |
| **System sleep** | `WM_POWERBROADCAST PBT_APMSUSPEND` → pause animations, save mascot state. On `RESUME`: re-init |
| **Theme change (light ↔ dark)** | No-op; glow yellow works on both |

### 20.3 IPC & Process

| Edge Case | Handling |
|---|---|
| **WS port already in use** | Pick next port, retry up to 10 ports. If all blocked: fall back to named pipe |
| **Backend crash mid-action** | Heartbeat-timeout in 3 s → overlay transitions to `idle` autonomously, attempts reconnect |
| **Overlay crash mid-frame** | Hauptjarvis' supervisor detects via 3-s heartbeat-timeout; spawns new overlay process |
| **Two state-changes within 16 ms** | Coalesce per AD-17 |
| **State change while overlay is mid-animation** | New state interrupts; CSS-transition between intensities |
| **Click coordinates outside any monitor** | Drop silently, log warning |
| **Cursor coordinates outside any monitor** | Drop silently |
| **WS message with unknown `type`** | Log warning, ignore; forward-compatible |
| **WS message with invalid schema** | Log warning, send `error` reply, ignore message |
| **Schema-version mismatch (`v` field)** | Log error, request newer overlay-update; continue with current schema if compatible |
| **Network unavailability for localhost (rare)** | Should never happen for loopback; if WS-bind to `127.0.0.1` fails, use named-pipe |
| **Hauptjarvis sends 1000 events in 100 ms** | Bounded queue (256 msgs), drops oldest non-state messages first |

### 20.4 Mascot

| Edge Case | Handling |
|---|---|
| **Mascot dragged off-screen** | Clamp to `rcWork` minus 16-px margin during drag |
| **Mascot's saved monitor disappears** | Snap to nearest live monitor's nearest valid position |
| **Two mascots accidentally spawned** | Singleton-check at startup; second spawn gets ignored, log warning |
| **Mascot drag while state is changing** | State updates continue normally; drag is independent |
| **User right-clicks mascot during animation** | Menu opens normally; animation continues in background |
| **Mascot Rive file fails to load** | Fall back to static PNG (implementer discretion: include a fallback PNG) |
| **System DPI change during drag** | Stop drag, recalculate position, resume |

### 20.5 Performance

| Edge Case | Handling |
|---|---|
| **Battery saver mode active** | Halve all FPS targets, skip cosmetic effects |
| **System under load (CPU > 80%)** | Throttle FPS; if can't meet target for 3 s, drop to lower FPS budget |
| **Heartbeat reports RSS > 200 MB** | Restart overlay process |

---

## 21. Configuration Schema

### 21.1 `jarvis.toml` — `[overlay]` section

```toml
[overlay]
enabled = true                       # bool — master switch
edge_glow_enabled = true             # bool — separate from `enabled`, allows mascot-only mode
mascot_enabled = true                # bool — separate from `enabled`, allows glow-only mode
all_monitors = false                 # bool — if true, glow on every screen; if false, primary only
hide_on_fullscreen = true            # bool
ignore_busy_state = false            # bool — if true, don't hide on QUNS_BUSY (only on actual fullscreen)
hide_from_capture = true             # bool — WDA_EXCLUDEFROMCAPTURE
respect_reduced_motion = true        # bool — honor OS reduced-motion preference
animations_enabled = true            # bool — master animation toggle (separate from reduced-motion)

# Throttling
fps_idle = 1                         # int 0..30
fps_active = 30                      # int 1..60
fps_burst = 60                       # int 1..60 — for typing/clicking only
idle_timeout_s = 30                  # int 5..600 — drop to fps_idle after this
hide_timeout_s = 300                 # int 60..3600 — set IsVisible=false after this

# IPC
ws_port = 7842                       # int 1024..65535
ws_host = "127.0.0.1"                # str — must be loopback
ws_port_range_max = 7852              # int — search range if port is busy
fallback_pipe = "\\\\.\\pipe\\jarvis-overlay"
shm_cursor_name_prefix = "jarvis-cursor"  # str — actual name has random suffix
heartbeat_interval_s = 1             # int 1..10
heartbeat_timeout_s = 3              # int 2..30

# Cursor trail
cursor_trail_enabled = true          # bool
cursor_stream_hz = 60                # int 10..120

[overlay.theme]
yellow_primary = "#FFC700"           # str (hex)
yellow_soft = "#FFE066"
yellow_amber = "#FFB300"
black = "#0A0A0A"
glow_width_px = 14                   # int 4..40
hue_drift_degrees = 15               # int 0..30 — max drift around primary

[overlay.mascot]
position_monitor = ""                # str (device name) — empty = use primary
position_x_relative = 200            # int — pixels from monitor's top-left
position_y_relative = 80             # int
size_px = 160                        # int 80..256
draggable = true                     # bool
snap_to_edges_px = 16                # int 0..32 — snap distance to monitor edges
hidden_for_session = false           # bool — runtime-set on right-click "Hide"
```

### 21.2 Pydantic Validation

All fields validated via `OverlayConfig(BaseModel)` in `jarvis/overlay/config.py`. Pydantic-validators enforce:
- Color strings match `#RRGGBB` regex.
- Numeric ranges as commented above.
- `ws_host` must be in `{"127.0.0.1", "::1", "localhost"}`.
- `position_monitor` is sanity-checked against `EnumDisplayMonitors` at startup; falls back to primary if invalid.

### 21.3 Atomic Write Pipeline (per AD-11)

```
1. pre-validate: parse new config via Pydantic
2. backup: copy jarvis.toml → jarvis.toml.bak
3. write: tempfile + os.replace() (atomic on Win32)
4. reload-test: re-parse new file, instantiate OverlayConfig
5. rollback if reload-test fails: copy jarvis.toml.bak → jarvis.toml
6. notify overlay via `config` IPC message
```

### 21.4 Defaults & Migration

If `[overlay]` section missing entirely → use Python-side defaults (above values), don't touch the TOML. On user's next manual config edit, they can add the section if they want to customize.

If individual fields missing from `[overlay]` → use Python-side defaults for those fields.

Schema-version: implicit. If we ever break compatibility (V2), add `[overlay] _schema_version = 2` and a Python migration path. V1: no version field needed.

---

## 22. File Structure

```
OS-Level/                                # NEW top-level project folder
  pyproject.toml                         # PySide6, websockets, pydantic, psutil, pywin32
  README.md
  OS-LEVEL_PLAN.md                       # this document
  
  src/overlay/
    __init__.py
    __main__.py                          # python -m overlay [--self-test]
    main.py                              # entry point; takes WS port as arg
    
    # Window management
    window_glow.py                       # PySide6 transparent click-through edge-glow window
    window_mascot.py                     # PySide6 transparent mascot window (clickable)
    transparency.py                      # ctypes Win32 (WS_EX_TRANSPARENT, WDA_EXCLUDEFROMCAPTURE, ...)
    monitors.py                          # EnumDisplayMonitors, DPI, hotplug
    
    # State + IPC
    state.py                             # OverlayState enum, transition logic
    schema.py                            # Pydantic v2 models (state, click, action, heartbeat, config, ...)
    ipc_ws.py                            # WS client (connects to Hauptjarvis WS-server on 127.0.0.1; see §10.5)
    ipc_pipe.py                          # Named-pipe fallback
    cursor_shm.py                        # multiprocessing.shared_memory wrapper
    
    # Detection / supervision
    fullscreen_detect.py                 # SHQueryUserNotificationState polling
    
    # Config (read-only inside overlay)
    config.py                            # OverlayConfig Pydantic model
    
    # Self-test for CI / smoke
    self_test.py                         # opens window, renders frame, exits with code
  
  overlay-ui/                            # rendered inside QtWebEngine
    package.json                         # vite, simplex-noise, @rive-app/canvas-lite
    vite.config.ts
    tsconfig.json
    index.html                           # main entry
    src/
      main.ts                            # boots renderer, connects to bridge
      bridge.ts                          # WebChannel ↔ Python; Zod validators
      schema.ts                          # Zod schemas (mirror of Python schema.py)
      
      edge-glow/
        index.html
        edge-glow.css                    # @property --angle, conic-gradient, mask-composite
        glow.svg                         # feGaussianBlur+feMerge+feColorMatrix bloom
        glow.ts                          # state subscription, intensity bumps, rAF loop
        ripple.ts                        # CSS-injected div pool
        cursor-trail.ts                  # OffscreenCanvas worker
        typing-sweep.ts                  # bottom-edge sweep
        noise.ts                         # 12 Hz Simplex modulation
      
      mascot/
        index.html
        mascot.css
        mascot.ts                        # Rive runtime, drag, right-click menu
        mascot.riv                       # ≤80 KB Rive asset (or PNG fallback)
        mascot-fallback.png              # static fallback if Rive load fails

# Modifications in existing jarvis/ tree:
jarvis/overlay/                          # NEW subpackage in main jarvis tree
  __init__.py                            # re-exports public API (get_overlay, decorators, ...)
  bridge.py                              # OverlayBridge singleton, WS-server, decorators, context-managers
  supervisor.py                          # subprocess management, Job Object, restart logic
  triggers.py                            # ActionKind enum, decorator/context-manager implementations
  schema.py                              # SAME Pydantic schemas as overlay-side (DRY via shared module preferred)
  
jarvis/control/                          # NEW (or augmented if exists) — single point of instrumentation
  __init__.py
  mouse.py                               # wrapped pyautogui mouse calls + decorators
  keyboard.py                            # wrapped pyautogui keyboard calls + decorators
  browser.py                             # wrapped Browser-Use calls + decorators

# Tests
tests/overlay/
  test_state_machine.py
  test_schema.py
  test_schema_symmetry.py                # Pydantic ↔ Zod CI gate
  test_ipc_ws.py                         # mock ws server, verify reconnect, backpressure
  test_ipc_pipe.py
  test_cursor_shm.py                     # write/read cycle, torn-write detection
  test_transparency.py                   # ctypes calls, hwnd flag verification
  test_monitors.py                       # virtual displays, hotplug
  test_supervisor.py                     # spawn, kill, auto-restart with backoff
  test_triggers.py                       # decorator/context-manager fire correct events
  test_subagent_noop.py                  # JARVIS_DEPTH > 0 → no events
  test_visual_regression.py              # Playwright screenshots of states (skipped on CI without display)
  
# Docs
docs/
  overlay-ipc-protocol.md                # detailed protocol reference (extracted from this plan)
  overlay-state-machine.md               # state diagram + transition tables
  overlay-troubleshooting.md             # known issues, debugging tips
```

**Implementer discretion:**
- Exact module-decomposition within `jarvis/overlay/` is implementer's choice (e.g., one file vs split).
- Whether `jarvis/overlay/schema.py` is a re-export of `OS-Level/src/overlay/schema.py` or a duplicated definition with CI-test for symmetry: implementer's choice. Single-source preferred.
- React vs vanilla TS for `overlay-ui/`: implementer's choice. Vanilla is leaner (no React-runtime cost); React reuses skill from existing pywebview frontend. Implementer documents the choice in the README.

---

## 23. Testing Strategy

### 23.1 Unit Tests (`pytest`)

| Test | Verifies |
|---|---|
| `test_state_machine.py` | All 8 states + all valid transitions + invalid transitions raise; coalescing 16 ms window |
| `test_schema.py` | Pydantic round-trip JSON ↔ model for every message type; rejects invalid values |
| `test_schema_symmetry.py` | Export Pydantic schema → JSON Schema; parse Zod schema → JSON Schema; assert equal |
| `test_cursor_shm.py` | Create SHM, write 1000 frames, read concurrently, no torn reads; cleanup on exit |
| `test_transparency.py` | Mock `ctypes.windll.user32`, verify `WS_EX_TRANSPARENT` is set, `WDA_EXCLUDEFROMCAPTURE` is set |
| `test_monitors.py` | Mock `QGuiApplication.screens()`, verify per-monitor windows are created/destroyed correctly |
| `test_supervisor.py` | Mock subprocess, verify spawn → heartbeat → kill → respawn-with-backoff sequence |
| `test_triggers.py` | Apply decorator to dummy function, verify `action_started`/`action_ended` events fire in correct order |
| `test_subagent_noop.py` | Set `JARVIS_DEPTH=2` env, instantiate OverlayBridge, verify all `emit_*` calls are no-ops |
| `test_config.py` | Pydantic validation rejects out-of-range values; atomic write pipeline rolls back on invalid file |

### 23.2 Integration Tests

| Test | Verifies |
|---|---|
| `test_e2e_state_change.py` | Spawn overlay subprocess, send state via WS, assert WebView reports rendered state via heartbeat |
| `test_e2e_click_ripple.py` | Spawn overlay, send click event, assert ripple-event reaches DOM within 25 ms |
| `test_e2e_kill_parent.py` | Spawn overlay under Job Object, kill parent forcibly, assert overlay process exits within 1 s |
| `test_e2e_overlay_crash.py` | Spawn overlay, send SIGKILL to overlay, assert supervisor respawns within 3 s + 0.5 s backoff |

### 23.3 Visual Regression Tests

`test_visual_regression.py` uses Playwright + headless WebView2 to snapshot each state and compare to baseline images (stored in `tests/__visual__/`). Pixel-diff tolerance 5%. Updates require explicit `--update-baseline` flag.

Skipped on CI runners without display (Linux containers); runs locally and on Windows-equipped CI.

### 23.4 Performance Smoke

`test_perf_60s_idle.py`:
- Spawn overlay in idle state for 60 s.
- Sample CPU/GPU/RSS via `psutil` every 1 s.
- Assert: median CPU < 0.5%, median GPU < 0.5% (via `nvidia-smi` or equivalent), max RSS < 100 MB.
- Skipped on CI without GPU detection.

### 23.5 Multi-Monitor Smoke (manual / Windows-only)

Test plan in `docs/overlay-troubleshooting.md`:
1. Single-monitor: smoke test all states.
2. Two monitors, same DPI: verify overlay on primary only by default; opt-in to `all_monitors = true`, verify glow on both.
3. Two monitors, different DPI (100% + 200%): verify per-monitor DPI-correct rendering.
4. Hotplug: with overlay running, unplug secondary; verify no crash, mascot recovers.
5. Hotplug: with overlay running, plug in secondary; verify if `all_monitors = true`, glow appears on it.

### 23.6 Manual Test Plan (per Release)

A human verifies before each release:
- Wake-word triggers `listening` state visually.
- Click in browser triggers ripple within perceptible window.
- Typing triggers bottom-edge sweep.
- Fullscreen game causes overlay to vanish; exiting brings it back.
- OBS recording omits overlay (privacy default).
- Right-click mascot → "Hide" works; tray-menu re-shows.
- Drag mascot to second monitor; restart Jarvis; mascot returns to second monitor.
- Unplug second monitor with mascot on it; mascot snaps to primary.

---

## 24. Success Criteria (Feature-Level Definition of Done)

The OS-Level Overlay feature ships when **all** of the following are true:

### 24.1 Functional
- ✅ Glow appears within 50 ms of any Hauptjarvis PC-action (typing/clicking/move/scroll/navigate).
- ✅ Glow does NOT appear during listening / thinking / speaking states.
- ✅ Glow does NOT appear when sub-agents perform actions in their subprocesses.
- ✅ Click ripple appears at exactly the click coordinate (within 1 logical pixel of accuracy) within 50 ms of the click.
- ✅ Cursor trail follows Jarvis-driven cursor moves smoothly at 60 Hz.
- ✅ Typing-indicator-sweep appears on each keystroke within 100 ms.
- ✅ Mascot displays correct state-animation for every state.
- ✅ Mascot is draggable; position persists across restarts.
- ✅ Mascot's monitor recovery works on hotplug.

### 24.2 Configuration
- ✅ All `[overlay]` config fields documented in this plan are validated, applied, and survive Atomic-write pipeline.
- ✅ Disabling `mascot_enabled` removes the mascot at runtime without restart.
- ✅ Disabling `edge_glow_enabled` removes the glow at runtime without restart.
- ✅ `enabled = false` cleanly stops the overlay process within 5 s.

### 24.3 Performance
- ✅ Idle CPU < 0.5%, idle GPU < 0.5%, idle RSS < 100 MB measured over a 5-min idle window.
- ✅ Active state (typing+ripple) CPU < 3%, GPU < 2%, RSS < 140 MB measured over a 60-s burst.
- ✅ Battery delta < 1% per hour idle, < 3% per hour active (measured on a battery-powered laptop).

### 24.4 Privacy & Safety
- ✅ Default OBS / Teams / Zoom / Snipping-Tool capture omits overlay (manually verified).
- ✅ User-toggleable to allow capture for legitimate use.
- ✅ No `import jarvis.speech.*` anywhere in `OS-Level/` (CI-grep enforced).
- ✅ No credentials, tokens, prompts, or audio ever transit overlay process (code-audit verified).
- ✅ WS port bound to `127.0.0.1` only (verified via `netstat`).

### 24.5 Process Lifecycle
- ✅ Overlay process exits within 1 s of Hauptjarvis hard-kill (verified via `taskkill /F`).
- ✅ Overlay-only crash triggers automatic restart within 3 s of heartbeat-timeout.
- ✅ Restart-cap (5 in 5 min) fires correctly; surfaces tray-warning.

### 24.6 Multi-Monitor / Edge Cases
- ✅ Per-monitor DPI works correctly (200% + 100% mixed monitors).
- ✅ Monitor hotplug doesn't crash overlay; mascot recovers.
- ✅ Fullscreen-exclusive game causes overlay to hide; exit brings it back within 2 s of `SHQueryUserNotificationState` poll.
- ✅ Vertical monitor renders glow correctly on rotated edge.

### 24.7 Tests
- ✅ All unit tests in `tests/overlay/` green.
- ✅ Integration tests for spawn / kill / respawn / IPC green.
- ✅ Visual regression baseline established and tests pass within 5% pixel-diff tolerance.
- ✅ Performance smoke test green on a reference machine.
- ✅ No regression in existing `pytest tests/` (still 248/250 or better).

### 24.8 Documentation
- ✅ `docs/overlay-ipc-protocol.md` complete.
- ✅ `docs/overlay-state-machine.md` with state diagram.
- ✅ `docs/overlay-troubleshooting.md` with known issues.
- ✅ CLAUDE.md updated to mention the new `jarvis/overlay/` and `OS-Level/` subtrees.

---

## 25. Risks & Mitigations

### 25.1 Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **WS_EX_TRANSPARENT regression in future Win11 update** | Low | High | Spy++ verification in test plan; ctypes-fallback for direct `SetWindowLongPtrW` if Qt's flag stops working |
| **WDA_EXCLUDEFROMCAPTURE doesn't honor on some build** | Medium | Medium | Detect via test, fall back to "warn user that overlay may appear in screen-share" |
| **PySide6 + QtWebEngine memory bloat over time** | Medium | Medium | RSS monitoring via heartbeat; restart at 200 MB ceiling |
| **SHM cursor stream race conditions** | Low | Low | Sequence-counter pattern (write seq last, read seq before+after); 60 Hz × 16-ms-coalesce-window is forgiving |
| **Job Object doesn't kill subprocess on parent crash** | Very Low | High | Documented Win32 pattern; tested in `test_e2e_kill_parent.py` |
| **Browser-Use / Computer-Use plugins don't go through `jarvis/control/`** | Medium | Medium | Implementer must wrap or instrument them; CI-grep-test for direct `pyautogui` imports outside `jarvis/control/` |
| **Rive runtime version mismatch with `.riv` file** | Low | Low | Pin `@rive-app/canvas-lite` version; PNG-fallback path |
| **State change races (two events within 16 ms)** | Medium | Low | Coalescing per AD-17 |

### 25.2 Performance Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Glow tanks battery on integrated GPU** | Medium | Medium | CSS-only renderer (no WebGL); throttle on battery; FPS-cap |
| **60 Hz SHM read in Python competes with Voice-Loop** | Low | High | SHM is zero-copy, no asyncio-wakeup; verified in perf-smoke-test |
| **WebView2 hidden-throttling doesn't kick in** | Low | Low | Fallback: explicitly call `IsVisible = false` at hide-timeout |

### 25.3 UX Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Mascot annoying / distracting** | Medium | Medium | Disable-toggle; user-tested mascot design (implementer discretion + iteration) |
| **Glow visible in wrong contexts (e.g., during sleep)** | Low | Low | Power-state event handling |
| **Glow on Sub-Agent action confuses user (it shouldn't appear)** | Low | Medium | AD-6 explicit; CI-test verifies sub-agents are no-op |
| **Color too saturated, dazzles user** | Low | Low | Default `--intensity` calibrated; user can override yellow-primary in TOML |

### 25.4 Schedule Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Trigger-instrumentation requires touching too many files** | Medium | Medium | Pre-Phase: introduce `jarvis/control/*` choke-point first; future code uses it |
| **Visual design iteration takes longer than build** | Medium | Low | Ship with reasonable default; iterate post-V1 |
| **Multi-monitor edge cases consume disproportionate time** | Medium | Low | Default to primary-only; defer multi-monitor polish if needed |

### 25.5 Maintenance Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Future PySide6 6.x update breaks transparency** | Low | Medium | Pin major version; manual upgrade gating |
| **Schema-drift between Python and TS** | Medium | High | CI gate `test_schema_symmetry.py` |
| **Hauptjarvis schema-version bumps without overlay update** | Medium | Medium | `v` field in envelope; overlay logs warning, continues with V1-compatible behavior |

---

## 26. Implementer Discretion — what OpenClaw may decide itself

This section explicitly lists choices left open. OpenClaw chooses these without re-confirming, documents the choice in commit-messages or inline comments.

### 26.1 Variable / Function / Module Naming

- Internal class names, method names, file names within the constraints of §22 file structure.
- CSS class names within `overlay-ui/`.
- Local variable names everywhere.

### 26.2 Decomposition

- How `jarvis/overlay/bridge.py` is internally decomposed (one file vs split modules) — implementer's choice.
- Helper functions, internal utility modules.
- Test helper fixtures.

### 26.3 Algorithmic Tuning Within Documented Ranges

- Exact tween coefficients within stated easing curves.
- Exact phase-offsets per layer (within "phase-offset" requirement).
- Exact Simplex-noise frequency within 8–16 Hz.
- Exact Hue-drift amplitude within ±15°.
- Pool-size for ripple divs (8 is suggested, 4–16 is acceptable).

### 26.4 Implementation Mechanism Choices

- **Decorator vs Context-Manager preference** in caller code (both must work; implementer can recommend one as primary in docstrings).
- **Pydantic vs dataclass** for non-IPC internal models (Pydantic for IPC is hard; internal stuff can be dataclass).
- **JSON vs MessagePack** for WS payload — JSON is hard (per AD-15 debugability). Internal SHM is binary (per §11). No room for choice here.
- **OffscreenCanvas vs main-thread Canvas** for cursor trail — OffscreenCanvas preferred but main-thread acceptable if Worker setup proves problematic.
- **WebChannel vs custom-postMessage** for Python ↔ JS bridge inside QtWebEngine — implementer's choice. WebChannel is slightly more idiomatic, but custom postMessage is fine if it works.

### 26.5 Fallback Paths

- If Rive integration is problematic: PNG-fallback (covered in AD-16).
- If WebView2 is problematic: pure QtWidgets-painting renderer (would require re-implementing CSS/SVG by hand — only as last resort).
- If `multiprocessing.shared_memory` has Windows-specific issues (Bug #82300 referenced): fall back to memory-mapped file via `mmap`.

### 26.6 Test Coverage Choices

- Exact test count per module (aim for "every public API + every state transition + every error path").
- Property-based tests (hypothesis) vs example-based: implementer's choice.
- E2E tests' setup-teardown style.

### 26.7 Documentation Style

- Format of inline docstrings (Google-style vs NumPy-style vs reST), as long as consistent within the OS-Level subtree.
- Whether to add ADR files (`docs/adr/00XX-*.md`) for sub-decisions or keep them inline in code.

### 26.8 Mascot Design

- Exact illustration of the mascot (per §13.1 — black + yellow + geometric + state-readable + ≤80 KB).
- Exact frame counts per state animation.
- Whether the mascot has eyes, an antenna, a face — implementer's call.

### 26.9 What is NOT Implementer's Discretion (hard requirements)

- The 18 Architecture Decisions in §5 are binding. Deviations require an ADR-update, not just a commit comment.
- The IPC envelope structure in §10.1 is binding for inter-version compatibility.
- The state names in §6 are binding (used in IPC).
- The privacy defaults in §18 are binding.
- The performance ceilings in §17.1 are binding (build to under them).
- The exclusion of voice/audio code from overlay (§18.3) is binding.

---

## 27. Glossary

| Term | Definition |
|---|---|
| **Hauptjarvis** | The main Jarvis process that hosts the voice loop, the RouterBrain, and the Sub-Jarvis manager. Not to be confused with Sub-Jarvis (subprocesses) or sub-agents (delegated harnesses). |
| **Sub-Agent** | A Sub-Jarvis subprocess or a dispatched harness (openclaw, codex, etc.). NEVER triggers the overlay (AD-6). |
| **Edge Glow** | The animated glow border around the screen edge. Appears only during interactive PC actions (typing, clicking). |
| **Mascot** | The small mascot figure in a separate window. Reflects state, is persistent, draggable. |
| **Ripple** | The yellow click effect at the coordinate when Jarvis clicks. Lifetime 600 ms. |
| **Cursor Trail** | The subtle dot trail behind the cursor during Jarvis mouse activity. |
| **Typing Indicator** | The bottom-edge sweep on each keystroke while typing. |
| **State Machine** | The 8-state automaton (idle/listening/thinking/typing/clicking/speaking/error/hidden), hosted in the overlay process. |
| **Conic-Gradient** | CSS primitive (`conic-gradient(...)`) — a color gradient that rotates around a center point. Animatable with `@property --angle` via the compositor thread. |
| **`@property` (CSS)** | CSS Houdini custom-property definition — allows typed CSS variables that can be smoothly interpolated. Here: `--angle` as `<angle>`. |
| **mask-composite** | CSS property that controls the layering of `mask-image`s. We use `exclude` to punch the interior out of the glow. |
| **mix-blend-mode: plus-lighter** | Chrome 105+ blend mode for luminous additive composition without saturation loss. |
| **Click-Through** | Window property: mouse events pass through the window to apps underneath. Achieved via `WS_EX_TRANSPARENT`. |
| **WS_EX_TRANSPARENT** | Win32 extended window style. Makes the window transparent to mouse hit-testing. Combined with `WS_EX_LAYERED`. |
| **WS_EX_LAYERED** | Win32 EWS. Enables per-pixel alpha and makes transparency possible. A prerequisite for `WS_EX_TRANSPARENT`. |
| **WS_EX_TOPMOST** | Win32 EWS. The window is always-on-top. Does not work over exclusive fullscreen / UAC / lock screen. |
| **WS_EX_TOOLWINDOW** | Win32 EWS. The window is not in the taskbar / Alt-Tab. |
| **WS_EX_NOACTIVATE** | Win32 EWS. Clicking the window does not activate it (no focus steal). |
| **WDA_EXCLUDEFROMCAPTURE** | Win10 2004+ flag for `SetWindowDisplayAffinity`. The window is excluded from the DWM bitstream to screen-capture APIs → invisible in OBS, Teams, Zoom. |
| **DWM** | Desktop Window Manager — the compositor service in Windows since Vista. Combines all window outputs. |
| **DComp / DirectComposition** | Lower-level compositor API; enables animation interpolation on the DWM thread (CPU-free). |
| **DXGI** | DirectX Graphics Infrastructure — responsible for swap chains and fullscreen-exclusive. |
| **Layered Window** | Win32 window concept with per-pixel alpha and external render composition. Enabled via `WS_EX_LAYERED`. |
| **Redirection Bitmap** | A per-window off-screen bitmap that the DWM uses to compose layered windows. Memory cost scales with window size. |
| **Exclusive Fullscreen** | A game/app takes direct ownership of the display surface via DXGI; always-on-top is ignored. |
| **PerMonitorV2 DPI Awareness** | Win10 1607+ API; each monitor's DPI is handled separately; a window can "migrate" between monitors with correctly scaled content. |
| **Logical Pixel** | A DPI-scaled pixel (what JS/CSS sees). 1 logical px @ 100% = 1 physical px; @ 200% = 2 physical px. |
| **Physical Pixel** | A hardware pixel on the monitor (what Win32 / pyautogui sees). |
| **Refresh Rate** | The monitor's refresh rate in Hz. 60/120/240/360 typical. Variable Refresh Rate (G-Sync, FreeSync) is variable. |
| **Job Object** | A Win32 mechanism for grouping processes with shared limits. `KILL_ON_JOB_CLOSE` causes automatic killing of job members when the job handle is closed. |
| **SHM (Shared Memory)** | `multiprocessing.shared_memory` — zero-copy IPC between Python processes via a memory-mapped region. |
| **Named Pipe** | A Win32 IPC mechanism over `\\.\pipe\NAME`. Not TCP/IP; escapes firewall blocks. |
| **WebSocket** | A bidirectional TCP/WS protocol. Here: localhost loopback `127.0.0.1:7842`. |
| **Pydantic v2** | A Python data-validation library; v2 is Rust-backed (10× faster than v1). We use models for IPC schemas. |
| **Zod** | A TypeScript data-validation library, equivalent to Pydantic for TS. |
| **Rive** | A realtime animation tool and runtime; .riv files are binary animation assets with state machines. The `canvas-lite` runtime is Canvas2D-only (no WebGL conflict). |
| **Lottie** | An Adobe-After-Effects-based animation format (JSON). Larger and without state machines (for our purposes). |
| **OffscreenCanvas** | A canvas API for web workers; enables GPU-rendered drawing without blocking the main thread. |
| **rAF / requestAnimationFrame** | A browser API for 60-Hz-synchronized render callbacks. |
| **Simplex Noise** | A Perlin-noise variant; slightly faster, fewer artifacts. We use it for organic animation modulation. |
| **Coalescing** | Combining multiple identical events within a short time window into a single one. Here: a 16 ms window. |
| **Heartbeat** | A periodic "I'm alive" message between IPC endpoints. Here: 1 Hz; timeout 3 s. |
| **Backpressure** | A mechanism to throttle the sender when the receiver cannot keep up. Here: a bounded queue with drop-oldest. |
| **Atomic Write Pipeline** | Pre-validate → backup → tempfile-write → reload-test → rollback. Prevents broken configs. |
| **CSS Houdini** | A browser API for low-level CSS extension. `@property` is part of it. |
| **TTFW (Time To First Word)** | A voice-loop latency metric. Jarvis' target: < 1.5 s from the wake word to the first TTS sample. Sacrosanct. |
| **`prefers-reduced-motion`** | An OS accessibility setting; we respect it with reduced animations. |
| **`prefers-contrast: high`** | An OS accessibility setting; we respect it with increased contrasts. |
| **GIL (Global Interpreter Lock)** | Python's lock on bytecode execution. SHM bypasses the GIL for reads/writes. WS IPC uses asyncio tasks (cooperative, no GIL issue). |
| **JARVIS_DEPTH** | An env variable that marks the Sub-Jarvis recursion depth. `> 0` → sub-agent. Used to detect the sub-agent OverlayBridge no-op. |

---

## Document Status

- **Version:** Draft 1.0
- **Author:** Joint output of Claude Opus 4.7 + Ruben (Personal Jarvis Project Owner)
- **Date:** 2026-05-01
- **Next step:** Prompt-chain derivation in a separate session. This plan file is the single source of truth against which the prompt chain is verified.
- **Review cycle:** Before the implementation starts, Ruben should go through the plan completely once and respond either with a ✅ approve or with an issue list.

---
