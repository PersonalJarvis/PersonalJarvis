# Personal Jarvis

> **Context-drop README.** This file is intentionally dense and self-contained: paste it into a fresh chat (Gemini Web, Claude Web, ChatGPT, Cursor) and the model gets enough context to reason about the project without paging through 30+ files. For the binding source-of-truth, see [`CLAUDE.md`](CLAUDE.md) + the docs linked at the bottom.

**Personal Jarvis** is a voice-driven **Supervisor-Agent meta-orchestrator** — not a classical voice assistant. The core pattern is a fast **Router-Brain** that dispatches work to interchangeable harnesses (OpenClaw subprocess, Codex CLI, Open Interpreter, MCP servers, raw computer-use loops). The voice layer is just the I/O surface; the soul is the dispatcher discipline that keeps the Router-Brain lean and delegates heavy reasoning to specialized subagents under critic-loop + worktree-isolation guard rails. The project is **provider-agnostic by mandate** — the user has no Anthropic API account (Claude Max OAuth is used only inside workers) — and **self-modifying** via an atomic config-writer with a 10-step validate-backup-tempfile-replace-rollback-audit pipeline.

> ### The product — Read This Before The Install Section
> **Personal Jarvis is a full, downloadable desktop app — one product, three native faces (Linux, macOS, Windows).** You install it like any normal app, the installer pulls *every* feature in one step (`pip install -e .[full]` or the native per-OS installer), you enter your own API keys on first run, and you talk to it. The standard install includes the desktop GUI, local voice models (downloaded on first use), telephony, and chat channels. A machine with no GPU still runs — every provider class (Brain / STT / TTS / Vision / Wake) keeps a cloud-API fallback, so local models are an installed-by-default *upgrade*, not a hard requirement. A **headless / server** deployment (browser UI over WebSocket, or a Telegram/Discord/SMS/webhook channel) is fully supported as the secondary mode. **Binding doctrine: [`CLOUD.md`](CLOUD.md) + [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md)**. On conflict between this README and the doctrine, the doctrine wins.

- **Runtime:** Linux / macOS / Windows with Python 3.11+. The full desktop app is the default; a headless browser/channel deployment is the supported alternative.
- **Bring your own keys:** the first-run wizard collects your own cloud API keys and stores them in the OS credential manager (Windows Credential Manager / macOS Keychain / Secret Service). Keys are never committed or bundled.
- **Cross-platform parity (Rule #1):** a feature that works on only one OS is *incomplete*. OS-specific packages are selected by platform markers so the same install command resolves the correct native wheels per OS.
- **Maintainer's reference machine** (developed on, but **not required**): RTX 5070 Ti, 32 GB RAM, CUDA 12.8, Windows 11 Pro — the full app must run natively on a plain Linux/macOS/Windows machine too.
- **Binding plan:** `~/.claude/plans/also-er-muss-auch-lexical-pond.md` (plan wins on plan-vs-code conflict).
- **Binding doctrine:** [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) (wins over the binding plan when the two disagree on hardware/OS assumptions).

---

## Hardware — what you actually need

Personal Jarvis ships in two very different shapes with **very different hardware needs**. The cloud-first base install is tiny; only the full desktop power-user profile with local voice models needs real hardware. Find your row below.

**Measured footprint** — maintainer's Windows 11 workstation, full voice + UI mode, cloud Brain/STT/TTS, local openWakeWord, measured 2026-05-29:

| Component | Resident (Working Set) | Committed |
|---|---|---|
| Backend (`pythonw -m jarvis.ui.web.launcher` — FastAPI + voice pipeline) | 0.95 GB | 2.2 GB |
| Desktop UI (pywebview / WebView2 Chromium child processes) | 0.41 GB | — |
| **Full desktop process tree** | **≈ 1.4 GB** | **≈ 2.6 GB** |

> Budget **~2–2.5 GB of free RAM for the app** in desktop voice mode, not the 1.4 GB resident figure. Python *commits* the ML libraries at boot (chromadb / sentence-transformers embeddings, Silero-VAD, openWakeWord ONNX) even when they aren't all paged in — and under memory pressure, voice latency is the first thing to suffer.

### RAM / CPU by deployment profile

| Profile | What it does | Min system RAM | Min CPU | GPU | Disk |
|---|---|---|---|---|---|
| **A · Headless VPS / cloud** | base install, browser UI, all-cloud providers, no local audio | **2 GB** (1 GB boots with swap) | 1 vCPU | none | ~3 GB |
| **B · Desktop, cloud voice** | pywebview window + mic/speaker + cloud STT/TTS + local wake word | **8 GB** | 2-core | none | ~4 GB |
| **C · Desktop, full local** | local faster-whisper STT + local screen-vision (`--with-voice-local`) | **16 GB** | 4-core | NVIDIA ≥ 6 GB VRAM *or* Apple Silicon | ~8 GB + models |

Profiles **A and B need no GPU at all** — that is the entire point of the cloud-first doctrine. A GPU only matters for Profile C, where Whisper / Vision run locally instead of calling Groq / Deepgram / Google.

### Recommendation — Apple / macOS

Apple Silicon (arm64) recommended. Intel Macs run, but get no Neural-Engine benefit — budget 16 GB there.

| Use case | Minimum that runs well | Comfortable / recommended |
|---|---|---|
| **Cloud voice on a laptop** (Profile B) | MacBook Air **M1 / M2, 8 GB** | MacBook Air **M3 / M4, 16 GB** |
| **Always-on Jarvis host, 24/7** (Profile B/C) | Mac mini **M4, 16 GB** (≈ $599–799) | Mac mini **M4, 24–32 GB** |
| **Local Whisper + Vision** (Profile C) | M-series, **16 GB** unified (covers `whisper-medium`) | M-series, **32 GB** (covers `large-v3` + Vision) |

The **Mac mini M4 (16 GB unified, ≈ $799)** is the sweet spot for a dedicated household Jarvis: silent, ~7 W idle, and unified memory means CPU-side Whisper runs fine without a discrete GPU. On macOS, pywebview uses the native WKWebView, so the UI is *lighter* than the Chromium/WebView2 figures measured above.

### Recommendation — self-built (Linux / Windows)

| Use case | Minimum that runs well | Comfortable / recommended |
|---|---|---|
| **VPS / headless** (Profile A) | any **2 vCPU / 2 GB** Linux box | Hetzner **CX22 — 2 vCPU / 4 GB / 40 GB NVMe, ≈ €3.79–4.15 / mo** |
| **Desktop, cloud voice** (Profile B) | any x86-64, **4-core / 8 GB**, SSD, no GPU | **16 GB**, modern 6-core |
| **Desktop, full local** (Profile C) | **16 GB** + NVIDIA **RTX 3060 12 GB / 4060** (CUDA 12.x) | **32 GB** + RTX 4070+ / 5070 |

A **Hetzner CX22 at ≈ €4 / month** is the canonical €5-VPS this whole project is designed around — 2 vCPU, 4 GB, 40 GB NVMe, 20 TB traffic — and runs the headless base install + browser UI comfortably. The maintainer's reference desktop (RTX 5070 Ti / 32 GB / CUDA 12.8) is Profile C maxed out, and is explicitly **not** a requirement.

### What runs where (platform capability matrix)

The base + cloud-voice experience is fully cross-platform. The six desktop power-user features that were historically Windows-only are now **cross-platform behind a `jarvis/platform/` seam** (`detect_platform()` + `Capabilities` factories) — each carries an honest per-feature verification badge (see [Verification status](#verification-status) below).

**Badge legend:** ✅ CI-configured = the `ci.yml` matrix is configured to prove this on the ubuntu/macos runners (first green run pending the initial push). 🟡 unverified-on-real-desktop = a live GUI/permission behavior that needs a real macOS/Linux device — not yet signed off (this repo's maintainer has Windows only). ⚙ degrade-by-design = the documented graceful fallback on that OS.

| Capability | Linux | macOS | Windows |
|---|---|---|---|
| Headless base + browser UI | ✅ | ✅ | ✅ |
| Voice (wake → VAD → STT → TTS) | ✅ | ✅ | ✅ |
| Desktop window (pywebview) + tray | ✅ GTK/Qt | ✅ WKWebView | ✅ WebView2 |
| Local Whisper STT + screen vision | ✅ | ✅ | ✅ |
| Computer-Use — screenshot → click → type (vision, pixel coordinates) | ✅ X11 | ✅ | ✅ Win32 |
| Built-in terminal view (PTY) | ✅ CI-configured (`ptyprocess`) | ✅ CI-configured (`ptyprocess`) | ✅ ConPTY |
| Launch apps by name | ✅ CI-configured (`xdg-open`/exec) | ✅ CI-configured (`open -a`) | ✅ App Paths |
| Computer-Use — click by UI-element name | 🟡 unverified-on-real-desktop (AT-SPI; ⚙ pixel fallback) | 🟡 unverified-on-real-desktop (AX; ⚙ pixel fallback) | ✅ UIA |
| Global-hotkey wake | 🟡 unverified-on-real-desktop (X11 `pynput`; ⚙ Wayland no-op + wake-word) | 🟡 unverified-on-real-desktop (`pynput`) | ✅ |
| Orb overlay | 🟡 unverified-on-real-desktop (best-effort; ⚙ tray fallback) | 🟡 unverified-on-real-desktop (Tk `-transparentcolor`) | ✅ |
| Admin-helper / elevation | 🟡 unverified-on-real-desktop (pkexec/sudo; ⚙ NullElevator) | 🟡 unverified-on-real-desktop (Authorization Services) | ✅ UAC |

> macOS / Linux get the full Router-Brain → Worker-Critic → Mission-Manager experience — including voice, local Whisper, and the **screenshot → click → type** Computer-Use loop: the vision model picks pixel targets, and `mss` (screen capture) + `pyautogui` (click / type / scroll) are cross-platform **base** dependencies, not Windows-only. The six former Windows-only desktop features — the built-in terminal, launch-app-by-name, click-by-UI-element-name (UIA / AX / AT-SPI accessibility trees), global-hotkey wake, the Orb overlay, and the admin-helper / elevation — are now **cross-platform behind the `jarvis/platform/` seam** (one per-OS implementation per feature, selected by a `detect_platform()` factory, each degrading to a logged English no-op when its capability is absent). They remain **opt-in `[desktop]` / `[desktop-macos]` extras** (the headless €5-VPS base install ships none of them). Terminal and launch-by-name are fully CI-provable on the ubuntu/macos runners; UI-element-click, global-hotkey capture, the Orb transparency, and the elevation prompt are live GUI/permission behaviors that need a one-time sign-off on a real device (AD-3) — see the per-feature verdicts in [Verification status](#verification-status). See the `[desktop]` extras in [`pyproject.toml`](pyproject.toml), the cloud-first doctrine in [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md), and ADR-0020 (cross-platform elevation, supersedes ADR-0001).

#### Verification status

These cross-platform claims are labelled honestly per feature. The full, dated,
device-attributed verdict log lives in
[`docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md`](docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md),
with the operator checklist in
[`LIVE-SIGNOFF-CHECKLIST.md`](docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md)
and the 20-scenario benchmark results in
[`JARVIS-20-RESULTS.md`](docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md).
As of this writing the maintainer has only a Windows machine and nothing has been
pushed, so every macOS/Linux **live** GUI/permission row is honestly labelled
`unverified-on-real-desktop` and the CI matrix is `CI-configured` (its first green
run is pending the initial push) — *not* "CI-verified" and *not* "live-verified".
Run [`scripts/crossplatform/signoff_probe.py`](scripts/crossplatform/signoff_probe.py)
on a real macOS box and a real Linux desktop to convert each row to a dated
`live-verified` verdict.

**One-line buying advice.** *Just want to try it?* Any laptop you already own (8 GB+) or a €4 Hetzner CX22. *Want a dedicated always-on Jarvis?* Mac mini M4 (16 GB). *Want everything local, no cloud STT?* 16–32 GB plus an NVIDIA RTX 3060 / 4060 (or better), or an Apple Silicon Mac with 16–32 GB.

---

## Table of contents

> ★ **[Hardware — what you actually need](#hardware--what-you-actually-need)** — measured RAM footprint + concrete Mac / Linux / Windows specs. Read this first if you're picking a machine.

1. [Quick install & first-run](#1-quick-install--first-run)
2. [Runtime modes](#2-runtime-modes)
3. [8-Layer architecture](#3-8-layer-architecture)
4. [Plugin system](#4-plugin-system)
5. [EventBus + streaming contract](#5-eventbus--streaming-contract)
6. [Brain providers + Ack-Brain](#6-brain-providers--ack-brain)
7. [Risk-Tier + Router discipline](#7-risk-tier--router-discipline)
8. [Output filter (voice path)](#8-output-filter-voice-path)
9. [Configuration & secrets](#9-configuration--secrets)
10. [Atomic config writes & Self-Mod pipeline](#10-atomic-config-writes--self-mod-pipeline)
11. [Phase-6 isolation invariants](#11-phase-6-isolation-invariants)
12. [Phase status table](#12-phase-status-table)
13. [5-layer anti-drift enum pattern](#13-5-layer-anti-drift-enum-pattern)
14. [Anti-patterns (AP-1..AP-18)](#14-anti-patterns-ap-1ap-18)
15. [Recurring bug classes](#15-recurring-bug-classes)
16. [Desktop App (UI)](#16-desktop-app-ui)
17. [Testing — buckets, markers, conventions](#17-testing--buckets-markers-conventions)
18. [Linting & type checking](#18-linting--type-checking)
19. [Scripts inventory](#19-scripts-inventory)
20. [External integrations & accounts](#20-external-integrations--accounts)
21. [ADR + Phase doc index](#21-adr--phase-doc-index)
22. [Operational rhythms](#22-operational-rhythms)
23. [Repo hygiene & contribution](#23-repo-hygiene--contribution)
24. [Key module index](#24-key-module-index)
25. [Doc-vs-code drift (verified)](#25-doc-vs-code-drift-verified)
26. [Pointers](#26-pointers)
27. [Legal](#27-legal)

---

## 1. Quick install & first-run

**One-liner (live since 2026-05-26):**

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/personal-jarvis/PersonalJarvis/main/install/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/personal-jarvis/PersonalJarvis/main/install/install.sh | bash
```

The installer clones into `~/.personal-jarvis`, builds a Python venv, installs deps (incl. the companion packages), **builds the React desktop UI** (`npm`), runs the 7-step wizard (API keys → mic → hotkey → mascot → profile), and launches the Desktop App. Re-running the same one-liner detects the existing checkout and updates instead of re-cloning. Optional flags: `--no-launch`, `--no-wizard`, `--headless` (VPS mode, no GUI deps), `--with-voice-local` (pulls faster-whisper + Silero + openWakeWord, ~1.5 GB). See [`install/README.md`](install/README.md) for the full surface.

> **Prerequisites:** Python 3.11+, git, and — for the desktop GUI — **Node.js + npm** (the installer builds the React bundle from source; without Node the backend still runs but the window shows a loading placeholder).
>
> **Status:** the anonymous one-liner works only while the GitHub repo is **public** — it fetches `install.ps1` / `install.sh` from `raw.githubusercontent.com`, which returns `404` for a private repo. Spec: [`docs/superpowers/specs/2026-05-26-quick-install-one-liner-design.md`](docs/superpowers/specs/2026-05-26-quick-install-one-liner-design.md).

**Manual install (advanced):**

```bash
git clone https://github.com/personal-jarvis/PersonalJarvis ~/.personal-jarvis
cd ~/.personal-jarvis
python -m venv .venv
source .venv/bin/activate                    # Windows: .\.venv\Scripts\Activate.ps1
pip install -e . --no-deps                   # activate entry_points (BUG-006/014 recovery)
pip install -e ".[full]"                      # full app deps (cross-platform; picks per-OS wheels)
pip install -e ./board-backend ./OS-Level ./skillbook   # companion packages (imported at boot)
( cd jarvis/ui/web/frontend && npm ci && npm run build ) # build the desktop UI (needs Node.js)
pip install -e ".[dev]"                       # optional: pytest, ruff, mypy, pyinstaller

python -m jarvis --wizard                     # 7-step Setup-Wizard
```

> The companion packages (`board-backend`, `OS-Level` → `overlay`, `skillbook`) are **not** pulled by `pip install -e .[full]` — they each carry their own `pyproject.toml` and must be installed separately (the one-liner installer does this for you). `requirements.txt` is a Linux-only hash-pinned lockfile for the supply-chain-hardened install, **not** the cross-platform dependency source — use `.[full]` above on Windows/macOS.

Wizard (`jarvis/setup/wizard.py`) is idempotent: hardware analysis → API keys (written to Windows Credential Manager) → mic check → hotkey → external CLI deps (auto-installs `claude` CLI via npm; `node`/`npm` manual) → mascot install → profile.

**Console scripts** (from `[project.scripts]`):

| Script | Purpose |
|---|---|
| `jarvis` | Tray app or first-run wizard |
| `jarvis-review-gc` | Phase 8.5 review-pipeline GC |
| `jarvis-review-eval` | Phase 8.6 eval harness (`--quick`/`--real`/`--bucket`) |

**Top-level `python -m jarvis` flags** (argparse in `jarvis/__main__.py`): `--version`, `--wizard`, `--check`, `--plugins`, `--debug`, `--phase5-doctor`, `--install-admin-helper`.

### Headless / VPS first-run (no interactive TTY)

This is the **primary path** for €5 VPS users, Docker containers, and CI environments. The interactive wizard (`setup/wizard.py`) detects when no TTY is attached and skips all prompts automatically. You only need to supply your API keys before launching.

**Step 1 — Provide your API key(s).** Choose one:

```bash
# Option A: environment variables (recommended for containers and systemd units)
export GEMINI_API_KEY=your-key-here      # or whichever brain provider you use
export GROQ_API_KEY=your-groq-key        # for fast STT (optional)

# Option B: .env file at the repo root (dev / docker-compose convenience)
cp .env.example .env
# edit .env and fill in your keys — full list of names in .env.example
```

The full set of recognised ENV variable names is in `.env.example`.  Each name
mirrors the `env_fallback` field in the `SECRETS` list in `jarvis/setup/wizard.py`
(e.g. `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`,
`GROK_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `ELEVENLABS_API_KEY`, …).

**Step 2 — Launch in headless mode:**

```bash
# Preferred: API + WebSocket only, no GUI deps, no audio required.
python -m jarvis.ui.web.launcher --headless
```

On first run the wizard fires, detects the missing TTY, skips prompts, writes the
`data/.setup-complete` marker, and hands off to the app — all automatically.
You can also force non-interactive mode explicitly:

```bash
JARVIS_NONINTERACTIVE=1 python -m jarvis.ui.web.launcher --headless
```

**Step 3 — Open the browser UI.** Navigate to `http://<your-vps-ip>:47821`.
The full Router-Brain → Worker-Critic → Mission-Manager experience is available
in the browser, including voice via the browser's microphone (WebRTC).

> **What is NOT needed on a VPS:** no GPU, no audio hardware, no Windows APIs,
> no microphone, no desktop display. The `[desktop]` extras (tray app, Orb
> overlay, global-hotkey wake, local Whisper, Computer-Use harness) are
> optional — a clean `python:3.11-slim` Linux container boots without them.

**Code gap (deliberate, left for orchestrator decision):** the wizard's
`step_mic_check()`, `step_hotkey_check()`, `step_wake_word_setup()`,
`step_dependency_check()`, and `step_finalize()` (autostart prompt) are all
skipped in non-interactive mode together with `step_api_keys()`.  A future
hardening pass could run the TTY-free steps (hardware check, dep check) even
in non-interactive mode and only gate on the `input()` calls.  This is safe
as-is: all skipped steps are idempotent and can be re-run via
`python -m jarvis --wizard` from an interactive shell.

---

## 2. Runtime modes

`run.bat` is the preferred launcher — it auto-invokes `scripts/check-working-tree.ps1` as pre-boot drift restore.

| Command | Effect |
|---|---|
| `run.bat` | `pythonw` + FastAPI + pywebview window + voice + Orb (default; hidden console) |
| `run.bat --debug` | Console visible, `JARVIS_DEBUG=1`, verbose logging |
| `run.bat --dev` | Frontend from Vite dev server `:5173`, hot reload (`JARVIS_DEV=1`) |
| `run.bat --headless` | API + WebSocket only, no window, Mock-Brain wiring for E2E |

Direct launcher: `python -m jarvis.ui.web.launcher [--headless|--dev|--port N|--no-lock]`. `--no-lock` disables the `filelock` single-instance guard for parallel dev sessions. Voice opt-out: `set JARVIS_VOICE=0`.

---

## 3. 8-Layer architecture

```
L7 UI/UX           Tray, Toasts, Admin-API, Desktop-App (FastAPI+React+pywebview), Orb-Overlay
L6 Orchestrator    State-Machine, Router, BrainManager, Supervisor, Mission-Manager, Kontrollierer
L5 Harness-Adapter OpenClaw, Codex, Open Interpreter, Python-Script, MCP-Remote
L4 Brain           5 providers (Claude-API, OpenRouter, OpenAI, Gemini, Grok) + Ack-Brain sub-second tier
L3 Intent/Risk     Classifier, Risk-Tier-Policy, Approval, Rate-Limit-Tracker
L2 Speech          Wake → VAD (Silero) → STT (faster-whisper / Google / Deepgram) → TTS (Gemini Flash / Grok-Voice / SAPI5)
L1 Audio I/O       WASAPI via sounddevice, Device-Routing, Chime-Feedback
L0 OS/Hardware     Win32, CUDA, Mic/Speakers, global-hotkeys
```

**Dependency rule (strict):** higher layers reach lower layers **only via Protocols** (`jarvis/core/protocols.py:161-541` — all `runtime_checkable`, structural typing). Lateral communication is **only** via typed events on `EventBus` (`jarvis/core/bus.py`) with `frozen=True` dataclasses carrying `trace_id: UUID` + `timestamp_ns: int`. Subscriber exceptions are swallowed in `_safe_dispatch` (`bus.py:65-76`) and logged — never propagated (AP-18).

---

## 4. Plugin system

Seven groups frozen as `PLUGIN_GROUPS` in `jarvis/core/protocols.py:363-371`: `jarvis.wakeword`, `jarvis.stt`, `jarvis.tts`, `jarvis.brain`, `jarvis.harness`, `jarvis.tool`, `jarvis.channel` (plus `jarvis.turn` for end-of-turn detection, declared in `pyproject.toml:104`).

Plugins live under `jarvis/plugins/<group>/<name>.py`, register via `pyproject.toml` `[project.entry-points."jarvis.<group>"]`, and **must not import from `jarvis.*`** — only structural Protocol compatibility (registry at `jarvis/core/registry.py`). After editing entry-points: **`pip install -e . --no-deps`** is mandatory to activate them (this is the fourth layer of BUG-006/014).

---

## 5. EventBus + streaming contract

- Events are `frozen=True` slotted dataclasses inheriting from `Event` (`jarvis/core/events.py:29`), with auto-generated `trace_id` + `timestamp_ns`. 100+ subclasses.
- Immutability enables flight-recorder replay; `bus.subscribe_all(handler)` makes the recorder a wildcard subscriber.
- **All Brain/STT/TTS/Harness provider methods return `AsyncIterator[...]`** — non-streaming providers yield exactly one element. Consumers always write `async for chunk in provider.xxx()`.
- `EventBus.publish` (`bus.py:44-62`) parallel-dispatches via `asyncio.gather(..., return_exceptions=True)` and routes each handler through `_safe_dispatch`. **A broken subscriber must never kill the pipeline (AP-18).**

---

## 6. Brain providers + Ack-Brain

Six brain plugins (`pyproject.toml:130-137`): `claude-api`, `openrouter`, `openai`, `gemini`, `grok`, `codex`. Multi-provider is mandatory — **never hardcode** Anthropic/Claude. `cfg.brain.primary` is the master selector; `BrainManager` (`jarvis/brain/manager.py`) owns a smart fallback chain (rate-limit aware, frontier aware) and a `(provider_name, model) → Brain` cache. Voice aliases in `manager.py:100-113` accept `claude/opus/haiku/sonnet/gpt/chatgpt/flash/pro/grok`.

**Ack-Brain** (`jarvis/brain/ack_brain/generator.py`) emits a sub-second butler-style preamble before the deep brain replies. Provider plugins: Gemini, Grok, OpenAI, Ollama. Suppress-if-fast gate at `[ack_brain].suppress_if_brain_faster_than_ms = 2000` (ADR-0014 Flash-Brain). `AckGenerator.run()` is failure-safe across F1–F10 modes and never raises.

User context: **No Anthropic API account** (AP-6). Workers use Claude Sonnet via **Claude Max OAuth** through the `claude-cli` backend (`@anthropic-ai/claude-code`, npm-installed by the wizard). Personal Jarvis Brain stays on Gemini by default.

---

## 7. Risk-Tier + Router discipline

Four levels: `RiskTier = Literal["safe", "monitor", "ask", "block"]` (`protocols.py:108`). `RiskTierEvaluator.evaluate()` (`jarvis/safety/risk_tier.py:64-100`) applies **blacklist > whitelist > tool default** — blacklist match raises `ActionBlocked`; whitelist match downgrades to `safe` with `approved_by="whitelist"` (the anti-confirmation-fatigue contract). Glob matching via `fnmatch` on `"<tool_name> <serialized_args>"`. **`ToolExecutor.execute()` is the only authorized call path; AP-3 forbids direct `Tool.execute()`.**

**Router discipline (ADR-0011 amended):** the Router-Brain is a **pure dispatcher**. Tool surface is the `ROUTER_TOOLS` frozenset at `jarvis/brain/factory.py:40-77` — 13 tools: `run-shell`, `screen-snapshot`, `dispatch-to-harness`, `multi-spawn`, `spawn-openclaw`, `dispatch-with-review`, `awareness-snapshot`, `awareness-recall`, `run-skill`, `wiki-recall`, `wiki-page-read`, `wiki-ingest`, plus the Self-Mod trio (`list_mutable_settings`, `get_config_value`, `set_config_value`).

Force-spawn heuristic in `BrainManager._should_force_openclaw`:
- Smalltalk allowlist wins → never spawn.
- Action verb (`lies/baue/installiere/öffne/mach/zeig` + repair words) → spawn.
- External-system marker (PR/Repo/GitHub/Issue) → spawn.

**The sub-jarvis tier was deleted in Welle 4** — only `"router"` remains (`factory.py:119-123` raises on unknown tiers). Resurrecting `SUB_TOOLS` or adding `spawn-openclaw`/`dispatch-with-review`/`run-skill` to any worker set breaks the D9 recursion guard (AP-14).

---

## 8. Output filter (voice path)

`scrub_for_voice` in `jarvis/brain/output_filter.py` is **regex-only, no LLM calls** (latency mandate, AP-11). Order: stacktrace → fallback phrase early-return → markdown strip → tool-JSON removal (3 forms) → self-reference → echo paraphrase (only `OPENER_BUDGET = 60` chars) → filler-opener → engineering jargon (with hyphen-compound whitelist — `Browser-Provider` stays) → whitespace normalize.

**Sacred whitelist** (`output_filter.py:39-42`): `Datei, Email, Browser, Terminal, Notiz, Termin, Kalender`. Jargon list: `Harness, MCP, Subprocess, Provider`; compound jargon: `Sub-Agent, Supervisor-Agent, Subagent`.

Wired into TTS at two sites:
- `pipeline.py:1330` — `_handle_utterance` → `_speak()` → `tts.synthesize`
- `pipeline.py:647` — `_on_announcement` (skill/sub-agent announcements, OpenClaw `summary_de` readback)

40-case test suite at `tests/unit/brain/test_output_filter.py`. ADR-0010 (Output-Filter Pattern-Based).

---

## 9. Configuration & secrets

**Secret access** — `jarvis.core.config.get_secret(key, env_fallback)` is the only authorized path. Hierarchy:

1. Windows Credential Manager (service `personal-jarvis`) ← wizard writes here
2. ENV variable
3. `.env` (dev fallback)
4. **NEVER** `jarvis.toml`, code, commits, or `.claude/` files (AP-12)

Voice/chat must never accept secrets (AP-2: STT log leak vector).

**Config drift triple-defense (BUG-010 — parallel Claude sessions silently rolling back provider switches):**

- **ENV overrides** at user scope (e.g. `JARVIS__TTS__PROVIDER=grok-voice`)
- `jarvis.toml` set OS read-only after edits
- `scripts/drift-guard-daemon.ps1` — Userland daemon, 5-min cron via `shell:startup` shortcut (UAC-free; Task Scheduler + ScheduledJob both elevated-only). Checks `jarvis.toml` + ENV against `scripts/config-soll.json`, fixes drift, re-locks TOML. Lock file: `logs/drift-guard-daemon.lock`. BOM-safe writes via `UTF8Encoding($false)` (BUG-018 fix).

---

## 10. Atomic config writes & Self-Mod pipeline

`jarvis/core/config_writer.py` uses `tomlkit` (preserves comments), `_WRITE_LOCK = threading.Lock()`, BOM-aware read/write, tempfile + `os.replace`.

**Phase-7 Self-Mod 10-step pipeline** in `jarvis/core/self_mod/writer.py` is non-negotiable:

1. Allowlist via `SelfModRegistry.require_spec`
2. `tomlkit` load
3. Read old value via dotted path
4. In-memory mutate
5. **Pre-validate** via `JarvisConfig.model_validate(doc.unwrap())`
6. Backup to `<config>.parent/.backups/jarvis.toml.<iso>.bak` (**outside watchdog scope, AP-13**)
7. Tempfile + `os.replace` atomic swap
8. **Synchronous reload-test** via `ConfigLoader.load()` (AP-14 forbids watchdog-driven verify)
9. `ConfigReloaded` bus event
10. Backup GC (FIFO cap + age floor) → audit via `SelfModAudit`

Locking is `ClassVar threading.Lock`. Three router-tier Self-Mod tools: `list_mutable_settings`, `get_config_value`, `set_config_value` (`jarvis/brain/tools/self_mod_tools.py`).

---

## 11. Phase-6 isolation invariants

- Every worker runs in a fresh `git worktree add -b agent/<task-slug>` under `<repo_parent>/sub-agents-outputs/` via `WorktreeManager` (`jarvis/missions/isolation/worktree.py`). ≤200-char path cap. No writes to user's working tree.
- Every worker subprocess is wrapped in a **Windows Job Object** with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`jarvis/missions/isolation/job_object.py`). No zombies on orchestrator crash.
- `MAX_CRITIC_LOOPS: Final[int] = 3` is **hardcoded** at `jarvis/missions/critic/runner.py:50-51` ("Hardcoded per ADR-0009. Not configurable without a new decision record."). Used by `Orchestrator.run_mission` at `jarvis/missions/kontrollierer/orchestrator.py:388`.
- **Action/Observation invariant (ADR-0009):** the LLM never authors its own Observation. Voice readback reads only the Kontrollierer-signed `MissionApproved.summary_de`, never `correction_instruction` from the Critic-LLM.
- **Persist-vor-Publish discipline** (`manager.py:8-11`): `upsert_mission(PENDING)` happens before `append_and_publish(MissionDispatched)` — the event log is authoritative.

---

## 12. Phase status table

Verified against the actual file tree (not just CLAUDE.md claims):

| Phase | Status | Evidence |
|---|---|---|
| 0–4 Foundations | live | Protocols, plugin system, FastAPI/React, speech pipeline, skill system, tool-use loop, harness dispatch, core memory |
| 5 Vision/Action/Admin/Async/Control + Tiered Routing | live | `jarvis/{vision,admin,tasks,control,telemetry}/`, `ROUTER_TOOLS` frozenset, ADR-0001..0011 |
| 6 Self-Healing Worker-Critic | live | `jarvis/missions/{manager,kontrollierer,critic,workers,isolation,openclaw,voice,safety}/`. 458 mission tests. Wired via `bootstrap_missions` |
| 7 Self-Mod (foundation + writer + tools) | live | `jarvis/core/self_mod/` (audit, errors, pending, registry, schema, writer). `spawn-skill-author` **IS** registered at `pyproject.toml:208` (CLAUDE.md says otherwise — see §25) |
| Awareness A0–A5 | live | `jarvis/awareness/` (state, story, salience, verdichter, working_set, episode, watchers, probes). A1 + A3 router tools registered |
| Wiki B0/B1/B2/B3/B5/B7/B8/B9 | live | `jarvis/memory/wiki/` (curator, atomic_writer, page, integration, scheduler, session_rollup, voice_bridge, telemetry, vault_index, watcher, search). 3 router tools |
| Wiki B4 (legacy Curator) | soft-disabled | `factory.py:736-757` gates on `cfg.memory.legacy_curator.enabled` (default `false` since 2026-05-17). `data/workspace/` snapshot stays on disk for 35 reader sites |
| Wiki B6 | not started | — |
| OpenClaw bridge Welle 1+4 | done | `jarvis/plugins/harness/openclaw.py` (Mock-Mode), `jarvis/missions/openclaw/` (provider_map, workspace, mcp, setup) |
| OpenClaw bridge Welle 2 (live default) | open | Mock-Mode in plugin file; live subprocess factory pending |
| OpenClaw bridge Welle 3 (full live mode) | open | — |
| Ack-Brain (pre-thinking) | live | `jarvis/brain/ack_brain/` + factory hook (`factory.py:1034`) |
| CLI catalog + terminal view | live | `jarvis/clis/` (catalog, installer, loader, prober, registry, risk_integration, usage_log) + `jarvis/terminal/` (ConPTY via `pywinpty`). Tools `cli-tools` + `spawn-cli-worker` |

Status drift moves fast. Verify with `git log -- <module>` rather than trusting this table.

---

## 13. 5-layer anti-drift enum pattern

Documented in [`docs/anti-drift-three-layer.md`](docs/anti-drift-three-layer.md). Any string crossing module boundaries lives in **five places**: producer Python module → SQLite schema → Pydantic `Literal` → TypeScript union → UI label switch.

**Canonical example `HangupReason`:**

| Layer | File | Role |
|---|---|---|
| L0 source of truth | `jarvis/sessions/constants.py` | `HANGUP_REASONS` tuple + symbolic constants (`HANGUP_TURN_COMPLETE`, …) |
| L1 producers | `jarvis/speech/pipeline.py`, `jarvis/sessions/init.py` | Import symbols — never raw strings |
| L2 schema | `jarvis/sessions/schema.sql` | Doc-comment listing allowed values |
| L3 Pydantic | `jarvis/sessions/models.py` | `HangupReason = Literal[...]` + **import-time `RuntimeError`** asserting `get_args(HangupReason) == HANGUP_REASONS` |
| L4 TS | `jarvis/ui/web/frontend/src/components/sessions/types.ts` | Union type |
| L5 UI | `jarvis/ui/web/frontend/src/components/sessions/SessionList.tsx` | `hangupLabel(reason)` switch |

**Five defenses (D1–D5):** symbolic constants → import-time assertion → parity test (`tests/unit/sessions/test_hangup_reason_parity.py` regex-extracts all 5 layers and asserts equality) → DB compatibility test (`tests/integration/test_sessions_db_compatibility.py`) → self-defending list endpoint (`SessionStore.list_sessions` wraps per-row Pydantic in `try/except ValidationError`, returns partial list with `hangup_reason_drift_skipped` warning).

**BUG-008 recurred four times** because this scaffolding was missing. Adopt preemptively for any new wire-format enum (`MessageRole` already done 2026-05-16; future candidates: `VoiceTier`, mission status, `SkillLifecycleState`).

---

## 14. Anti-patterns (AP-1..AP-18)

The single most important table for an LLM joining cold:

| # | If you do this... | ...you get this bug |
|---|---|---|
| AP-1 | Spawn `subprocess.Popen` without `NO_WINDOW_CREATIONFLAGS` | BUG-012 flicker storm under `pythonw.exe` |
| AP-2 | Accept API keys via voice/chat | STT log leak — credential exfiltration vector |
| AP-3 | Call `Tool.execute()` directly (bypassing `ToolExecutor`) | Risk-tier/whitelist/plausibility skipped |
| AP-4 | Add a new `hangup_reason`/mission-status string in one site only | BUG-008 recurrence: HTTP 500, empty UI |
| AP-5 | Put `spawn-openclaw`/`dispatch-with-review`/`run-skill` in a worker tool set | D9 recursion: worker spawns supervisor, infinite loop |
| AP-6 | Hardcode `Claude`/`Anthropic` API client | User has no Anthropic API account; breaks `cfg.brain.primary` |
| AP-7 | Write `jarvis.toml` without `_WRITE_LOCK` + tempfile + BOM handling | BUG-018: BOM-corrupted TOML, backend won't boot |
| AP-8 | Skip `scripts/preflight.ps1` in a new worktree | BUG-006/014: edits go to a worktree the live Python doesn't import from |
| AP-9 | Run new awareness/wiki code in the voice critical path | Latency regression — awareness is read-only, off the hot path |
| AP-10 | Write a worker without `git worktree` + Job Object | Race conditions + zombie processes on crash |
| AP-11 | Add an LLM call inside `scrub_for_voice` | TTS latency tank |
| AP-12 | Encode API keys in `jarvis.toml` or commit `.env` | Credential leak; bypasses `keyring` audit trail |
| AP-13 | Block on watchdog reload for atomic-write verification | Race: file half-applied, no sync rollback |
| AP-14 | Re-add a Sub-Jarvis tier or `SUB_TOOLS` set | Welle 4 deleted it; resurrection breaks the OpenClaw-bridge contract |
| AP-15 | Auto-activate generated skills (`state` ≠ `draft`) | Lateral-movement vector; skills run without review |
| AP-16 | Add `[phase6.*]`/`[memory.wiki.*]` keys without `ConfigDict(extra="allow")` | Pre-validate rejects → boot fails after self-mod |
| AP-17 | Run Jarvis as a Windows Service | SYSTEM has no mic/headset access |
| AP-18 | Propagate a subscriber exception from `EventBus._safe_dispatch` | One handler kills the pipeline |

---

## 15. Recurring bug classes

Detail in [`docs/BUGS.md`](docs/BUGS.md) (26-entry register). Five classes recur — recognize the signal, apply the defense.

**1. Four-layer restore trap** (BUG-006 → -014 → -015). Signal: fix "works in tests" but Jarvis behavior unchanged after restart. Four layers: stale worktree + outdated frontend `dist/` build + RAM-resident pywebview + **editable-install pin to a deleted clone**. Defense: `pwsh scripts/preflight.ps1` + `python -c "import jarvis; print(jarvis.__file__)"` + frontend rebuild + `taskkill /F /IM pythonw.exe`.

**2. Multi-layer enum drift** (BUG-008, four episodes). Signal: empty UI list while DB has rows, HTTP 500, `literal_error` in Pydantic. Defense: the five-layer pattern from §13 — symbolic constants, import-time assertion, parity test, DB-compat test, self-defending endpoint.

**3. Config drift** (BUG-010 triple-defense). Signal: provider switches silently rolled back, parallel Claude sessions rewriting `jarvis.toml`. Defense: `scripts/drift-guard-daemon.ps1` (5-min) + ENV overrides + OS-read-only TOML + BOM-safe writer.

**4. Subprocess console flicker** (BUG-012). Signal: black/flickering windows under `pythonw.exe`, mascot restart storm. Cause: missing `NO_WINDOW_CREATIONFLAGS` (16 call sites fixed). Defense: every new subprocess call imports from `jarvis.core.process_utils`.

**5. Audio host-API blocking-write trap** (BUG-014). Signal: TTS silent, PortAudio `PaErrorCode -9999`. Cause: auto-resolver picks WDM-KS which PortAudio's blocking write doesn't support. Defense: `_FORBIDDEN_OUTPUT_HOSTAPIS` filter + shortest-unique-token device matching (`"PRO X"`, not full Razer marketing name).

---

## 16. Desktop App (UI)

- **Backend:** FastAPI on `localhost:47821` (admin_api_port). Single-instance lock via `filelock`. 25 route modules under `jarvis/ui/web/*_routes.py`: `board`, `cli`, `docs`, `federation_proxy`, `friends`, `frontier`, `marketplace`, `mcp`, `missions`, `missions_pty`, `missions_ws`, `outputs`, `preview`, `profile`, `provider`, `review`, `self_mod`, `sessions`, `setup`, `skills`, `sub_agents`, `tasks`, `tools`, `wiki`, `workflows`. WebSocket events serialized via `EventBus` (`subscribe_all` wildcard for flight recorder).
- **Frontend:** React 18 + Vite 5 + TypeScript 5 + Tailwind + Radix UI + `@xterm/xterm` (ConPTY terminal view) + `react-force-graph-2d` (wiki graph) + `@xyflow/react` (workflow canvas) + Zustand + TanStack Query. Build output → `jarvis/ui/web/dist/`.
- **Shell:** `pywebview` loads `dist/index.html` (production) or `http://localhost:5173` (`--dev`).
- **Orb overlay:** separate `pythonw` subprocess (`jarvis/overlay/`), bus-bridged so mascot-originated events (e.g. mute on double-click) republish to the EventBus.

---

## 17. Testing — buckets, markers, conventions

**335 test files.** Bucket counts:

| Bucket | Files | Purpose |
|---|---|---|
| `unit/` | 175 | Per-module |
| `integration/` | 49 | Phase-level E2E |
| `missions/` | 36 | Phase 6 worker-critic-kontrollierer |
| `overlay/` | 29 | Orb mascot |
| `review/` | 17 | Phase 8.4 quality-gate pipeline |
| `contract/` | 17 | Protocol parametrised tests (mandatory for new providers) |
| `board/` | 7 | Personal-board sync |
| `e2e/` | 2 | Self-mod + voice-review |
| `eval/` | 1 | Golden-query suite |
| `setup/` | 1 | Wizard / dependencies |

**Markers** (`[tool.pytest.ini_options]`): `phase5`, `e2e`, `voice_latency`, `eval`, `slow`, `skip_ci`, `openclaw_live`. `asyncio_mode = "auto"`. **Fakes (not `unittest.mock`)** live in `tests/fakes/`; audio fixtures `tests/fixtures/audio/`; trace replays `tests/fixtures/traces/`. New STT/Brain/Tool/Channel providers MUST pass `tests/contract/`.

Common runs:

```bash
pytest tests/                                  # full suite
pytest -m "not slow"                           # fast CI subset
pytest -m phase5 / -m e2e / -m voice_latency / -m eval / -m openclaw_live
pytest tests/unit/brain/test_routing.py        # 32 router-discipline cases
pytest tests/unit/brain/test_output_filter.py  # 41 voice-scrubber cases
pytest tests/unit/sessions/test_hangup_reason_parity.py   # BUG-008 drift guard
```

---

## 18. Linting & type checking

```bash
ruff check jarvis/         # rules E,F,I,UP,B,ASYNC,S,A; ignores S101/S603/S607
ruff format jarvis/
mypy jarvis/

# Frontend (jarvis/ui/web/frontend/)
npm install
npm run dev       # http://localhost:5173
npm run build     # tsc -b && vite build --outDir ../dist --emptyOutDir
npm run test      # vitest
```

Line length 100. Target `py311`. One per-file `E501` exception (`jarvis/awareness/prompts.py` — long few-shot strings).

---

## 19. Scripts inventory (`scripts/`)

**Cron daemons (production):**
- `auto-push-eod.ps1` — nightly tag+push safety net. Skips worktrees with active OpenClaw session (`<30 min` modify time).
- `install-auto-push-task.ps1 -Time "22:00"` — register Task Scheduler job.
- `drift-guard-daemon.ps1` — 5-min config drift defense. Singleton-locked, hidden, started via `shell:startup` shortcut.
- `install-config-drift-guard-task.ps1` — install variant.
- `jarvis-config-drift-guard.ps1` — actual drift-check pass (called by daemon).
- `check-working-tree.ps1` — pre-boot restore of any tracked file missing from working tree. Always exits 0; rotating log `data/working-tree-check.log` (10 runs).
- `cleanup-stale-agent-branches.ps1` — prunes leftover `agent/*` branches after `git worktree remove`.

**One-shot guards / install:**
- `preflight.ps1` — 5-check worktree health (git worktree assertion, editable-install re-pin, `import jarvis` path, `__editable__*.pth` stale scan, summary). **Mandatory before any new worktree code.**
- `install_shortcuts.py`, `stop_watchdog.ps1`, `uninstall-auto-push-task.ps1`.

**Smoke / probe (manual):**
- `smoke_brain_e2e.py`, `smoke_frontier.py`, `smoke_phase6_p{1,2,2_jobkill,3,3_real}.py`, `smoke-test-ack.ps1`.
- `voice_e2e_probe.py`, `voice_compare.py`, `tts_brain_endtoend.py`, `tts_output_sanity.py`, `warm_keep_bench.py`.
- `awareness_smoke_a{1,2}.py`, `vision_smoke.py`.
- `diag_audio_devices.py`, `diag_mic_{live,mute,wasapi}.py`, `verify_orb_{appears,mute_toggle,drag}.py`, `snap_orb.py` — audio/orb debugging.

**Migration / one-time:**
- `wiki_migrate_v0_to_v1.py`, `migrate_adrs.py`, `shorten_adr_titles.py`, `bulk_translate{,_2,_3}.py`, `rewrite_locales.py`, `find_german_strings.py`, `add_useT_inner.py`, `export_ws_schema.py`, `build_frontend.py`.

---

## 20. External integrations & accounts

**Brain providers** (6 plugins):

- **Gemini = primary** (user's main brain; `gemini-3-flash-preview` / `gemini-3.1-pro-preview`).
- **Grok = fallback** (xAI — primary TTS voice "leo" + Brain fallback). API key reused for both.
- **Claude-API** plugin exists but **user has NO Anthropic API account** (AP-6). Workers use Claude Sonnet via Claude Max OAuth through the `claude-cli` backend (`@anthropic-ai/claude-code`), auto-installed by wizard via npm.
- **OpenRouter** universal gateway, **OpenAI** GPT, **Codex** API-key mode.
- **GitHub Claude Bot:** deployed on 29 private repos via `.github/workflows/claude.yml`. **Public-repo safety gate** baked in (workflow only runs if `repository.private == true`). OAuth tokens auto-synced from `.credentials.json` every 3h.

**STT** (6 plugins): `faster-whisper` (local, GPU), `openai-api`, `groq-api`, `deepgram`, `deepgram-flux` (Phase L.3 streaming), `deepgram-nova3`. **Google Cloud STT** wired 2026-05-15 — `chirp_2` @ `europe-west4`, ~680ms warm latency. SA-key under `~/.config/jarvis/`.

**TTS** (8 plugins): `elevenlabs`, `elevenlabs-flash`, `gemini-flash-tts`, `google-neural2`, `openai-tts`, `piper-local`, `grok-voice` (default; 5 voices, `leo`=Jarvis-Butler), `cartesia-sonic3`.

**Wake** (2): `porcupine` (Picovoice access key), `openwakeword` (local, threshold tuned to 0.10 — BUG-009 lesson).

**Turn detection** (3): `flux-integrated` (default — Phase L.2 AD-L-4), `smart-turn-v3`, `silero-only`.

**Wiki vault:** `wiki/obsidian-vault/`. Subdirs: `00-index`, `10-notes`, `90-attachments`, `99-templates`, `concepts`, `entities`, `projects`, `sessions`, `_archive`, `.obsidian`. Obsidian registration is opt-in via Wiki tab dialog (ADR-0015); supports both `%LOCALAPPDATA%\Obsidian\` and `%PROGRAMFILES%\Obsidian\`.

**Channels** (2): `web` (WS to desktop frontend), `telegram` (`python-telegram-bot 22.x`, long-polling, F1).

**MCP servers:** loaded from `data/mcp.json`, registered via `MCPRegistry`. Tools auto-bridged into the global tool registry through `register_mcp_tools_in_registry`.

---

## 21. ADR + Phase doc index

**ADRs in `docs/adr/`** (18 files; duplicate-number cleanup pending):

- ADR-0001 — IPC Named-Pipe HMAC (Admin-Helper)
- ADR-0002 — UIA Tree Pruning (Vision)
- ADR-0003 — Task Queue Storage (Phase-5 Async)
- ADR-0004 — Kill Propagation
- ADR-0005 — Lightweight Scheduler (croniter, no APScheduler)
- ADR-0006 — Cost-Budget Hook
- ADR-0007 — Flight-Recorder JSONL
- ADR-0008 — Computer-Use Harness In-Process (POAV)
- ADR-0009 — Awareness Architecture / Self-Healing Worker-Critic *(duplicate number)*
- ADR-0010 — Output-Filter Pattern-Based / Window-Focus Watcher MsgWait *(duplicate number)*
- ADR-0011 — Router Pure Dispatcher (amended for Welle 4: SUB_TOOLS deleted)
- ADR-0012 — Awareness Recall Router-Tier
- ADR-0013 — Knowledge Wiki Architecture (Karpathy-style structured vault)
- ADR-0014 — Flash-Brain Suppress-If-Fast / Memory-Trigger Contract *(duplicate number)*
- ADR-0015 — Obsidian Setup Wizard (B9)

**Phase docs in `docs/`:**

- `phase1a-verify.md` — Desktop App scaffold (FastAPI + pywebview + React)
- `phase1c-test-results.md` / `phase1c-review-report.md` — Skill system + MCP integration
- `phase2-integration-test-results.md` — Tool-use loop + risk-tier executor
- `phase4-integration-test-results.md` — Harness dispatch + core memory
- `phase5-research.md` / `phase5-integration-test-results.md`
- `phase6-test-report.md` — 458 mission tests, 5 sub-phases verified
- `phase6-worker-layer.md` — worktree + Job Object isolation
- `phase6-prompt-chain.md` — Action/Observation invariant (ADR-0009)
- `phase6-handoff-2026-04-26.md`

Self-mod writeup: [`docs/self_mod.md`](docs/self_mod.md) — 8 mutable settings (`tts.provider`, `tts.voice_{de,en}`, `tts.speed`, `stt.provider`, `brain.primary`, `ui.theme`, `profile.language`), append-only audit at `data/self_mod.log`.

---

## 22. Operational rhythms

- **`run.bat` pre-boot:** `scripts/check-working-tree.ps1` auto-invoked. Always exits 0; surfaces any restored file via banner + rotating log.
- **Drift-guard daemon:** every 5 min via `shell:startup` shortcut, Userland (no UAC). Singleton-locked.
- **Auto-push-EOD:** Task Scheduler 22:00 default. Tags `safety/eod-*` per branch, skips active OpenClaw worktrees (modified within last 30 min).
- **Wiki triggers** (ADR-0014 contract — all classified silent vs loud):
  - `WikiContextInjector` (silent) — runs before every brain turn
  - `VoiceFactBridge` ack path (loud) — `ResponseGenerated` with ack keyword, async via `asyncio.create_task`
  - `VoiceFactBridge` aggressive path (loud, rate-limited 60s default, opt-out `aggressive_mode=false`)
  - `SessionRollupWorker` (loud) — `IdleEntered` past `session_idle_threshold_minutes`, writes one Markdown page per session
- **Preflight:** mandatory in every new worktree before any code edit.

---

## 23. Repo hygiene & contribution

- **Output Language Policy (highest priority):** every artifact in the repo is **English** — code, comments, docstrings, Markdown, commit messages, PR titles/bodies, tests, CLI help, REST descriptions, error responses, audit logs, telemetry, UI i18n source. German stays only for the assistant's user-facing chat reply, TTS at runtime, and already-committed German content.
- **Plan vs. code:** on conflict, plan wins. Master plan binds: `~/.claude/plans/also-er-muss-auch-lexical-pond.md`. Architecture contracts: [`docs/openclaw-bridge.md`](docs/openclaw-bridge.md), [`docs/anti-drift-three-layer.md`](docs/anti-drift-three-layer.md).
- **Worktree activation checklist:** **`pwsh scripts/preflight.ps1`** before code in any new worktree. If exit non-zero, fix before proceeding (BUG-006/014, AD-UF23).
- **Before larger edits:** read [`docs/BUGS.md`](docs/BUGS.md) — every recurring bug class catalogued there.
- **Plugin contract:** plugin modules MUST NOT import from `jarvis.*` — structural compatibility with Protocol only. After edit: `pip install -e . --no-deps`.
- **Streaming first:** all Brain/STT/TTS/Harness methods return `AsyncIterator[...]`.
- **Events:** `frozen=True` dataclasses with `trace_id: UUID` + `timestamp_ns`. Subscriber exceptions never propagate (AP-18).
- **Worker isolation invariants:** every worker = fresh `git worktree` + Windows Job Object kill-on-close. `MAX_CRITIC_LOOPS=3` hardcoded; changes require new ADR via `/skill phase6-adr-update`.

---

## 24. Key module index

| File | Purpose |
|---|---|
| `jarvis/core/protocols.py:161` | All plugin Protocols (`WakeWordProvider`, `STTProvider`, `TTSProvider`, `Brain`, `Harness`, `Tool`, `MemoryStore`, `ChannelAdapter`, `TurnDetector`, `VisionSource`, `CancelToken`, `CostMeter`, `IntentClassifier`). Frozen dataclasses for all wire types. |
| `jarvis/core/bus.py:23` | `EventBus` — async pub/sub with `_safe_dispatch` exception isolation, wildcard `subscribe_all` for flight recorder. |
| `jarvis/core/events.py:29` | `Event` base + 100+ subclasses, all `frozen=True` with `trace_id` + `timestamp_ns`. |
| `jarvis/core/config.py` | `JarvisConfig` Pydantic model + loader + `get_secret(key, env_fallback)`. |
| `jarvis/core/config_writer.py` | `tomlkit`-based atomic patch with `_WRITE_LOCK`, BOM-aware. |
| `jarvis/core/self_mod/writer.py` | `AtomicConfigWriter` — 10-step validate/backup/rollback pipeline. |
| `jarvis/core/process_utils.py:33` | `NO_WINDOW_CREATIONFLAGS` — mandatory for every subprocess call (AP-1). |
| `jarvis/brain/factory.py:40` | `ROUTER_TOOLS` frozenset + `_phase2_full_brain` builder. |
| `jarvis/brain/manager.py` | `BrainManager` — provider cache, fallback chain, force-spawn heuristic. |
| `jarvis/brain/output_filter.py` | `scrub_for_voice` — regex-only TTS scrubber, 40-case test suite. |
| `jarvis/brain/ack_brain/generator.py` | `AckGenerator` — 11-step pipeline, F1–F10 coverage, never raises. |
| `jarvis/safety/risk_tier.py:64` | `RiskTierEvaluator` — blacklist>whitelist>tool, fnmatch glob, `ActionBlocked`. |
| `jarvis/safety/tool_executor.py` | `ToolExecutor` — only authorized call path for `Tool.execute()`. |
| `jarvis/missions/manager.py:48` | `MissionManager` — SQLite event store, state machine, persist-vor-publish discipline. |
| `jarvis/missions/kontrollierer/orchestrator.py` | Phase-6 heart — TaskGroup + Semaphore wiring of MissionDecomposer→Worker→Critic→ReflectionMemory→BudgetTracker. |
| `jarvis/missions/critic/runner.py:50` | `MAX_CRITIC_LOOPS = 3` hardcoded. |
| `jarvis/missions/workers/{base,claude_direct_worker,codex_worker,gemini_worker,subjarvis_worker,supervisor}.py` | Worker variants — `WorkerProtocol` structural contract. |
| `jarvis/missions/isolation/{worktree,job_object,env}.py` | Git-worktree manager + Windows Job Object kill-on-close. |
| `jarvis/plugins/harness/openclaw.py` | OpenClaw bridge plugin (Welle-2 Mock-Mode). |
| `jarvis/harness/screenshot_only_loop.py` | Screenshot-only POAV loop — the sole `computer_use` engine (vision picks pixel targets; cross-platform `mss` + `pyautogui`). |
| `jarvis/awareness/manager.py` | `AwarenessManager` — state holder; **never on voice critical path** (AP-9). |
| `jarvis/memory/wiki/integration.py` | `bootstrap_wiki_integration` — wires SessionRollupWorker (B7) + WikiCurator (B1). |
| `jarvis/sessions/constants.py` | `HANGUP_REASONS` single source of truth — canonical 5-layer enum example. |
| `jarvis/speech/pipeline.py` | Voice pipeline — wake→VAD→STT→Brain→TTS, scrub_for_voice at lines 647 and 1330. |
| `jarvis/ui/web/server.py` | FastAPI app, mission stack bootstrap (`_init_mission_stack`). |
| `jarvis/clis/loader.py` | Virtual-loader expanding `cli-tools` entry-point into N `cli_<name>` Tool instances. |

---

## 25. Doc-vs-code drift (verified)

Two drifts surfaced during the agent audit; flag them before quoting CLAUDE.md to a fresh chat:

1. **`spawn-skill-author` IS registered in `pyproject.toml:208`** as `spawn-skill-author = "jarvis.brain.tools.skill_authoring:SpawnSkillAuthorTool"` (landed 2026-05-17 per inline comment). CLAUDE.md still says "7.5 `spawn_skill_author` not yet registered in `pyproject.toml`" — outdated. The Phase-7.5 self-modifying skill-authoring tool is wired.

2. **`jarvis/sub_jarvis/` directory still exists** as an **empty placeholder** (no `__init__.py`, no files) despite CLAUDE.md and `docs/openclaw-bridge.md §11` declaring it deleted in Welle 4. Cleanup is structurally complete (no code references), but the empty dir itself is leftover.

Neither blocks anything — but a fresh chat parroting CLAUDE.md verbatim will be wrong on point 1, and confused by `ls jarvis/` on point 2.

---

## 26. Pointers

- [`CLAUDE.md`](CLAUDE.md) — project guidance (highest priority for any agent working in this repo)
- [`docs/BUGS.md`](docs/BUGS.md) — 26-entry bug register with regression-test pointers
- [`docs/openclaw-bridge.md`](docs/openclaw-bridge.md) — AD-1..AD-21 OpenClaw harness contract
- [`docs/anti-drift-three-layer.md`](docs/anti-drift-three-layer.md) — five-layer enum pattern (mandatory for any new wire-format string)
- [`docs/self_mod.md`](docs/self_mod.md) — Phase 7 self-mod user guide
- [`docs/obsidian-setup.md`](docs/obsidian-setup.md) — Wiki vault registration walkthrough
- `~/.claude/plans/also-er-muss-auch-lexical-pond.md` — binding master plan
- `~/.claude/projects/<your-claude-project-dir>/memory/MEMORY.md` — auto-memory with stable user preferences (multi-provider brain, no-Anthropic-API, bilingual, anti-confirmation-fatigue, frontier-quality-before-cost)

---

## 27. Legal

**License — MIT.** Personal Jarvis is released under the MIT License. The full license text lives in [`LICENSE`](LICENSE) at the repository root and is also declared in `pyproject.toml` (`license = { text = "MIT" }`). In short: you are free to use, copy, modify, merge, publish, distribute, sublicense, and sell copies of the software, including for commercial purposes — provided you comply with the **Attribution & provenance** clause below. The software is provided "as is", without warranty of any kind.

**Copyright.** © 2026 Personal Jarvis Maintainers. All contributors retain copyright on their respective contributions; opening a pull request against this repository is taken as agreement that the contribution is licensed under the MIT License above.

**Canonical source.** This repository — **[`github.com/personal-jarvis/PersonalJarvis`](https://github.com/personal-jarvis/PersonalJarvis)** — is the authoritative upstream of Personal Jarvis. Forks, mirrors, vendored copies, and derivative works are welcome under MIT, but the **original authors are the Personal Jarvis Maintainers**, not whoever is hosting the copy you happened to find. If you encounter Personal Jarvis code outside this URL, you can verify provenance by comparing the git history against the canonical repository.

**Attribution & provenance (mandatory).** Section 2 of the MIT License is **not decorative** — it requires that *"the above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software."* In plain English: anyone who uses substantial portions of this code in their own project, fork, distribution, product, or training corpus **must retain the `Copyright (c) 2026 Personal Jarvis Maintainers` line and the full MIT permission text in a place where downstream users can find them** (typical conventions: a `LICENSE` / `LICENSES/` / `NOTICE` / `THIRD_PARTY_NOTICES` file at the root of the derivative work, or an "Open Source Licenses" screen reachable from a product's UI). Stripping the notice and republishing the code is **not** a licensing question — it is **copyright infringement**, actionable under copyright law in the 179 Berne-Convention signatory states. We additionally request — though we cannot legally compel beyond MIT — that derivative works include a visible link back to the canonical repository above so users can find the upstream project.

**No misrepresentation of authorship.** The MIT License grants broad freedom to modify and redistribute, but it does **not** grant the right to claim original authorship of unmodified portions of this work. Re-uploading substantial parts of this codebase to another repository, package registry, model card, or product page while presenting yourself as the original author — by removing the copyright notice, by removing references to the canonical source, or by adding statements that imply you wrote what you in fact copied — misrepresents the provenance of the work. This is treated as **both a license violation (because the MIT attribution clause was not honored) and as deceptive attribution under most jurisdictions' consumer-protection and unfair-competition law**, independent of the license. If you fork and significantly modify the code, please describe your modifications clearly so users can tell your work apart from the upstream — that is good citizenship, not a legal requirement, but it is how the open-source ecosystem stays trustworthy.

**Reporting violations.** If you encounter a project that has copied substantial portions of this codebase without preserving the copyright notice, or that misrepresents authorship of the original Personal Jarvis work, please file a copyright-violation report with the hosting platform — for GitHub, the [DMCA Takedown Policy](https://docs.github.com/en/site-policy/content-removal-policies/dmca-takedown-policy) is the standard route — or open an issue on the canonical repository. The Personal Jarvis Maintainers reserve all rights granted by copyright law.

**Third-party software.** This project bundles, imports, or vendors a number of open-source dependencies, each governed by its own license — notably MIT (React, Vite, Zustand, TanStack Query, `pywebview`, `tomlkit`, `keyring`, `croniter`), Apache 2.0 (`faster-whisper`, `silero-vad`, `onnxruntime`, the OpenAI / Gemini / Grok / Anthropic SDKs), BSD-3-Clause (`numpy`, `sounddevice`, `mss`), and Picovoice's commercial-with-free-tier terms for `pvporcupine`. **No third-party dependency is relicensed by inclusion in this repository**, and the attribution requirement above applies to Personal Jarvis's own code, not to the upstream licenses of bundled dependencies (which carry their own attribution clauses you must also honor).

**Trademarks.** "Claude", "Anthropic", "OpenAI", "Gemini", "Grok", "Picovoice", "Porcupine", "Obsidian", "GitHub", "Windows", and any other product or company names referenced in this repository are trademarks of their respective owners and are used here for identification purposes only. Their use does not imply endorsement of this project by, or affiliation with, the trademark holder.

**Audio, voice, and screen-capture privacy disclosure.** When the `[desktop]` extras are installed, Personal Jarvis can capture microphone audio (STT), play synthesized speech (TTS), take screenshots and read UI element trees (Vision / Computer-Use), and read text from the active window. All of these capabilities are opt-in at install time and run only in the user's session. STT, TTS, and Brain calls are routed through the providers the user configures (Gemini, Grok, OpenAI, Deepgram, ElevenLabs, Google Cloud, etc.) — please review each provider's privacy policy before sending production data through them. The headless VPS base install captures no local audio and no screen content.

**No warranty for self-modifying behaviour.** The Phase-7 self-mod tools (`set_config_value`, `spawn-skill-author`) and the Phase-6 Worker-Critic loop can write to `jarvis.toml`, generate skills (created with `state="draft"`, never auto-activated — AP-15), and create isolated `git worktree` branches under `<repo_parent>/sub-agents-outputs/`. The 10-step atomic-write pipeline (allowlist → pre-validate → backup → tempfile → synchronous reload → rollback → audit) is designed to be safe and reversible, but the MIT "as is" disclaimer applies in full: the maintainers are not liable for any state the system writes on your behalf, on your filesystem or against the third-party APIs you have configured.

**Contributing.** Pull requests are welcome. By contributing you agree that your contribution is licensed under the MIT License above. See [`CLAUDE.md`](CLAUDE.md) and [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) for the binding output-language policy (English-only artifacts) and the cloud-first design doctrine. The repository's bug register lives at [`docs/BUGS.md`](docs/BUGS.md); architecture decisions are recorded under [`docs/adr/`](docs/adr/).
