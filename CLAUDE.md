# CLAUDE.md

Guidance for OpenClaw (claude.ai/code) and any sub-agent working in this repository.

---

## ⚠️ THE GitHub repository (read BEFORE any push — recurring confusion, settled 2026-06-09)

There are TWO GitHub repos and sessions keep mixing them up. This is the truth table — case-sensitive on the **second** path segment:

| Repo | Role | Who writes to it |
|---|---|---|
| **`PersonalJarvis/PersonalJarvis`** (PascalCase) | **THE project.** The one public-facing flagship repo the maintainer means when they say "the GitHub repo", "veröffentliche", "push it", "sichere den Stand", "update GitHub". | **ONLY** the `ship-public-release` skill (depersonalized snapshot — never raw `git push`, never secrets/PII/`data/`/Vault). |
| `PersonalJarvis/personal-jarvis` (lowercase, remote `origin`) | Private **backstage**: raw dev history, branches, maintainer identity, day-to-day commits. | Dev sessions, as infrastructure. It is NOT "the project" the maintainer talks about. |

**Binding rules:**
1. When the maintainer asks to save/publish/push their work to GitHub, the user-visible deliverable is an updated **`PersonalJarvis/PersonalJarvis`** via the `ship-public-release` skill. Never ask "which repo". A raw-work push to `origin` may happen *in addition* (dev safety net), but it does not fulfill the request by itself.
2. Never `git push` raw working state to `PersonalJarvis/PersonalJarvis`. That repo only ever receives the scrubbed release snapshot.
3. Full doctrine + history: [`CLOUD.md`](CLOUD.md) § "Canonical repositories" (points 1–5).

---

## Output Language Policy (HIGHEST PRIORITY)

**Every artifact an agent produces in this repo is English.** No exceptions.

This covers code, comments, docstrings, log/exception messages, Markdown (READMEs, ADRs, plans, `BUGS.md` entries, handoffs), `SKILL.md` files, commit messages, PR titles/bodies, test names + docstrings, CLI help text, FastAPI route descriptions, error responses, JSON schema `description` fields, audit-log entries, telemetry event names, and any new UI strings (i18n key + English source — never German source).

This rule overrides historical "Sprache: Deutsch" sections in this file and any memory entry asking for German artifacts. If unsure: **default to English**.

**What stays German:** the user-facing chat reply written by the assistant in conversation (per global `~/.claude/CLAUDE.md`), TTS output (bilingual DE+EN auto-detect at runtime), and already-committed German content (do not retro-translate unless asked).

**Enforced in CI (not just documented):** the `language-policy` job in `.github/workflows/ci.yml` blocks any *newly added* German line from merging to `main` (diff-based, so the in-flight DE→EN backlog stays green). It is a required status check. Intentionally-German exceptions: an inline `i18n-allow` marker on the line, or a glob in `scripts/ci/german-allowlist.txt`. See `docs/superpowers/specs/2026-05-30-ci-language-policy-gate-design.md`.

---

## Cloud-First Philosophy (BINDING — overrides historical "Windows 11" framing)

**Target runtime is any €5 / month VPS or low-spec laptop** with a modern browser. *Not* the maintainer's RTX 5070 Ti / Windows 11 workstation. Every architectural decision, default value, doc paragraph, dependency, and PR from 2026-05-18 forward is evaluated against the VPS user — the maintainer's machine represents fewer than 0.1 % of the install base this project is being designed for.

**Top-level charter:** [`CLOUD.md`](CLOUD.md) (Rule #1 = cross-platform). **Full doctrine:** [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md). On conflict between this file and `PHILOSOPHY.md`, the doctrine wins. On conflict between the doctrine and the binding plan, the doctrine wins on hardware assumptions, the plan wins on architectural decisions.

The short version:

- **RULE #1 — everything we build must run on Linux, macOS, AND Windows (plus headless Linux server).** No platform is second-class; a feature that only works on one OS is *incomplete*, not "done with a known limitation". The base `pip install` + boot must succeed on a fresh `python:3.11-slim` Linux container, on macOS, and on Windows. OS-specific code is allowed only behind (a) a runtime capability check, (b) an extras group, and (c) a graceful English-message no-op on the other platforms. Use `pathlib`, capability probes, and UTF-8 by default — never hardcode `C:\Users\...` or assume cp1252. See [`CLOUD.md`](CLOUD.md).
- **All five provider classes (Brain, STT, TTS, Vision, Wake) must have a fully cloud-reachable default path** — no required local GPU, no required local model, no required Windows API, no required microphone, no required speaker.
- **No new hard dependency on Windows-specific or GPU-specific packages.** New imports of `pywin32`, `pywinauto`, `pyautogui`, `sounddevice`, `faster-whisper`, `onnxruntime-gpu`, `openwakeword`, `global-hotkeys`, `mss`, `pywebview` go into a `[desktop]` extras group, *not* the base install.
- **The maintainer's setup is a power-user profile, not a baseline.** Tray app, Orb overlay, global-hotkey wake, local Whisper, Silero-VAD-in-process, drift-guard PowerShell daemon, Computer-Use harness — **all opt-in extras**, all degrade gracefully (with a clear English-language message) when the extras are not installed.
- **Headless VPS + browser UI is a first-class runtime.** A user opening the FastAPI / WebSocket frontend in any browser, using the browser's microphone and speakers (or a channel adapter — Telegram, Discord, SMS, webhook), must reach the full Router-Brain → Worker-Critic → Mission-Manager experience without installing a Windows binary, a CUDA toolkit, or a native audio driver.
- **Documentation, defaults, install instructions, and onboarding lead with the VPS path.** Windows-desktop instructions are a footnote inside an "Optional power-user extras" section. Re-order the doc when in doubt.

**Pre-existing code that violates this doctrine is grandfathered until touched.** A code path that already hardcodes a local-hardware assumption does not need to be rewritten in a panic — but any *touch* of that path is an opportunity to migrate it toward the cloud-first default, not extend the violation.

**Maintainer dev tooling (`scripts/preflight.ps1`, `run.bat`, `scripts/drift-guard-daemon.ps1`, `scripts/check-working-tree.ps1`, `scripts/auto-push-eod.ps1`) may stay Windows-PowerShell-only.** That is the maintainer's *developer* environment, not the consumer's *runtime*. The line is drawn at the boundary between `scripts/` (developer tools — fair game to stay Windows-only) and the importable `jarvis/` package (runtime — must run on a Linux VPS).

**Decision lens for any PR:** *Would this PR work, end-to-end, for a user on a fresh `python:3.11-slim` Linux container with 1 vCPU, 1 GB RAM, no GPU, no audio hardware, no Windows APIs, and only a network connection?* If yes → merge. If no → either (a) the local-only portion is correctly gated behind an extras group with a graceful no-op fallback in the base install, or (b) split the PR.

---

## Project

**Personal Jarvis** — voice-driven meta-orchestrator for Windows 11. Not a classical voice assistant: the core pattern is **Supervisor-Agent** that dispatches work to interchangeable harnesses (OpenClaw subprocess, Codex CLI, Open Interpreter, MCP servers). The voice layer is just the interface.

**Master plan:** `~/.claude/plans/also-er-muss-auch-lexical-pond.md` — binding for all design decisions. On conflict between plan and code, the plan wins; code deviations must be documented back in the plan.

**Binding architecture contracts:**
- [`docs/openclaw-bridge.md`](docs/openclaw-bridge.md) — OpenClaw harness contract (AD-1..AD-21, AP-OC1..AP-OC14). Welle 1 (spike) + Welle 4 (sub-jarvis deletion) are done; Welle 2 (live bridge in default voice path) + Welle 3 (full live mode) remain.
- [`docs/anti-drift-three-layer.md`](docs/anti-drift-three-layer.md) — five-layer enum pattern (Python ↔ SQL ↔ Pydantic ↔ TS ↔ UI). Mandatory for any string crossing module boundaries.

**Bug register:** [`docs/BUGS.md`](docs/BUGS.md). Read before larger edits — the recurring bug classes (restore-trap, multi-layer enum drift, config drift, subprocess flicker, audio host-API) are catalogued there.

---

## Phase Status

| Phase | Live? | Pointers |
|---|---|---|
| **0–4 — Foundations** | ✅ | Plugin system + protocols, FastAPI/React desktop app, speech pipeline, skill system, tool-use loop, risk-tier executor, core memory, harness dispatch. Detail in `docs/phase{0,1,1a,1c,2,4}-*.md`. |
| **5 — Vision/Action/Admin/Async/Control + Tiered Routing** | ✅ | `jarvis/{vision,admin,tasks,control,telemetry}/`. Computer-Use enabled. Tiered routing via `ROUTER_TOOLS` frozenset. ADR-0001..0011. |
| **6 — Self-Healing Worker-Critic** | ✅ | `jarvis/missions/` (event store, manager, recovery, state machine, budget, cleanup, workers, critic, kontrollierer, safety, voice, isolation). ADR-0009, `docs/phase6-*.md`. Wired into REST + voice path via `bootstrap_missions`. |
| **7 — Self-Mod (foundation + writer + tools)** | ✅ | `jarvis/core/self_mod/` (audit, errors, pending, registry, schema, writer). Three router-tier tools: `list_mutable_settings`, `get_config_value`, `set_config_value`. ADR + writeup in `docs/self_mod.md`. **7.5 `spawn_skill_author` not yet registered in `pyproject.toml`.** |
| **Awareness A0–A5** | ✅ | `jarvis/awareness/` (state, story, salience, verdichter, working_set, episode, recall_store, watchers, probes). Router-tier tools `awareness-snapshot` (A1) + `awareness-recall` (A3). ADR-0009/0010/0012. Hard rule: **never on the voice critical path**. |
| **Wiki B0/B1/B2/B3/B5/B7/B8/B9** | ✅ | `jarvis/memory/wiki/` (curator, atomic_writer, page_repository, integration, session_rollup, voice_bridge, telemetry, scheduler). Three router-tier tools: `wiki-recall`, `wiki-page-read`, `wiki-ingest`. ADR-0013/0014/0015. B2 = `docs/obsidian-setup.md` + B9-Wizard. B3 = `WikiView.tsx` + 6-endpoint `wiki_routes.py` + `wiki_ws.py` live-reload. **B4 soft-disabled** 2026-05-17 via `[memory.legacy_curator] enabled = false` — legacy Curator package + `data/workspace/` snapshot stay on disk; Hart-Cut (vault migration + reader refactor + package delete) remains open. **B6 not started**. |
| **OpenClaw bridge** | ⚙ Welle 1+4 done | `jarvis/plugins/harness/openclaw.py`, `jarvis/missions/openclaw/`. `[harness.openclaw].enabled=true`. Welle 2 (live bridge as default) + Welle 3 (live-mode wrap) open. |
| **Ack-Brain (pre-thinking)** | ✅ | `jarvis/brain/ack_brain/` — sub-second butler ACK before the deep brain replies. Gemini 3.1 Flash Lite primary, Grok fallback. UI preamble bubble. Suppress-if-fast gate at 2000ms (`[ack_brain].suppress_if_brain_faster_than_ms`). ADR-0014 (flash-brain). |
| **CLI catalog + terminal view** | ✅ | `jarvis/clis/` (catalog, installer, loader, prober, registry, risk_integration, usage_log) + `jarvis/terminal/` (ConPTY via `pywinpty`). Tools `cli-tools` + `spawn-cli-worker`. UI views: `ClisView`, `TerminalView`. |

Status drift moves fast. Verify with `git log -- <module>` rather than trusting this table.

---

## Architecture (the parts an agent must respect)

### 8-Layer model

```
L7 UI/UX           Tray, Toasts, Admin-API, Desktop-App (FastAPI+React+pywebview), Orb-Overlay
L6 Orchestrator    State-Machine, Router, BrainManager, Supervisor, Mission-Manager
L5 Harness-Adapter OpenClaw, Codex, Open Interpreter, Python-Script, MCP-Remote
L4 Brain           5 providers (Claude-API, OpenRouter, OpenAI, Gemini, Grok) + Ack-Brain sub-second tier
L3 Intent/Risk     Classifier, Risk-Tier-Policy, Approval, Rate-Limit-Tracker
L2 Speech          Wake → VAD (Silero) → STT (faster-whisper / Google) → TTS (Gemini Flash / Grok-Voice / SAPI5)
L1 Audio I/O       WASAPI via sounddevice, Device-Routing, Chime-Feedback
L0 OS/Hardware     Win32, CUDA, Mic/Speakers, global-hotkeys
```

**Dependency rule (strict):** higher layers reach lower layers **only via protocols** (`jarvis/core/protocols.py`). Lateral communication is **only** via typed events on `EventBus` (`jarvis/core/bus.py`) with `frozen=True` dataclasses carrying `trace_id` + `timestamp_ns`. Subscriber exceptions are swallowed in `_safe_dispatch` — they must never propagate.

### Plugin system (structural, not nominal)

Plugins live under `jarvis/plugins/<group>/<name>.py`, register via `pyproject.toml` `[project.entry-points."jarvis.<group>"]`, and **must not import from `jarvis.*`** inside the plugin module — only structural compatibility with the protocol (registry at `jarvis/core/registry.py`). After editing entry-points: `pip install -e . --no-deps`.

Groups (frozen in `PLUGIN_GROUPS`): `jarvis.wakeword`, `jarvis.stt`, `jarvis.tts`, `jarvis.brain`, `jarvis.harness`, `jarvis.tool`, `jarvis.channel`.

### Streaming first

All `Brain`, `STT`, `TTS`, `Harness` provider methods return `AsyncIterator[...]`. Non-streaming providers yield exactly one element. Consumers always write `async for chunk in provider.xxx()`.

### Event-Bus

- Events are `frozen=True` dataclasses (`jarvis/core/events.py`) with `trace_id: UUID` + `timestamp_ns`. Immutability enables flight-recorder replay.
- `subscribe_all` receives every event — the flight recorder is a wildcard subscriber.
- A broken subscriber is logged, never propagated.

### Secrets

Access via `jarvis.core.config.get_secret(key, env_fallback)` only. Hierarchy: Windows Credential Manager (service `personal-jarvis`) → ENV → `.env` (dev fallback). The wizard (`jarvis/setup/wizard.py`) populates Credential Manager. **Never** put API keys in code, `jarvis.toml`, commits, or `.claude/` files. Voice/chat must never accept secrets (AP-2 — STT log leak vector).

### Brain providers + ack-brain

Multi-provider is mandatory — **never hardcode** Anthropic/Claude. Config under `[brain.providers.*]` in `jarvis.toml`. Runtime switch via voice ("Jarvis, switch to Gemini") is a plan requirement; `BrainManager` must support it. Smart fallback chain in `jarvis/brain/manager.py`. Workers use `claude-cli` backend via Claude Max OAuth (user has no Anthropic API account).

Ack-Brain (`jarvis/brain/ack_brain/`) emits a sub-second butler-style preamble before the deep brain replies. Suppress-if-fast gate at 2000ms keeps it out of the way when the deep brain is already fast.

### Risk-Tier system

Four levels: `safe` / `monitor` / `ask` / `block`. Priority is **blacklist > whitelist > tool default** (`jarvis/safety/risk_tier.py`). Whitelist downgrades a tier to `safe` with `approved_by="whitelist"` — this is the anti-confirmation-fatigue contract. **Direct calls to `Tool.execute()` are a bug**; only `ToolExecutor.execute()` is authorized.

### Router discipline (binding, ADR-0011 amended)

The router-tier brain is a **pure dispatcher**. Tool surface is the `ROUTER_TOOLS` frozenset in `jarvis/brain/factory.py`. Direct actions outside this set are delegated to OpenClaw via `spawn_openclaw`.

Force-spawn heuristic in `BrainManager._should_force_openclaw`:
- Smalltalk allowlist wins → never spawn.
- Action verb (`lies/baue/installiere/öffne/mach/zeig` + repair words) → spawn.
- External-system marker (PR/Repo/GitHub/Issue) → spawn.

Patterns configurable under `[brain.routing]`. **The sub-jarvis tier was deleted in Welle 4 — only `"router"` remains.** Resurrecting `SUB_TOOLS` or adding `spawn-openclaw`/`dispatch-with-review`/`run-skill` to any worker set breaks the D9 recursion guard. When extending `ROUTER_TOOLS`: amend ADR-0011 + extend `tests/unit/brain/test_routing.py`.

### Output filter discipline (voice path)

Brain output → TTS goes through `scrub_for_voice` in `jarvis/brain/output_filter.py` — **regex only, no LLM calls** (latency mandate). Two TTS paths are wired through scrub:
- `_handle_utterance` → `_speak()` → `tts.synthesize` (`pipeline.py:1330`).
- `_on_announcement` (skill/sub-agent announcements, OpenClaw `summary_de` readback) (`pipeline.py:647`).

Whitelist (sacred, never scrubbed): `Datei, Email, Browser, Terminal, Notiz, Termin, Kalender`. Hyphen-compounds preserved (`Browser-Provider` stays). For the full blacklist (tool leaks, jargon, Sir-opener, markdown, self-reference, echo paraphrase, fillers, post-scrub fallback): see the module docstring and `tests/unit/brain/test_output_filter.py` (40 cases). ADR-0010.

### Atomic config writes

Mutations of `jarvis.toml` go via `jarvis/core/config_writer.py` only — `tomlkit`-based (preserves comments), `_WRITE_LOCK` mutex, BOM-aware read/write, tempfile + `os.replace`. For Phase-7 self-mod, the pipeline is **non-negotiable**: Allowlist → Read → Apply → Pre-Validate (`JarvisConfig.model_validate`) → Backup → Tempfile+replace → sync reload-test → Rollback-on-fail → `ConfigReloaded` dispatch → Backup GC → Audit (AD-5; AP-3/4/5/13/14).

Backup directory must be **outside** the watchdog scope (AP-13). Reload-test is **synchronous**, not watchdog-driven (AP-14).

### Multi-layer enum drift prevention

When a vocabulary spans Python ↔ SQL ↔ Pydantic ↔ TypeScript ↔ UI label (e.g. `HangupReason`), use the five-layer pattern from `docs/anti-drift-three-layer.md`. Reference: `jarvis/sessions/constants.py` is the single source of truth; `models.py` runtime-asserts the Pydantic `Literal` against it. Regression guards: `tests/unit/sessions/test_hangup_reason_parity.py` + `tests/integration/test_sessions_db_compatibility.py`. **BUG-008 recurred four times because this scaffolding was missing — apply it preemptively for any new wire-format enum (mission status, skill lifecycle, voice tier).**

### Phase-6 isolation invariants

- Every worker runs in a fresh `git worktree add -b agent/<task-slug>` under `<repo_parent>/sub-agents-outputs/` (≤200-char path cap). No writes to the user's working tree.
- Every worker subprocess is wrapped in a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. No zombies on orchestrator crash.
- `MAX_CRITIC_LOOPS = 3` is hardcoded. Not parameterizable. Changing requires a new ADR via `/skill phase6-adr-update`.
- Action/Observation invariant (ADR-0009): the LLM never authors its own Observation. Voice readback reads only Kontrollierer-signed `MissionApproved.summary_de`, never `correction_instruction` from the Critic-LLM.

---

## Optimistic Execution & the "Oops" Protocol (binding)

The core UX contract is **one uninterrupted spoken conversation**. The Talker (router-brain + ack-brain) acknowledges optimistically and never blocks on an MCP round-trip; the Heavy-Duty Worker (Mission-Manager + `claude-cli` Sonnet) executes in the background off the chat transcript. Seed vision: `Architektur-Spezifikation v1.0`. Reality-aligned close-the-gap plan + KPIs (M1–M5) + 4-wave execution: [`docs/plans/optimistic-execution-v1/README.md`](docs/plans/optimistic-execution-v1/README.md). The pillars below are partly live (ack-brain, force-spawn, mission store) and partly open (guaranteed-ACK-before-dispatch, the closed Oops loop, p95 SLO gates).

### Architecture Decisions
- **AD-OE1** The optimistic ACK ("Geht klar") is emitted **before** the worker dispatch returns — never after. Audit every `_handle_utterance` return path (BUG-007/BUG-020 territory).
- **AD-OE2** The Talker never `await`s an MCP/network call on the voice path. The talker↔worker queue is the in-process `EventBus` + mission event store — no external broker (cloud-first €5-VPS doctrine, no new hard dep).
- **AD-OE3** Dumb tools (local scripts) resolve in-process via `local_action_gate`; they MUST NOT wake the worker (false-spawn rate = 0).
- **AD-OE4** Smart tools: the **worker** issues the MCP call, never the Talker.
- **AD-OE5** Oops loop: worker failure → frozen `WorkerCorrectionNeeded` event → inject into Talker context → speak ONLY at the next Silero-VAD turn-boundary → through `scrub_for_voice`. Never interrupt mid-utterance.
- **AD-OE6** Zero silent drops: every worker/MCP failure yields a silent retry OR a spoken correction OR an audited apology (anti-BUG-020 invariant).

### Coding Standards
- Latency budgets are SLO-gated: p95 wake→ACK < 1.2 s, intent→ACK < 3.0 s, router decision < 150 ms. Regressions fail CI.
- Every spoken path (utterance + announcement) goes through `scrub_for_voice` (regex only, no LLM call — AP-11).
- New wire-format vocab (correction reasons, mission status) uses the five-layer enum pattern (`docs/anti-drift-three-layer.md`) + parity test.
- `ROUTER_TOOLS` stays a frozenset; no spawn-tool ever enters a worker set (AP-5/AP-14). Every subprocess uses `NO_WINDOW_CREATIONFLAGS` (AP-1). Config writes go through `config_writer` (lock + tempfile + BOM-safe, AP-7).

---

## Cross-platform desktop features (the six ports, behind `jarvis/platform/`)

The six desktop power-user features that were historically Windows-only are now **cross-platform behind the shared `jarvis/platform/` capability seam** (`detect_platform()` + a cached frozen `Capabilities` snapshot, AD-5). Each feature is one `Protocol` + one per-OS implementation + a `sys.platform` factory + a graceful logged null-fallback (AD-6); the Windows implementations are **untouched** (AD-7 — they carry the BUG-009/012/014/030 fixes). The migration plan is `docs/plans/cross-platform-mac-linux/`; **ADR-0020 (cross-platform elevation) supersedes ADR-0001** but reuses the HMAC / Pydantic-argv / `shell=False` security core unchanged.

| Feature | Factory | Windows | macOS | Linux | Verification |
|---|---|---|---|---|---|
| Terminal (PTY) | `terminal.backend.make_pty_backend` | ConPTY (`pywinpty`) | `ptyprocess` | `ptyprocess` | CI-provable (real PTY, EK-4) |
| App-launch | `plugins.tool.app_resolver.resolve_app_launch_target` | App Paths | `open -a` | `xdg-open`/exec | CI-provable (resolution) |
| UI-element-click | `vision.tree_factory.make_ui_tree_source` | UIA | AX (`pyobjc`) | AT-SPI (`pyatspi`) | live sign-off (AX/AT-SPI tree) |
| Orb overlay | `overlay.surface.make_overlay_surface` | Tk color-key | Tk `-transparentcolor` | best-effort + tray | live sign-off (transparency) |
| Hotkey | `trigger.backends.make_hotkey_backend` | `global-hotkeys` | `pynput` | `pynput` (X11); Wayland no-op | live sign-off (capture) |
| Admin/elevation | `admin.transport.make_admin_transport` + `admin.elevator.make_elevator` | UAC + SDDL pipe | Authorization Services + unix socket | pkexec/sudo + unix socket | live sign-off (prompt); never CI-E2E |

**Verification is honestly labelled per feature in `docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md`.** As of this writing the maintainer has Windows only and nothing is pushed, so every macOS/Linux **live** GUI/permission behavior is `unverified-on-real-desktop` and the `ci.yml` matrix is `CI-configured` (first green run pending push) — **never** claim "CI-verified" or "live-verified" until that log says so. Operators run `scripts/crossplatform/signoff_probe.py` on a real device to fill it in.

- **Dependency reality (AD-14 — do not "fix" this):** `pynput` + `ptyprocess` live in the `[desktop]` extra (no platform marker; `ptyprocess` gated `sys_platform != 'win32'`); `pyobjc-framework-{Quartz,ApplicationServices,Accessibility}` in `[desktop-macos]` (`sys_platform == 'darwin'`). **Linux `pyatspi` is NOT on PyPI — never add it as a pip dependency.** It is GObject-Introspection, distro-packaged (`apt install python3-pyatspi gir1.2-atspi-2.0`), surfaced via the `capabilities.has_ax_tree` runtime probe. A future agent "fixing" a missing-pyatspi pip dep would be wrong.
- **Doctrine intact:** the headless €5-VPS base install ships **none** of these desktop extras and still boots on a fresh `python:3.11-slim` Linux container — every port is extras-gated and degrades to a logged no-op (AD-6) when its capability is absent. These labels say *which* extras now also work on Mac/Linux, never that any of them are required.

## Windows specifics (do not skip)

- **Unicode stdout:** cp1252 default. New CLI modules must call `sys.stdout.reconfigure(encoding='utf-8')` or stick to ASCII.
- **Subprocess hygiene:** every `subprocess.*` / `asyncio.create_subprocess_exec` call must pass `creationflags=NO_WINDOW_CREATIONFLAGS` from `jarvis/core/process_utils.py`. Missing this triggers the BUG-012 flicker storm under `pythonw.exe`.
- **Audio:** WASAPI via `sounddevice`. **WDM-KS host-API is forbidden** (`_FORBIDDEN_OUTPUT_HOSTAPIS` in `jarvis/audio/player.py`) — PortAudio's blocking write API crashes there (BUG-014). Pattern-match device names on shortest unique token (`"PRO X"`), not marketing name.
- **Hotkeys:** `global-hotkeys`. Avoid `Alt+F4`, `Ctrl+C`, `Win+*`. Safe combos: `ctrl+right_alt+<letter>`.
- **No Windows Service.** SYSTEM user has no headset/mic access. Jarvis is a tray app in the user session under `pythonw.exe`; autostart via shortcut in `shell:startup`.
- **UAC manifest:** `asInvoker`. Elevate per-action, never globally.

---

## Screenshots & scratch captures

**All development/verification screenshots go in `screenshots/` at the repo root** — never the repo root itself, never a random cwd. `jarvis/core/screenshots.py` defines `screenshots_dir()` plus a boot sweep (`sweep_screenshots`, wired into `SingleInstance._on_primary_claim`) that (a) consolidates any stray root-level image into `screenshots/` and (b) prunes captures older than **10 days** by mtime. App-runtime Vision frames are separate — they live in `data/flight_recorder/blobs/` and are pruned by `jarvis/telemetry/retention.py`. When an agent or tool saves a UI capture, target `screenshots/`; the folder is git-ignored and self-tidying.

---

## Commands

```bash
# Install
pip install -e . --no-deps                   # makes entry_points active (BUG-006/014 recovery)
pip install -r requirements.txt              # full runtime deps
pip install -e ".[dev]"                      # adds pytest, ruff, mypy, pyinstaller

# Console scripts (from [project.scripts])
jarvis                                       # tray app or first-run wizard
jarvis-ask                                   # one-shot prompt CLI
jarvis-review-gc                             # Phase 8.5 review-pipeline GC
jarvis-review-eval                           # Phase 8.6 eval harness (--quick/--real/--bucket)

# python -m jarvis (root CLI)
python -m jarvis --version
python -m jarvis --wizard                    # re-run setup wizard
python -m jarvis --check                     # hardware analysis
python -m jarvis --plugins                   # entry_points registry dump
python -m jarvis --debug                     # console logging + config dump
python -m jarvis --phase5-doctor             # vision/admin/CU/HMAC/cost status
python -m jarvis --install-admin-helper      # generate HMAC secret in Credential Manager

# Desktop-App launcher
python -m jarvis.ui.web.launcher             # FastAPI + pywebview + voice + Orb
python -m jarvis.ui.web.launcher --headless  # API+WS only, no window, Mock-Brain
python -m jarvis.ui.web.launcher --dev       # frontend from Vite dev server :5173
python -m jarvis.ui.web.launcher --no-lock   # disable single-instance lock (parallel dev)

# run.bat (root, recommended)
run.bat                                      # pythonw + voice + Orb
run.bat --debug                              # console visible, JARVIS_DEBUG=1
run.bat --dev                                # Vite hot-reload
run.bat --headless                           # API only, no voice
# Pre-boot hook: invokes scripts\check-working-tree.ps1 automatically (BUG-014 guard).
# Voice opt-out: set JARVIS_VOICE=0 before launch.

# Lint/format
ruff check jarvis/
ruff format jarvis/
mypy jarvis/

# Frontend (jarvis/ui/web/frontend/)
npm install
npm run dev                                  # http://localhost:5173
npm run build                                # → jarvis/ui/web/dist
npm run test                                 # vitest

# Mandatory worktree preflight (BUG-006/014/015 guard)
pwsh scripts/preflight.ps1                   # exit non-zero → fix before coding

# Operational scripts
powershell scripts/auto-push-eod.ps1 [-DryRun]              # nightly tag+push safety net
powershell scripts/install-auto-push-task.ps1 -Time "22:00" # register Task Scheduler job
powershell scripts/drift-guard-daemon.ps1                   # config drift defense (BUG-010)
powershell scripts/install-config-drift-guard-task.ps1
powershell scripts/check-working-tree.ps1                   # working-tree drift recovery

# Smoke / probe scripts
python scripts/smoke_brain_e2e.py
python scripts/smoke_phase6_p{1,2,2_jobkill,3,3_real}.py
python scripts/voice_e2e_probe.py
python scripts/voice_compare.py
python scripts/awareness_smoke_a{1,2}.py
```

---

## Testing conventions

```bash
# Buckets
pytest tests/                                # full suite (asyncio_mode=auto)
pytest tests/contract/ -v                    # protocol contract tests (parametrised)
pytest tests/unit/ -v                        # per-module
pytest tests/integration/ -v                 # phase-level E2E
pytest tests/e2e/ -v                         # self-mod + voice review
pytest tests/missions/ -v                    # Phase 6
pytest tests/voice_routing/ tests/voice_latency/ -v

# Markers (from [tool.pytest.ini_options])
pytest -m phase5                             # Phase-5 integration
pytest -m e2e                                # Phase-7 E2E
pytest -m voice_latency                      # Phase-L latency
pytest -m eval                               # golden-query eval
pytest -m openclaw_live                      # real OpenClaw subprocess (skips when binary missing)
pytest -m "not slow"                         # fast subset
pytest -m skip_ci                            # tests that need desktop/admin/click

# Layer-targeted
pytest -k test_tier1_speed                                  # latency regression
pytest tests/unit/brain/test_routing.py -v                  # 26-case router discipline
pytest tests/unit/brain/test_output_filter.py               # 40-case scrubber
pytest tests/unit/awareness/ -v
pytest tests/unit/memory/wiki/ -v
pytest tests/unit/sessions/test_hangup_reason_parity.py     # BUG-008 drift guard
pytest tests/unit/plugins/tool/test_wiki_tools.py
pytest tests/integration/test_openclaw_e2e.py
pytest tests/integration/test_openclaw_lazy_bootstrap.py
```

**Conventions:** fakes (not `unittest.mock`) in `tests/fakes/`; audio fixtures under `tests/fixtures/audio/`; trace replays under `tests/fixtures/traces/`; new STT/Brain/Tool/Channel providers must pass the contract suite.

---

## Critical anti-patterns (do not do this)

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
| AP-19 | Reuse a process-global progress counter in a stall/heartbeat watchdog without resetting it per unit of work | BUG-032: watchdog measures the idle gap *between* turns → spuriously aborts a fresh TTS answer before its first frame ("Jarvis listens forever") |

---

## Recurring bug classes (must internalize)

Detail in [`docs/BUGS.md`](docs/BUGS.md). Six classes recur — recognize the signal, apply the defense:

1. **Four-layer restore trap** (BUG-006 → -014 → -015): worktree + frontend build + RAM + **editable-install pin to a deleted clone**. Signal: fix "works in tests" but Jarvis behavior unchanged after restart. Defense: `pwsh scripts/preflight.ps1` + `python -c "import jarvis; print(jarvis.__file__)"`.
2. **Multi-layer enum drift** (BUG-008, 4 episodes): empty UI list while DB has rows, HTTP 500, `literal_error` in Pydantic. Defense: `docs/anti-drift-three-layer.md` pattern + parity test.
3. **Config drift** (BUG-010 triple-defense): parallel sessions rewriting `jarvis.toml`, silently rolling back provider switches. Defense: `scripts/drift-guard-daemon.ps1` (5-min cron) + ENV overrides + read-only TOML + BOM-safe writer (`UTF8Encoding($false)`).
4. **Subprocess console flicker** (BUG-012): missing `NO_WINDOW_CREATIONFLAGS` on `pythonw.exe`. Defense: every new subprocess call imports from `jarvis.core.process_utils`.
5. **Audio host-API blocking-write trap** (BUG-014): WDM-KS picked by auto-resolver, PortAudio blocking API crashes. Defense: `_FORBIDDEN_OUTPUT_HOSTAPIS` filter; shortest-unique-token device matching.
6. **Watchdog stale cross-unit counter** (BUG-032): a stall/heartbeat watchdog reads a process-global progress counter (`last_write_ns`) that is never reset per unit, so it measures idle time *between* units and fires spuriously — e.g. the TTS playback watchdog aborted a fresh answer before its first frame ("Jarvis listens forever / never speaks") whenever the brain thought longer than the 5 s stall window. Signal: a "wedge" abort whose timestamps are impossible (fires earlier than the threshold; the resource responds *after* the abort). Defense: reset the counter at unit start (before any lock wait); re-arm the "not started yet" guard per unit, not at construction; guards in `tests/unit/audio/test_player_stall_recovery.py` + `tests/unit/speech/test_speak_playback_timeout.py`.

---

## Memory + Wiki

This project has an auto-memory at `~/.claude/projects/<your-claude-project-dir>/memory/`. **Check `MEMORY.md` before larger decisions** — stable user preferences (multi-provider brain, hybrid privacy, bilingual, anti-confirmation-fatigue, no-Anthropic-API, frontier-quality-before-cost) live there.

The Knowledge Wiki is the long-term memory tier (B0/B1/B5/B7/B8/B9 live). Three router-tier tools: `wiki-recall` (search), `wiki-page-read` (read by vault path), `wiki-ingest` (deterministic save-fact). Vault root configured in `[wiki_integration].vault_root`; default `wiki/obsidian-vault/`. Telemetry snapshot at `GET /api/wiki/telemetry`. Trigger contract in ADR-0014.

The legacy Curator-Merger is soft-disabled since 2026-05-17 (`[memory.legacy_curator] enabled = false`, gated in `jarvis/brain/factory.py`). The `data/workspace/` snapshot stays on disk and 35 reader sites (Whoami-Tool, ProfileView, OpenClaw-Workspace setup, etc.) keep rendering it — but no new writes land there. Searching the wrong path still returns frozen-in-time hits. Hart-Cut (migrate snapshot to `wiki/obsidian-vault/`, refactor readers, delete `jarvis/memory/curator/`) remains open and can be triggered by flipping `enabled = true` ↔ false during the rewrite.

---

## Worktree activation checklist

Before writing any code in a new worktree: **`pwsh scripts/preflight.ps1`**. If it exits non-zero, fix the reported issue before proceeding. Reference: BUG-006, BUG-014.

---

## Pointers (no need to inline)

- ADRs: `docs/adr/0001..0023` (0009/0010/0014 carry legacy duplicates — see `tests/unit/docs/test_adr_uniqueness.py`). **ADR-0001 (admin IPC named-pipe + HMAC) is superseded by ADR-0020** (cross-platform elevation: `AdminTransport` + `Elevator` seams; the HMAC/envelope/Pydantic-argv security core is reused unchanged; per-OS op vocabulary in `jarvis/admin/schema_unix.py`).
- Phase docs: `docs/phase{0,1,1a,1c,2,4,5,6}-*.md`.
- Architecture contracts: `docs/openclaw-bridge.md`, `docs/anti-drift-three-layer.md`.
- Operational: `scripts/README-auto-push.md`, `scripts/preflight.ps1`, `scripts/drift-guard-daemon.ps1`.
- Self-Mod: `docs/self_mod.md`.
- Bug register: `docs/BUGS.md`.

---

## Plan vs. code

On conflict, the plan wins (`~/.claude/plans/also-er-muss-auch-lexical-pond.md`, `docs/openclaw-bridge.md`). Code deviations must be documented back in the plan, not silently accepted.
