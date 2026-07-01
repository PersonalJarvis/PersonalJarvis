# CLAUDE.md

Binding guidance for any coding agent working in this repository. This file is
deliberately lean — it holds the rules you must respect on **every** change.
Product/architecture reference detail lives in
[`docs/architecture-overview.md`](docs/architecture-overview.md).

---

## 0. CLAUDE.md ≡ AGENTS.md (mirror rule — BINDING)

`CLAUDE.md` and `AGENTS.md` are **byte-identical twins**. `AGENTS.md` is the
cross-tool standard name other agents read; `CLAUDE.md` is the canonical source.
**Every edit to this file is mirrored into `AGENTS.md` automatically — you do not
need to be asked, and you must never let them drift.** The sync engine is
`scripts/ci/sync_agents_md.py` (live `PostToolUse` hook + `.githooks/pre-commit
--stage` + a `--check` CI gate). After editing either file, run the sync (or just
copy one onto the other) so the tree stays in sync before you commit.

---

## 1. Language — English-only artifacts, fail-closed (BINDING, HIGHEST PRIORITY)

**Personal Jarvis is an international open-source project. The working language for everything an agent commits is English — full stop, with no exception outside the single closed list below.** Assume an arbitrary downloader anywhere on earth: English is the lingua franca that reaches the most of them, so English is **always** preferred and is never weighed against any single locale. This is not "English-first, German-tolerated" — it is **English-only for artifacts**. Any historical note that this is a "German project" / "internationales deutsches Projekt" is **dead** and is overridden by this section. <!-- i18n-allow: names the dead German label being retired -->

**Fail-closed default.** Treat every line you write *or touch* — code, comments, docstrings, log/exception/error messages, Markdown (READMEs, ADRs, plans, `BUGS.md`, handoffs, design/forensic docs), `SKILL.md`, commit messages, PR titles/bodies, test names + docstrings, CLI help, FastAPI route descriptions, JSON-schema `description` fields, audit-log entries, telemetry names, and UI strings (i18n key + **English** source — never a non-English source) — as English **unless it provably falls inside the closed product-surface list below.** When in doubt, it is English. "It's only a comment / a test / an internal doc / a quick note" is **not** an exemption — those are exactly where German keeps leaking in. You may not invent a new German-allowed category; if you believe one exists, stop and ask rather than committing the German.

**The ONLY German permitted — the multilingual *product surface* (this list is CLOSED):**
1. **Runtime voice/TTS + chat output** Jarvis speaks or writes back to the user — governed by the per-turn resolver in the Runtime-output subsection below.
2. **i18n / locale *source* files and the website's localized copy** (`*/i18n/*`, `*/locales/*`, `*.de.json`, `*.de.ts`, and the like) — German there *is* the product content.
3. **Speech-recognition *input vocabulary*** — the German tokens a classifier must literally contain to match German voice utterances (router, navigation-intent, local-action gate, trigger matcher, …). This is matching *data*, not prose.
4. **The tests / fixtures and forensic voice-bug deep-dives** that necessarily quote 1–3 as the content under test.

That list is materialized file-by-file in `scripts/ci/german-allowlist.txt`. **Anything not in one of those four buckets must be English.** German anywhere else is a *defect*, not a stylistic choice — translate it.

- **The allowlist is a curated, justified register — never a free pass to silence the gate.** Every entry must name which of the four product-surface reasons it serves. **"Whole-file scope, narrow later" and "shared tree, do it later" are no longer acceptable justifications for a new entry.** Prefer an inline `i18n-allow` on the one load-bearing German line over exempting a whole file, so a future English line added to that same file is still checked. Add a file only because it is genuinely product surface — not because it is red.
- **Pre-existing German is *backlog*, not a protected state.** The old "don't retro-translate / already-committed content is exempt" carve-out is **removed**. There is no mass-rewrite mandate (a shared working tree makes a giant sweep risky), but when you edit a file and pass non-product-surface German, **translate it on the way through** — do not preserve it, copy it forward, or add more German beside it.
- **The CI gate only sees what a diff ADDS, and only on PRs** (`scripts/ci/check_no_new_german.py`) — it is structurally blind to the backlog. So the first line of defense is the agent: writing or leaving German where English was required is a bug, gate or no gate.
- **Chat / conversation language is each contributor's own choice**, set in their personal global config (`~/.claude/CLAUDE.md` / `AGENTS.md`) — a German, Spanish, or English maintainer each picks their own. This repo neither requires nor assumes any chat language; it only fixes the **artifacts** as English. Conversation = your choice; artifacts = English, always.

### Runtime output language (voice + chat) — BINDING

This governs what Jarvis **speaks/writes back to the user** (a multilingual product surface — it does NOT weaken the English-artifact rule). Supported languages are equal — `de`, `en`, `es`, and any future locale; never a German- or English-only bias.

1. **A turn's output language is decided exactly once, by one resolver — `jarvis/core/turn_language.py::resolve_output_language` — and every output layer consumes that one decision.** Precedence: explicit `brain.reply_language` pin → conversation stickiness (a thin one/two-word interjection is spoken in the running `conversation_language` and must NOT flip it; only a substantive turn switches) → detected input language → configured `DEFAULT_LOCALE`.
2. **It applies to ALL user-facing output** — deep reply, ack preamble, spawn announcements, every canned status/error/clarify/timeout/provider-down phrase, every deterministic Computer-Use/local-action readback, AND the TTS voice/BCP-47 pick. A mid-session language flip between layers is a bug.
3. **No layer may re-derive the language on its own** — no `"de" if _looks_german() else "en"` shortcut (drops `es`), no `de`/`en`-only phrase table, no per-layer hardcoded default, no path that ignores the pin. New phrase tables carry **all** supported languages and resolve their key through the one resolver.
4. **Honesty over guessing** — a layer that genuinely cannot tell falls back to `DEFAULT_LOCALE`. The durable guarantee for a single-language user is to set `brain.reply_language` (`auto` | `de` | `en` | `es`), editable from the desktop **Languages** view. Regression guards: `tests/unit/core/test_turn_language.py`, `tests/unit/speech/test_phrase_language.py`, `tests/unit/brain/test_*language*.py`.

---

## 2. GitHub — ONE public repo + mandatory privacy gate (BINDING)

**There is ONE project repo: the public flagship `https://github.com/PersonalJarvis/PersonalJarvis`** (PascalCase). EVERY maintainer "commit / push / save to GitHub / sichere den Stand" targets THIS repo and no other. <!-- i18n-allow: quoted German maintainer trigger phrase --> The lowercase `personal-jarvis` (git remote `origin`) is kept ONLY as a **silent local backup** — never the deliverable. Never ask "which repo"; it is always the public flagship.

**Why the gate exists:** the public repo's history is world-readable forever. One leaked API key, real name, personal path (`C:\Users\<name>\...`), private email, internal project id, or Windows SID that lands in a single commit is permanent — deleting it later does NOT un-publish it (it survives in clones, forks, and caches). So nothing reaches the public repo raw: every push is rebuilt as a **depersonalized snapshot** that runs the full gate below, in order, **fail-closed** — at any step, any uncertainty STOPS the push rather than letting a questionable line through.

1. **Tracked-files-only export** — only git-tracked files are exported, so `.gitignore` is the first cut for free: `data/`, `.env`, `jarvis.toml`, the Vault, and key material are never even candidates.
2. **Distribution denylist** — strips files that ARE tracked but must not ship publicly: internal dev docs, scratch/experiment scripts, signing keys, red-team notes.
3. **Deterministic PII scrub** — pattern-based masking of the maintainer's real name, personal filesystem paths, machine/user identifiers, and internal project ids across every staged file.
4. **Mandatory sub-agent privacy review** — a dedicated sub-agent reads the ENTIRE staged snapshot end-to-end and reports anything personal/sensitive the regex scanners could miss (keys, tokens, credentials, personal data, private paths/emails, internal-only content). It is **additive-only**: it can ADD a blocking finding but can never clear what the deterministic gate flagged. A single non-empty finding STOPS the push.
5. **Deterministic secret/PII scan** — a final regex sweep tuned to real credential shapes/lengths (catches an actual key, not every random hex string), fail-closed.
6. **Human review** — an explicit maintainer go-ahead before any byte crosses the network. No silent auto-publish.

**Isolation (so the gate can't be bypassed by accident):** the push runs from a **separate clean clone**, never your live working tree — the working tree is never touched and the raw, un-scrubbed state physically cannot leak through. As a hard backstop, the pre-push guard (`scripts/ci/guard_no_raw_public_push.py` + `privacy_pre_push.py`, wired via `.githooks/pre-push`, `core.hooksPath=.githooks`) HARD-BLOCKS any attempt to `git push` raw working state straight to the public repo. Do not remove or weaken it — it has already caught a real leak (a Windows SID that reached public history was blocked, then purged with filter-repo). This whole gate is the `ship-public-release` skill.

**Two volumes, same gate:** *Discreet (DEFAULT)* — "push", "sichere den Stand", "update GitHub" → clean snapshot through the gate, **no version bump / tag / release / announcement**. *Release (explicit only)* — "Mach ein Release", "Publish release" → the same gate **plus** a SemVer bump + git tag + CHANGELOG entry. <!-- i18n-allow: quoted German maintainer trigger phrases --> When ambiguous, default to discreet.

**Guardrails:** **Do not PUSH unless the maintainer asks** (local auto-commit is fine and is governed by the Git-workflow rules in §9). `save-to-github` and `github-version` MUST NOT run in this repo — they push raw state and bypass the privacy gate above. Repo doctrine: [`CLOUD.md`](CLOUD.md).

---

## 3. Open-source universality — the maintainer's config is NEVER the baseline (BINDING)

The recurring, expensive bug class on this project is **building/testing a feature against the maintainer's own machine, keys, providers, and OS — then shipping it broken for everyone else.** Two faces of ONE rule: **assume an arbitrary downloader, never the maintainer.** It governs the WHOLE product surface, and specifically the **entire API-Keys / credential / integration surface — not just brain providers:** STT, TTS, Vision, Wake, **Telephony (Twilio), Channels (Telegram/Discord), Marketplace plugins (OAuth + MCP), AND credential STORAGE itself.** Every one must:

- **work with WHATEVER single key / login / account the user has** — no provider, model, or integration is load-bearing; a missing / empty / depleted / rate-limited (429) / out-of-credit (402) / unreachable one degrades or crosses to a **different family** with an honest message, and never bricks a core path (router, ack/flash, STT, Jarvis-Agent worker, mission critic). A tier whose primary AND fallback resolve to the same family is a single-provider brick (AP-22). Gate on **capability**, never a provider name or model id (AP-21).
- **work on EVERY OS, including a headless `python:3.11-slim` VPS** with no OS keyring, no D-Bus Secret Service, no GPU, no audio, no Windows APIs. The base `pip install` + boot must succeed there. OS-specific code is allowed only behind (a) a runtime capability check, (b) an extras group, and (c) a graceful English-message no-op elsewhere. Use `pathlib` + capability probes + UTF-8; never hardcode `C:\Users\...` or assume cp1252. **The base install stays torch-/GPU-free and platform-universal**, enforced pre-push + in CI by `check_requirements_sync.py` (source ↔ `pyproject` deps) and `check_lockfile_universal.py` (the compiled `requirements.txt` keeps its `--universal` marker and admits no `nvidia-*`/`torch`/CUDA/`faster-whisper` wheel — those live only in the opt-in `[local-voice]` extra); regenerate the lock only with `uv pip compile --universal`, never plain `pip-compile`.
- **be recoverable IN-APP** — entering / switching / connecting a credential, and recovering from a dead one, happens inside the app, never by hand-editing `jarvis.toml`, exporting an ENV var, or spinning up a cloud instance.
- **store credentials portably** — the OS keyring when present, else ENV/.env, else the local-file fallback (`config._ensure_keyring_backend`). A save/connect must never 500 on a host without a Secret Service.

**Definition of done (NON-NEGOTIABLE).** A change touching config, credentials, a provider/integration, or OS-specific code is NOT "done" — and must not be claimed done — until you verify, with a test or an honest manual trace, the THREE paths that are NOT the maintainer's:
1. **Fresh install, ONE arbitrary key** — a downloader whose only credential is for a DIFFERENT provider reaches a working path (chat + voice + Jarvis-Agent + the touched feature), entirely in-app.
2. **Headless Linux** — base `pip install` + boot + the touched feature work on `python:3.11-slim`; local-only parts degrade to a logged no-op.
3. **Cross-family fallback** — when the configured provider/integration is absent or dead, the path crosses to whatever the user actually has, or degrades honestly — never dead-ends on the maintainer's favorite.

**"It works on my machine" is the *defect*, not the evidence.** The maintainer's RTX-5070-Ti / Windows box with Gemini + OpenRouter keys is <0.1 % of the install base. When in doubt, gate behind an extras group with a graceful no-op and lead the docs with the VPS path. Full doctrine: [`CLOUD.md`](CLOUD.md) + [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) (on conflict, the doctrine wins). See AP-21/22/23.

---

## 4. Naming — the agent/mission system is "Jarvis-Agents"

The internal agent/mission/harness system is named **Jarvis-Agents** (singular **Jarvis-Agent**) everywhere a human or an LLM could read it — UI labels, docs, comments, new code identifiers, log/voice text. Do not introduce other names for it. **Glossary:** this system was formerly called "Subagents" / "Sub-Agent" / "sub_jarvis" / "SubJarvis" and "OpenClaw" / "openclaw"; those names were renamed repo-wide to Jarvis-Agents (2026-06-30). The only surviving old-name occurrences are intentional and documented where they stay: the external `openclaw` npm worker binary (it owns that executable name), read-time back-compat config aliases (e.g. the new `[brain.worker]` / `[harness.jarvis_agent]` keys still accept the old `[brain.sub_jarvis]` / `[harness.openclaw]` keys), and historical migration notes.

---

## 5. Architecture essentials (respect on every change)

Full model + module catalog: [`docs/architecture-overview.md`](docs/architecture-overview.md). The rules you cannot break:

- **8-layer dependency rule:** higher layers reach lower ones **only via protocols** (`jarvis/core/protocols.py`); lateral communication **only** via typed `frozen=True` events on `EventBus` (`jarvis/core/bus.py`) carrying `trace_id` + `timestamp_ns`. A broken subscriber is logged in `_safe_dispatch`, never propagated (AP-18).
- **Plugins are structural:** under `jarvis/plugins/<group>/<name>.py`, registered via `pyproject.toml` entry-points, and **must not import `jarvis.*`** in the plugin module. After editing entry-points: `pip install -e . --no-deps`. Groups: wakeword, stt, tts, brain, harness, tool, channel.
- **Streaming first:** all Brain/STT/TTS/Harness methods return `AsyncIterator[...]` (non-streaming yields one element).
- **Secrets** only via `jarvis.core.config.get_secret`: OS keyring → ENV → `.env` → local-file fallback. Never put keys in code / `jarvis.toml` / commits / `.claude/`. Voice/chat must never accept secrets (AP-2).
- **Brain is multi-provider** — never hardcode Anthropic/Claude (AP-6); gate features on a **capability** (`supports_vision`, `supports_tools`, `can_call_tools()`), never a provider name/model id (AP-21). Persona comes from `jarvis/brain/persona_loader.py` (editable override → packaged `JARVIS_PERSONA.md`); never hardcode it.
- **Router discipline (ADR-0011):** the router-tier brain is a pure dispatcher over the `ROUTER_TOOLS` frozenset (`jarvis/brain/factory.py`). No spawn tool ever enters a worker set (AP-5/AP-14). Extending `ROUTER_TOOLS` → amend ADR-0011 + `tests/unit/brain/test_routing.py`.
- **Voice scrub:** brain→TTS goes through `scrub_for_voice` (`jarvis/brain/output_filter.py`) — **regex only, no LLM call** (AP-11). ADR-0010.
- **Atomic config writes:** mutate `jarvis.toml` only via `jarvis/core/config_writer.py` (lock + tempfile + BOM-safe, AP-7). Self-mod pipeline (Allowlist→Pre-Validate→Backup→replace→sync reload-test→Rollback→Audit) is non-negotiable (AP-13/14).
- **Multi-layer enum drift:** any value crossing Python ↔ SQL ↔ Pydantic ↔ TS ↔ UI uses the five-layer pattern (`docs/anti-drift-three-layer.md`) + a parity test, preemptively (BUG-008 recurred 4×).
- **Worker isolation:** every mission worker runs in a fresh `git worktree` under `<repo_parent>/jarvis-agent-outputs/` (legacy `sub-agents-outputs/` still read as a fallback) with a kill-on-crash containment (Job Object on Windows; process-group reaper on POSIX). `MAX_CRITIC_LOOPS = 3` is fixed.
- **Platform gotchas:** UTF-8 stdout (cp1252 default on Windows); every subprocess passes `NO_WINDOW_CREATIONFLAGS` (AP-1); WASAPI audio, WDM-KS forbidden (BUG-014); no Windows Service (SYSTEM has no mic); UAC `asInvoker`, elevate per-action.

---

## 6. Safety / risk tiers

Four tiers: `safe` / `monitor` / `ask` / `block`, priority **blacklist > whitelist > tool default** (`jarvis/safety/risk_tier.py`). Whitelist downgrades to `safe` — the anti-confirmation-fatigue contract. **Direct `Tool.execute()` is a bug; only `ToolExecutor.execute()` is authorized (AP-3).** Generated skills land as `state="draft"` and are never auto-activated (AP-15).

---

## 7. Critical anti-patterns (do not do this)

| # | If you do this... | ...you get this bug |
|---|---|---|
| AP-1 | Spawn `subprocess.Popen` without `NO_WINDOW_CREATIONFLAGS` | BUG-012 flicker storm under `pythonw.exe` |
| AP-2 | Accept API keys via voice/chat | STT log leak — credential exfiltration vector |
| AP-3 | Call `Tool.execute()` directly (bypassing `ToolExecutor`) | Risk-tier/whitelist/plausibility skipped |
| AP-4 | Add a new `hangup_reason`/mission-status string in one site only | BUG-008 recurrence: HTTP 500, empty UI |
| AP-5 | Put the harness-spawn / `dispatch-with-review` / `run-skill` tool in a worker tool set | D9 recursion: worker spawns supervisor, infinite loop |
| AP-6 | Hardcode `Claude`/`Anthropic` API client | User has no Anthropic API account; breaks `cfg.brain.primary` |
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
| AP-20 | `continue` (instead of `break`) a WS receive loop on an error that isn't `WebSocketDisconnect` | Unclean disconnects raise `RuntimeError` → loop re-reads a dead socket forever → log storm + self-restart. Catch `RuntimeError` and `break` |
| AP-21 | Pin a feature to a provider **name** or **model id** instead of gating on a **capability** | The feature silently breaks for every other provider and for whatever the user selected. Gate on `supports_vision` / `supports_tools` / `can_call_tools()`; if a flag is wrong/missing, fix/add the capability — don't pin the feature |
| AP-22 | Configure a tier (router / ack / STT / TTS / worker / critic / fallback) whose primary AND fallback resolve to the SAME provider family, or build a fallback chain from hardcoded provider NAMES | Single-provider brick: one missing key / 429 / 402 / outage takes the whole tier down even when the user has a healthy DIFFERENT provider. Resolve every tier through one key-aware chain that skips the dead/keyless provider, crosses families, and degrades honestly only when NO family is reachable — recoverable in-app |
| AP-23 | Build or TEST a feature only against the maintainer's own config / keys / provider / OS and claim it done | The whole API-Keys section silently bricked for every other downloader and on a headless VPS (credentials couldn't even be SAVED, ENV keys read as "not configured", channels/plugins/OAuth 500'd). "Works on my machine" is the defect — verify the three non-maintainer paths in §3 |
| AP-24 | Call a shared native inference engine (ctranslate2 / faster-whisper, an ONNX / torch session) concurrently from two callers, OR "recover" a hung inference with only a timeout that re-polls the SAME wedged engine | `WhisperModel.transcribe` is NOT thread-safe: the wake poll loop + the VAD probe sharing one provider hung it forever; a hung `to_thread` can't be cancelled, so a timeout only BOUNDS, never RECOVERS. Fix: a non-blocking per-instance inference lock (2nd call → skip) + a `recover()` that rebuilds a FRESH model after N failures (BUG-036) |
| AP-25 | Run the always-on wake/keyword Whisper on the **GPU** via CTranslate2/faster-whisper, or auto-upgrade a custom-phrase (`stt_match`) wake to `large-v3-turbo/cuda` | On the maintainer's NVIDIA Blackwell GPU (RTX 5070 Ti / sm_120) CTranslate2 `model.transcribe` **HANGS on every inference** (8 s timeout → self-heal drops+rebuilds the model → the cold rebuild hangs again → a vicious cycle that leaves the wake **permanently DEAF**). The same hang also appears on `base`/`cpu` when ctranslate2's auto thread-pool deadlocks against PyTorch's OpenMP in the shared process. Keep the wake on `base`/`cpu` with a FIXED `cpu_threads` (bounds ctranslate2 vs torch); `[stt].wake_high_accuracy` GPU turbo is **OFF by default** and opt-in ONLY on a GPU verified not to hang. Truly instant+accurate custom-word wake needs a trained neural KWS model (openWakeWord `custom_onnx`), NOT transcription (2026-06-30 live-log forensic; see `docs/local-wakeword/WAKE-RELIABILITY-DEEPDIVE.md`) |

---

## 8. Recurring bug classes (must internalize)

Detail in [`docs/BUGS.md`](docs/BUGS.md). Recognize the signal, apply the defense:

1. **Restore trap** (BUG-006/014/015): worktree + frontend build + RAM + editable-install pinned to a deleted clone. Signal: fix "works in tests" but behavior unchanged after restart. Defense: `pwsh scripts/preflight.ps1` + `python -c "import jarvis; print(jarvis.__file__)"`.
2. **Multi-layer enum drift** (BUG-008): empty UI list while DB has rows, HTTP 500, Pydantic `literal_error`. Defense: five-layer pattern + parity test.
3. **Config drift** (BUG-010): parallel sessions rewriting `jarvis.toml`, silently rolling back provider switches. Defense: `scripts/drift-guard-daemon.ps1` + ENV overrides + BOM-safe writer.
4. **Subprocess console flicker** (BUG-012): missing `NO_WINDOW_CREATIONFLAGS`. Defense: import from `jarvis.core.process_utils`.
5. **Audio host-API blocking-write trap** (BUG-014): WDM-KS auto-picked, PortAudio crashes. Defense: `_FORBIDDEN_OUTPUT_HOSTAPIS` + shortest-unique-token matching.
6. **Watchdog stale cross-unit counter** (BUG-032): a stall watchdog reads a process-global counter never reset per unit → fires spuriously between units. Defense: reset the counter at unit start; re-arm the "not started" guard per unit.
7. **Loop on an unexpected teardown error** (AP-20): a socket loop `continue`s on a non-`WebSocketDisconnect` error → spins on a dead socket. Defense: treat any read error as terminal — `break`.
8. **Wedged un-cancellable native inference** (BUG-036): a shared ctranslate2/faster-whisper model called concurrently hangs unrecoverably; every later transcribe re-polls the dead engine forever. Defense: serialize with a non-blocking lock; self-heal by rebuilding a fresh model (AP-24).

---

## 9. Operational reality & Git workflow

- **The working tree is frequently SHARED** — several parallel agent sessions edit it at once. Never assume the staged diff is only yours; commit **hunk-isolated** (`git add -p` / pathspec-scoped). A large uncommitted diff is the **normal** state. If the index/HEAD looks corrupted by a concurrent write, recover via temp-index commit + `update-ref` CAS + safety branch (reach for `git-rescue` on repo-wide disorder).
- **App restart is `POST /api/settings/restart-app`**, not `Stop-Process` (Access Denied under the tray `pythonw.exe`). Editable-install fixes still need this restart to take effect.
- **New worktree:** run `pwsh scripts/preflight.ps1` before writing code; exit non-zero → fix first (BUG-006/014).
- **Memory:** check `MEMORY.md` (`~/.claude/projects/.../memory/`) before larger decisions — stable user preferences live there.
- **Plan vs. code:** on conflict, the plan wins (`~/.claude/plans/also-er-muss-auch-lexical-pond.md`); code deviations get documented back in the plan. <!-- i18n-allow -->

### Git workflow

*(The maintainer already pins these in their personal global config; they are restated here at the project level so every contributor and agent in this repo follows the same workflow.)*

- **Auto-commit after each completed logical step**, without being asked. BUT because the working tree is shared (above), stage only **your own** changed files by explicit path or `git add -p`, hunk-isolated — **never** `git add -A` / `git add .` (that sweeps another session's in-flight, possibly unfinished or secret-bearing work into your commit, and is the main concurrency hazard here).
- **Use meaningful Conventional-Commit messages** (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`).
- **Never push to the remote automatically** — push only when the maintainer explicitly says so (and a public push still runs the privacy gate in §2).
- **Never commit secrets, `.env` files, keys, tokens, or credentials.** If any appear untracked, STOP, flag them to the maintainer, and do not commit them.
- **Never `git add -A` / `git add .`** without first scanning the staged set for secrets and for files that belong to another session.

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

- **Architecture + module catalog + product detail:** [`docs/architecture-overview.md`](docs/architecture-overview.md).
- **Doctrine:** [`CLOUD.md`](CLOUD.md) (cross-platform charter), [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).
- **Bug register:** [`docs/BUGS.md`](docs/BUGS.md). **Anti-drift:** [`docs/anti-drift-three-layer.md`](docs/anti-drift-three-layer.md). **Self-Mod:** [`docs/self_mod.md`](docs/self_mod.md).
- **ADRs:** `docs/adr/0001..0023` (ADR-0001 superseded by ADR-0020). **Phase docs:** `docs/phase{0,1,1a,1c,2,4,5,6}-*.md`.
- **Operational scripts:** `scripts/preflight.ps1`, `scripts/drift-guard-daemon.ps1`, `scripts/README-auto-push.md`.
- **Jarvis control CLI** (drive a running Jarvis from the terminal / an agent / itself): `docs/jarvis-cli.md` + generated `docs/jarvis-cli-reference.md` (`jarvis/cli_ctl/`, binaries `jarvis`/`jarvisctl`/`jctl`). New REST routes must stay mounted — enforced by `scripts/ci/check_cli_coverage.py`.
