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

## 1. Language — an international, English-first project

**Personal Jarvis is an international open-source project. Its working language is English — not German, not any other single human language.** Assume contributors and downloaders worldwide; never bias the codebase, docs, or defaults toward one locale. Every artifact an agent produces in this repo is English, full stop.

- **Artifacts = English, always.** This covers code, comments, docstrings, log/exception messages, Markdown (READMEs, ADRs, plans, `BUGS.md` entries, handoffs), `SKILL.md` files, commit messages, PR titles/bodies, test names + docstrings, CLI help text, FastAPI route descriptions, error responses, JSON-schema `description` fields, audit-log entries, telemetry event names, and any new UI strings (i18n key + English source — never a non-English source). If unsure, default to English. This overrides any historical "Sprache: Deutsch" note. Enforced by the diff-based `language-policy` CI job (escape hatch: an inline `i18n-allow` marker or a glob in `scripts/ci/german-allowlist.txt`).
- **The chat / conversation language is each contributor's own choice, NOT something this repo dictates.** How an agent talks to *you* in chat is set in your personal global config (`~/.claude/CLAUDE.md` / `AGENTS.md`) — a German maintainer, a Spanish contributor, and an English one each set their own. This project neither requires nor assumes any particular chat language; it only fixes the artifacts as English.
- **Exempt from the English rule:** already-committed non-English content (don't retro-translate), and the runtime TTS/voice product surface (below), which is multilingual by design.

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

- **work with WHATEVER single key / login / account the user has** — no provider, model, or integration is load-bearing; a missing / empty / depleted / rate-limited (429) / out-of-credit (402) / unreachable one degrades or crosses to a **different family** with an honest message, and never bricks a core path (router, ack/flash, STT, sub-agent worker, mission critic). A tier whose primary AND fallback resolve to the same family is a single-provider brick (AP-22). Gate on **capability**, never a provider name or model id (AP-21).
- **work on EVERY OS, including a headless `python:3.11-slim` VPS** with no OS keyring, no D-Bus Secret Service, no GPU, no audio, no Windows APIs. The base `pip install` + boot must succeed there. OS-specific code is allowed only behind (a) a runtime capability check, (b) an extras group, and (c) a graceful English-message no-op elsewhere. Use `pathlib` + capability probes + UTF-8; never hardcode `C:\Users\...` or assume cp1252.
- **be recoverable IN-APP** — entering / switching / connecting a credential, and recovering from a dead one, happens inside the app, never by hand-editing `jarvis.toml`, exporting an ENV var, or spinning up a cloud instance.
- **store credentials portably** — the OS keyring when present, else ENV/.env, else the local-file fallback (`config._ensure_keyring_backend`). A save/connect must never 500 on a host without a Secret Service.

**Definition of done (NON-NEGOTIABLE).** A change touching config, credentials, a provider/integration, or OS-specific code is NOT "done" — and must not be claimed done — until you verify, with a test or an honest manual trace, the THREE paths that are NOT the maintainer's:
1. **Fresh install, ONE arbitrary key** — a downloader whose only credential is for a DIFFERENT provider reaches a working path (chat + voice + sub-agent + the touched feature), entirely in-app.
2. **Headless Linux** — base `pip install` + boot + the touched feature work on `python:3.11-slim`; local-only parts degrade to a logged no-op.
3. **Cross-family fallback** — when the configured provider/integration is absent or dead, the path crosses to whatever the user actually has, or degrades honestly — never dead-ends on the maintainer's favorite.

**"It works on my machine" is the *defect*, not the evidence.** The maintainer's RTX-5070-Ti / Windows box with Gemini + OpenRouter keys is <0.1 % of the install base. When in doubt, gate behind an extras group with a graceful no-op and lead the docs with the VPS path. Full doctrine: [`CLOUD.md`](CLOUD.md) + [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) (on conflict, the doctrine wins). See AP-21/22/23.

---

## 4. Naming — the agent/mission system is "Jarvis-Agents"

The internal agent/mission/harness system is named **Jarvis-Agents** (singular **Jarvis-Agent**) everywhere a human or an LLM could read it — UI labels, docs, comments, new code identifiers, log/voice text. Do not introduce other names for it. (A repo-wide rename of older internal terms to this brand is in progress; write new surfaces as Jarvis-Agents.)

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
- **Worker isolation:** every mission worker runs in a fresh `git worktree` under `<repo_parent>/sub-agents-outputs/` with a kill-on-crash containment (Job Object on Windows; process-group reaper on POSIX). `MAX_CRITIC_LOOPS = 3` is fixed.
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
- **Plan vs. code:** on conflict, the plan wins (`~/.claude/plans/also-er-muss-auch-lexical-pond.md`); code deviations get documented back in the plan.

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
