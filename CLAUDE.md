# CLAUDE.md

Binding rules for any coding agent in this repo — respect them on **every**
change. Architecture and product detail live in
[`docs/architecture-overview.md`](docs/architecture-overview.md).

---

## 0. CLAUDE.md ≡ AGENTS.md and .claude/ ≡ .agents/ (mirror rule — BINDING)

`CLAUDE.md` and `AGENTS.md` are **byte-identical twins**: `CLAUDE.md` is
canonical, `AGENTS.md` is the cross-tool standard name other agents read.
Never let them drift. Sync engine: `scripts/ci/sync_agents_md.py` (live
`PostToolUse` hook + `.githooks/pre-commit --stage` + a `--check` CI gate).
After editing either file, run the sync (or copy one onto the other) before
you commit.

**The same mirror rule covers the versioned agent-knowledge trees.**
`.claude/agents/`, `.claude/commands/` and `.claude/skills/` are twins of
`.agents/agents/`, `.agents/commands/` and `.agents/skills/`: `.claude/` is
canonical, `.agents/` is the tool-neutral standard directory every other
coding agent (Codex, Gemini CLI, ...) reads. Everything in these trees —
subagent definitions, slash commands, skills — is addressed to **every**
coding agent generally, never to Claude Code alone; write it accordingly.
Sync engine: `scripts/ci/sync_agents_dir.py` (same three layers: live
`PostToolUse` hook + `.githooks/pre-commit --stage` + `--check`). Edit either
side and the other follows; deletions propagate too. Gitignored/private
entries (e.g. `.claude/skills/security-github/`) are excluded from the mirror
and must stay that way on both sides.

---

## 1. Language — English-only artifacts, fail-closed (BINDING, HIGHEST PRIORITY)

**Everything an agent commits is English — no exception outside the closed
list below.** Personal Jarvis is an international open-source project; assume
an arbitrary downloader anywhere on earth. This is English-only for artifacts,
not "English-first, German-tolerated". Any "German project" / "internationales
deutsches Projekt" label is dead, overridden here. <!-- i18n-allow: names the dead German label being retired -->

**Fail-closed default.** Every line you write *or touch* is English: code,
comments, docstrings, log/exception/error messages, Markdown (READMEs, ADRs,
plans, `BUGS.md`, handoffs, design/forensic docs), `SKILL.md`, commit messages,
PR titles/bodies, test names and docstrings, CLI help, FastAPI route
descriptions, JSON-schema `description` fields, audit-log entries, telemetry
names, UI strings (i18n key + **English** source) — unless it provably falls
inside the closed list below. When in doubt, English. "It's only a comment /
test / internal doc / quick note" is **not** an exemption — that is exactly
where German keeps leaking in. Never invent a new German-allowed category; if
you think one exists, stop and ask rather than commit the German.

**The ONLY German permitted — the multilingual *product surface* (CLOSED list):**

1. **Runtime voice/TTS + chat output** Jarvis speaks/writes back — governed by
   the per-turn resolver in the subsection below.
2. **i18n / locale *source* files + the website's localized copy** (`*/i18n/*`,
   `*/locales/*`, `*.de.json`, `*.de.ts`, etc.) — German there *is* the product.
3. **Speech-recognition *input vocabulary*** — the German tokens a classifier
   must literally contain to match German utterances (router, navigation-intent,
   local-action gate, trigger matcher). Matching *data*, not prose.
4. **Tests / fixtures + forensic voice-bug deep-dives** that quote 1–3 as the
   content under test.

Materialized file-by-file in `scripts/ci/german-allowlist.txt`. German anywhere
else is a **defect** — translate it.

- **The allowlist is a curated, justified register, never a free pass.** Every
  entry names which of the four reasons it serves. "Whole-file scope, narrow
  later" and "shared tree, do it later" are not acceptable. Prefer an inline
  `i18n-allow` on the one load-bearing German line over exempting a whole file,
  so a future English line in that file is still checked.
- **Pre-existing German is *backlog*, not protected.** The old
  "don't retro-translate / already-committed is exempt" carve-out is removed.
  No mass-rewrite mandate (a shared tree makes a giant sweep risky), but when
  you edit a file and pass non-product-surface German, **translate it on the
  way through** — never preserve, copy forward, or add more beside it.
- **The CI gate only sees what a PR diff ADDS**
  (`scripts/ci/check_no_new_german.py`) — structurally blind to the backlog,
  so the agent is the first line of defense.
- **Chat/conversation language is each contributor's own choice**, set in
  their personal global config (`~/.claude/CLAUDE.md` / `AGENTS.md`). The repo
  fixes only the artifacts. Conversation = your choice; artifacts = English,
  always.

### Runtime output language (voice + chat) — BINDING

Governs what Jarvis **speaks/writes back** (a multilingual product surface;
does NOT weaken the English-artifact rule). Supported languages are equal —
`de`, `en`, `es`, any future locale; never a German- or English-only bias.

1. **A turn's output language is decided once, by one resolver** —
   `jarvis/core/turn_language.py::resolve_output_language` — and every output
   layer consumes that decision. Precedence: explicit `brain.reply_language`
   pin → conversation stickiness (a thin one/two-word interjection stays in
   the running `conversation_language` and must NOT flip it; only a
   substantive turn switches) → detected input language → configured
   `DEFAULT_LOCALE`.
2. **Applies to ALL user-facing output** — deep reply, ack preamble, spawn
   announcements, every canned status/error/clarify/timeout/provider-down
   phrase, every deterministic Computer-Use/local-action readback, AND the
   TTS voice/BCP-47 pick. A mid-session flip between layers is a bug.
3. **No layer re-derives the language** — no `"de" if _looks_german() else
   "en"` (drops `es`), no de/en-only phrase table, no per-layer hardcoded
   default, no path that ignores the pin. New phrase tables carry **all**
   supported languages and resolve their key through the one resolver.
4. **Honesty over guessing** — a layer that genuinely can't tell falls back
   to `DEFAULT_LOCALE`. The durable single-language guarantee is
   `brain.reply_language` (`auto` | `de` | `en` | `es`), editable from the
   desktop **Languages** view. Guards:
   `tests/unit/core/test_turn_language.py`,
   `tests/unit/speech/test_phrase_language.py`,
   `tests/unit/brain/test_*language*.py`.

---

## 2. GitHub — ONE public repo, standard flow, fail-closed credential protection (BINDING)

**ONE project repo: the public flagship
`https://github.com/PersonalJarvis/PersonalJarvis`** (PascalCase). EVERY
maintainer "commit / push / save to GitHub / sichere den Stand" targets THIS
repo. <!-- i18n-allow: quoted German maintainer trigger phrase --> The
lowercase `personal-jarvis` (remote `origin`) is a **silent local backup**
only, never the deliverable. Never ask "which repo".

**Standard professional flow (maintainer directive 2026-07-17).** The former
depersonalization/snapshot gate is RETIRED: the maintainer explicitly does
not require name/path/PII scrubbing. The public repo carries the ONE shared
git history; every machine (Windows dev box, the test Mac, test machines)
pushes and pulls it normally — no rebuilt snapshot copies, no separate
histories. What remains is credential protection, **fail-closed**:

1. **`.gitignore` is the first line** — `data/`, `.env`, `jarvis.toml`, the
   Vault, and key material are never tracked.
2. **Never commit credentials** — real API keys, tokens, private keys
   (including `*.key.enc`, AP-29), passphrases. `check_no_private_keys.py`
   and the deterministic secret sweep stay wired in pre-commit + pre-push.
3. **GitHub secret scanning + push protection + validity checks are ON**
   (enabled 2026-07-17). Never disable them. A blocked push means a real
   finding: stop and fix — never bypass or allowlist around it.
4. **Trademark/brand review is a human release checkpoint** — product
   naming, logos, third-party assets. No scanner clears it; flag concerns to
   the maintainer instead of shipping them.

**Transition to the shared history (delete this block once done):** before
the FIRST direct push of local `main` to the public repo: (a) purge
`install/keys/offline-ceremony.key.enc` + `install/keys/pq-mldsa65.key.enc`
from local history (`git filter-repo --invert-paths`) — they were never
public and must not become so; (b) force-push the unified, filtered `main`
as the new public history; (c) retool `.githooks/pre-push` from raw-push
blocking (`guard_no_raw_public_push.py` / `privacy_pre_push.py`) to
credential-scan-only. Until (a)–(c) land, do NOT raw-push to the public
repo — full history verified key-clean otherwise (gitleaks, 2026-07-17).

**Two volumes:** *Update (DEFAULT)* — "push", "sichere den Stand", "update
GitHub" → normal push, **no bump / tag / release**. *Release (explicit
only)* — "Mach ein Release", "mach eine neue Version" <!-- i18n-allow: quoted German maintainer trigger phrases -->
→ SemVer bump + git tag + CHANGELOG entry.
A release always ships the ENTIRE current local state — reconcile with
`public/main` first and never knowingly ship a release lacking local fixes
without saying so. When ambiguous, default to update.

**Guardrails:** **Do not PUSH unless the maintainer asks** (local
auto-commit is fine, governed by §9). Releases are cut from the dev machine
line — a session on another machine ports its work back to `main` instead of
cutting its own releases (the 2026-07-17 two-line divergence cost a day).
Doctrine: [`CLOUD.md`](CLOUD.md).

---

## 3. Open-source universality — the maintainer's config is NEVER the baseline (BINDING)

The recurring, expensive bug class: **building/testing a feature against the
maintainer's own machine, keys, providers, and OS — then shipping it broken
for everyone else.** One rule: **assume an arbitrary downloader, never the
maintainer.** It governs the whole product surface and the **entire API-keys /
credential / integration surface — not just brain providers:** STT, TTS,
Vision, Wake, Telephony (Twilio), Channels (Telegram/Discord), Marketplace
plugins (OAuth + MCP), AND credential STORAGE itself. Every one must:

- **Work with WHATEVER single key/login the user has** — no provider, model,
  or integration is load-bearing; a missing / empty / depleted / 429 / 402 /
  unreachable one degrades or crosses to a **different family** with an honest
  message, never bricking a core path (router, ack/flash, STT, Jarvis-Agent
  worker, mission critic). Primary AND fallback in the SAME family = a
  single-provider brick (AP-22). Gate on **capability**, never a provider
  name/model id (AP-21).
- **Work on EVERY OS, incl. a headless `python:3.11-slim` VPS** with no
  keyring, D-Bus Secret Service, GPU, audio, or Windows APIs. Base
  `pip install` + boot must succeed there. OS-specific code only behind (a) a
  runtime capability check, (b) an extras group, or (c) a graceful
  English-message no-op elsewhere. Use `pathlib` + capability probes + UTF-8;
  never hardcode `C:\Users\...` or assume cp1252. **Base install stays
  torch-/GPU-free and universal**, enforced by `check_requirements_sync.py`
  (source ↔ `pyproject`) and `check_lockfile_universal.py` (compiled
  `requirements.txt` keeps its `--universal` marker, admits no
  `nvidia-*`/`torch`/CUDA/`faster-whisper` — those live only in the opt-in
  `[local-voice]` extra). Regenerate the lock only with
  `uv pip compile --universal`, never plain `pip-compile`. **The ONE
  advertised install path is the `[full]` profile** (incl. `[local-voice]`,
  design 2026-07-07); the torch-free base remains the internal floor for CI
  and tiny servers (`--headless`), never the headline path.
- **Be recoverable IN-APP** — entering / switching / connecting a credential
  and recovering from a dead one happens inside the app, never by hand-editing
  `jarvis.toml`, exporting an ENV var, or spinning up a cloud instance.
- **Store credentials portably** — OS keyring → ENV/.env → local-file fallback
  (`config._ensure_keyring_backend`). A save/connect must never 500 on a host
  without a Secret Service.

### OS feature parity — macOS and Linux are first-class (BINDING)

**Every feature ships working on ALL THREE OSes — Windows, macOS, and Linux
(desktop AND headless server) — in the SAME change, never as a "later"
follow-up.** The maintainer's Windows box is one install target among three,
never the yardstick. Concretely:

- A feature that needs an OS-specific backend (window control, screenshots,
  input injection, hotkeys, app launching, audio, autostart, notifications)
  implements **per-OS backends behind ONE capability probe** — e.g. Win32/UIA
  on Windows, AppleScript/Quartz/AXUIElement on macOS, xdotool/AT-SPI/D-Bus on
  Linux. Checking `sys.platform == "win32"` and silently doing nothing
  elsewhere is a defect, not a gate.
- Where a backend is genuinely impossible (no display on a headless server),
  the feature degrades to a **clearly-messaged English no-op** — never a
  crash, never a silent absence the user can't diagnose.
- A Windows-only implementation may land ONLY with (a) the capability gate +
  honest degradation above, AND (b) a tracked parity-gap entry in
  [`docs/os-parity.md`](docs/os-parity.md) so the gap is a visible backlog
  item, not folklore.

**Definition of done (NON-NEGOTIABLE).** A change touching config,
credentials, a provider/integration, or OS-specific code is NOT done until you
verify (test or honest manual trace) the FOUR non-maintainer paths:

1. **Fresh install, ONE arbitrary key** — a downloader whose only credential
   is for a DIFFERENT provider reaches a working path (chat + voice +
   Jarvis-Agent + the touched feature), entirely in-app.
2. **Headless Linux** — base `pip install` + boot + the feature work on
   `python:3.11-slim`; local-only parts degrade to a logged no-op.
3. **macOS** — the touched feature works on a Mac (test or honest manual
   trace: no Windows-only import, path, or API on its code path), or degrades
   there with an honest message + a `docs/os-parity.md` entry.
4. **Cross-family fallback** — when the configured provider/integration is
   absent or dead, the path crosses to whatever the user actually has, or
   degrades honestly.

**"It works on my machine" is the *defect*.** The maintainer's RTX-5070-Ti /
Windows box with Gemini + OpenRouter keys is <0.1 % of the install base. When
in doubt, gate behind an extras group with a graceful no-op and lead the docs
with the VPS path. Doctrine: [`CLOUD.md`](CLOUD.md) +
[`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) (doctrine wins). See AP-21/22/23.

---

## 4. Naming — internal system "Jarvis-Agents", user-visible brand is DYNAMIC (BINDING)

**Two layers, never mix them up (2026-07-17 rebrand):**

1. **Internal name** — the agent/mission/harness system is named
   **Jarvis-Agents** (singular **Jarvis-Agent**) in everything that is NOT
   end-user-visible: code identifiers, file/class names, docs, comments,
   commit messages, log lines, i18n KEYS, API paths. No other internal names.
2. **User-visible brand** — every surface an END USER sees or hears (UI
   labels, spoken TTS output, transcript labels, API `detail`/`label` strings
   the UI displays, tool-schema prose the router brain converts into speech)
   derives the name from the configured wake word: wake word "Hey Ruben" →
   **"Ruben-Agent(s)"**, "Harald" → "Harald-Agent(s)" — for ANY wake word,
   with the neutral **"Assistant-Agent"** fallback when none is set. NEVER
   hardcode "Jarvis-Agent" (or any fixed name) in a user-visible string.
   Plumbing: i18n token `{name}-Agent` in locale values;
   `agentBrand`/`useAgentBrand` (`src/lib/agentBrand.ts`) in TS;
   `agent_brand`/`agent_brand_from_name`
   (`jarvis/brain/assistant_name.py`) in Python. Tests must pin an arbitrary
   brand (e.g. "Nova-Agent") and must NEVER assert against the host's live
   wake-word config.

**Glossary:** formerly "Subagents" / "Sub-Agent" / "sub_jarvis" / "SubJarvis"
and "OpenClaw" / "openclaw", renamed repo-wide 2026-06-30. Surviving old-name
occurrences are intentional: the external `openclaw` npm worker binary (it
owns that name), read-time back-compat config aliases (new `[brain.worker]` /
`[harness.jarvis_agent]` still accept old `[brain.sub_jarvis]` /
`[harness.openclaw]`), and historical migration notes.

---

## 5. Architecture essentials (respect on every change)

Full model + module catalog:
[`docs/architecture-overview.md`](docs/architecture-overview.md). Rules you
cannot break:

- **8-layer dependency rule:** higher layers reach lower ones **only via
  protocols** (`jarvis/core/protocols.py`); lateral communication **only** via
  typed `frozen=True` events on `EventBus` (`jarvis/core/bus.py`) carrying
  `trace_id` + `timestamp_ns`. A broken subscriber is logged in
  `_safe_dispatch`, never propagated (AP-18).
- **Plugins are structural:** under `jarvis/plugins/<group>/<name>.py`,
  registered via `pyproject.toml` entry-points, and **must not import
  `jarvis.*`** in the plugin module. After editing entry-points:
  `pip install -e . --no-deps`. Groups: wakeword, stt, tts, brain, harness,
  tool, channel.
- **Streaming first:** all Brain/STT/TTS/Harness methods return
  `AsyncIterator[...]` (non-streaming yields one element).
- **Secrets** only via `jarvis.core.config.get_secret`: OS keyring → ENV →
  `.env` → local-file fallback. Never put keys in code / `jarvis.toml` /
  commits / `.claude/`. **Installer signing PRIVATE keys live ONLY in GitHub
  Actions secrets — never in the repo, not even encrypted; only PUBLIC keys
  ship (AP-29).** Voice/chat must never accept secrets (AP-2).
- **Brain is multi-provider** — never hardcode Anthropic/Claude (AP-6); gate
  features on a **capability** (`supports_vision`, `supports_tools`,
  `can_call_tools()`), never a provider name/model id (AP-21). Persona comes
  from `jarvis/brain/persona_loader.py` (editable override → packaged
  `JARVIS_PERSONA.md`); never hardcode it.
- **Router discipline (ADR-0011):** the router-tier brain is a pure dispatcher
  over the `ROUTER_TOOLS` frozenset (`jarvis/brain/factory.py`). No spawn tool
  ever enters a worker set (AP-5/AP-14). Extending `ROUTER_TOOLS` → amend
  ADR-0011 + `tests/unit/brain/test_routing.py`.
- **Voice scrub:** brain→TTS goes through `scrub_for_voice`
  (`jarvis/brain/output_filter.py`) — **regex only, no LLM call** (AP-11).
  ADR-0010.
- **Atomic config writes:** mutate `jarvis.toml` only via
  `jarvis/core/config_writer.py` (lock + tempfile + BOM-safe, AP-7). The
  self-mod pipeline (Allowlist → Pre-Validate → Backup → replace → sync
  reload-test → Rollback → Audit) is non-negotiable (AP-13/14).
- **CLI-first feature contract (maintainer mandate 2026-07-11):** every new
  user-facing capability ships its actions as REST routes under
  `jarvis/ui/web/*_routes.py`, mounted + tagged (enforced fail-closed by
  `scripts/ci/check_cli_coverage.py`) — which makes each action a
  `jarvis api <tag> <op>` CLI command AUTOMATICALLY, with `--json`, `--yes`
  and `--dry-run` for free. A feature that exists only in the UI, only as an
  internal function, or only as a brain tool is NOT done. On top of that
  floor: voice/agent-relevant actions add a Command-Registry entry
  (`jarvis/commands/registry.py` — becomes a flat brain tool, appears in
  `GET /api/commands`, and lands in the generated
  `docs/commands-reference.md`; drift-gated by
  `gen_commands_reference.py --check`); destructive routes declare
  `openapi_extra={"x-jarvis-dangerous": True}` (gated by
  `check_danger_metadata.py`); high-value routes get a curated
  `jarvis <group> <command>` (the `generate-cli-command` skill is the
  definition-of-done checklist).
- **Multi-layer enum drift:** any value crossing Python ↔ SQL ↔ Pydantic ↔ TS
  ↔ UI uses the five-layer pattern (`docs/anti-drift-three-layer.md`) + a
  parity test, preemptively (BUG-008 recurred 4×).
- **Worker isolation:** every mission worker runs in a fresh `git worktree`
  under `<repo_parent>/jarvis-agent-outputs/` (legacy `sub-agents-outputs/`
  still read as fallback) with kill-on-crash containment (Job Object on
  Windows; process-group reaper on POSIX). Headless installs keep both outputs
  and their per-user `HOME` under `JARVIS_DATA_DIR` (ADR-0027).
  `MAX_CRITIC_LOOPS = 3` is fixed.
- **Worker tool broker:** connected tools delegated to mission workers use a
  short-lived, mission-scoped supervisor grant (ADR-0025). Tool objects and
  credentials stay in the supervisor; every call still runs through
  `ToolExecutor`. Recursive, skill, secret, and config-mutation tools are never
  exported, and an unattended ask-tier action never becomes an implicit yes.
- **Native Windows Codex workers:** keep `--ignore-user-config`, explicitly use
  the ACL-bounded `unelevated` sandbox, and recover a rejected file-change tool
  only through BOM-free UTF-8 writes inside the current worktree (ADR-0026).
- **Platform gotchas:** UTF-8 stdout (cp1252 default on Windows); every
  subprocess passes `NO_WINDOW_CREATIONFLAGS` (AP-1); WASAPI audio, WDM-KS
  forbidden (BUG-014); no Windows Service (SYSTEM has no mic); UAC
  `asInvoker`, elevate per-action.

---

## 6. Safety / risk tiers

Four tiers: `safe` / `monitor` / `ask` / `block`, priority **blacklist >
whitelist > tool default** (`jarvis/safety/risk_tier.py`). Whitelist
downgrades to `safe` — the anti-confirmation-fatigue contract. **Direct
`Tool.execute()` is a bug; only `ToolExecutor.execute()` is authorized
(AP-3).** Generated skills land as `state="draft"` and are never
auto-activated (AP-15).

---

## 7. Critical anti-patterns (do not do this)

| # | If you do this... | ...you get this bug |
|---|---|---|
| AP-1 | Spawn `subprocess.Popen` without `NO_WINDOW_CREATIONFLAGS` | BUG-012 flicker storm under `pythonw.exe` |
| AP-2 | Accept API keys via voice/chat | STT log leak — credential exfiltration vector |
| AP-3 | Call `Tool.execute()` directly (bypassing `ToolExecutor`) | Risk-tier/whitelist/plausibility skipped |
| AP-4 | Add a new `hangup_reason`/mission-status string in one site only | BUG-008 recurrence: HTTP 500, empty UI |
| AP-5 | Put the harness-spawn / `dispatch-with-review` / `run-skill` tool in a worker tool set | D9 recursion: worker spawns supervisor, infinite loop |
| AP-6 | Hardcode `Claude`/`Anthropic` API client | User has no Anthropic account; breaks `cfg.brain.primary` |
| AP-7 | Write `jarvis.toml` without `_WRITE_LOCK` + tempfile + BOM handling | BUG-018: BOM-corrupted TOML, backend won't boot |
| AP-8 | Skip `scripts/preflight.ps1` in a new worktree | BUG-006/014: edits go to a worktree the live Python doesn't import from |
| AP-9 | Run new awareness/wiki code in the voice critical path | Latency regression — awareness is read-only, off the hot path |
| AP-10 | Write a worker without `git worktree` + Job Object | Race conditions + zombie processes on crash |
| AP-11 | Add an LLM call inside `scrub_for_voice` | TTS latency tank |
| AP-12 | Encode API keys in `jarvis.toml` or commit `.env` | Credential leak; bypasses `keyring` audit trail |
| AP-13 | Block on watchdog reload for atomic-write verification | Race: file half-applied, no sync rollback |
| AP-14 | Re-add a sub-tier or `SUB_TOOLS` set | Welle 4 deleted it; resurrection breaks the agent-harness bridge contract |
| AP-15 | Auto-activate generated skills (`state` ≠ `draft`) | Lateral-movement vector; skills run without review |
| AP-16 | Add `[phase6.*]`/`[memory.wiki.*]` keys without `ConfigDict(extra="allow")` | Pre-validate rejects → boot fails after self-mod |
| AP-17 | Run Jarvis as a Windows Service | SYSTEM has no mic/headset access |
| AP-18 | Propagate a subscriber exception from `EventBus._safe_dispatch` | One handler kills the pipeline |
| AP-19 | Reuse a process-global progress counter in a stall/heartbeat watchdog without resetting it per unit of work | BUG-032: watchdog measures the idle gap *between* turns → spuriously aborts a fresh TTS answer before its first frame |
| AP-20 | `continue` (instead of `break`) a WS receive loop on a non-`WebSocketDisconnect` error | Unclean disconnect raises `RuntimeError` → loop re-reads a dead socket forever → log storm + self-restart. Catch `RuntimeError` and `break` |
| AP-21 | Pin a feature to a provider **name** or **model id** instead of a **capability** | Silently breaks for every other provider. Gate on `supports_vision` / `supports_tools` / `can_call_tools()`; if a flag is wrong/missing, fix/add the capability — don't pin the feature |
| AP-22 | Configure a tier (router/ack/STT/TTS/worker/critic/fallback) whose primary AND fallback resolve to the SAME provider family, or build a fallback chain from hardcoded provider NAMES | Single-provider brick: one missing key / 429 / 402 / outage kills the whole tier even with a healthy DIFFERENT provider present. Resolve every tier through one key-aware chain that skips dead/keyless providers, crosses families, and degrades honestly only when NO family is reachable — recoverable in-app |
| AP-23 | Build or TEST a feature only against the maintainer's config / keys / provider / OS and claim it done | The whole API-keys surface silently bricked for every other downloader + on a headless VPS (credentials couldn't be SAVED, ENV keys read as "not configured", channels/plugins/OAuth 500'd). Verify the three non-maintainer paths in §3 |

AP-24 to AP-29 need more room than a table cell:

### AP-24 — Never share a native inference engine between concurrent callers

This covers ctranslate2/faster-whisper and ONNX/torch sessions. Equally
banned: "recovering" a hung inference with only a timeout that re-polls the
SAME wedged engine. `WhisperModel.transcribe` is NOT thread-safe: the wake poll loop + VAD probe
sharing one provider hung it forever, and a hung `to_thread` can't be
cancelled — a timeout only BOUNDS, never RECOVERS. Fix: a non-blocking
per-instance inference lock (second call → skip) + a `recover()` that rebuilds
a FRESH model after N failures (BUG-036).

### AP-25 — Never enable GPU wake on CUDA *presence* or a hardware name

The always-on wake/keyword Whisper (CTranslate2/faster-whisper) must never
move to the GPU on CUDA *presence* alone, nor be hand-pinned to a hardware
name. CUDA presence and CUDA *usability* diverge: on
the maintainer's Blackwell GPU (RTX 5070 Ti / sm_120), CTranslate2
`model.transcribe` once HUNG every inference under the then-current runtime
(8 s timeout → self-heal rebuilds the model → the cold rebuild hangs again →
wake **permanently DEAF**) while `get_cuda_device_count()` was happily > 0;
the same class hit `base`/`cpu` when ctranslate2's auto thread-pool deadlocked
against PyTorch's OpenMP in the shared process. Re-measured 2026-07-05 on the
SAME GPU (ctranslate2 4.7.1 + torch 2.11-cu128): 40/40 turbo/cuda inferences
under in-process torch-OpenMP load, zero hangs, p50 117 ms — the hang was
**constellation-specific, not "Blackwell forever"**.

Rule: gate the GPU wake upgrade on the automated out-of-process **inference
probe** (`jarvis.plugins.stt._wake_gpu_inference_verified`: one real
turbo/cuda transcribe in a killable subprocess, stdout-marker verdict — never
the exit code, CUDA teardown can die after correct work — cached per
ctranslate2 version in `data/wake_gpu_probe.json`). Keep the probe OFF the
boot path (only non-`fast_first` builds, i.e. the background hot-swap), keep a
FIXED `cpu_threads` on the CPU floor, and keep the live backstop (a wedge on
the swapped-in GPU model swaps back to the retained base/cpu fallback +
`mark_wake_gpu_bad()`). `[stt].wake_high_accuracy=false` stays the hard
opt-out. Truly instant + accurate custom-word wake still needs a trained
neural KWS model (openWakeWord `custom_onnx`), NOT transcription. See
`docs/local-wakeword/WAKE-RELIABILITY-DEEPDIVE.md`.

### AP-26 — Never put a feature's init/import on the startup critical path

Nothing initializes before `APP_INTERACTIVE` / `VOICE_USABLE`: no sync load
in `_run_backend`, the `WebServer` ctor, or `_start_speech_and_orb`, no
module-level heavy import. Boot creep: every feature "only adds a second"
until boot takes 30+ s (TTU forensic: one wake change added a 114.7 s
cascade). New subsystems hook into `_heavy_backend_bg`, a deferred registry
scan, or a post-ready task; heavy imports stay lazy or use a `warmup_prefetch`
daemon; routes answer 503/None while warming. **Enforced by the pre-push BOOT
BUDGET gate** (`scripts/ci/check_boot_budget.py`: window ≤ 8 s, voice-usable +
app-interactive ≤ 20 s). Doctrine: `docs/diagnostics/BOOT-TTU-NOTES.md`.

### AP-27 — Never suppress wake ghosts by tightening transcript content

For `stt_match` wake ghosts that means: never require the bias-echo confirm's
unbiased pass to reproduce the wake word, never raise phrase-matcher
strictness on the confirm, never add any second content check that must
contain the phrase. That kills recognition ENTIRELY: "fires on
silence" flips to "never fires". A bias-primed local Whisper
(`initial_prompt=<phrase>`, needed for proper-noun recall) HALLUCINATES the
phrase on silence AND GARBLES it on speech
(`'Mythos'`→`'Mütos'`/`'Hey, Mut!'`, `'Fable'`→`'Farbe'`); <!-- i18n-allow: forensic quotes of the German STT-garble tokens under test -->
the unprimed base model can't spell a hard
custom wake — so NO transcript-content rule separates ghost from wake without
rejecting genuine wakes. The ONLY word-agnostic discriminator is raw audio
ENERGY. Fix: suppress silence at the match site via the RMS gate
(`RollingWhisperWake._match_min_rms`, ghost ≤ 0.0043 vs quiet-mic recall
0.009); keep the bias-echo confirm PERMISSIVE (heard any real speech →
genuine, fail-open) and SKIP it on a loud window (`_ECHO_CONFIRM_SKIP_RMS`,
latency). Guard:
`tests/unit/speech/test_rolling_whisper_wake_silence_ghost.py::test_loud_wake_fires_even_when_unbiased_pass_garbles_the_hard_word`.

**The same trap in the `vosk_kws` path (2026-07-13) — it is NOT an `stt_match`
quirk, it is a property of every wake engine that verifies via a TRANSCRIPT.**
The Vosk verify accepted a candidate only when the FREE (unconstrained) decoder
spelled the phrase back. An offline small model holds no arbitrary proper noun
in its lexicon, so it CANNOT: replaying 159 real captured `Hey Ruben` calls, it
spelled the phrase in 28 % of genuine calls and otherwise produced sound-alike
garbage (`'herum'`, `'erhoben'`, `'hey room'`, `'hey oben'`); for `Hey Jarvis`
on the de model, `'hey jahwe'` / `'hey genres'` / `'herr jahres'`. <!-- i18n-allow: forensic quotes of the German free-decode garble under test -->
That gate ate 38 % of ALL real wakes (recall 32 % — the maintainer had to
repeat the wake word four or five times) at 0/400 false accepts, i.e. far past
the point of diminishing precision. **And no spelling threshold can close it:
the free transcript `'herr oben'` was produced BOTH by a genuine call and by
room chatter, and EVERY wake word is out-of-vocabulary for SOME installed
language model** — so a spelling rule is guaranteed to be deaf for some phrase
in some language, which is exactly the universality requirement (§3) it
violates. Loosening the similarity floors only trades one mishearing for the
next false wake. Fix (`candidate_shape_ok`): confirm on the word-agnostic SHAPE
of what the free ear heard AT the span — a wake call is short and stands alone
(measured 0.72 s / 2 words), room speech is a longer stream of words the
decoder CONFIDENTLY recognises (1.29 s / 5 words, top conf ~1.0); every bound
derives from the configured phrase, never its spelling. Keep the spelling match
as a BONUS path that may only ACCEPT, never reject. Measured: verify pass-rate
on genuine calls 50 % → 74 %, false accepts 1 → 3 per 1650 real windows; the
identical thresholds, untuned, lift `Hey Jarvis` from 36 % → 66 %. Guard:
`tests/unit/plugins/wake/test_vosk_wake_word_agnostic.py`.

### AP-28 — Never gate CI checks on `isinstance` against an unpinned third-party lib

Never gate a pre-push / CI check on `isinstance` against the *installed* copy
of a third-party lib that CI installs **unpinned** (newest), then trust "green
on my machine". Version-skew brick: the lib's next release changes internals
and the gate CRASHES or false-fails in CI while green locally. Typer 0.26
vendored its own Click (`typer._click`), so `typer.main.get_command`'s root
group + params stopped being `isinstance` of the *external*
`click.Group`/`Argument`/`Option` → the CLI-drift gate `AssertionError`'d on
every push though green on local Typer 0.21 (fix 621f837a). Discriminate by
CAPABILITY, never concrete type (`.commands`, Click's stable
`param_type_name`). **Before anything goes PUBLIC: CI must be GREEN and every
red cause understood + fixed — reproduce against the SAME unpinned versions CI
installs, not your local pins.**

### AP-29 — Never commit a signing PRIVATE key, its passphrase, or a `*.key.enc` "encrypted at rest" copy

The installer is signed on key-bound axes (Wave 2 Ed25519, Wave 4 ML-DSA-65). The former scheme stored encrypted private keys
in-repo + a DEMO passphrase in plaintext docs; that passphrase leaked into 14
public snapshots (v0.1.0..v0.9.1), permanent + world-readable — so any key it
wrapped is forgeable. Private keys now live ONLY in GitHub Actions secrets
(base64 PKCS#8 PEM: `WAVE2_OFFLINE_KEY_B64` / `WAVE4_MLDSA65_KEY_B64`), local
backup in the maintainer's password manager; the repo holds ONLY public keys
(`install/keys/*.pub*`) + inlined verifier copies. Rotation = new keypair →
`gh secret set` → swap public keys + verifier blocks + fingerprints.
**Enforced by `scripts/ci/check_no_private_keys.py` (pre-commit + pre-push):**
a full PEM PRIVATE KEY block, a tracked `*.key`/`*.key.enc`, or a
`WAVE2_CEREMONY_PASSPHRASE=<value>` line blocks it. Doctrine:
`docs/supply-chain/wave2-key-ceremony.md`.

---

## 8. Recurring bug classes (must internalize)

Detail in [`docs/BUGS.md`](docs/BUGS.md). Recognize the signal, apply the
defense:

1. **Restore trap** (BUG-006/014/015): worktree + frontend build + RAM +
   editable-install pinned to a deleted clone. Signal: fix "works in tests"
   but behavior unchanged after restart. Defense: `pwsh scripts/preflight.ps1`
   + `python -c "import jarvis; print(jarvis.__file__)"`.
2. **Multi-layer enum drift** (BUG-008): empty UI list while DB has rows,
   HTTP 500, Pydantic `literal_error`. Defense: five-layer pattern + parity
   test.
3. **Config drift** (BUG-010): parallel sessions rewriting `jarvis.toml`,
   silently rolling back provider switches. Defense:
   `scripts/drift-guard-daemon.ps1` + ENV overrides + BOM-safe writer.
4. **Subprocess console flicker** (BUG-012): missing
   `NO_WINDOW_CREATIONFLAGS`. Defense: import from
   `jarvis.core.process_utils`.
5. **Audio host-API blocking-write trap** (BUG-014): WDM-KS auto-picked,
   PortAudio crashes. Defense: `_FORBIDDEN_OUTPUT_HOSTAPIS` +
   shortest-unique-token matching.
6. **Watchdog stale cross-unit counter** (BUG-032): a stall watchdog reads a
   process-global counter never reset per unit → fires between units.
   Defense: reset at unit start; re-arm the "not started" guard per unit.
7. **Loop on an unexpected teardown error** (AP-20): a socket loop
   `continue`s on a non-`WebSocketDisconnect` error → spins on a dead socket.
   Defense: treat any read error as terminal — `break`.
8. **Wedged un-cancellable native inference** (BUG-036): a shared
   ctranslate2/faster-whisper model called concurrently hangs unrecoverably;
   every later transcribe re-polls the dead engine. Defense: serialize with a
   non-blocking lock; self-heal by rebuilding a fresh model (AP-24).
9. **Hallucinating-ASR wake precision/recall trap** (AP-27, BUG-037): the
   `stt_match` wake transcribes with a bias prompt; the primed model INVENTS
   the phrase on silence and GARBLES it on speech, so **"fires on silence"
   and "stops working" share ONE root** — any transcript-content ghost filter
   that tightens the wake also rejects real wakes. Signal: a "ghost fix"
   lands and the wake stops triggering for a hard custom word
   (`Mythos`→`Mütos`), or vice-versa. <!-- i18n-allow: German STT-garble token under test -->
   Defense: gate silence on raw ENERGY (word-agnostic RMS at the match site),
   NEVER on transcript content; keep the confirm permissive + skip it when
   loud; the recall guard test must stay green. Truly-instant + zero-ghost
   custom wake needs a neural KWS model, not transcription (AP-25).
10. **Transcript-verified wake goes deaf on its own wake word** (AP-27, the
   general form; `vosk_kws` 2026-07-13): ANY wake engine whose verify asks a
   decoder to SPELL the phrase is deaf for every phrase that decoder has no
   lexicon entry for — and every wake word is out-of-vocabulary for some
   installed language model, so the bug is guaranteed, not incidental. Signal:
   the user repeats the wake word four or five times; the log shows a healthy
   stage-1 candidate followed by `verify SUPPRESSED` with a sound-alike
   transcript (`Hey Ruben`→`'herum'`, `Hey Jarvis`→`'hey jahwe'`). <!-- i18n-allow: German free-decode garble under test -->
   The trap: it looks like a PRECISION win (false accepts drop to zero) while
   recall quietly collapses, and every "fix" loosens a similarity floor,
   trading one mishearing for the next false wake. Defense: verify on
   WORD-AGNOSTIC properties (energy, spoken duration, word count at the
   candidate span, the free decoder's own confidence that it heard something
   ELSE) — all derived from the configured phrase, never its spelling. A
   spelling match may only ever ACCEPT (bonus path), never reject.

---

## 9. Operational reality & Git workflow

- **The working tree is frequently SHARED** — several parallel agent sessions
  edit it at once. Never assume the staged diff is only yours; commit
  **hunk-isolated** (`git add -p` / pathspec-scoped). A large uncommitted diff
  is **normal**. If the index/HEAD looks corrupted by a concurrent write,
  recover via temp-index commit + `update-ref` CAS + safety branch
  (`git-rescue` on repo-wide disorder).
- **App restart is `POST /api/settings/restart-app`**, not `Stop-Process`
  (Access Denied under the tray `pythonw.exe`). Editable-install fixes still
  need this restart to take effect.
- **New worktree:** run `pwsh scripts/preflight.ps1` before writing code; exit
  non-zero → fix first (BUG-006/014).
- **Memory:** check `MEMORY.md` (`~/.claude/projects/.../memory/`) before
  larger decisions — stable user preferences live there.
- **Plan vs. code:** on conflict, the plan wins
  (`~/.claude/plans/also-er-muss-auch-lexical-pond.md`); code deviations get <!-- i18n-allow -->
  documented back in the plan.

### Git workflow

*(The maintainer pins these in their personal global config; restated at the
project level so every contributor and agent here follows the same workflow.)*

- **Auto-commit after each completed logical step**, without being asked. BUT
  because the working tree is shared, stage only **your own** changed files by
  explicit path or `git add -p`, hunk-isolated — **never** `git add -A` /
  `git add .` (that sweeps another session's in-flight, possibly
  secret-bearing work into your commit — the main concurrency hazard here).
- **Use meaningful Conventional-Commit messages** (`feat:`, `fix:`,
  `refactor:`, `docs:`, `chore:`).
- **Never push to the remote automatically** — push only when the maintainer
  explicitly says so (a public push still honors §2's credential protection).
- **Never commit secrets, `.env` files, keys, tokens, or credentials.** If
  any appear untracked, STOP, flag them, and do not commit them.
- **Never `git add -A` / `git add .`** without first scanning the staged set
  for secrets and for files belonging to another session.

---

## 10. Run & test

```bash
# Install / activate entry-points (BUG-006/014 recovery) + full deps + dev tools
pip install -e . --no-deps
pip install -r requirements.txt
pip install -e ".[dev]"            # pytest, ruff, mypy

# Launch
run.bat                            # tray app + voice + Orb (recommended)
run.bat --headless                 # API/WS only, no voice
python -m jarvis.ui.web.launcher --dev   # frontend from Vite :5173
python -m jarvis --wizard | --check | --plugins | --debug | --phase5-doctor

# Lint / typecheck
ruff check jarvis/ && ruff format jarvis/ && mypy jarvis/

# Frontend (jarvis/ui/web/frontend/)
npm install && npm run dev         # build → npm run build ; tests → npm run test
```

```bash
# Tests (asyncio_mode=auto; fakes in tests/fakes/, not unittest.mock)
pytest tests/                      # full suite
pytest tests/unit/ -v              # per-module
pytest tests/integration/ -v       # phase-level E2E (real subprocesses; self-skips when prereqs missing)
pytest tests/missions/ -v          # Phase 6
pytest -m "not slow"               # fast subset
# Registered markers: phase5, skip_ci, e2e, voice_latency, eval, slow, integration.
# Targeted guards: test_routing.py (router), test_output_filter.py (scrubber),
# test_hangup_reason_parity.py (enum drift). New STT/Brain/Tool/Channel providers
# must pass tests/contract/.
```

---

## 11. Pointers

- **Architecture + module catalog + product detail:**
  [`docs/architecture-overview.md`](docs/architecture-overview.md).
- **Doctrine:** [`CLOUD.md`](CLOUD.md) (cross-platform charter),
  [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).
- **Bug register:** [`docs/BUGS.md`](docs/BUGS.md). **Anti-drift:**
  [`docs/anti-drift-three-layer.md`](docs/anti-drift-three-layer.md).
  **Self-Mod:** [`docs/self_mod.md`](docs/self_mod.md).
- **ADRs:** `docs/adr/0001..0023` (ADR-0001 superseded by ADR-0020).
  **Phase docs:** `docs/phase{0,1,1a,1c,2,4,5,6}-*.md`.
- **Operational scripts:** `scripts/preflight.ps1`,
  `scripts/drift-guard-daemon.ps1`, `scripts/README-auto-push.md`.
- **Jarvis control CLI** (drive a running Jarvis from the terminal / an agent
  / itself): `docs/jarvis-cli.md` + generated `docs/jarvis-cli-reference.md`
  (`jarvis/cli_ctl/`, binaries `jarvis`/`jarvisctl`/`jctl`). New REST routes
  must stay mounted — enforced by `scripts/ci/check_cli_coverage.py`.
