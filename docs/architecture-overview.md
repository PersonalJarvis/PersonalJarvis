# Architecture & product overview

This file holds the **reference detail** that used to live inline in `CLAUDE.md`:
the phase status table, the full layer model, the deep architecture subsections,
the optimistic-execution decisions, the cross-platform desktop ports, and the
platform/brand/wake specifics. `CLAUDE.md` keeps only the binding rules an agent
must respect on every change and links here for the depth.

Status drift moves fast. The filesystem + `git log -- <module>` is the source of
truth; verify against it rather than trusting any table below.

---

## Project

**Personal Jarvis** — voice-driven meta-orchestrator. Not a classical voice
assistant: the core pattern is a **Supervisor Agent** that routes heavy work
through the Mission Manager to capability-selected Jarvis-Agent workers. MCP
servers and marketplace integrations contribute gated tools to that same path;
they are not alternate worker harnesses. The voice layer is just the interface.

**Master plan:** `~/.claude/plans/also-er-muss-auch-lexical-pond.md` — binding for  <!-- i18n-allow -->
all design decisions. On conflict between plan and code, the plan wins; code
deviations must be documented back in the plan.

**Binding architecture contracts:**
- Realtime, chat, and voice delegate heavy work through the same
  `spawn-worker` → Mission Manager → worker → Critic/Kontrollierer lifecycle.
  The retired OpenClaw sub-tier and phantom harness paths must not be restored.
- [`docs/anti-drift-three-layer.md`](anti-drift-three-layer.md) — five-layer enum
  pattern (Python ↔ SQL ↔ Pydantic ↔ TS ↔ UI). Mandatory for any string crossing
  module boundaries.

**Bug register:** [`docs/BUGS.md`](BUGS.md). Read before larger edits — the
recurring bug classes (restore-trap, multi-layer enum drift, config drift,
subprocess flicker, audio host-API) are catalogued there.

---

## Phase Status

| Phase | Live? | Pointers |
|---|---|---|
| **0–4 — Foundations** | ✅ | Plugin system + protocols, FastAPI/React desktop app, speech pipeline, skill system, tool-use loop, risk-tier executor, core memory, harness dispatch. Detail in `docs/phase{0,1,1a,1c,2,4}-*.md`. |
| **5 — Vision/Action/Admin/Async/Control + Tiered Routing** | ✅ | `jarvis/{vision,admin,tasks,control,telemetry}/`. Computer-Use enabled. Tiered routing via `ROUTER_TOOLS` frozenset. ADR-0001..0011. |
| **6 — Self-Healing Worker-Critic** | ✅ | `jarvis/missions/` (event store, manager, recovery, state machine, budget, cleanup, workers, critic, kontrollierer, safety, voice, isolation). ADR-0009, `docs/phase6-*.md`. Wired into REST + voice path via `bootstrap_missions`. **Live progress (2026-06-15):** both the Codex worker and `ClaudeDirectWorker` stream stdout line-by-line; the orchestrator drain loop emits throttled `WorkerProgress` (`jarvis/missions/events.py`) → WS → `ReasoningPanel`. **Re-run (2026-06-15):** `POST /api/missions/{id}/rerun` re-dispatches a terminal mission's stored prompt as a NEW mission linked via `parent_mission_id` ("Continue" cancelled / "Restart" failed) — the source card is untouched audit; **no state-machine or idempotency change** (deliberate, avoids AP-14). |
| **7 — Self-Mod (foundation + writer + tools)** | ✅ | `jarvis/core/self_mod/` (audit, errors, pending, registry, schema, writer). Three router-tier tools: `list_mutable_settings`, `get_config_value`, `set_config_value`. ADR + writeup in `docs/self_mod.md`. **7.5 `spawn-skill-author` IS now registered** (`pyproject.toml` → `jarvis.brain.tools.skill_authoring:SpawnSkillAuthorTool`, router-tier, `ask`; spawns the `SkillAuthoringRunner`). Generated skills land as `state="draft"` and are never auto-activated (AP-15). |
| **Awareness A0–A5** | ✅ | `jarvis/awareness/` (state, story, salience, verdichter, working_set, episode, recall_store, watchers, probes). Router-tier tools `awareness-snapshot` (A1) + `awareness-recall` (A3). ADR-0009/0010/0012. Hard rule: **never on the voice critical path**. |
| **Wiki B0/B1/B2/B3/B5/B7/B8/B9** | ✅ | `jarvis/memory/wiki/` (curator, atomic_writer, page_repository, integration, session_rollup, voice_bridge, telemetry, scheduler). Three router-tier tools: `wiki-recall`, `wiki-page-read`, `wiki-ingest`. ADR-0013/0014/0015. B2 = `docs/obsidian-setup.md` + B9-Wizard. B3 = `WikiView.tsx` + 6-endpoint `wiki_routes.py` + `wiki_ws.py` live-reload. **B4 soft-disabled** 2026-05-17 via `[memory.legacy_curator] enabled = false` — legacy Curator package + `data/workspace/` snapshot stay on disk; Hart-Cut (vault migration + reader refactor + package delete) remains open. **B6 not started**. |
| **Realtime → Jarvis-Agents bridge** | ✅ canonical path live | Realtime emits the provider-neutral `jarvis_action`; `BrainManager` delegates heavy work through `spawn-worker`; `jarvis/missions/` owns worker selection, isolation, retries, Critic review, and Kontrollierer sign-off. MCP and marketplace tools are granted by capability, not exposed as worker harness names. |
| **Ack-Brain (pre-thinking)** | ✅ | `jarvis/brain/ack_brain/` — sub-second butler ACK before the deep brain replies. Gemini Flash Lite primary. UI preamble bubble. Suppress-if-fast gate at 2000ms (`[ack_brain].suppress_if_brain_faster_than_ms`). ADR-0014 (flash-brain). |
| **CLI catalog + terminal view** | ✅ | `jarvis/clis/` (catalog, installer, loader, prober, registry, risk_integration, usage_log) + `jarvis/terminal/` (cross-platform PTY via `terminal.backend.make_pty_backend` — ConPTY/`pywinpty` on Windows, `ptyprocess` on POSIX). Router tool `cli-tools` (virtual loader → one `cli_<name>` tool per connected CLI). UI views: `ClisView`, `TerminalView`. **`spawn-cli-worker` was REMOVED 2026-05-24** (dead entry point; heavy multi-step CLI work goes through `spawn-worker`, single-step through the `cli_<name>` tools — never re-add a CLI spawn tool, it is a D9 recursion vector, AP-5/AP-14). |
| **Board / Profile ("Knows-you" dashboard)** | ✅ | `jarvis/board/` (aggregator, store, achievements, evaluator, bio prompts, scheduler, `schema.sql`) → `data/board/personal.db`. Parses FlightRecorder JSONL into daily stats / personal records / 10 achievements + an anti-cliché AI-bio. Routes `board_routes.py` (`/api/board/*`) + `profile_routes.py`; views `BoardView.tsx`, `ProfileView.tsx`, `frontend/src/views/profile/`. Deterministic profile writes via the `update-profile` tool. Separate standalone service stub in `board-backend/` (FastAPI + Docker). CHANGELOG `v1.0.0-board`. |
| **Channels (Web / Telegram / Discord)** | ✅ | `jarvis/channels/` (`base.py` ChannelAdapter, `manager.py`, `bootstrap.py` `bootstrap_channels`, `chat_bridge.py`, `web.py`/`telegram.py`/`discord.py`). Bridges a DM/guild message into the normal Jarvis chat path. Entry points `web` (base), `telegram` (base dep `python-telegram-bot`), `discord` (optional `[channels]` extra, lazy-imported, graceful `ChannelStartError` when absent). Tokens via the OS credential store. |
| **Friends + Socials** | ✅ | `jarvis/friends/` (`registry.py` `FriendRegistry` on `aiosqlite`, `status_publisher.py`, `status_filter.py`, `messages.py`, `schemas.py`). Routes `friends_routes.py` + `socials_routes.py`; views `FriendsView.tsx`, `frontend/src/views/friends/` + `socials/`. Telegram channel is the live transport (F-FRIENDS F0/F1). |
| **Contacts + Telephony** | ✅ | `jarvis/contacts/` (`store.py`, `schema.py`, `notify.py`) — contacts mirror to guaranteed Wiki person pages `people/<slug>.md` on `ContactChanged` (PII stays out of the page). Tools `contact-lookup` (safe), `contact-upsert` (monitor, write), `call-contact` (ask, echo-confirm). `jarvis/telephony/` (`outbound.py`, `twiml.py`, `provisioning.py`, `security.py`, `session.py`) places real outbound calls via Twilio. **Twilio is the optional `[telephony]` extra** — routes (`telephony_routes.py`) + `TelephonyManager` degrade gracefully when absent (AD-T8). Views `TelephonyView.tsx`, `frontend/src/views/contacts/`. |
| **Marketplace plugins** | ✅ | `jarvis/marketplace/` (catalog + `auth/` OAuth + `oauth_callback_server.py` + `plugin_loader.py`/`plugin_registry.py`/`plugin_relevance.py` + `mcp_bridge.py`). The `plugin-tools` entry-point loader expands connected marketplace plugins into live brain tools. Native REST tools where a catalog transport was insufficient: `gmail` (`gmail_rest`, ask — send is consequential) + `vercel` (`vercel_rest`, monitor — read-only). Router-tier, never a spawn (AP-5/AP-14). Route `marketplace_routes.py`; views `PluginsView.tsx`, `ExtensionsView.tsx`. |
| **Workflows** | ✅ | `jarvis/workflows/` (`runner.py`, `scheduler.py`, `store.py`, `schema.sql`, `seed.py`). Imperative cron/manual-triggered multi-step pipelines (brain-prompt / harness-dispatch / shell / tool-call / speak steps) — distinct from Phase-6 *missions* (single persistent self-healing action). Route `workflows_routes.py` (CRUD); `bootstrap_workflows` on `app.state`. View `WorkflowsView.tsx`. |
| **Conductor** | ✅ | **Separate root package `conductor/`** (`api/`, `core/`, `jobs/`, `seed/`, `cli.py`) with its own SQLite store — a YAML-first agentic-workflow canvas (shell/http/agent jobs, cron/webhook/manual triggers, timeline view). Jarvis mounts the Conductor router inside its own FastAPI server → `ConductorView.tsx`. Do not confuse with **Workflows** (imperative, in-`jarvis/`) — Conductor is YAML-first and standalone-capable. |
| **Jarvis-Agents / Outputs** | ✅ | `jarvis/agents/registry.py` builds an in-RAM agent event tree from the EventBus (harness/Brain/Tool signals; TTL-cached, no DB) → `sub_agents_routes.py` (`/api/sub-agents/tree`) + `SubAgentsView.tsx`. **Outputs** (`outputs_routes.py` + `OutputsView.tsx`) list a mission's *deliverables* from the filesystem (`<repo_parent>/sub-agents-outputs/<slug>/`, NOT a DB). An "artifact" is a `.md`/PDF/HTML/code file a worker produced. Per-artifact download (`Content-Disposition` attachment) / view (server-rendered markdown→HTML under a strict `default-src 'none'` CSP) / desktop-only reveal+open-with-default-app via `jarvis/platform/open_path.py`; native actions are off on headless/VPS (`native_file_actions` launcher flag). |
| **Frontier (model auto-switch)** | ✅ | `jarvis/brain/frontier_{resolver,autoswitch}.py` query each provider's `/v1/models` at boot, detect newer models, and propose switching `BrainProviderConfig`; the user acknowledges via a modal → `POST /api/frontier/ack`. Route `frontier_routes.py` (`/api/frontier/{pending,ack}`); cache `data/frontier_cache.json` (24h TTL). Aligns with the "frontier-quality-before-cost" user preference. |
| **Preview / Pointer / Federation** | ✅ | `jarvis/preview/registry.py` + `preview_routes.py` — registry of dev-server iframes (Vite `:5173`, Storybook) surfaced in the sidebar; paired with the `start-preview-server` / `verify-localhost` self-verification tools. `jarvis/pointer/` (`intent.py`, `context.py`, `turn.py`) — "AI Pointer": resolves the UI element under the mouse cursor via the OS accessibility tree (not a screenshot), attached only on deictic intent ("what's that", "click there"); router tool `inspect-pointer`. `federation_proxy_routes.py` — local signing proxy to the Board-federation backend (frontend has no privkey; signs with the credential-store key; path-whitelist anti-traversal). |

Infra-only (no UI, consumed internally): `jarvis/hardware/detection.py` (CPU/GPU/VRAM/CUDA probe + Whisper-model sizing for the wizard / `--check`); `jarvis/orchestrator/` (currently a thin L6 seam — most supervisor logic lives in `jarvis/missions/`); `jarvis/diagnostics/`.

Still unrowed (verify with `ls jarvis/ui/web/*routes*.py` + `git log`): `chats`/`sessions`. Treat the absence of a row as "undocumented, not absent".

---

## Architecture (the parts an agent must respect)

### 8-Layer model

```
L7 UI/UX           Tray, Toasts, Admin-API, Desktop-App (FastAPI+React+pywebview), Orb-Overlay
L6 Orchestrator    State-Machine, Router, BrainManager, Supervisor, Mission-Manager
L5 Harness-Adapter Capability-gated Computer Use and universal Python Script
L4 Brain           5 providers (Claude-API, OpenRouter, OpenAI, Gemini, NVIDIA) + Ack-Brain sub-second tier
L3 Intent/Risk     Classifier, Risk-Tier-Policy, Approval, Rate-Limit-Tracker
L2 Speech          Wake → VAD (Silero) → STT (faster-whisper / Google) → TTS (Gemini Flash / SAPI5)
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

Access via `jarvis.core.config.get_secret(key, env_fallback)` only. Hierarchy: OS credential store (keyring, service `personal-jarvis`) → ENV → `.env` (dev fallback) → local-file fallback (`config._ensure_keyring_backend`, 0600 JSON, headless hosts with no Secret Service). The wizard (`jarvis/setup/wizard.py`) populates the store. **Never** put API keys in code, `jarvis.toml`, commits, or `.claude/` files. Voice/chat must never accept secrets (AP-2 — STT log leak vector).

### Brain providers + ack-brain

Multi-provider is mandatory — **never hardcode** Anthropic/Claude. Config under `[brain.providers.*]` in `jarvis.toml`. Runtime switch via voice ("Jarvis, switch to Gemini") is a plan requirement; `BrainManager` must support it. Smart fallback chain in `jarvis/brain/manager.py`. Workers use a `claude-cli` backend via Claude Max OAuth (user has no Anthropic API account).

#### Provider-agnostic features (no provider/model hardcoding) — BINDING

Every feature must work with **whatever brain provider the user has selected/activated**, and across all configured providers — there are five API providers (`claude-api`, `openrouter`, `openai`, `gemini`, `nvidia`) plus two subscription-CLI brains (`codex` over ChatGPT, `antigravity` over the Google login), and any of them may be the active one. **Never branch on a provider name or a model id to enable or disable behavior** — no `if provider == "grok"`, no hardcoded `grok-4.3`, no provider-specific code path. Gate on a **capability** instead: `supports_vision`, `supports_tools`, the runtime `can_call_tools()` (and codex's runtime `supports_vision`). If the capability you need doesn't exist yet, **add a capability flag — do not name-check the provider**.

When the active/selected provider lacks a needed capability — e.g. a text-only CLI brain (`antigravity` / `codex-CLI`) cannot see images for Computer-Use — fall through to the first **available** provider that *has* the capability, provider-agnostically and never pinned to a favorite; if none is available, degrade gracefully with an honest message. A provider or model literal may appear **only** as a documented default/fallback (a plugin's own `DEFAULT_MODEL`) or behind a runtime capability probe — never as the gate that decides whether a feature runs. The Computer-Use "no vision" incident was a *capability-flag* bug (grok's `supports_vision` was wrongly `False`) — the correct fix was to set the flag, **not** to pin Computer-Use to grok. This generalizes AP-6 (don't hardcode Claude) to **don't hardcode *any* provider** (see AP-21).

**Open-source single-provider resilience (BINDING).** This is an open-source project: it must work for **any** downloader with **whatever single provider key** they happen to have. So the same fall-through is mandatory when the active/selected provider *fails at runtime* — its key is missing/empty, it is rate-limited (HTTP 429), out of credit (HTTP 402), or unreachable: the chain MUST advance to the next **available** provider in a **different family**, never retry the same dead one, never give up. **No single provider being absent or depleted may brick a core path** — and "core path" is not only the deep answer: it is the **router**, the **ack/flash** tier, the **STT (voice input)**, the **Jarvis-Agent/mission worker**, and the **mission critic**. A tier whose primary AND its fallback resolve to the same provider family is a single-provider brick (see AP-22). Build every tier's chain from the providers that *actually have a usable key at runtime* (model it on `manager._build_fallback_chain` + the pre-boot key check), never from hardcoded names. Recovery from a dead provider must be reachable **in-app**, never via a hand-edited `jarvis.toml` or a spun-up cloud instance.

Ack-Brain (`jarvis/brain/ack_brain/`) emits a sub-second butler-style preamble before the deep brain replies. Suppress-if-fast gate at 2000ms keeps it out of the way when the deep brain is already fast.

**Persona / custom system prompt:** the live persona comes from `jarvis/brain/persona_loader.py`. `load_effective_persona_prompt()` returns an editable override (`data/custom_system_prompt.md`, written atomically via the Settings UI / `settings_routes.py`) when present, else the packaged `JARVIS_PERSONA.md`. Edits apply on the **next turn** (no restart); `invalidate_cache()` clears the in-process cache. Never hardcode the persona string elsewhere.

### Risk-Tier system

Four levels: `safe` / `monitor` / `ask` / `block`. Priority is **blacklist > whitelist > tool default** (`jarvis/safety/risk_tier.py`). Whitelist downgrades a tier to `safe` with `approved_by="whitelist"` — this is the anti-confirmation-fatigue contract. **Direct calls to `Tool.execute()` are a bug**; only `ToolExecutor.execute()` is authorized.

### Router discipline (binding, ADR-0011 amended)

The router-tier brain is a **pure dispatcher**. Tool surface is the `ROUTER_TOOLS` frozenset in `jarvis/brain/factory.py`. Direct actions outside this set are delegated to the agent harness via the harness-spawn tool.

Force-spawn heuristic in `BrainManager._should_force_openclaw`:
- Smalltalk allowlist wins → never spawn.
- Action verb (`lies/baue/installiere/öffne/mach/zeig` + repair words) → spawn. <!-- i18n-allow -->
- External-system marker (PR/Repo/GitHub/Issue) → spawn.

Patterns configurable under `[brain.routing]`. **The sub-tier was deleted in Welle 4 — only `"router"` remains.** Resurrecting `SUB_TOOLS` or adding the harness-spawn / `dispatch-with-review` / `run-skill` tool to any worker set breaks the D9 recursion guard. When extending `ROUTER_TOOLS`: amend ADR-0011 + extend `tests/unit/brain/test_routing.py`.

### Output filter discipline (voice path)

Brain output → TTS goes through `scrub_for_voice` in `jarvis/brain/output_filter.py` — **regex only, no LLM calls** (latency mandate). Two TTS paths are wired through scrub:
- `_handle_utterance` → `_speak()` → `tts.synthesize` (`pipeline.py:1330`).
- `_on_announcement` (skill/agent announcements, harness `summary_de` readback) (`pipeline.py:647`).

Whitelist (sacred, never scrubbed): `Datei, Email, Browser, Terminal, Notiz, Termin, Kalender`. Hyphen-compounds preserved (`Browser-Provider` stays). For the full blacklist see the module docstring and `tests/unit/brain/test_output_filter.py` (40 cases). ADR-0010.

### Web search (`search_web`, router-tier)

The `search-web` tool (`jarvis/plugins/tool/search_web.py`) runs a **priority backend chain** in `jarvis/plugins/tool/search_backends.py`: keyed Brave API (if a key is set) → **real DuckDuckGo SERP via the key-free `ddgs` dependency (default)** → DuckDuckGo Instant Answer (last-resort encyclopedic abstract). Backend preference is `[search].backend`; the chain stays key-free so the base VPS install still searches. **Honesty contract:** each attempt returns a `SearchOutcome` with status `ok` / `empty` / `unavailable`. `empty` = searched, genuinely nothing; `unavailable` = backend unreachable — the brain must NOT say "no results" for `unavailable`, it must say search is down.

### Atomic config writes

Mutations of `jarvis.toml` go via `jarvis/core/config_writer.py` only — `tomlkit`-based (preserves comments), `_WRITE_LOCK` mutex, BOM-aware read/write, tempfile + `os.replace`. For Phase-7 self-mod, the pipeline is **non-negotiable**: Allowlist → Read → Apply → Pre-Validate (`JarvisConfig.model_validate`) → Backup → Tempfile+replace → sync reload-test → Rollback-on-fail → `ConfigReloaded` dispatch → Backup GC → Audit (AD-5; AP-3/4/5/13/14). Backup directory must be **outside** the watchdog scope (AP-13). Reload-test is **synchronous**, not watchdog-driven (AP-14).

### Multi-layer enum drift prevention

When a vocabulary spans Python ↔ SQL ↔ Pydantic ↔ TypeScript ↔ UI label (e.g. `HangupReason`), use the five-layer pattern from `docs/anti-drift-three-layer.md`. Reference: `jarvis/sessions/constants.py` is the single source of truth; `models.py` runtime-asserts the Pydantic `Literal` against it. Regression guards: `tests/unit/sessions/test_hangup_reason_parity.py` + `tests/integration/test_sessions_db_compatibility.py`. **BUG-008 recurred four times because this scaffolding was missing — apply it preemptively for any new wire-format enum (mission status, skill lifecycle, voice tier).**

### Phase-6 isolation invariants

- Every worker runs in a fresh `git worktree add -b agent/<task-slug>` under `<repo_parent>/sub-agents-outputs/` (≤200-char path cap). No writes to the user's working tree.
- Every worker subprocess is contained for kill-on-crash: Windows via a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (kernel guarantee — no zombies even on a hard kill); macOS/Linux via a POSIX process-group reaper (`start_new_session` + `os.killpg`-on-close) that reaps the worker tree on a clean shutdown / cancel / timeout / exception but, being userspace, leaks on a hard `kill -9` of the orchestrator itself.
- `MAX_CRITIC_LOOPS = 3` is hardcoded. Not parameterizable. Changing requires a new ADR via `/skill phase6-adr-update`.
- Action/Observation invariant (ADR-0009): the LLM never authors its own Observation. Voice readback reads only Kontrollierer-signed `MissionApproved.summary_de`, never `correction_instruction` from the Critic-LLM.

---

## Optimistic Execution & the "Oops" Protocol (binding)

The core UX contract is **one uninterrupted spoken conversation**. The Talker (router-brain + ack-brain) acknowledges optimistically and never blocks on an MCP round-trip; the Heavy-Duty Worker (Mission-Manager + `claude-cli` Sonnet) executes in the background off the chat transcript. Reality-aligned plan + KPIs (M1–M5) + 4-wave execution: [`docs/plans/optimistic-execution-v1/README.md`](plans/optimistic-execution-v1/README.md).

### Architecture Decisions
- **AD-OE1** The optimistic ACK ("Geht klar") is emitted **before** the worker dispatch returns — never after. Audit every `_handle_utterance` return path (BUG-007/BUG-020 territory).
- **AD-OE2** The Talker never `await`s an MCP/network call on the voice path. The talker↔worker queue is the in-process `EventBus` + mission event store — no external broker.
- **AD-OE3** Dumb tools (local scripts) resolve in-process via `local_action_gate`; they MUST NOT wake the worker (false-spawn rate = 0).
- **AD-OE4** Smart tools: the **worker** issues the MCP call, never the Talker.
- **AD-OE5** Oops loop: worker failure → frozen `WorkerCorrectionNeeded` event → inject into Talker context → speak ONLY at the next Silero-VAD turn-boundary → through `scrub_for_voice`. Never interrupt mid-utterance.
- **AD-OE6** Zero silent drops: every worker/MCP failure yields a silent retry OR a spoken correction OR an audited apology (anti-BUG-020 invariant).

### Coding Standards
- Latency budgets are SLO-gated: p95 wake→ACK < 1.2 s, intent→ACK < 3.0 s, router decision < 150 ms. Regressions fail CI.
- Every spoken path (utterance + announcement) goes through `scrub_for_voice` (regex only, no LLM call — AP-11).
- New wire-format vocab (correction reasons, mission status) uses the five-layer enum pattern + parity test.
- `ROUTER_TOOLS` stays a frozenset; no spawn-tool ever enters a worker set (AP-5/AP-14). Every subprocess uses `NO_WINDOW_CREATIONFLAGS` (AP-1). Config writes go through `config_writer` (AP-7).

---

## Cross-platform desktop features (the six ports, behind `jarvis/platform/`)

The six desktop power-user features that were historically Windows-only are now **cross-platform behind the shared `jarvis/platform/` capability seam** (`detect_platform()` + a cached frozen `Capabilities` snapshot, AD-5). Each feature is one `Protocol` + one per-OS implementation + a `sys.platform` factory + a graceful logged null-fallback (AD-6); the Windows implementations are **untouched** (AD-7). The migration plan is `docs/plans/cross-platform-mac-linux/`; **ADR-0020 (cross-platform elevation) supersedes ADR-0001** but reuses the HMAC / Pydantic-argv / `shell=False` security core unchanged.

| Feature | Factory | Windows | macOS | Linux | Verification |
|---|---|---|---|---|---|
| Terminal (PTY) | `terminal.backend.make_pty_backend` | ConPTY (`pywinpty`) | `ptyprocess` | `ptyprocess` | CI-provable (real PTY, EK-4) |
| App-launch | `plugins.tool.app_resolver.resolve_app_launch_target` | App Paths | `open -a` | `xdg-open`/exec | CI-provable (resolution) |
| UI-element-click | `vision.tree_factory.make_ui_tree_source` | UIA | AX (`pyobjc`) | AT-SPI (`pyatspi`) | live sign-off (AX/AT-SPI tree) |
| Orb overlay | `overlay.surface.make_overlay_surface` | Tk color-key | Tk `-transparentcolor` | best-effort + tray | live sign-off (transparency) |
| Hotkey | `trigger.backends.make_hotkey_backend` | `global-hotkeys` | `pynput` | `pynput` (X11); Wayland no-op | live sign-off (capture) |
| CU screen indicator | `cu.indicator.controller.wire_cu_indicator` | PySide6 sidecar + capture-affinity | PySide6 sidecar + capture guard | PySide6 sidecar (X11); Wayland no-op | live sign-off (glow + Esc) |
| Admin/elevation | `admin.transport.make_admin_transport` + `admin.elevator.make_elevator` | UAC + SDDL pipe | Authorization Services + unix socket | pkexec/sudo + unix socket | live sign-off (prompt); never CI-E2E |

**Verification is honestly labelled per feature in `docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md`.** As of this writing the maintainer has Windows only and nothing is pushed, so every macOS/Linux **live** GUI/permission behavior is `unverified-on-real-desktop` and the `ci.yml` matrix is `CI-configured` (first green run pending push) — **never** claim "CI-verified" or "live-verified" until that log says so.

- **Dependency reality (AD-14 — do not "fix" this):** `pynput` + `ptyprocess` live in the `[desktop]` extra (`ptyprocess` gated `sys_platform != 'win32'`); `pyobjc-framework-{Quartz,ApplicationServices,Accessibility}` in `[desktop-macos]` (`sys_platform == 'darwin'`). **Linux `pyatspi` is NOT on PyPI — never add it as a pip dependency.** It is GObject-Introspection, distro-packaged (`apt install python3-pyatspi gir1.2-atspi-2.0`), surfaced via the `capabilities.has_ax_tree` runtime probe.
- **Doctrine intact:** the headless €5-VPS base install ships **none** of these desktop extras and still boots on a fresh `python:3.11-slim` Linux container — every port is extras-gated and degrades to a logged no-op (AD-6) when its capability is absent.
- **Computer-Use screen indicator (ADR-0028):** while a CU mission drives the
  local mouse/keyboard, a breathing Jarvis-gold border glows on every monitor
  edge with a localized "Esc to cancel" pill (Codex-style). It is a minimal
  PySide6 sidecar (`jarvis/cu/indicator`, `pyside6-essentials` in
  `[desktop]`) spawned lazily per mission off the `CUControlStarted/Ended`
  events at the `ComputerUseHarness.invoke()` boundary; a global Escape
  (existing hotkey backends, armed only while a mission runs) cancels all
  active missions through the CU-scoped token registry. Windows hides the
  border from all capture via `WDA_EXCLUDEFROMCAPTURE`; macOS/Linux blank it
  around CU's own frame grabs (fail-open). Headless/Wayland → logged no-op.
- **CPU-first device selection (ADR-0024):** the default compute device is always `cpu`. A GPU is used only on an *explicit* config request (`device = "cuda"`) with a *verified* capability; `auto`/empty/unknown resolve to CPU, and a known-bad GPU degrades to CPU with a logged warning. One central policy — `jarvis/core/device.py::resolve_device` — expresses this; the capability verdict is injected so the always-on wake path keeps its strict out-of-process inference gate (AP-25) and the policy module adds no `torch`/`ctranslate2` import to any path (AP-26).

## Windows specifics (do not skip)

- **Unicode stdout:** cp1252 default. New CLI modules must call `sys.stdout.reconfigure(encoding='utf-8')` or stick to ASCII.
- **Subprocess hygiene:** every `subprocess.*` / `asyncio.create_subprocess_exec` call must pass `creationflags=NO_WINDOW_CREATIONFLAGS` from `jarvis/core/process_utils.py`. Missing this triggers the BUG-012 flicker storm under `pythonw.exe`.
- **Audio:** WASAPI via `sounddevice`. **WDM-KS host-API is forbidden** (`_FORBIDDEN_OUTPUT_HOSTAPIS` in `jarvis/audio/player.py`) — PortAudio's blocking write API crashes there (BUG-014). Pattern-match device names on shortest unique token (`"PRO X"`), not marketing name.
- **Hotkeys:** `global-hotkeys`. Avoid `Alt+F4`, `Ctrl+C`, `Win+*`. Safe combos: `ctrl+right_alt+<letter>`.
- **No Windows Service.** SYSTEM user has no headset/mic access. Jarvis is a tray app in the user session under `pythonw.exe`; autostart via shortcut in `shell:startup`.
- **UAC manifest:** `asInvoker`. Elevate per-action, never globally.

---

## Brand mark / logo (BINDING)

**The official Jarvis logo is the Gigi GHOST mascot** — the black ghost character with glowing yellow eyes (`jarvis-gigi-256.png` == the maintainer's master `Jarvis-Logo (1).png` at the repo root, md5 `7de0a930`; also served to the frontend as `/jarvis-logo.png`). **The gold four-point star (`jarvis-mark-256.png` / `jarvis.ico` md5 `73bd5837`) is "AI-slop" the maintainer rejects — do NOT use it** as the brand mark anywhere (UI avatar, titlebar/taskbar icon, marketing, videos, intro/onboarding films). Titlebar/taskbar icon must be the ghost (`assets/icons/jarvis.ico`); sidebar avatar is `Sidebar.tsx <img src="/jarvis-logo.png">`. When a feature needs "the Jarvis logo", it is always the ghost mascot.

---

## Wake word — works with ANY user-chosen phrase (BINDING)

**The wake word is whatever the user configures (`[trigger.wake_word]`), and EVERY part of the wake path must work with ANY such phrase — never hardcode, assume, or special-case a specific wake word.** There is no built-in/trademarked default ("Hey Jarvis", "Alexa", etc. are NOT assumed); the maintainer's own is a custom phrase, but the code must behave identically for any phrase the user picks. This applies end-to-end: the wake-plan resolver, the OpenWakeWord vs. custom-phrase (`stt_match` rolling-Whisper) routing, the phrase matcher/verifier, AND the wake transcription itself.

Concretely, for the custom-phrase (`stt_match`) path: the rolling-Whisper wake **must transcribe in the user's wake language** (`[stt].wake_language`, default `de`) and must NOT silently auto-detect — auto-detect mis-hears a German/short wake phrase as English and mangles it, so the phrase never matches and the wake never fires. A wake word that the user set but that Jarvis cannot recognize is a release-blocking bug. Regression-guard any change here against at least one non-English custom phrase.

---

## Screenshots & scratch captures

**All development/verification screenshots go in `screenshots/` at the repo root** — never the repo root itself, never a random cwd. `jarvis/core/screenshots.py` defines `screenshots_dir()` plus a boot sweep (`sweep_screenshots`, wired into `SingleInstance._on_primary_claim`) that (a) consolidates any stray root-level image into `screenshots/` and (b) prunes captures older than **10 days** by mtime. App-runtime Vision frames are separate — they live in `data/flight_recorder/blobs/` and are pruned by `jarvis/telemetry/retention.py`. The folder is git-ignored and self-tidying.

---

## Memory + Wiki

This project has an auto-memory at `~/.claude/projects/<your-claude-project-dir>/memory/`. **Check `MEMORY.md` before larger decisions** — stable user preferences (multi-provider brain, hybrid privacy, bilingual, anti-confirmation-fatigue, no-Anthropic-API, frontier-quality-before-cost) live there.

The Knowledge Wiki is the long-term memory tier (B0/B1/B5/B7/B8/B9 live). Three router-tier tools: `wiki-recall` (search), `wiki-page-read` (read by vault path), `wiki-ingest` (deterministic save-fact). Vault root configured in `[wiki_integration].vault_root`; default `wiki/obsidian-vault/`. Telemetry snapshot at `GET /api/wiki/telemetry`. Trigger contract in ADR-0014.

Realtime and chat memory use a durable two-stage path. A recall-biased extractor
reviews each eligible user turn in the background and performs an overlapping,
chunked whole-session sweep at session end. Candidates carry an exact user-turn
ID plus a bounded, secret-redacted user-only evidence excerpt in
`data/jarvis.db`; assistant text can resolve a reference but is never evidence.
The body-aware consolidator is the binding cleanliness gate: it compares that
evidence with complete related pages, then chooses ADD, UPDATE, NOOP, or
INVALIDATE through the guarded AtomicWriter. Same-target candidates are
serialized, token-capped batches are bisected, transient edit conflicts remain
pending, and successful pages receive deterministic session/turn provenance.
Topic questions cannot become user-interest claims without an explicit
self-disclosure, even when a provider proposes one. Stage 2 also rejects new
numeric values absent from the candidate, exact evidence, or existing page.
An explicit remember/note/add-to-wiki request makes its supported fact binding:
a NOOP is valid only for an unchanged duplicate or unsupported evidence; the
control command itself is never stored as knowledge.
`POST /api/wiki/backfill` safely re-reviews recent persisted Realtime sessions
under policy-v3 review keys without draining unrelated journal rows.

The legacy Curator-Merger is soft-disabled since 2026-05-17 (`[memory.legacy_curator] enabled = false`, gated in `jarvis/brain/factory.py`). The `data/workspace/` snapshot stays on disk and ~35 reader sites keep rendering it — but no new writes land there. Hart-Cut (migrate snapshot to `wiki/obsidian-vault/`, refactor readers, delete `jarvis/memory/curator/`) remains open.
