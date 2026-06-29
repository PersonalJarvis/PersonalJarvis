# CLAUDE.md

Guidance for OpenClaw (claude.ai/code) and any sub-agent working in this repository.

---

## ⚠️ THE GitHub repository — ONE public repo + mandatory privacy gate (BINDING, updated 2026-06-19)

**There is ONE project repo: the public flagship `https://github.com/PersonalJarvis/PersonalJarvis`** (PascalCase). EVERY maintainer "commit / push / save to GitHub / sichere den Stand" targets THIS repo and no other. <!-- i18n-allow: quoted German maintainer trigger phrase --> The lowercase `personal-jarvis` (git remote `origin`) is kept ONLY as a **silent local backup / safety net** — it is not "the project" and is never the deliverable. Never ask "which repo"; it is always the public flagship.

### Nothing reaches the public repo raw — the fail-closed privacy gate (MANDATORY on EVERY push)

The public repo only ever receives a **depersonalized snapshot**, never the raw working tree. Every push runs the full gate, in order, fail-closed (any uncertainty STOPS the push):

1. **Tracked-files-only export** — `.gitignore` exclusions enforced for free: no `data/`, `.env`, `jarvis.toml`, Vault, keys.
2. **Distribution denylist** — internal dev docs, scratch scripts, signing keys removed.
3. **Deterministic PII scrub** — real name, personal paths, project ids masked.
4. **🔍 Mandatory sub-agent privacy review (the maintainer's hard requirement, 2026-06-19)** — before any push, a dedicated sub-agent reads the ENTIRE staged snapshot and reports anything personal or sensitive the deterministic scanners might miss: API keys / tokens / secrets, credentials, personal data, private paths / emails, internal-only content. It may only ADD blocking findings; it can never clear what the deterministic gate blocks. A non-empty finding STOPS the push.
5. **Deterministic secret/PII scan** — fail-closed, real-credential-length regexes.
6. **Human review** — explicit maintainer go before any network write.

This whole gate is the `ship-public-release` skill; the public push runs from a separate clean clone, so the working tree is never touched and raw state cannot leak.

### Two volumes on the SAME gate

- **Discreet (DEFAULT)** — "Push nach GitHub", "push", "commit and push", "sichere den Stand", "update GitHub". → Clean snapshot through the full gate above, committed to the public repo, with **no SemVer bump, no git tag, no GitHub Release, no announcement**. Just the current clean state, quietly updated. <!-- i18n-allow: quoted German maintainer trigger phrases -->
- **Release (explicit only)** — "Neue Version shippen", "Mach ein Release", "Publish release", "veröffentliche eine neue Version". → the same gate **plus** a MAJOR/MINOR/PATCH bump + git tag + CHANGELOG entry. <!-- i18n-allow: quoted German maintainer trigger phrases -->

When the volume is ambiguous, default to **discreet** (snapshot, no version bump).

### Guardrails that stay in force

- **Never `git push` raw working state to `PersonalJarvis/PersonalJarvis`.** The pre-push guard (`scripts/ci/guard_no_raw_public_push.py` + `scripts/ci/privacy_pre_push.py`, wired into `.githooks/pre-push`, `core.hooksPath=.githooks`) HARD-BLOCKS a raw push to the public repo. It protects you precisely *because* the legitimate snapshot pushes from a separate clone — the guard only ever catches an *accidental* raw push. Do not remove it.
- The silent `origin` backup may receive raw dev commits as infrastructure, but that is never what "get my work onto GitHub" means — the deliverable is always the clean public snapshot.
- **Skill routing:** any maintainer "push / commit / save to GitHub" in this repo is the `ship-public-release` skill (discreet mode by default). `save-to-github` and `github-version` share those trigger phrases but MUST NOT run here — they would push raw state to `origin` / cut a tag, bypassing the privacy gate. They are out of scope for this repo.

Full mechanism: the `ship-public-release` skill (`.claude/skills/ship-public-release/SKILL.md`). Repo doctrine + history: [`CLOUD.md`](CLOUD.md) § "Canonical repositories".

---

## Output Language Policy (HIGHEST PRIORITY)

**Every artifact an agent produces in this repo is English.** No exceptions.

This covers code, comments, docstrings, log/exception messages, Markdown (READMEs, ADRs, plans, `BUGS.md` entries, handoffs), `SKILL.md` files, commit messages, PR titles/bodies, test names + docstrings, CLI help text, FastAPI route descriptions, error responses, JSON schema `description` fields, audit-log entries, telemetry event names, and any new UI strings (i18n key + English source — never German source).

This rule overrides historical "Sprache: Deutsch" sections in this file and any memory entry asking for German artifacts. If unsure: **default to English**.

**What stays German:** the user-facing chat reply written by the assistant in conversation (per global `~/.claude/CLAUDE.md`), TTS output (bilingual DE+EN auto-detect at runtime), and already-committed German content (do not retro-translate unless asked).

**Enforced in CI (not just documented):** the `language-policy` job in `.github/workflows/ci.yml` blocks any *newly added* German line from merging to `main` (diff-based, so the in-flight DE→EN backlog stays green). It is a required status check. Intentionally-German exceptions: an inline `i18n-allow` marker on the line, or a glob in `scripts/ci/german-allowlist.txt`. See `docs/superpowers/specs/2026-05-30-ci-language-policy-gate-design.md`.

---

## Runtime Output Language (voice + chat) — BINDING

This is the **runtime** language contract for what Jarvis *speaks and writes back to the user*. It is the sibling of the Output Language Policy above and does **not** weaken it: source artifacts stay English; the live spoken/written reply is multilingual product surface. (Forensic that motivated this section: 2026-06-18 — a German utterance was mis-transcribed by STT as English text, and because the language decision was re-derived per layer with no authoritative pin, the *entire* chain — ack preamble, answer, status line, TTS voice — went English. See `docs/BUGS.md`.)

**The rule (every supported language equally — de, en, es, and any future locale; never a German- or English-only bias):**

1. **A turn's output language is decided exactly once, by one authoritative resolver, and every output layer consumes that one decision.** The resolver is `jarvis/core/turn_language.py::resolve_output_language(reply_language, stt_language, text, *, default, conversation_language)`. Precedence, highest first:
   - an explicit **`brain.reply_language` pin** (`de`/`en`/`es`) — the user-selected language wins over everything, regardless of what STT heard;
   - else **conversation stickiness**: a *thin* turn (a one- or two-word interjection like "Now"/"Stop"/"jetzt", or a lone loanword — `is_substantive_turn`) is spoken in the running `conversation_language` and must NOT flip it; only a *substantive* turn switches the language. The `BrainManager` owns `conversation_language` (updated only on substantive turns), exposes it to the speech pipeline, and threads it — via `ExecutionContext.config["output_language"]` stamped by the tool-use loop — to deterministic tool readbacks (e.g. `computer_use` "On it"/"Done"). Forensic 2026-06-18: a lone English "Now" in a German voice chat flipped the whole turn (CU ack + status + readback) to English;
   - else the **detected input language** of the turn (text heuristic first, STT tag breaks ties — `resolve_turn_language`);
   - else the **configured default locale** (`turn_language.DEFAULT_LOCALE`) — never a per-layer hardcoded constant.
2. **The rule applies to ALL user-facing output, with no exception for "intermediate" surfaces:** the deep-brain reply, the ack-brain preamble, spawn announcements, every canned status / error / clarify / timeout / provider-down / STT-unavailable phrase, every deterministic Computer-Use / local-action readback, **and** the TTS voice / BCP-47 selection. A status or error line like "Something went wrong" must come out in the same language as the answer would have. An unprompted mid-session language switch between layers is a bug.
3. **No layer may re-derive the language on its own terms.** Specifically forbidden: a binary `"de" if _looks_german(...) else "en"` shortcut (it silently drops `es`); a phrase table keyed `de`/`en` only (an `es` speaker then hears the wrong language); a per-layer hardcoded default (`"en"` in one place, `"de"` in another → layers diverge); any spoken/written path that ignores the `brain.reply_language` pin. When you add a new spoken/written phrase table, it carries **all** supported languages, and you resolve its key through the one resolver.
4. **Honesty over guessing.** When a layer genuinely cannot determine the language, it falls back to `DEFAULT_LOCALE` — it does not invent a different default. Pure-auto mode cannot fully defend against a confident STT mis-transcription (German speech → clean English text); the durable guarantee for a single-language user is to **set `brain.reply_language`**, which pins every layer.

`brain.reply_language` is the single user-facing control (`auto` | `de` | `en` | `es`), editable from the desktop **Languages** view (`PUT /api/settings/reply-language`, hot-reloads, no restart). `auto` mirrors the spoken/written input language; a pin forces the selected language across the whole chain. Regression guards live in `tests/unit/core/test_turn_language.py`, `tests/unit/speech/test_phrase_language.py`, and the `tests/unit/brain/test_*language*.py` set — extend them whenever you touch a spoken path.

**Coverage today (2026-06-18) and the one known gap.** The deep-brain reply, the speech-pipeline output-language resolution + every canned status / error / clarify / timeout / brain-unavailable / STT-unavailable phrase, the deterministic Computer-Use / local-action readbacks, the leak-recovery + evidence-unfulfilled + provider-down honesty phrases, and the TTS voice pin all resolve through `resolve_output_language` and cover **de / en / es**. The one layer still **de / en only** is the Ack-Brain pre-thinking *preamble persona* (`jarvis/brain/ack_brain/persona_prompt.py`) and the *spawn-announcement composer* (`spawn_announcement.py`): an `es` turn there falls back (preamble → German persona; spawn → English/pool). These two are **not** a free edit — `PERSONA_PROMPT_DE/EN` are constants locked to the flash-brain spec (`docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md`), so adding a Spanish preamble persona requires amending that spec first. Bringing the Ack-Brain surfaces to `es` is the tracked follow-up to fully satisfy this doctrine; until then, an `es`-pinned user still gets the correct language everywhere except the optional sub-second preamble.

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

### Open-source universality — the maintainer's config is NEVER the baseline (BINDING)

The recurring, expensive bug class on this project is **building and testing a feature against the maintainer's own machine, keys, providers, and OS — then shipping it broken for everyone else.** The Cloud-First doctrine above (OS) and the Provider-agnostic doctrine below (brain) are two faces of ONE rule: **assume an arbitrary downloader, never the maintainer.** This rule governs the WHOLE product surface, and specifically the **entire API-Keys / credential / integration surface — not just brain providers**: STT, TTS, Vision, Wake, **Telephony (Twilio), Channels (Telegram/Discord), Marketplace plugins (OAuth + MCP), AND the credential STORAGE itself.** Every one of them must:

- **work with WHATEVER single key / login / account the user has** — no provider, model, or integration is load-bearing; a missing / empty / depleted / rate-limited one degrades or crosses to a different family with an honest message, and never bricks a core path;
- **work on EVERY OS, including a headless `python:3.11-slim` VPS with no OS keyring, no D-Bus Secret Service, no GPU, no audio, no Windows APIs** — OS-specific code is capability-gated with a graceful no-op + an extras group, never assumed;
- **be recoverable IN-APP** — entering / switching / connecting a credential, and recovering from a dead one, happens inside the app, never by hand-editing `jarvis.toml`, exporting an ENV var, or spinning up a cloud instance;
- **store credentials portably** — the OS keyring when present, else ENV/.env, else the local-file fallback (`config._ensure_keyring_backend`); a save / connect must never 500 on a host without a Secret Service.

**Definition of done (NON-NEGOTIABLE).** A change that touches config, credentials, a provider/integration, or OS-specific code is NOT "done" — and MUST NOT be claimed done — until you have verified, with a test or an honest manual trace, the THREE paths that are NOT the maintainer's:
1. **Fresh install, ONE arbitrary key** — a downloader whose only credential is for a DIFFERENT provider than the maintainer's reaches a working path (chat + voice + sub-agent + the touched feature), entirely in-app.
2. **Headless Linux** — the base `pip install` + boot + the touched feature work on `python:3.11-slim` (no OS keyring, no GPU, no audio, no Windows APIs); local-only parts degrade to a logged no-op.
3. **Cross-family fallback** — when the configured provider/integration is absent or dead, the path crosses to whatever the user actually has, or degrades honestly — it never dead-ends on the maintainer's favorite.

**"It works on my machine" is the *defect*, not the evidence.** The maintainer's RTX-5070-Ti / Windows box with Gemini + OpenRouter keys is <0.1 % of the install base; a feature proven only there is unproven. When in doubt, gate behind an extras group with a graceful no-op and lead the docs with the VPS path. (Forensic 2026-06-29: the entire API-Keys section — STT/TTS/Twilio/channels/marketplace/keyring — silently bricked on a headless VPS and for any non-Gemini single-key downloader; the doctrine existed but was brain-scoped and had no "test the non-maintainer paths" gate. See AP-22 + AP-23.)

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
| **6 — Self-Healing Worker-Critic** | ✅ | `jarvis/missions/` (event store, manager, recovery, state machine, budget, cleanup, workers, critic, kontrollierer, safety, voice, isolation). ADR-0009, `docs/phase6-*.md`. Wired into REST + voice path via `bootstrap_missions`. **Live progress (2026-06-15):** both the Codex worker and `ClaudeDirectWorker` stream stdout line-by-line; the orchestrator drain loop emits throttled `WorkerProgress` (`jarvis/missions/events.py`) → WS → `ReasoningPanel` (the chain was dormant before). **Re-run (2026-06-15):** `POST /api/missions/{id}/rerun` re-dispatches a terminal mission's stored prompt as a NEW mission linked via `parent_mission_id` ("Continue" cancelled / "Restart" failed) — the source card is untouched audit; **no state-machine or idempotency change** (deliberate, avoids AP-14). |
| **7 — Self-Mod (foundation + writer + tools)** | ✅ | `jarvis/core/self_mod/` (audit, errors, pending, registry, schema, writer). Three router-tier tools: `list_mutable_settings`, `get_config_value`, `set_config_value`. ADR + writeup in `docs/self_mod.md`. **7.5 `spawn-skill-author` IS now registered** (`pyproject.toml` → `jarvis.brain.tools.skill_authoring:SpawnSkillAuthorTool`, router-tier, `ask`; spawns the `SkillAuthoringRunner`). Generated skills land as `state="draft"` and are never auto-activated (AP-15). |
| **Awareness A0–A5** | ✅ | `jarvis/awareness/` (state, story, salience, verdichter, working_set, episode, recall_store, watchers, probes). Router-tier tools `awareness-snapshot` (A1) + `awareness-recall` (A3). ADR-0009/0010/0012. Hard rule: **never on the voice critical path**. |
| **Wiki B0/B1/B2/B3/B5/B7/B8/B9** | ✅ | `jarvis/memory/wiki/` (curator, atomic_writer, page_repository, integration, session_rollup, voice_bridge, telemetry, scheduler). Three router-tier tools: `wiki-recall`, `wiki-page-read`, `wiki-ingest`. ADR-0013/0014/0015. B2 = `docs/obsidian-setup.md` + B9-Wizard. B3 = `WikiView.tsx` + 6-endpoint `wiki_routes.py` + `wiki_ws.py` live-reload. **B4 soft-disabled** 2026-05-17 via `[memory.legacy_curator] enabled = false` — legacy Curator package + `data/workspace/` snapshot stay on disk; Hart-Cut (vault migration + reader refactor + package delete) remains open. **B6 not started**. |
| **OpenClaw bridge** | ⚙ Welle 1+4 done | `jarvis/plugins/harness/openclaw.py`, `jarvis/missions/openclaw/`. `[harness.openclaw].enabled=true`. Welle 2 (live bridge as default) + Welle 3 (live-mode wrap) open. |
| **Ack-Brain (pre-thinking)** | ✅ | `jarvis/brain/ack_brain/` — sub-second butler ACK before the deep brain replies. Gemini 3.1 Flash Lite primary, Grok fallback. UI preamble bubble. Suppress-if-fast gate at 2000ms (`[ack_brain].suppress_if_brain_faster_than_ms`). ADR-0014 (flash-brain). |
| **CLI catalog + terminal view** | ✅ | `jarvis/clis/` (catalog, installer, loader, prober, registry, risk_integration, usage_log) + `jarvis/terminal/` (cross-platform PTY via `terminal.backend.make_pty_backend` — ConPTY/`pywinpty` on Windows, `ptyprocess` on POSIX). Router tool `cli-tools` (virtual loader → one `cli_<name>` tool per connected CLI). UI views: `ClisView`, `TerminalView`. **`spawn-cli-worker` was REMOVED 2026-05-24** (dead entry point; heavy multi-step CLI work goes through `spawn-worker`, single-step through the `cli_<name>` tools — never re-add a CLI spawn tool, it is a D9 recursion vector, AP-5/AP-14). |
| **Board / Profile ("Knows-you" dashboard)** | ✅ | `jarvis/board/` (aggregator, store, achievements, evaluator, bio prompts, scheduler, `schema.sql`) → `data/board/personal.db`. Parses FlightRecorder JSONL into daily stats / personal records / 10 achievements + an anti-cliché AI-bio. Routes `board_routes.py` (`/api/board/*`) + `profile_routes.py`; views `BoardView.tsx`, `ProfileView.tsx`, `frontend/src/views/profile/`. Deterministic profile writes via the `update-profile` tool. Separate standalone service stub in `board-backend/` (FastAPI + Docker). CHANGELOG `v1.0.0-board`. |
| **Channels (Web / Telegram / Discord)** | ✅ | `jarvis/channels/` (`base.py` ChannelAdapter, `manager.py`, `bootstrap.py` `bootstrap_channels`, `chat_bridge.py`, `web.py`/`telegram.py`/`discord.py`). Bridges a DM/guild message into the normal Jarvis chat path. Entry points `web` (base), `telegram` (base dep `python-telegram-bot`), `discord` (optional `[channels]` extra, lazy-imported, graceful `ChannelStartError` when absent). Tokens via Credential Manager. |
| **Friends + Socials** | ✅ | `jarvis/friends/` (`registry.py` `FriendRegistry` on `aiosqlite`, `status_publisher.py`, `status_filter.py`, `messages.py`, `schemas.py`). Routes `friends_routes.py` + `socials_routes.py`; views `FriendsView.tsx`, `frontend/src/views/friends/` + `socials/`. Telegram channel is the live transport (F-FRIENDS F0/F1). |
| **Contacts + Telephony** | ✅ | `jarvis/contacts/` (`store.py`, `schema.py`, `notify.py`) — contacts mirror to guaranteed Wiki person pages `people/<slug>.md` on `ContactChanged` (PII stays out of the page). Tools `contact-lookup` (safe), `contact-upsert` (monitor, write), `call-contact` (ask, echo-confirm). `jarvis/telephony/` (`outbound.py`, `twiml.py`, `provisioning.py`, `security.py`, `session.py`) places real outbound calls via Twilio. **Twilio is the optional `[telephony]` extra** — routes (`telephony_routes.py`) + `TelephonyManager` degrade gracefully when absent (AD-T8). Views `TelephonyView.tsx`, `frontend/src/views/contacts/`. |
| **Marketplace plugins** | ✅ | `jarvis/marketplace/` (catalog + `auth/` OAuth + `oauth_callback_server.py` + `plugin_loader.py`/`plugin_registry.py`/`plugin_relevance.py` + `mcp_bridge.py`). The `plugin-tools` entry-point loader expands connected marketplace plugins into live brain tools. Native REST tools where a catalog transport was insufficient: `gmail` (`gmail_rest`, ask — send is consequential) + `vercel` (`vercel_rest`, monitor — read-only). Router-tier, never a spawn (AP-5/AP-14). Route `marketplace_routes.py`; views `PluginsView.tsx`, `ExtensionsView.tsx`. |
| **Workflows** | ✅ | `jarvis/workflows/` (`runner.py`, `scheduler.py`, `store.py`, `schema.sql`, `seed.py`). Imperative cron/manual-triggered multi-step pipelines (brain-prompt / harness-dispatch / shell / tool-call / speak steps) — distinct from Phase-6 *missions* (single persistent self-healing action). Route `workflows_routes.py` (CRUD); `bootstrap_workflows` on `app.state`. View `WorkflowsView.tsx`. |
| **Conductor** | ✅ | **Separate root package `conductor/`** (`api/`, `core/`, `jobs/`, `seed/`, `cli.py`) with its own SQLite store — a YAML-first agentic-workflow canvas (shell/http/agent jobs, cron/webhook/manual triggers, timeline view). Jarvis mounts the Conductor router inside its own FastAPI server → `ConductorView.tsx`. Do not confuse with **Workflows** (imperative, in-`jarvis/`) — Conductor is YAML-first and standalone-capable. |
| **Sub-Agents / Outputs** | ✅ | `jarvis/agents/registry.py` builds an in-RAM sub-agent event tree from the EventBus (OpenClaw/Brain/Tool signals; TTL-cached, no DB) → `sub_agents_routes.py` (`/api/sub-agents/tree`) + `SubAgentsView.tsx`. **Outputs** (`outputs_routes.py` + `OutputsView.tsx`) list a mission's *deliverables* from the filesystem (`<repo_parent>/sub-agents-outputs/<slug>/`, NOT a DB). An "artifact" is a `.md`/PDF/HTML/code file a worker produced. Per-artifact download (`Content-Disposition` attachment) / view (server-rendered markdown→HTML under a strict `default-src 'none'` CSP) / desktop-only reveal+open-with-default-app via `jarvis/platform/open_path.py`; native actions are off on headless/VPS (`native_file_actions` launcher flag). |
| **Frontier (model auto-switch)** | ✅ | `jarvis/brain/frontier_{resolver,autoswitch}.py` query each provider's `/v1/models` at boot, detect newer models, and propose switching `BrainProviderConfig`; the user acknowledges via a modal → `POST /api/frontier/ack`. Route `frontier_routes.py` (`/api/frontier/{pending,ack}`); cache `data/frontier_cache.json` (24h TTL). Aligns with the "frontier-quality-before-cost" user preference. |
| **Preview / Pointer / Federation** | ✅ | `jarvis/preview/registry.py` + `preview_routes.py` — registry of dev-server iframes (Vite `:5173`, Storybook) surfaced in the sidebar; paired with the `start-preview-server` / `verify-localhost` self-verification tools. `jarvis/pointer/` (`intent.py`, `context.py`, `turn.py`) — "AI Pointer": resolves the UI element under the mouse cursor via the OS accessibility tree (not a screenshot), attached only on deictic intent ("what's that", "click there"); router tool `inspect-pointer`. `federation_proxy_routes.py` — local signing proxy to the Board-federation backend (frontend has no privkey; signs with the Credential-Manager key; path-whitelist anti-traversal). |

Infra-only (no UI, consumed internally): `jarvis/hardware/detection.py` (CPU/GPU/VRAM/CUDA probe + Whisper-model sizing for the wizard / `--check`); `jarvis/orchestrator/` (currently a thin L6 seam — most supervisor logic lives in `jarvis/missions/`); `jarvis/diagnostics/`.

Still unrowed (verify with `ls jarvis/ui/web/*routes*.py` + `git log`): `chats`/`sessions`. Treat the absence of a row as "undocumented, not absent" — the filesystem + `git log -- <module>` is the source of truth.

Status drift moves fast. Verify with `git log -- <module>` rather than trusting this table. *(Table cross-checked against the working tree on 2026-06-16; the rows above were added that day after confirming each module's files + route + view exist.)*

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

#### Provider-agnostic features (no provider/model hardcoding) — BINDING

Every feature must work with **whatever brain provider the user has selected/activated**, and across all configured providers — there are five API providers (`claude-api`, `openrouter`, `openai`, `gemini`, `grok`) plus two subscription-CLI brains (`codex` over ChatGPT, `antigravity` over the Google login), and any of them may be the active one. **Never branch on a provider name or a model id to enable or disable behavior** — no `if provider == "grok"`, no hardcoded `grok-4.3`, no provider-specific code path. Gate on a **capability** instead: `supports_vision`, `supports_tools`, the runtime `can_call_tools()` (and codex's runtime `supports_vision`). If the capability you need doesn't exist yet, **add a capability flag — do not name-check the provider**.

When the active/selected provider lacks a needed capability — e.g. a text-only CLI brain (`antigravity` / `codex-CLI`) cannot see images for Computer-Use — fall through to the first **available** provider that *has* the capability, provider-agnostically and never pinned to a favorite; if none is available, degrade gracefully with an honest message. A provider or model literal may appear **only** as a documented default/fallback (a plugin's own `DEFAULT_MODEL`) or behind a runtime capability probe — never as the gate that decides whether a feature runs. The Computer-Use "no vision" incident was a *capability-flag* bug (grok's `supports_vision` was wrongly `False`) — the correct fix was to set the flag, **not** to pin Computer-Use to grok. This generalizes AP-6 (don't hardcode Claude) to **don't hardcode *any* provider** and is the operational form of the multi-provider mandate above (see AP-21).

**Open-source single-provider resilience (BINDING — extends the rule above from capability gaps to RUNTIME failures).** This is an open-source project: it must work for **any** downloader with **whatever single provider key** they happen to have. So the same fall-through is mandatory when the active/selected provider *fails at runtime* — its key is missing/empty, it is rate-limited (HTTP 429), out of credit (HTTP 402), or unreachable: the chain MUST advance to the next **available** provider in a **different family**, never retry the same dead one, never give up. **No single provider being absent or depleted may brick a core path** — and "core path" is not only the deep answer: it is the **router**, the **ack/flash** tier, the **STT (voice input)**, the **sub-agent/mission worker**, and the **mission critic**. A tier whose primary AND its fallback resolve to the same provider family is a single-provider brick (see AP-22). Build every tier's chain from the providers that *actually have a usable key at runtime* (model it on `manager._build_fallback_chain` + the pre-boot key check), never from hardcoded names. Recovery from a dead provider must be reachable **in-app**, never via a hand-edited `jarvis.toml` or a spun-up cloud instance, and a fresh download with exactly **one arbitrary** provider key must reach a working chat + voice + sub-agent path.

Ack-Brain (`jarvis/brain/ack_brain/`) emits a sub-second butler-style preamble before the deep brain replies. Suppress-if-fast gate at 2000ms keeps it out of the way when the deep brain is already fast.

**Persona / custom system prompt:** the live persona comes from `jarvis/brain/persona_loader.py`. `load_effective_persona_prompt()` returns an editable override (`data/custom_system_prompt.md`, written atomically via the Settings UI / `settings_routes.py`) when present, else the packaged `JARVIS_PERSONA.md`. Edits apply on the **next turn** (no restart); `invalidate_cache()` clears the in-process cache. Never hardcode the persona string elsewhere.

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

### Web search (`search_web`, router-tier)

The `search-web` tool (`jarvis/plugins/tool/search_web.py`) runs a **priority backend chain** in `jarvis/plugins/tool/search_backends.py` (added 2026-06-15): keyed Brave API (if a key is set) → **real DuckDuckGo SERP via the key-free `ddgs` dependency (default)** → DuckDuckGo Instant Answer (last-resort encyclopedic abstract). Backend preference is `[search].backend`; the chain stays key-free so the base VPS install still searches. **Honesty contract:** each attempt returns a `SearchOutcome` with status `ok` / `empty` / `unavailable`. `empty` = searched, genuinely nothing; `unavailable` = backend unreachable — the brain must NOT say "no results" for `unavailable`, it must say search is down. Do not re-flatten these into one reply (that was the charts/weather "found nothing" forensic). The old Instant-Answer-only backend had no real-time index, so freshness queries (charts, news, prices, sports) always came back empty.

### Atomic config writes

Mutations of `jarvis.toml` go via `jarvis/core/config_writer.py` only — `tomlkit`-based (preserves comments), `_WRITE_LOCK` mutex, BOM-aware read/write, tempfile + `os.replace`. For Phase-7 self-mod, the pipeline is **non-negotiable**: Allowlist → Read → Apply → Pre-Validate (`JarvisConfig.model_validate`) → Backup → Tempfile+replace → sync reload-test → Rollback-on-fail → `ConfigReloaded` dispatch → Backup GC → Audit (AD-5; AP-3/4/5/13/14).

Backup directory must be **outside** the watchdog scope (AP-13). Reload-test is **synchronous**, not watchdog-driven (AP-14).

### Multi-layer enum drift prevention

When a vocabulary spans Python ↔ SQL ↔ Pydantic ↔ TypeScript ↔ UI label (e.g. `HangupReason`), use the five-layer pattern from `docs/anti-drift-three-layer.md`. Reference: `jarvis/sessions/constants.py` is the single source of truth; `models.py` runtime-asserts the Pydantic `Literal` against it. Regression guards: `tests/unit/sessions/test_hangup_reason_parity.py` + `tests/integration/test_sessions_db_compatibility.py`. **BUG-008 recurred four times because this scaffolding was missing — apply it preemptively for any new wire-format enum (mission status, skill lifecycle, voice tier).**

### Phase-6 isolation invariants

- Every worker runs in a fresh `git worktree add -b agent/<task-slug>` under `<repo_parent>/sub-agents-outputs/` (≤200-char path cap). No writes to the user's working tree.
- Every worker subprocess is contained for kill-on-crash: Windows via a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (kernel guarantee — no zombies even on a hard kill); macOS/Linux via a POSIX process-group reaper (`start_new_session` + `os.killpg`-on-close) that reaps the worker tree on a clean shutdown / cancel / timeout / exception but, being userspace, leaks on a hard `kill -9` of the orchestrator itself (Linux follow-up: `PR_SET_PDEATHSIG`; no macOS equivalent).
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

## Brand mark / logo (BINDING)

**The official Jarvis logo is the Gigi GHOST mascot** — the black ghost character with glowing yellow eyes (`jarvis-gigi-256.png` == the maintainer's master `Jarvis-Logo (1).png` at the repo root, md5 `7de0a930`; also served to the frontend as `/jarvis-logo.png`). **The gold four-point star (`jarvis-mark-256.png` / `jarvis.ico` md5 `73bd5837`) is "AI-slop" the maintainer rejects — do NOT use it** as the brand mark anywhere (UI avatar, titlebar/taskbar icon, marketing, videos, intro/onboarding films). Titlebar/taskbar icon must be the ghost (`assets/icons/jarvis.ico`); sidebar avatar is `Sidebar.tsx <img src="/jarvis-logo.png">`. When a feature needs "the Jarvis logo", it is always the ghost mascot.

---

## Wake word — works with ANY user-chosen phrase (BINDING)

**The wake word is whatever the user configures (`[trigger.wake_word]`), and EVERY part of the wake path must work with ANY such phrase — never hardcode, assume, or special-case a specific wake word.** There is no built-in/trademarked default ("Hey Jarvis", "Alexa", etc. are NOT assumed); the maintainer's own is a custom phrase (e.g. "Nico"), but the code must behave identically for any phrase the user picks. This applies end-to-end: the wake-plan resolver, the OpenWakeWord vs. custom-phrase (`stt_match` rolling-Whisper) routing, the phrase matcher/verifier, AND the wake transcription itself.

Concretely, for the custom-phrase (`stt_match`) path: the rolling-Whisper wake **must transcribe in the user's wake language** (`[stt].wake_language`, default `de`) and must NOT silently auto-detect — auto-detect mis-hears a German/short wake phrase as English and mangles it (e.g. "Nico" → "cuf ich"), so the phrase never matches and the wake never fires. A wake word that the user set but that Jarvis cannot recognize is a release-blocking bug. Regression-guard any change here against at least one non-English custom phrase.

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
pytest -m integration                        # real subprocesses / live external services (Codex OAuth, OpenClaw); self-skips when prereqs missing
pytest -m "not slow"                         # fast subset
pytest -m skip_ci                            # tests that need desktop/admin/click
# NOTE: the registered markers are exactly: phase5, skip_ci, e2e, voice_latency,
# eval, slow, integration (see [tool.pytest.ini_options]). The historical
# `phase6` / `openclaw_live` markers are NOT registered and select 0 tests today —
# run those buckets by path instead (`pytest tests/missions/`, `tests/integration/`).

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
| AP-20 | `continue` (instead of `break`) a WS receive loop on an error that isn't `WebSocketDisconnect` | Unclean client disconnects raise `RuntimeError("WebSocket is not connected")`, not `WebSocketDisconnect` → the loop re-reads a dead socket forever → ~9 MB/s log storm + app self-restart that kills in-flight missions. Catch `RuntimeError` and `break` (`server.py` `_handle_ws`, fixed 1793ceaf) |
| AP-21 | Pin a feature to a provider **name** or a **model id** (`if provider == "grok"`, hardcoded `grok-4.3`, a provider-specific branch) instead of gating on a **capability** | The feature silently breaks for every other provider and for whatever provider the user selected — the multi-provider mandate (AP-6) generalized to *any* provider. Gate on `supports_vision` / `supports_tools` / `can_call_tools()`; if the flag is wrong or missing, fix/add the capability (e.g. grok's `supports_vision` was wrongly `False` → Computer-Use died "no vision"; the fix was the flag, not pinning CU to grok) |
| AP-22 | Configure a tier (router / ack / STT / TTS / worker / critic / fallback) whose primary AND its fallback resolve to the SAME provider family, or build a fallback chain from hardcoded provider NAMES instead of from the providers that actually have a usable key at runtime | Single-provider brick: one missing key / 429 / 402 / outage on that family takes the whole tier down even when the user has a healthy DIFFERENT provider. A depleted Gemini bricked the router+ack chain; the STT default (`groq-api`), the mission worker default (Claude CLI), and the Critic (claude-CLI for every non-codex provider) each bricked a fresh single-key install. Fix: resolve every tier through one key-aware chain that skips the just-failed/keyless provider, crosses to whatever family the user actually has a key for, and degrades with an honest message only when NO family is reachable. Recovery must be reachable in-app, never via a hand-edited `jarvis.toml` or a cloud instance |
| AP-23 | Build or TEST a feature only against the maintainer's own config / keys / provider / OS (e.g. "it works because I have a Gemini key on Windows") and claim it done, instead of verifying the fresh-install / one-arbitrary-key / headless-Linux / cross-family-fallback paths | The WHOLE API-Keys section (STT / TTS / Twilio / channels / marketplace / keyring) silently bricked for every other downloader and on a headless VPS: credentials couldn't even be SAVED (no writable keyring), ENV-set keys read as "not configured", channels / plugins / OAuth 500'd, and OpenRouter sent screenshots to text-only models. The maintainer's machine is <0.1 % of the install base, so "works on my machine" is the defect, not the evidence — a config / credential / provider / OS change is done only when the three non-maintainer paths in "Open-source universality" are verified (AP-22 covers the provider-chain half) |
| AP-24 | Call a shared native inference engine (ctranslate2 / faster-whisper, an ONNX / torch session) concurrently from two callers, OR "recover" a hung inference with only a timeout that re-polls the SAME wedged engine | ctranslate2's `WhisperModel.transcribe` is NOT thread-safe: the rolling-whisper wake poll loop + the VAD "listening bubble" probe sharing ONE `FasterWhisperProvider` (`pipeline._probe_stt = self._stt` for a custom phrase) hung it FOREVER — custom wake "Hey Nico" dead ~2 h, the `transcribed`/`matched` heartbeat counters frozen while `windows` climbed, "hung STT" logged every 8 s, and the un-killable `asyncio.to_thread` workers even starved the Restart button's thread pool so a soft restart could not clear it (only a hard `pythonw.exe` kill did). A hung `to_thread` call cannot be cancelled, so a timeout only BOUNDS, never RECOVERS. Fix: a NON-BLOCKING per-instance inference lock (2nd concurrent call → `TranscribeBusy`, skip — never overlap) + a `recover()` self-heal that rebuilds a FRESH model after N consecutive failures (`fwhisper._transcribe_sync` + `RollingWhisperWake`; BUG-036). |

---

## Recurring bug classes (must internalize)

Detail in [`docs/BUGS.md`](docs/BUGS.md). Eight classes recur — recognize the signal, apply the defense:

1. **Four-layer restore trap** (BUG-006 → -014 → -015): worktree + frontend build + RAM + **editable-install pin to a deleted clone**. Signal: fix "works in tests" but Jarvis behavior unchanged after restart. Defense: `pwsh scripts/preflight.ps1` + `python -c "import jarvis; print(jarvis.__file__)"`.
2. **Multi-layer enum drift** (BUG-008, 4 episodes): empty UI list while DB has rows, HTTP 500, `literal_error` in Pydantic. Defense: `docs/anti-drift-three-layer.md` pattern + parity test.
3. **Config drift** (BUG-010 triple-defense): parallel sessions rewriting `jarvis.toml`, silently rolling back provider switches. Defense: `scripts/drift-guard-daemon.ps1` (5-min cron) + ENV overrides + read-only TOML + BOM-safe writer (`UTF8Encoding($false)`).
4. **Subprocess console flicker** (BUG-012): missing `NO_WINDOW_CREATIONFLAGS` on `pythonw.exe`. Defense: every new subprocess call imports from `jarvis.core.process_utils`.
5. **Audio host-API blocking-write trap** (BUG-014): WDM-KS picked by auto-resolver, PortAudio blocking API crashes. Defense: `_FORBIDDEN_OUTPUT_HOSTAPIS` filter; shortest-unique-token device matching.
6. **Watchdog stale cross-unit counter** (BUG-032): a stall/heartbeat watchdog reads a process-global progress counter (`last_write_ns`) that is never reset per unit, so it measures idle time *between* units and fires spuriously — e.g. the TTS playback watchdog aborted a fresh answer before its first frame ("Jarvis listens forever / never speaks") whenever the brain thought longer than the 5 s stall window. Signal: a "wedge" abort whose timestamps are impossible (fires earlier than the threshold; the resource responds *after* the abort). Defense: reset the counter at unit start (before any lock wait); re-arm the "not started yet" guard per unit, not at construction; guards in `tests/unit/audio/test_player_stall_recovery.py` + `tests/unit/speech/test_speak_playback_timeout.py`.
7. **Loop on an unexpected teardown error** (AP-20, WS dead-socket): a receive/stream loop catches the *expected* clean-disconnect exception but `continue`s on any other error, so an unclean teardown that raises a *different* exception (here `RuntimeError` from FastAPI, not `WebSocketDisconnect`) spins forever — log storm + self-restart. Signal: runaway CPU/log growth + ErrorOccurred every turn after a client drops. Defense: on the socket loop, treat any read error as terminal — `break`, never `continue`.
8. **Wedged un-cancellable native inference** (BUG-036): a shared ctranslate2 / faster-whisper (or ONNX / torch) model called concurrently from two paths — the rolling-whisper wake poll loop + the VAD probe sharing `_probe_stt = _stt` — hangs unrecoverably. Every later transcribe times out and re-polls the SAME dead engine forever, the wake goes permanently silent, and the un-killable `to_thread` threads can starve other pools (even the in-app Restart button, so a soft restart doesn't help). Signal: `transcribed`/`matched` heartbeat counters FROZEN while `windows` climbs; "hung STT" every timeout; a restart that doesn't clear it. Defense: serialize the engine with a NON-BLOCKING lock (concurrent call → skip, never overlap), and self-heal a wedge by REBUILDING a fresh model object — a timeout that re-polls the same engine is a permanent dead state in disguise. Guards: `tests/unit/plugins/stt/test_fwhisper_concurrency.py` + `tests/unit/speech/test_rolling_whisper_wake.py::test_wake_self_heals_a_wedged_model_via_recover` (AP-24).

---

## Working tree is frequently SHARED (operational reality)

This repo's working tree is routinely edited by **several parallel agent sessions at once** (the MEMORY.md history is full of "shared tree", "a parallel session swept my index", "broke HEAD"). Treat the index as contested:

- **Never assume the staged diff is only yours.** Before committing, stage and commit **hunk-isolated** (`git add -p` / pathspec-scoped commits) so you don't sweep another session's in-flight work into your commit.
- A large uncommitted diff (this snapshot opened with ~65 changed files across `jarvis/`, `tests/`, frontend, docs) is the **normal** state, not a signal that something is broken. Most belong to other sessions; touch only what your task owns.
- If the index or HEAD looks corrupted by a concurrent write, the recovery pattern that has worked here is a **temp-index commit + `update-ref` CAS + safety branch** (see the custom-system-prompt and mission-rerun memory entries). Reach for `git-rescue` when it is repo-wide disorder.
- App restart is **`POST /api/settings/restart-app`**, not `Stop-Process` (which returns Access Denied under the tray `pythonw.exe`). Most uncommitted fixes are loaded via the editable install but still need this restart to take effect.

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
- Jarvis CLI (drive a running Jarvis from the terminal / an external coding agent / Jarvis itself): `docs/jarvis-cli.md` + generated `docs/jarvis-cli-reference.md`. Thin HTTP client over the REST API in `jarvis/cli_ctl/` (binaries `jarvis`/`jarvisctl`/`jctl`); the dynamic `jarvis api <tag> <op>` layer covers every mounted endpoint, curated `jarvis <group> <command>` groups add ergonomics. Registered in the CLI catalog as `jarvisctl` so Jarvis self-controls via `cli_jarvisctl` (dangerous/recursive verbs blocked). New REST routes must stay mounted — enforced by `scripts/ci/check_cli_coverage.py` (pre-push + the `jarvisctl` CI job). Skills: `.claude/skills/generate-cli-command` + `.claude/skills/drive-jarvis-cli`.

---

## Plan vs. code

On conflict, the plan wins (`~/.claude/plans/also-er-muss-auch-lexical-pond.md`, `docs/openclaw-bridge.md`). Code deviations must be documented back in the plan, not silently accepted.
