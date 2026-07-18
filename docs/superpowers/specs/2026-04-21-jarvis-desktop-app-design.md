# Jarvis Desktop App — Interface Design

**Status:** Draft
**Date:** 2026-04-21
**Scope:** L7 UI/UX layer for Personal Jarvis (desktop app)
**Related:** Master plan `<USER_HOME>\.claude\plans\also-er-muss-auch-lexical-pond.md` <!-- i18n-allow: literal filename identifier, must match the real file -->

---

## 1. Overview & Philosophy

Jarvis becomes a Windows desktop app — **voice-first, but fully observable and operable visually**. It is not a classic chat client with a microphone button, but a *conductor interface*: the user talks to a single persona that orchestrates multiple Jarvis-Agents and CLI tools (Jarvis-Agent worker, Codex, Open Interpreter, MCP servers) in the background. The UI makes the invisible visible — without overwhelming.

**Core principles:**

1. **Voice-first, visual-always-available** — every voice action has a visual counterpart (transcription, agent status, decisions visible live). The user *does not have to* look, but *can* at any time.
2. **One-mouth-many-hands** — only Jarvis speaks (one voice, one persona). Jarvis-Agents communicate exclusively visually.
3. **Autopilot with override** — Jarvis can open/edit any UI section by voice (*"Wechsel Brain auf Gemini"* (switch Brain to Gemini) → sidebar navigation + quick-swap run automatically). Manual operation remains possible at any time.
4. **Global default, project-sticky on demand** — a tray service always runs. *"Jarvis, arbeite in Ordner X"* (Jarvis, work in folder X) pins the context until the user releases it.
5. **The frontend is a privileged client, not a monolith** — the core engine (Python + FastAPI) and the desktop app (pywebview + React) communicate via WebSocket. The same protocol can later serve a TUI, a mobile app, or other clients; the desktop app is the primary, but not the only possible, client.

**Why this philosophy:** the principle *"every action passes through the event bus"* makes voice and click symmetric — there are no two code paths that can drift apart. Shared event types in `jarvis.core.events` enforce symmetry between the Python protocol and the TypeScript interface. Adding a feature = adding an event.

**Accepted trade-off:** autopilot can feel "magical" and break expectations when the UI moves without a visible click. Mitigation: gentle animations + a small toast banner *"Jarvis hat Skills geöffnet"* (Jarvis opened Skills) (can be turned off once the user is used to it). <!-- i18n-allow -->

---

## 2. Current state (what already exists)

Before implementation begins, it is important to understand what does not need to be built anew:

### Fully present (stays 1:1)

- **Black-and-yellow theme** — `jarvis/ui/web/frontend/src/index.css`
  - Matte black `#0A0A0A` (background), `#0F0F0F` (card), `#1A1A1A` (secondary)
  - Signal yellow `#FFD60A` (primary/accent/ring)
  - Text `#F4F4F5` (foreground), muted `#8F8F8F`
  - Border `#242424`
  - Radius 0.75rem
- **Typography** — Space Grotesk (display), Inter (body), JetBrains Mono (code). Loaded via Google Fonts.
- **Design utilities** — `jarvis-grid` (subtle grid pattern), `jarvis-glow` (radial yellow spot with blur), `btn-primary`, `btn-ghost`, `chip`, `chip-yellow`, `card-outline`
- **Animations** — `jarvis-pulse`, `jarvis-float`, `jarvis-spin-slow`, `text-gradient-yellow`, `scrollbar-jarvis`
- **Orb overlay** — `ui/orb/overlay.py` (Tkinter + numpy + PIL, 60 FPS, magenta color-key for transparency, 4 modes: idle/listen/speak/think, mic-reactive, shockwave rings, plasma swirls, rim light). Works standalone.

### Partially present (will be rebuilt/extended)

- **Shadcn primitives** — `button`, `card`, `scroll-area`, `switch`, `tabs`, `badge` (all present, match the theme)
- **Frontend building blocks** — `VoiceIndicator`, `EventTimeline`, `ChatInput`, `ProviderSwitcher`, `ThemeToggle` (exist, will be integrated into the new layout)
- **AdminPanel** — tabs for Providers/Plugins/Theme/Debug (will be dissolved into sidebar sections)
- **Sidebar + MainView** (rudimentary) — base structure present, but only a Conversations placeholder

### Existing state that will be replaced

- **`App.tsx` as a scroll landing page** (Hero → LiveStatus → FeatureGrid → ChatDemo → Footer → Nav) — will be replaced by a dashboard shell. The landing components are either discarded or recycled as a *Welcome* view in the Chats section.

### Memory correction (documented in this design)

`feedback_orb_popup_behavior.md` describes the Orb as a "frameless pywebview window". That is outdated — the actual implementation is **Tkinter-based**, because pywebview/WebView2 on Windows 11 does not support real transparency (DirectComposition ignores `SetWindowRgn`/`LWA_COLORKEY`). Memory will be updated post-spec.

---

## 3. Window structure

**Three mutually independent UI units:**

### 3.1 Main window (dashboard)

pywebview window, non-frameless, ~1280×800 px default, resizable.

- **Sidebar** (280 px wide, flex column)
  - Top: Jarvis status header (voice status + last transcription as a single line)
  - Middle: navigation (8 sections, icons + labels + badges)
  - Bottom: Brain provider indicator + quick-swap button
- **Main area** (flex-1, scrollable)
  - Renders context-dependently based on the sidebar selection
  - Toast layer top right for autopilot notifications/errors

### 3.2 Orb overlay (separate window)

Already implemented in `ui/orb/overlay.py`. Tkinter + numpy/PIL.

- Frameless, always-on-top, magenta color-key transparency
- 108×108 px, top right (margin 24 right, 28 top)
- Appears on wake-word / hotkey, disappears after TTS + a short timeout
- 4 modes with distinct animation: idle (hidden), listen (breathing pulse + shockwave rings on mic level), speak (simulated wave from TTS energy), think (leisurely pulsing)
- Independent of the main window — appears even without an open dashboard

### 3.3 Tray icon

pystray, persistent. Double-click opens the main window. Right-click menu:

- Mute / unmute microphone
- Open dashboard
- Open settings
- Restart
- Quit

---

## 4. Sidebar sections (8 of them)

All sections can be invoked by voice via the `NavigateSidebar(section=...)` event.

| # | Icon | Section | Content |
|---|------|---------|--------|
| 1 | 💬 | **Chats** | Conversation list (like Claude Desktop), searchable. Voice and text turns mixed. Each chat has a title (auto-generated), date, pin option. Main view: message list + ChatInput at the bottom. First view on app start. |
| 2 | 👥 | **Agents** | Live tiles of the currently active Jarvis-Agents. Per agent: role (Planner/Coder/Researcher), status (running/waiting/done/error), streaming output (last lines), kill/pause button. Count of active agents as a badge on the sidebar entry. |
| 3 | 🧩 | **Skills** | Plugin registry from `importlib.metadata` entry-points. Grouped by `jarvis.wakeword` / `.stt` / `.tts` / `.brain` / `.harness` / `.tool`. Per plugin: name, version, active toggle, config button. Default tier (`safe`/`monitor`/`ask`/`block`) visible. |
| 4 | 🔌 | **MCPs** | MCP server list (configured via `jarvis.toml`). Per server: name, status (connected/disconnected/error), start/stop/restart, discovered tools list, health-check button. |
| 5 | 🌍 | **Languages** | Bilingual config. DE/EN auto-detect is the default (per memory). Per-session override (*"Jarvis, answer in English"*). Configured default language visible. Voice language separate from UI language. |
| 6 | 🔑 | **API Keys** | Credential Manager UI. Lists all keys from `jarvis.setup.wizard.SECRETS`. Per key: provider name, status (set/unset/invalid), "Test" button. Reading via `get_secret()`, writing to Windows Credential Manager via `keyring`. ENV/`.env` shown read-only only. |
| 7 | ⚙️ | **Settings** | Wake-word selection (custom-training link), hotkeys (default `ctrl+right_alt+j`), safety whitelist/blacklist (edit patterns), privacy mode (Local/Cloud/Hybrid), autostart, project scope (active folder, pin/unpin). |
| 8 | 📊 | **Debug** | Flight-recorder replay (JSONL events as a scrubber/player), metrics charts (latency/tokens/cost per provider), event log (live stream of all bus events), full STT transcription with wake-word/VAD events + timestamps. |

---

## 5. Voice autopilot integration

### Mechanics

Each intent becomes a typed event that travels over the event bus. The frontend is a pure WebSocket subscriber — it reacts reactively to events.  **The same event type** arrives for voice and for a mouse click — no two code paths.

```
User-Voice → STT → Intent-Classifier → Event → Bus → {WebSocket → Frontend, Router → Harness}
User-Klick → Frontend → WebSocket → Event → Bus → (wie oben)
```

### Event catalog (excerpt)

All events in `jarvis.core.events`, mirrored as TypeScript interfaces in the frontend:

- `NavigateSidebar(section: str)` — switch the sidebar selection
- `SwapBrainProvider(name: str)` — switch the active Brain provider
- `AgentCommand(agent_id: str, cmd: "kill" | "pause" | "resume")` — control a Jarvis-Agent
- `PinProjectScope(path: str | None)` — pin/release the project context (None = global)
- `TranscriptionUpdate(text: str, is_final: bool)` — live STT results
- `AgentStateChange(agent_id: str, state: AgentState)` — status updates
- `ToastNotification(kind: str, message: str, dismissable: bool)` — UI notification

### Visibility

Every autopilot action shows a subtle toast notification (*"Jarvis: Skills geöffnet"* (Jarvis: Skills opened)). Can be turned off in Settings once the user is used to it. <!-- i18n-allow -->

---

## 6. Observability

### Always visible (without a click, regardless of the active section)

- **Sidebar top** — voice-status badge (Idle / Listening / Thinking / Speaking), color-animated
- **Sidebar top** — last transcription as a single line (live, 2s auto-fade after the end)
- **Sidebar agents** — badge with the count of active Jarvis-Agents
- **Sidebar bottom** — current Brain provider (name + colored dot), click → quick-swap
- **Toast layer** (top right) — autopilot notifications, errors, approval requests

### On demand (Debug tab)

- Full STT stream with timestamps
- Flight recorder (JSONL from `~/AppData/Roaming/Jarvis/traces/`) playable like a video scrubber
- Live charts for latency/tokens/cost per provider
- Event log of all bus events (filterable by type)

---

## 7. Architecture (brief)

### Processes and communication

```
┌─────────────────────────────────────────────────────────────┐
│ Jarvis-Tray (Python, persistent, User-Kontext)              │
│                                                             │
│  ┌─────────────────┐  ┌─────────────────────────────┐      │
│  │ Core-Engine     │◄─┤ FastAPI + WebSocket-Server  │      │
│  │ (Event-Bus,     │  │ (127.0.0.1:<dynport>)       │      │
│  │  Orchestrator,  │  └──────────────┬──────────────┘      │
│  │  Harnesses,     │                 │                     │
│  │  STT/TTS/Brain) │                 ▼                     │
│  └────────┬────────┘   ┌─────────────────────────────┐     │
│           │            │ pywebview-Fenster           │     │
│           │            │ (lädt React-Dist aus        │     │  <!-- i18n-allow -->
│           │            │  jarvis/ui/web/dist/)       │     │
│           │            └─────────────────────────────┘     │
│           │                                                 │
│           ▼                                                 │
│  ┌─────────────────┐                                       │
│  │ Orb-Overlay     │ (Tkinter, Magenta-Color-Key)          │  <!-- i18n-allow -->
│  │ (separater      │                                       │
│  │  Thread)        │                                       │
│  └─────────────────┘                                       │
└─────────────────────────────────────────────────────────────┘
```

### Key decisions

- **One process, three UI threads**: Main (FastAPI + async event loop), Tk thread (Orb), pywebview thread (dashboard). Communication is in-process Python with the core engine.
- **Single-instance lock** via `filelock` or a Windows named mutex — a double start brings the existing window to the foreground and does not start a second process.
- **Frontend build** as React/Vite, statically to `jarvis/ui/web/dist/`; pywebview loads the `index.html` from `file://` — no external web server is needed for the frontend files, but FastAPI provides the API + WebSocket.
- **Dev mode**: `vite dev` on `localhost:5173` with HMR; pywebview loads from there instead.

### Portability to Tauri (v2 path)

The frontend code stays portable: pure React + shadcn + TanStack Query, backend calls exclusively via FastAPI + WebSocket (no `window.pywebview.api.*` calls). A Tauri migration swaps only the wrapper layer — frontend and core stay identical.

---

## 8. Migration path (what concretely happens)

### Keep 1:1

- `index.css` in full
- All `components/ui/*` shadcn primitives
- `VoiceIndicator`, `EventTimeline`, `ChatInput`, `ProviderSwitcher`
- `ui/orb/overlay.py` in full
- `tray.py`
- The FastAPI backend structure

### Rebuild

- `App.tsx` — from a scroll landing page to a dashboard shell (sidebar + main with a router)
- `Sidebar.tsx` — from "Conversations + disabled button" to full 8-section navigation with a voice-status header + Brain footer
- `MainView.tsx` — becomes the router host for the 8 sections, no longer a single view
- `AdminPanel.tsx` — tabs become standalone sidebar sections (Skills, Debug, Settings)

### Discard (or Welcome view)

- `Hero`, `LiveStatus`, `FeatureGrid`, `ChatDemo`, `Footer`, `Nav` (landing components) — either delete them or repurpose them into a *Welcome* screen for the Chats tab in the empty state

### New

- Router (simple state-based switch, no react-router needed — only 8 sections)
- Views for each section (placeholder at first, content incrementally)
- FastAPI endpoints for sections that don't have any yet (MCPs, Languages, API Keys, project scope)
- Event types + schema generator (Python → TypeScript)

---

## 9. Design tokens (single source of truth)

| Token | Value | Usage |
|---|---|---|
| `--background` | `#0A0A0A` | Page BG |
| `--card` | `#0F0F0F` | Cards, sidebar BG |
| `--secondary` | `#1A1A1A` | Hover/input BG |
| `--border` | `#242424` | All borders |
| `--foreground` | `#F4F4F5` | Text |
| `--muted-foreground` | `#8F8F8F` | Secondary text |
| `--primary` | `#FFD60A` | Buttons, accents, ring |
| `--primary-foreground` | `#0A0A0A` | Text on yellow |
| `--jarvis-yellow` | `rgb(255 214 10)` | Glow, shadows, custom |

**Glow formula:** `0 0 32px rgba(255,214,10,0.35)` as a hover shadow on primary buttons, `rgba(255,214,10,0.22 → 0.08 → 0)` as a radial spot glow.

**Grid pattern:** `rgba(255,214,10,0.04)` 1px lines on 64×64 px, masked to a radial ellipse for subtle visibility in the center.

---

## 10. Out of scope (deliberately not in this iteration)

- ❌ Phone app / remote access — a separate project, later
- ❌ Multi-user / auth — single-user desktop
- ❌ Cloud sync of chats or configs — everything local (Windows Credential Manager)
- ❌ Tauri migration — stays a v2 upgrade path, pywebview is the MVP
- ❌ Plugin marketplace UI — skills are installed via `pip`, no store
- ❌ Live screen capture/video in the UI — observability is text + state
- ❌ Theme customization for users — black-and-yellow is fixed branding. The existing `ThemeToggle.tsx` is removed from the dashboard (or moved into the Debug tab as a dev-only preview).

---

## 11. Risks & open questions

1. **Router choice for 8 sections** — `react-router` is overkill, but a simple `useState` switch requires discipline for deep links. Recommendation: a zustand store + URL hash for back/forward support without a router.
2. **Toast layer lib** — `sonner` vs. `radix-ui/toast` vs. a custom build. shadcn has `sonner` integration; probably the simplest choice.
3. **Language section content** — bilingual auto-detect is not implemented yet (the phase spec on language is missing). For now the section shows only status info; full integration from Phase 3+.
4. **API Keys section security** — writing to Windows Credential Manager via the REST API requires an approval step (otherwise any web page in pywebview could manipulate the manager if the app ever loads external content). Mitigation: pywebview only loads `file://` + whitelisted `localhost`.

---

## 12. Success criteria (Definition of Done for this design)

- [ ] Dashboard shell with sidebar + main renders
- [ ] All 8 sections are navigable (sidebar click + voice intent)
- [ ] Voice status and last transcription visible in the sidebar top
- [ ] Brain provider indicator in the sidebar bottom is functional
- [ ] Orb overlay appears on a wake-word/hotkey trigger and disappears after TTS
- [ ] Tray icon opens the main window
- [ ] At least one section (Skills) is fully functional end-to-end (lists plugins, enable/disable acts on the event bus)
- [ ] Toast notifications for autopilot actions
- [ ] Theme visually consistent with the design references (Dribbble/Pinterest)

For detailed implementation steps, see the implementation plan (next step).
