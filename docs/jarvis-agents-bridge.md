# Jarvis-Agents-Bridge — Architecture Doc

**Status:** Draft — Wave 1 (Spike) complete, Wave 2 (Implementation) next
**Date:** 2026-05-06 (Jarvis-Agent cleanup 2026-05-09, Wave-1 spike findings 2026-05-09)
**Branch:** `claude/improve-subagents-structure-5094K`
**Reference:** Fully replaces the Phase-5 Jarvis-Agent tier with a subprocess bridge to OpenClaw (github.com/openclaw/openclaw, Peter Steinberger). The Phase-6 skeleton (Mission-Manager, Critic-Loop, worktree isolation, Kontrollierer) is retained and used as a shell around the bridge.
**Conflict resolution:** This doc may deviate from ADR-0009 (Self-Healing-Worker-Critic) as far as the inner worker implementation is concerned. Mission-Manager, Critic-Loop, Kontrollierer, and worktree isolation from ADR-0009 stay unchanged.

---

## 0. Glossary — binding terms

So that this doc, the code, and the subagent files consistently speak the same language:

| Term | Meaning | What it was called before 2026-05-09 |
|---|---|---|
| **Personal Jarvis** | The overall voice-assistant product. What the user sees, hears, talks to. **Not** a subagent. | "Jarvis" |
| **Jarvis-Brain** / **Router-Brain** | Inner classifier brain (Haiku 4.5) that triages user utterances into trivial / direct_action / spawn-subagent. Lives in `jarvis/brain/manager.py` as `BrainManager` with tier `"router"`. | "Hauptjarvis" |
| **OpenClaw** | The **only** subagent. Heavy-worker for tasks that the Router-Brain delegates. External subprocess via the bridge plugin. | "Sub-Jarvis" / "Sub-Jarvis tier" |
| **Bridge** | `jarvis/plugins/harness/openclaw.py` — adapter layer that starts OpenClaw as a subprocess, passes MCPs through, parses stdout, and emits bus events. | (did not exist) |
| **Mission-Manager** | Phase-6 skeleton under `jarvis/missions/` — orchestrates worktree, Job-Object, Critic-Loop, Kontrollierer around the bridge. | (Phase 6, was installed in parallel but not in the voice path) |
| **Sub-Jarvis** | **Deprecated term.** The Phase-5 tiered-routing construct with a second Opus brain instance (`SubJarvisManager`, `from_tier_config("sub_jarvis")`). Deleted entirely in Wave 4 — no parallel operation. | (was: second Opus brain instance) |
| **Hauptjarvis** | **Deprecated term.** Was used in contrast to Sub-Jarvis. Now called **Jarvis-Brain** or **Router-Brain**. | (was: Haiku dispatcher) |

**Language rule for all follow-up edits in CLAUDE.md, AGENTS.md, subagent files, tests, and code comments:**
- "Sub-Jarvis" → replace with "OpenClaw" (when the subagent is meant) or delete (when the whole tier construct is meant).
- "Hauptjarvis" → replace with "Jarvis-Brain" or "Personal Jarvis" (depending on context).
- "Sub-Jarvis tier" → "OpenClaw-Bridge".

---

## 1. Context and Motivation

Phase 5 (master plan §22) introduced the **Jarvis-Agent tier** — a second Opus-4.7 brain instance in the same Python process, dispatched via the `spawn_sub_jarvis` tool in the Router-Brain. In parallel, Phase 6 delivered a Mission-Manager skeleton (worktree, Critic-Loop, Kontrollierer) that was, however, not wired into the default voice path. In practice, three classes of pain points emerged:

1. **"Who is answering?" bug** — the user could not reliably tell whether the Router-Brain (Haiku) directly or the Jarvis-Agent tier (Opus) had produced a result. Output quality was often so weak that it raised suspicion that the wrong tier had executed the task.
2. **Output-folder drift** — outputs did not reliably land in the designated per-task folder. Mission-reattach paths after a crash were fragile.
3. **Worker-output weakness** — even when the Jarvis-Agent tier was routed correctly, the code/reasoning quality lagged behind what established open-source agent loops (OpenClaw, Codex, Aider, OpenClaw) achieve with comparable models.

Instead of evolving these loops ourselves, **we replace the Jarvis-Agent tier entirely with OpenClaw**. OpenClaw is a TypeScript/Node-based local-first agent gateway with a tested tool-use loop, MCP support, and a CLI one-shot mode (`openclaw agent --message "..."`). We install it as an external subprocess and dock it on via the plugin layer and the Mission-Manager shell.

**Goal:** the Phase-6 skeleton (Mission-Manager, Critic, worktree, Job-Object, Kontrollierer, cost tracker) is **activated** and wired into the default voice path. The Phase-5 Jarvis-Agent tier is **deleted entirely** — no backwards compatibility, no parallel operation.

---

## 2. Architecture Decisions

| ID | Decision | Rationale |
|----|---|---|
| AD-1 | OpenClaw runs as a **one-shot subprocess per task** (`openclaw agent --message ...`) | Cleanly isolated per task, killable via Job-Object, no state leak between spawns. Cold-start latency ~2–5s is acceptable because heavy-worker tasks run >30s anyway. |
| AD-2 | OpenClaw runs **natively on Windows Node**, no WSL2 | Jarvis is native Windows (WASAPI, Win32, Credential Manager). A WSL2 layer would bring in a path-mapping + performance layer. **Needs a spike**: the README says WSL2 for Windows — we verify native operability as a pre-condition. |
| AD-3 | **Black-box model** — OpenClaw's own skills, channels, UI, voice-wake are ignored entirely | Jarvis already has all of these layers. OpenClaw is used as a pure brain engine. Duplicate state and trigger conflicts are avoided. No fork — we use upstream unchanged. |
| AD-4 | The bridge lives as a new plugin in `jarvis/plugins/harness/openclaw.py` | Fits into the existing `jarvis.harness` plugin schema alongside OpenClaw/Codex/Open-Interpreter. The Phase-6 worker calls the harness instead of an LLM client directly. Clean layer separation. |
| AD-5 | **OpenClaw is the only Jarvis-Agent.** The Phase-5 Jarvis-Agent tier (`SubJarvisManager` module, `BrainManager.from_tier_config("sub_jarvis")`, `_should_force_sub_jarvis`, `_force_spawn_sub_jarvis`, `spawn_sub_jarvis` tool) is deleted entirely in Wave 4. | No parallel operation, no backwards compatibility. All spawn requests from the Router-Brain run through the renamed `spawn_openclaw` tool → Mission-Manager → Jarvis-Agents-Bridge. **Not affected:** the OpenClaw/Codex/Open-Interpreter harness plugins stay registered, because `dispatch-to-harness` is an orthogonal mechanism (direct harness call without a Jarvis-Agent tier). Skill authoring (Phase 7.5) must be switched from SubJarvisManager to Mission-Manager spawn — see R-6. |
| AD-6 | API keys come from the Windows Credential Manager via a **new wizard section** | Single source of truth for secrets. Wizard and Desktop-App extend the `SECRETS` list with `OPENCLAW_<PROVIDER>_API_KEY`. The bridge passes those to the subprocess as ENV. |
| AD-7 | Model choice is **static in `[harness.openclaw].model`**, hot-reload via watchdog | "Everyone should be able to pick their own model." A config edit switches the frontier model without a code change. A voice switch is deliberately not added (Phase-7 self-mod dependency, can be retrofitted later). |
| AD-8 | **All registered MCPs** are passed through to OpenClaw | Convenience mode. Consequence: at registration, the MCP wizard must display "this MCP is also passed on to Jarvis-Agents". User awareness instead of an engineering filter. |
| AD-9 | **Full trust for tool calls** inside OpenClaw, no bridge intercept | The safety valves are: (a) worktree isolation, (b) Job-Object hard kill, (c) the Critic-Loop in front of it, (d) MCP selection (placed upstream). Tool latency stays low; the risk-tier policy does not apply to OpenClaw-internal calls. |
| AD-10 | OpenClaw runs **async / fire-and-forget** | "Auflegen" (hanging up) ends the voice session; OpenClaw keeps running in the background. Personal Jarvis says immediately "okay, mache ich" (okay, will do) and releases the voice mic. |
| AD-11 | **Stop is explicitly separated** from hang-up — voice + UI + auto-stop on time-cap | Voice patterns "brich ab", "stop OpenClaw", "abbrechen" (cancel/abort)  <!-- i18n-allow --> + UI button + automatic abort when the 30-min limit is exceeded. |
| AD-12 | **Status read** via voice ("läuft das noch?", "Status?" — is it still running? / status?)  <!-- i18n-allow --> plus a UI live view | The Router-Brain recognizes status phrases via pattern match and calls a Mission-Manager read instead of a spawn. The UI Mission-Control view shows everything in parallel. |
| AD-13 | **Concurrency cap 3** — up to 3 Jarvis-Agent missions in parallel | Configurable via `[harness.openclaw].concurrency`. A fourth request goes into the Mission-Manager queue. |
| AD-14 | **Crash persistence via SQLite**, reattach on restart | Phase 6 already has this. On a Jarvis restart, live OpenClaw subprocesses are reattached via PID + worktree state. Orphans (mission deleted, subprocess alive) are killed. |
| AD-15 | **Stateless per spawn** — no conversation memory between spawns | Multi-step tasks via Mission-Manager composition. OpenClaw always gets atomic assignments. |
| AD-16 | **Retry on 429, otherwise hard fail** | The Phase-5 RateLimitTracker provides backoff logic (max 3x). Crash, hallucination, worktree corruption → mission failure, the Critic sees an empty result. |
| AD-17 | **Notifications via the existing `_on_announcement` bus** | pipeline.py:647 is the bus bypass for Jarvis-Agent/skill announcements. The Jarvis-Agents-Bridge pipes the `summary_de` in there, scrubbed via `scrub_for_voice`. Reuse of existing, tested infrastructure. |
| AD-18 | **Telemetry minimal**: only start/end events on the bus | `OpenClawTaskStarted(task_id, model)` and `OpenClawTaskCompleted(task_id, cost, ms, success)`. Enough to fix the "who is answering" bug (clear identification) without bus-traffic bloat. |
| AD-19 | **Time cap fixed at 30 min**, no per-task override | Carried over from Phase 5. Auto-stop on exceeding via the Mission-Manager. |
| AD-20 | **No cost cap** in v1, the schema leaves room for retrofitting | The time cap acts as an implicit brake (~$5–15 per mission worst case). `[harness.openclaw].cost_cap_eur` stays reserved in the schema for later retrofitting. |
| AD-21 | **Pinned version** for OpenClaw, manual bump | `[harness.openclaw].version` fixes the version. New upstream versions are verified via spike + bridge tests before a bump. Protection against breakage from upstream drift. |

### Amendments from the Wave-1 spike (2026-05-09)

The following ADs are **refined** by empirical findings from `docs/spike-results-jarvis-agents.md` (B-1..B-12). The architecture remains sound; only the mechanics and assumptions are sharpened. Old table entries remain as history.

- **Amendment AD-1 (spawn pattern)** — see B-1, B-5. Against the assumption "one-shot with `openclaw agent --message ...`", three mandatory flags have been added, without which the call fails with `Error: Pass --to <E.164>, --session-id, or --agent to choose a session`:
  - `--local` (embedded agent without channel routing to Telegram/WhatsApp/etc.)
  - `--session-id <uuid>` (mandatory identifier; a new UUID per mission)
  - `--model <provider>/<model>` (otherwise default drift)
  Plus: the cold-start assumption "2–5s" is falsified — empirically **17.3s** (plugin loading 7.5s + auth 8.7s dominate). Stays under the 30s time cap because heavy tasks run >60s anyway, but the bridge should consider pre-warmed `OPENCLAW_STATE_DIR` for hot-path repetition (see AD-23).

- **Amendment AD-6 (API-key schema)** — see B-2. Against the assumption "its own `OPENCLAW_<PROVIDER>_API_KEY` namespace in the Credential Manager", OpenClaw reads the **standard provider ENV vars** like any other Anthropic/OpenAI/Gemini client:

  | jarvis.toml provider | OpenClaw slug | OpenClaw reads from ENV | Personal-Jarvis Credential-Manager key |
  |---|---|---|---|
  | `gemini` | `google` | `GEMINI_API_KEY` (fallback `GOOGLE_API_KEY`) | `gemini_api_key` |
  | `claude-api` | `anthropic` | `ANTHROPIC_API_KEY` | `anthropic_api_key` |
  | `openai` | `openai` | `OPENAI_API_KEY` | `openai_api_key` |
  | `openrouter` | `openrouter` | `OPENROUTER_API_KEY` | `openrouter_api_key` |
  | `grok` | `xai` | `XAI_API_KEY` (fallback `GROK_API_KEY`) | (Grok key) |

  Bridge mechanics: read the matching Personal-Jarvis secret key, set the ENV var OpenClaw expects in the subprocess spawn (`subprocess.Popen(env={...,"GEMINI_API_KEY": secret})` or `[System.Environment]::SetEnvironmentVariable($name, $value, "Process")`). **NO** custom `OPENCLAW_*` namespace in the wizard; instead, reuse the existing `gemini_api_key`/`anthropic_api_key`/etc. secrets from `jarvis/setup/wizard.py:SECRETS`. Plus: provider-slug mapping is critical (`gemini` → `google`, `grok` → `xai`, `claude-api` → `anthropic`).

- **Amendment AD-8 (MCP handover)** — see B-4. Against the assumption "MCPs as a `--mcp <json>` CLI argument to `openclaw agent`", there is **no** `--mcp` flag. MCP configuration runs via its own top-level subcommand `openclaw mcp <add|set|...>` and is read implicitly by the `agent --local` run from `~/.openclaw/<state>`. Bridge obligations:
  1. Pre-boot setup per mission: `openclaw mcp add <each-mcp>` for each MCP from `jarvis.mcp.registry`
  2. State isolation: set `OPENCLAW_STATE_DIR=<mission_dir>` (see AD-23) — otherwise MCP state leaks between missions
  3. Default persistence: without `OPENCLAW_STATE_DIR`, everything lands in `~/.openclaw/`, which is unsafe for parallel missions

  AD-8 otherwise stays valid (all registered MCPs passed through, user awareness instead of an engineering filter).

### New Architecture Decisions from the Wave-1 spike (AD-22..AD-24)

Three findings were outside the scope of the original ADs and are appended here as new ADs:

| ID | Decision | Rationale |
|----|---|---|
| AD-22 | **Provider idle timeout via pre-boot config patch** to 900s per provider | OpenClaw has an internal `[llm-idle-timeout]` watchdog (~264s default) that fires on frontier-premium models at full reasoning depth (`gemini-3.1-pro-preview` with default-adaptive, or Pro with `--thinking max`). Failure mode: `meta.aborted: true` + JSON payload `"The model did not produce a response before the model idle timeout. Please try again, or increase models.providers.<id>.timeoutSeconds for slow local or self-hosted providers."`. Before the first spawn per provider, the bridge must raise the timeout to 900s via `openclaw config patch --stdin` with a complete provider block (see AD-24). Empirically demonstrated by Wave-1 spike run 20260509-212000. |
| AD-23 | **Workspace isolation per mission via `OPENCLAW_STATE_DIR=<mission_dir>`** | OpenClaw automatically injects workspace files from `~/.openclaw/workspace/` (`AGENTS.md`, `SOUL.md`, `TOOLS.md`, `IDENTITY.md`, `USER.md`, ~35.4k chars total) into the system prompt of every `agent --local` run. Risk: OpenClaw's `SOUL.md`/`IDENTITY.md` overrides the Personal-Jarvis persona mandate, plus cross-mission state leak because the default path is shared. Mitigation: per mission, the bridge sets an `OPENCLAW_STATE_DIR=<mission_dir>/openclaw_state` (matches the worktree path), copies or symlinks a minimal workspace profile with only Personal-Jarvis mission context, plus an audit via `meta.systemPromptReport.injectedWorkspaceFiles[]` from the JSON output (every injected file must be expected). |
| AD-24 | **Provider config via `openclaw config patch --stdin` with a complete JSON5 block**, no incremental `config set` | The OpenClaw schema validates `models.providers.<id>` as a complete structure — a single `openclaw config set models.providers.google.timeoutSeconds 900` fails with `Config validation failed: models.providers.google.baseUrl: Invalid input: expected string, received undefined`. At pre-boot, the bridge must write a complete provider block per provider (`baseUrl`, `models[]`, `timeoutSeconds`). Cleanly via `echo $patchJson \| openclaw config patch --stdin` from Python/PowerShell. Alternatively: write a one-shot `openclaw.config.json` template into `OPENCLAW_CONFIG_PATH`. |

---

## 3. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ Voice "mach mir X"                                          │
└─────────────────┬───────────────────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Personal Jarvis Brain (Haiku 4.5, Pure Dispatcher)          │
│   = jarvis/brain/manager.py BrainManager Tier "router"      │
│   - recognizes spawn verb / external-system marker          │
│   - or: recognizes status/stop phrase via pattern match     │
└─────────────────┬───────────────────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Spawn path: spawn_openclaw(task, mode="async")             │
│ (renamed from spawn_sub_jarvis in Wave 4)                   │
│ → Mission-Manager (Phase 6, SQLite-persisted)              │
│ → Brain says immediately: "Okay, sage Bescheid wenn fertig"  <!-- i18n-allow --> │
│ → voice session freed                                       │
└─────────────────┬───────────────────────────────────────────┘
                  │  (async, max 3 in parallel)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Bridge: jarvis/plugins/harness/openclaw.py                  │
│   1. git worktree add agent/<task-id>                       │
│   2. ENV: <PROVIDER>_API_KEY = get_secret(...)             │
│      (mapping: gemini→GEMINI_API_KEY, claude-api→          │
│       ANTHROPIC_API_KEY, grok→XAI_API_KEY, ... see AD-6)   │
│      OPENCLAW_STATE_DIR = agent/<id>/openclaw_state         │
│   3. Pre-boot per provider:                                 │
│      openclaw config patch --stdin (complete block,        │
│      AD-24, with timeoutSeconds=900 for Pro models)        │
│   4. Pre-boot MCPs (AD-8 + AD-23):                          │
│      openclaw mcp add ... per registered MCP                │
│   5. Spawn (AD-1): openclaw agent                           │
│             --local                                         │
│             --session-id <task-uuid>                        │
│             --message "<task>"                              │
│             --model <provider>/<model>                      │
│             --json                                          │
│             [--verbose on] [--thinking <level>]             │
│      cwd=agent/<id> (spawner sets cwd, NO --workdir)       │
│   6. Job-Object holds the subprocess tree (Windows)         │
│      taskkill /F /T on the node.exe PID (B-7: openclaw is   │
│      a .ps1 wrapper, not a Win32 binary)                    │
│   7. stdout → openclaw.log + JSON parser:                   │
│      payloads[0].text  → voice readback (after scrub)       │
│      meta.usage.*      → CostMeter                          │
│      meta.aborted      → cancellation confirmation          │
│      meta.agentMeta.sessionId → mission trace ID            │
│   8. Audit (AD-23): meta.systemPromptReport                 │
│      .injectedWorkspaceFiles[] check                        │
│   9. Bus: OpenClawTaskStarted / OpenClawTaskCompleted       │
└─────────────────┬───────────────────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Critic-Loop (Phase 6, ADR-0009, reflexion ≤ 3 loops)        │
│ → on re-try: feedback into the next spawn                   │
│ → on pass: Kontrollierer signs summary_de                  │
└─────────────────┬───────────────────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Bus: AnnouncementRequested(summary_de)                      │
│ → pipeline._on_announcement (existing!)                     │
│ → scrub_for_voice                                           │
│ → TTS announces it (if voice is active)                     │
│ → Toast in Desktop-App (always)                             │
│ → UI badge in the Mission-Control view                      │
└─────────────────────────────────────────────────────────────┘

Parallel paths:
  Voice "Status?"     → Brain (pattern match) → Mission-Manager read
  Voice "brich ab"    → Mission-Manager.cancel(id) → Job-Object hard kill
  Personal-Jarvis crash/restart → SQLite replay → reattach via PID + worktree
```

---

## 4. Components

### 4.1 Bridge plugin (`jarvis/plugins/harness/openclaw.py`)

Implements the `jarvis.harness` protocol (see `jarvis/core/protocols.py`). Expected methods:

- `dispatch(task: HarnessTask) -> AsyncIterator[HarnessChunk]` — spawns the OpenClaw subprocess, yields exactly one result chunk for a one-shot run.
- `cancel(task_id: str) -> None` — calls the Mission-Manager cancel path, which terminates the Job-Object.

**Responsibilities** (post-Wave-1, with an empirically validated mechanics set):
- Read `[harness.openclaw]` config (model, version, time_cap_min, concurrency).
- Provider-slug mapping (AD-6 amendment): `cfg.brain.primary` → OpenClaw slug → ENV var name → Personal-Jarvis secret key.
- API-key lookup via `get_secret("<provider>_api_key", env_fallback="<PROVIDER>_API_KEY")` (standard provider convention, **not** the OpenClaw namespace).
- Pre-boot per provider (AD-22 + AD-24): `openclaw config patch --stdin` with a complete `models.providers.<slug>` block (`baseUrl`, `models[]`, `timeoutSeconds: 900`).
- Pre-boot MCPs (AD-8 amendment + AD-23): set `OPENCLAW_STATE_DIR=<mission_dir>/openclaw_state`, then `openclaw mcp add` per registered MCP against this state dir.
- Workspace isolation (AD-23): copy a minimal workspace profile into `<mission_dir>/openclaw_state/workspace/` or null out the default files, audit the `meta.systemPromptReport.injectedWorkspaceFiles[]` from the JSON output.
- Subprocess lifecycle (AD-1 amendment): `subprocess.Popen([openclaw_cmd, "agent", "--local", "--session-id", task_uuid, "--message", task, "--model", provider_slug+"/"+model, "--json"], cwd=worktree_path, env=env_with_provider_key+OPENCLAW_STATE_DIR)`.
- Job-Object (Windows) for process-tree kill via `taskkill /F /T /PID <node-PID>` (B-7: `openclaw` is a `.ps1` wrapper, not a direct Win32 binary; reference the `.cmd` variant on spawn).
- Emit bus events (`OpenClawTaskStarted/Completed`).
- Write logfile to `agent/<task-id>/openclaw.log`.
- JSON result parsing (B-6, schema in `docs/spike-results-jarvis-agents.md` SP-2): `payloads[0].text` as the voice-readback source, `meta.usage.*` as the CostMeter input, `meta.aborted` as the cancellation confirmation, `meta.agentMeta.sessionId/provider/model` as telemetry.

**Not responsible for:**
- Worktree creation (the Mission-Manager does that before the call).
- The Critic-Loop (the Mission-Manager does that after the call).
- Voice output (`_on_announcement` does that after the Critic).

### 4.2 Configuration schema

```toml
[harness.openclaw]
enabled = true
version = "1.4.2"                              # Pin to tested upstream version
binary_path = "openclaw"                       # on PATH or absolute path
model = "anthropic/claude-opus-4-7"            # Hot-reloadable
time_cap_min = 30                              # fix per AD-19
concurrency = 3                                # parallele Missions max
# cost_cap_eur =                               # AD-20 reserved, v1 leer

[harness.openclaw.notification]
via = "announcement_bus"                       # uses _on_announcement
toast = true                                   # always desktop toast
voice_when_active = true                       # voice announcement only when listening
```

**Pydantic model:** `OpenClawConfig` in `jarvis/core/config.py` alongside the existing sections. Hot-reload via the config watchdog (Phase 0, present).

### 4.3 Wizard / setup extension

**Amendment 2026-05-09 (via B-2):** **No new `OPENCLAW_*` secrets** are created in the Credential Manager. OpenClaw reads the standard provider ENV vars (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, etc.). The Personal-Jarvis wizard `SECRETS` list in `jarvis/setup/wizard.py` already contains all needed keys (`gemini_api_key`, `anthropic_api_key`, `openai_api_key`, `openrouter_api_key`). The bridge fetches them via `get_secret(...)` and sets the ENV var OpenClaw expects in the subprocess spawn. See the full mapping table at the AD-6 amendment above.

The Desktop-App `SettingsView` gets a new section **"Jarvis-Agents"** with:
- Model dropdown (extensible via config; the default is the frontier-Pro configured in `jarvis.toml [brain.providers.<primary>].deep_model`)
- **NO** per-provider API-key input field — the existing API-key fields under "Brain-Provider" are reused
- Hint text: *"All registered MCP servers are passed on to OpenClaw — via pre-boot `openclaw mcp add` with a mission-isolated state dir."*
- Provider-slug display (e.g. "Personal-Jarvis: gemini → OpenClaw: google/gemini-3.1-pro-preview") for transparency

### 4.4 Phase-5 Sub-Jarvis components to be DELETED

In Wave 4 the following components are **removed entirely** — no parallel operation, no backwards compatibility (see AD-5):

| Asset | Path | What gets removed |
|---|---|---|
| SubJarvisManager module | `jarvis/sub_jarvis/manager.py` (entire directory) | Class `SubJarvisManager`, all helpers |
| Sub-Jarvis tier configuration | `jarvis/brain/factory.py` line ~156 + lines ~395-405 | `"sub_jarvis"` entry in the tier definitions, SubJarvisManager construction and wiring |
| Force-spawn heuristic | `jarvis/brain/manager.py` `_should_force_sub_jarvis()`, `_force_spawn_sub_jarvis()`, pattern definitions | Replaced by `_should_force_openclaw()` (same patterns, new tool name) |
| Sub-Jarvis tool | `jarvis/plugins/tool/spawn_sub_jarvis.py` | File renamed to `spawn_openclaw.py`, calls the Mission-Manager instead of SubJarvisManager |
| Tier branch in `from_tier_config` | `jarvis/brain/manager.py` line ~362 | Remove the literal `"sub_jarvis"` from the type hint, the method becomes router-only |
| Sub-Jarvis events | `jarvis/missions/events.py` (or wherever `SubJarvisCompleted` is defined) | Renamed to `OpenClawTaskCompleted` (see AD-18). Achievement subscribers must be migrated along with it |
| Achievement references | `jarvis/board/{achievements,evaluator,aggregator,prompts}.py` | `SubJarvisCompleted` → `OpenClawTaskCompleted`, `sub_jarvis_success_total` → `openclaw_success_total`, adjust persona strings in `prompts.py` line ~44 |

### 4.5 Reused Phase-6 infrastructure

The following components stay unchanged and are used by the bridge:

| Asset | Path | Provides |
|---|---|---|
| Mission-Manager | `jarvis/missions/manager.py` | Spawn lifecycle, concurrency tracking, SQLite persistence |
| Event-Store | `jarvis/missions/event_store.py` | Mission-event replay after a crash |
| Worktree isolation | `jarvis/missions/isolation/` | `git worktree add agent/<id>` |
| Job-Object (Windows) | `jarvis/missions/workers/` | Subprocess-tree kill |
| Critic-Loop | `jarvis/missions/critic/` | Reflexion ≤ 3, verdict schema |
| Kontrollierer | `jarvis/missions/kontrollierer/` | summary_de signature (NEVER LLM output directly to voice) |
| Cost-Tracker | `jarvis/control/cost.py` | Per-mission cost accumulation |
| Cancel-Token | `jarvis/control/cancel.py` | Cancellation propagation |

### 4.6 Reused Phase-1 / Phase-3 infrastructure

| Asset | Path | Provides |
|---|---|---|
| Announcement-Bus | `jarvis/speech/pipeline.py:647` | Voice bypass for Jarvis-Agent/skill announcements |
| `scrub_for_voice` | `jarvis/brain/output_filter.py:193` | Tool-use-leak protection, markdown strip |
| Router-Brain | `jarvis/brain/manager.py` (tier `"router"`) | Spawn-verb recognition, status-phrase recognition (NEW for AD-12) |

---

## 5. Anti-Patterns (AP-OC1..AP-OC14)

Forbidden when building / extending the bridge — this list is also mirrored in `AGENTS.md` section 5:

- **AP-OC1 Forking OpenClaw** — we install upstream unchanged. Forks force us into maintenance hell (41k commits, very active).
- **AP-OC2 Enabling OpenClaw's frontend/UI/voice/channels** — violates the AD-3 black-box model. Duplicate state, trigger conflicts. Choose CLI flags so that only the `agent` mode runs.
- **AP-OC3 OpenClaw as a long-lived daemon** — violates AD-1. Cancellation is more complicated, state-leak risk.
- **AP-OC4 Passing LLM output directly to voice** — the Kontrollierer must sign `summary_de`. Otherwise hallucinations, sycophancy, tool-use leak in the voice path.
- **AP-OC5 Status-phrase recognition in the LLM** — must be a pattern match in the Router-Brain. Otherwise latency and hallucination ("yes of course it's still running", even though the mission is dead).
- **AP-OC6 Adding the cost cap later as a bridge layer** — when needed, that belongs in the Mission-Manager (central cost accumulation), not separately in every harness plugin.
- **AP-OC7 Placing OpenClaw skills in the user skill directory** — OpenClaw's skill system is **dead**. The skill layer is Jarvis-owned (`jarvis/skills/builtin/` + `~/.jarvis/skills/`).
- **AP-OC8 Voice switch for model choice** — violates AD-7. The Phase-7 self-mod mechanism is needed and not yet live. A manual config edit is enough for v1.
- **AP-OC9 Vetting MCP tools from inside the OpenClaw subprocess** — we chose "full trust" (AD-9). Security is placed upstream (which MCPs are registered at all) rather than downstream (tool-call filter).
- **AP-OC10 Mapping the stop command to "Auflegen" (hang up)** — violates AD-11. Hanging up lets OpenClaw keep running. Only explicit stop phrases / UI button / time cap kill the mission.
- **AP-OC11 Output folder does not exist before spawn** — `git worktree add agent/<id>` is a pre-condition for the spawn, not a downstream `os.makedirs`.
- **AP-OC12 "Who is answering?" confusion** — the bus events `OpenClawTaskStarted/Completed` must be fired with `task_id` AND `model`. Otherwise the Phase-5 bug comes back.
- **AP-OC13 Leaving the model to default** — the spawn args MUST contain `--model <value-from-config>`. If missing, OpenClaw uses its own default — cost and quality drift.
- **AP-OC14 Leaving Sub-Jarvis code alongside OpenClaw** — the Phase-5 Sub-Jarvis tier (SubJarvisManager module, force-spawn methods, `spawn_sub_jarvis` tool, tier configuration) is deleted entirely in Wave 4 (see AD-5). Backwards-compatibility attempts ("let's just leave it in case…") are a violation of AD-5 — the only Jarvis-Agent is OpenClaw.
- **AP-OC15 Not isolating the OpenClaw workspace per mission** *(new from the Wave-1 spike, B-9)* — OpenClaw automatically injects ~35.4k chars of system prompt from `~/.openclaw/workspace/{AGENTS,SOUL,TOOLS,IDENTITY,USER}.md`. Without `OPENCLAW_STATE_DIR=<mission_dir>/openclaw_state` plus a minimal workspace profile in the mission dir, OpenClaw's default persona (e.g. SOUL.md with "I am OpenClaw, an automation assistant…") leaks into the prompt — overriding the Personal-Jarvis persona mandate from `jarvis/brain/persona.py` and potentially surfacing via voice readback. Pre-spawn obligation of the bridge: workspace isolation per mission + audit via `meta.systemPromptReport.injectedWorkspaceFiles[]` from the JSON output.

---

## 6. Spike questions — Wave 1 completed 2026-05-09

Empirical findings fully documented in `docs/spike-results-jarvis-agents.md` (B-1..B-12). Summary of the SP status:

| ID | Question | Status | Finding (short) |
|----|---|---|---|
| SP-1 | Does `openclaw agent --message` run natively on Windows Node 24, without WSL2? | **RESOLVED ✅** | Yes, `OpenClaw 2026.5.7` runs natively via `npm i -g openclaw`. No WSL2 plan B needed. R-5 is dropped. |
| SP-2 | What stdout format does OpenClaw produce? | **RESOLVED ✅** | JSON document with the `--json` flag. Schema: `payloads[].text` + `meta.{durationMs,usage,aborted,agentMeta,systemPromptReport}`. Fully in spike-results-jarvis-agents.md SP-2. |
| SP-3 | Streaming behavior | **RESOLVED ✅** | `--verbose on` (the value is mandatory!) yields bracketed-prefix lines `[<source>] <event>: <kv>`, ~92 lines per run. Pattern-parseable, mid-mission bus events possible. |
| SP-4 | Model config | **RESOLVED ✅** | CLI flag `--model <provider>/<model>`. Default override does NOT work — `--local` and `--session-id` are mandatory (see AD-1 amendment). |
| SP-5 | MCP handover | **RESOLVED ✅** | NO `--mcp` flag. Instead: a separate top-level subcommand `openclaw mcp <add\|set\|...>` plus state in `~/.openclaw/` (or `OPENCLAW_STATE_DIR`). The bridge must do pre-boot setup per mission. See AD-8 amendment. |
| SP-6 | Cost tracking | **RESOLVED ✅** | `meta.usage.{input,output,cacheRead,cacheWrite,total}` in the JSON output, plus `meta.agentMeta.promptTokens`. CostMeter mapping is straightforward. |
| SP-7 | Cancellation | **PARTIALLY RESOLVED 🟡** | Mechanics documented (taskkill /F /T on node PIDs, plus `meta.aborted: true` as an indicator). Live test with Gemini Flash (8s) and Pro (idle timeout) inconclusive — Wave-2 validation with an Anthropic-Opus long task recommended. |
| SP-8 | Worktree path | **RESOLVED ✅ (negative)** | No workdir flags (`--workdir`, `--cwd`, `--working-directory` all rejected). The spawner must set `cwd=<worktree>`, plus `OPENCLAW_STATE_DIR=<mission_dir>/openclaw_state` for state isolation (AD-23). |

**Plus 12 bridge-architecture findings (B-1..B-12)** in `docs/spike-results-jarvis-agents.md` — the three findings not in the SP scope (B-8 provider idle timeout, B-9 system-prompt auto-injection, B-12 config-schema trap) are documented as new ADs AD-22..AD-24 in §2 above.

**Spike script:** `scripts/spikes/openclaw_probe.ps1` (provider-agnostic via the `Resolve-ProviderEnv` helper, default model `google/gemini-3.1-pro-preview` matches `jarvis.toml [brain.providers.gemini].deep_model`). Re-run with a different model: `-Model "anthropic/claude-opus-4-7"` (for SP-7 long-task cancellation validation).

---

## 7. Test strategy

Three test layers, all with fakes instead of `unittest.mock` (CLAUDE.md convention):

### 7.1 Contract tests (`tests/contract/test_harness_protocol.py`)

The Jarvis-Agents-Bridge is hooked into the parametrized harness contract test — the same catalog as OpenClaw, Codex, Open-Interpreter. Verifies: `dispatch` returns an AsyncIterator, `cancel` throws no exception for an unknown task_id, etc.

### 7.2 Unit tests (`tests/unit/harness/test_openclaw_bridge.py`)

Against `FakeOpenClawProcess` (scripts stdout, exit codes, timing). Covered:
- API-key lookup error paths (key missing → clear exception)
- MCP serialization (empty list, multiple MCPs, MCP with complex args)
- stdout parsing (result format, half-sentence output, empty output)
- Job-Object lifecycle (spawn → kill → cleanup)
- Bus-event emission (start/end with correct fields)
- Cancellation path (cancel token set → subprocess is terminated)
- Time-cap auto-stop (30 min exceeded → auto-kill)
- 429 retry logic (max 3x, then fail)

### 7.3 Integration tests (`tests/integration/test_openclaw_e2e.py`)

End-to-end with a real OpenClaw subprocess (local box, manual trigger via `pytest -m openclaw_live`). Test tasks:
- "Schreibe Hallo-Welt-Datei" (write a hello-world file) → worktree contains the file, summary_de is correct.
- "Brich ab nach Spawn" (abort after spawn) → mission canceled, worktree cleanup ok.
- "Crash-Recovery" → simulate a Jarvis restart, mission state recovered.
- "Concurrency-Cap" → 5 spawns in a row, max 3 run in parallel, the rest in the queue.

### 7.4 Mock bridge for headless mode

`tests/fakes/fake_openclaw_bridge.py` simulates OpenClaw without a subprocess. Produces deterministic results, used in CI. The test suite must be green in both modes.

---

## 8. Migration path

Phases for the implementation, each testably completed before the next starts:

| # | Phase | Provides | Acceptance criterion |
|---|---|---|---|
| 1 | **Spike** | `docs/spike-results-jarvis-agents.md` with empirical findings on SP-1..SP-8 | Native Windows run confirmed, stdout format documented |
| 2 | **Pydantic schema + config** | `OpenClawConfig` in `jarvis/core/config.py`, `[harness.openclaw]` in `jarvis.toml` | Hot-reload takes effect, validation throws on an invalid model spec |
| 3 | **Wizard extension** | `OPENCLAW_*_API_KEY` entries in `SECRETS`, new settings section in the Desktop-App | The setup wizard populates the Credential Manager, read via `get_secret` works |
| 4 | **Bridge plugin (mock mode)** | `jarvis/plugins/harness/openclaw.py` with `FakeOpenClawProcess` | Contract + unit tests green without real OpenClaw |
| 5 | **Bridge plugin (live mode)** | real subprocess spawn, Job-Object, stdout capture | E2E test with "Hallo-Welt-Datei" (hello-world file) green |
| 6 | **Mission-Manager wiring (Wave 4 start)** | Tool renamed `spawn_sub_jarvis.py` → `spawn_openclaw.py`, calls the Mission-Manager instead of SubJarvisManager. Activates the Phase-6 skeleton in the default voice path. | Voice command "mach mir X" (do X for me) lands at the Mission-Manager → bridge → OpenClaw, bus events flow |
| 7 | **Router-Brain extension** | Status/stop-phrase patterns in `jarvis/brain/manager.py`. `_should_force_sub_jarvis` → `_should_force_openclaw` renamed (same patterns). | "Status?" → Mission-Manager read, "brich ab" (cancel) → mission cancel. Tests in `tests/unit/brain/test_routing.py` migrated along |
| 8 | **Notification wiring** | `summary_de` from the Kontrollierer → `_on_announcement` | Voice announces the result when the mission is done (provided voice is listening) |
| 9 | **UI Mission-Control-view extension** | OpenClaw-specific columns (model, cost, logfile link) | The live view shows running OpenClaws, the stop button works |
| 10 | **Sub-Jarvis code deletion (Wave 4 main phase)** | Full list see §11 code-migration table. Module `jarvis/sub_jarvis/` gone, force-spawn methods renamed, tier definitions cleaned up, achievement events renamed, skill authoring redirected to the Mission-Manager. | Grep `sub_jarvis` and `SubJarvis` in `jarvis/` is empty (except historical comments with a migration note). Tests green |
| 11 | **End-of-day backstop** | the existing `auto-push-eod.ps1` also runs with an active mission | Push happens even when an OpenClaw worktree is dirty (exclude worktrees or warn) |

---

## 9. ADR status

This doc replaces **no** ADR. When bridge implementation starts, two new ADRs are created and two existing ones are amended:

**New ADRs:**
- **ADR-0012 Jarvis-Agents-Bridge: subprocess model** — formalizes AD-1 through AD-9 (technical architecture).
- **ADR-0013 Jarvis-Agents-Bridge: user surface** — formalizes AD-10 through AD-21 (UX / control surface).

**Existing ADRs that get amended:**
- **ADR-0009 Self-Healing-Worker-Critic** — worker internals may now be an external subprocess. The Action/Observation invariant stays unchanged (`summary_de` from the Kontrollierer source is still the only permissible voice-readback source).
- **ADR-0011 Router-Discipline** — `spawn-sub-jarvis` in the pure-dispatcher baseline becomes `spawn-openclaw`. The recursive-tools protection (D9) stays: `spawn-openclaw` and `dispatch-with-review` may NEVER land in `SUB_TOOLS`.

---

## 10. Known residual risks

- **R-1 An OpenClaw update breaks the bridge** — mitigated by the AD-21 pinned version. Before a bump: spike + bridge tests.
- **R-2 Cost explosion without a cap** — mitigated by the 30-min time cap and concurrency-3. The schema leaves room for `cost_cap_eur` retrofitting.
- **R-3 MCP side effects outside the worktree** — e.g. filesystem MCP with absolute paths, GitHub-MCP push, Drive-MCP upload. The safety valve is the deliberate MCP selection at the wizard. A UI hint at registration becomes mandatory.
- **R-4 Async-mode confusion** — the user forgets a mission is still running, closes the app, expects a result. Mitigated by toast + voice announcement on completion + Mission-Control-view persistence.
- **R-5 OpenClaw runs on Windows only via WSL2** — ~~fallback strategy: WSL2 path with path mapping~~ **DROPPED (2026-05-09):** Wave-1 spike SP-1 empirically confirmed that `OpenClaw 2026.5.7` runs natively on Windows Node 24 (`npm i -g openclaw` + `openclaw --version` responds, no WSL2 needed). Risk closed.
- **R-6 Skill authoring (Phase 7.5) hangs on the SubJarvisManager** — `jarvis/skills/authoring/runner.py` today calls SubJarvisManager directly to generate code for new skills. If Wave 4 deletes the Sub-Jarvis components without migrating the authoring path along, skill authoring breaks. Mitigation: the skill-authoring migration is a mandatory sub-phase of Phase 10 (see §8) — `runner.py` calls the Mission-Manager with task type `"skill_author"`, the Mission-Manager spawns the Jarvis-Agents-Bridge with a constrained tool set (only `file-write` in `~/.jarvis/skills/staging/`). `draft_writer.py` stays unchanged (forces `state=draft` regardless of what Sub-Jarvis produces).
- **R-7 OpenClaw workspace persona leak** *(new from the Wave-1 spike, B-9, AP-OC15)* — OpenClaw automatically injects ~35.4k chars of system prompt from `~/.openclaw/workspace/` into every `agent --local` run. Risk: persona override (OpenClaw's SOUL.md/IDENTITY.md overrides the Personal-Jarvis persona mandate), cross-mission state leak (shared default path), voice-readback drift ("I am OpenClaw"). Mitigation see AD-23: workspace isolation per mission via `OPENCLAW_STATE_DIR=<mission_dir>/openclaw_state` plus a minimal mission-workspace profile plus an audit loop via `meta.systemPromptReport.injectedWorkspaceFiles[]` from the JSON output.

---

## 11. Sub-Jarvis code-migration table

Full list of all code paths that are migrated or deleted in Wave 4 (phases 6-10 in §8):

| Path | Action | Wave-4 sub-phase | Note |
|---|---|---|---|
| `jarvis/sub_jarvis/` (entire directory) | DELETE | Phase 10 | SubJarvisManager module, all helpers |
| `jarvis/brain/manager.py` `from_tier_config(tier="sub_jarvis")` | DELETE from the literal type | Phase 10 | Tier becomes router-only |
| `jarvis/brain/manager.py` `_should_force_sub_jarvis()` | RENAME to `_should_force_openclaw()` | Phase 7 | same patterns, new tool lookup |
| `jarvis/brain/manager.py` `_force_spawn_sub_jarvis()` | RENAME to `_force_spawn_openclaw()` | Phase 7 | now calls the `spawn_openclaw` tool |
| `jarvis/brain/factory.py` tier definitions `"sub_jarvis"` | DELETE | Phase 10 | only `"router"` remains |
| `jarvis/brain/factory.py` `from jarvis.sub_jarvis.manager import SubJarvisManager` | DELETE | Phase 10 | import + all usages |
| `jarvis/plugins/tool/spawn_sub_jarvis.py` | RENAME to `spawn_openclaw.py` | Phase 6 | Body completely rewritten — calls the Mission-Manager instead of SubJarvisManager. Spawn args (post-Wave-1): `agent --local --session-id <uuid> --message <task> --model <provider>/<model> --json [--verbose on]` (see AD-1 amendment, AD-22..AD-24, no `--workdir` — `cwd=<worktree>` + `OPENCLAW_STATE_DIR=<mission_dir>/openclaw_state` via the `subprocess.Popen` env). Provider-slug mapping `cfg.brain.primary` → OpenClaw slug from the AD-6 table (`gemini→google`, `claude-api→anthropic`, etc.). |
| `jarvis/plugins/harness/openclaw.py` | CREATE NEW | Phase 4-5 | The bridge plugin itself (see §4.1). Spawn args + pre-boot setup (`config patch` + `mcp add`) + workspace isolation + JSON parser. |
| `pyproject.toml` `[project.entry-points."jarvis.tool"]` | RENAME `spawn-sub-jarvis` → `spawn-openclaw` | Phase 6 | after edit `pip install -e . --no-deps` |
| `jarvis/brain/factory.py` `ROUTER_TOOLS` frozenset | RENAME entry | Phase 6 | `spawn-sub-jarvis` → `spawn-openclaw` |
| `jarvis/missions/events.py` `SubJarvisCompleted` event | RENAME to `OpenClawTaskCompleted` | Phase 10 | Schema stays, name + subscriber lists migrate |
| `jarvis/board/achievements.py` SubJarvis references | RENAME | Phase 10 | `SubJarvisCompleted` → `OpenClawTaskCompleted`, IDs `sub_jarvis_summoner` → `openclaw_summoner` |
| `jarvis/board/evaluator.py` `sub_jarvis_success_total` | RENAME to `openclaw_success_total` | Phase 10 | migrate the persisted SQLite column along or create a new one in parallel |
| `jarvis/board/aggregator.py` event lists | RENAME | Phase 10 | `SubJarvisBackgroundCompleted` → `OpenClawBackgroundCompleted` |
| `jarvis/board/prompts.py` persona string line ~44 | REWRITE | Phase 10 | "Sub-Jarvis-Spawns" → "OpenClaw-Spawns" |
| `jarvis/skills/authoring/runner.py` SubJarvisManager spawn | CONVERT to Mission-Manager spawn | Phase 10 | see R-6 — constrained tool set for the skill-author path |
| `jarvis/skills/authoring/__init__.py` docstring | REWRITE | Phase 10 | "Sub-Jarvis (Opus 4.7) generates…" → "OpenClaw generates…" |
| `jarvis/skills/authoring/schema.py` docstring | REWRITE | Phase 10 | analogous |
| `jarvis/skills/{cli,trigger_matcher,loader,schema}.py` comments | REWRITE | Phase 10 | "Sub-Jarvis-authored" → "OpenClaw-authored" |
| `tests/unit/brain/test_routing.py` 26 test cases | MIGRATE | Phase 7 | adjust tool name + pattern tests |
| `tests/unit/test_brain_manager_tier_config.py` | MIGRATE | Phase 10 | remove `"sub_jarvis"` tier tests |
| `tests/integration/test_*sub_jarvis*` | MIGRATE/RENAME | Phase 10 | rebuilt as an E2E test against the OpenClaw-Bridge |
| `CLAUDE.md` Phase-5 section + Router-Discipline | REWRITE | Drift-Edit #2 (separate mission) | already announced |
| `AGENTS.md` section 5 header | REWRITE | Drift-Edit #3 (separate mission) | "Sub-Jarvis and OpenClaw-Bridge" → "OpenClaw-Bridge" |
| `ADR-0011` (Router-Discipline) | AMEND | Phase 10 | `spawn-sub-jarvis` → `spawn-openclaw` in the pure-dispatcher baseline |

**Consistency check after Phase 10:** `grep -ri "sub.jarvis\|SubJarvis\|sub_jarvis"` across the whole repo may only produce hits in (a) git history (commits, tags), (b) historical ADRs with status `Superseded`, and (c) this table here. Live code is sub-jarvis-free.

---

## 12. Sources

- OpenClaw repo: https://github.com/openclaw/openclaw
- OpenClaw website: https://openclaw.ai/
- Peter Steinberger's blog post: https://steipete.me/posts/2026/openclaw
- Jarvis plan: `<USER_HOME>\.claude\plans\also-er-muss-auch-lexical-pond.md` §22 (historical Sub-Jarvis-tier plan, superseded by this doc)
- ADR-0009 Self-Healing-Worker-Critic
- ADR-0011 Router-Discipline (amended in Wave 4)
- CLAUDE.md (Phase-5/6 status, plugin system, streaming, event bus — the Phase-5 Sub-Jarvis section is rewritten in Drift-Edit #2)
- `docs/spike-results-jarvis-agents.md` (Wave-1 spike findings B-1..B-12, empirical basis for AD-22..AD-24 + AD-1/AD-6/AD-8 amendments)

---

## 13. Change history

| Date | Tag | What changed |
|---|---|---|
| 2026-05-06 | initial | Doc created, AD-1..AD-21 + AP-OC1..AP-OC13 + SP-1..SP-8 + code-migration table |
| 2026-05-09 | sub-jarvis-cleanup | Glossary §0 + AD-5 tightened + AP-OC14 + code-migration table extended (Sub-Jarvis cleanup in Wave 4) |
| 2026-05-09 | wave-1-spike | Wave-1 spike completed: AD-1/AD-6/AD-8 amended (spawn pattern, API-key schema, MCP mechanics), new ADs AD-22 (provider idle timeout) + AD-23 (workspace isolation) + AD-24 (config-schema patch). AP-OC15 added (workspace persona leak). R-5 (WSL2 plan B) dropped, R-7 (workspace persona leak) added. SP-1..SP-8 marked RESOLVED (except SP-7 partial). ASCII diagram + §4.1 bridge responsibilities + §4.3 wizard updated to the empirical mechanics. Cross-ref: AGENTS.md AP-OC15 maintained along. |
