# Agent contract — full detail

This is the unabridged agent contract for Personal Jarvis. `CLAUDE.md` /
`AGENTS.md` at the repo root is the compressed, always-loaded index of these
rules; THIS file carries the full wording, rationale, and history. On any
conflict, treat the two as one document — the root file is a summary of this
one. Section numbers here match the root file's section numbers.

---

## 0. Mirror rule — CLAUDE.md ≡ AGENTS.md, .claude/ ≡ .agents/ (BINDING)

`CLAUDE.md` is canonical; `AGENTS.md` is its **byte-identical twin** for other
tools. The same holds for the versioned agent-knowledge trees:
`.claude/{agents,commands,skills}/` ↔ `.agents/{agents,commands,skills}/` —
everything in them addresses **every** coding agent (Codex, Gemini CLI, ...),
never Claude Code alone; write it accordingly. Sync engines
`scripts/ci/sync_agents_md.py` / `sync_agents_dir.py` run as a live
`PostToolUse` hook, `.githooks/pre-commit --stage`, and a `--check` CI gate;
after editing either side, sync before committing. Deletions propagate.
Gitignored/private entries (e.g. `.claude/skills/security-github/`) stay out
of the mirror on both sides.

---

## 1. Language — English-only artifacts, fail-closed (BINDING, HIGHEST PRIORITY)

Personal Jarvis is an international open-source project — assume an arbitrary
downloader anywhere on earth. **Everything an agent commits is English**: code,
comments, docstrings, log/exception/error messages, Markdown (READMEs, ADRs,
plans, handoffs, forensic docs), `SKILL.md`, commit messages, PR/issue text,
test names and docstrings, CLI help, route/schema descriptions, telemetry
names, UI strings (i18n key + **English** source). When in doubt, English.
Never invent a new German-allowed category — ask instead of committing German.

**The ONLY German permitted — the multilingual product surface (CLOSED list):**

1. **Runtime voice/TTS + chat output** — governed by the resolver below.
2. **i18n / locale source files + the website's localized copy** — German
   there *is* the product.
3. **Speech-recognition input vocabulary** — German tokens a classifier must
   literally contain to match German utterances. Matching *data*, not prose.
4. **Tests / fixtures + forensic voice-bug deep-dives** quoting 1–3 as the
   content under test.

Materialized file-by-file in `scripts/ci/german-allowlist.txt` — a curated,
justified register, never a free pass: every entry names its reason, and an
inline `i18n-allow` comment on the one load-bearing line beats a whole-file
exemption. **Pre-existing German is backlog, not protected:** when you edit a
file and pass non-product-surface German, translate it on the way through —
never preserve or extend it. The CI gate (`scripts/ci/check_no_new_german.py`)
only sees what a diff ADDS, so the agent is the first line of defense.
Conversation language is each contributor's own choice (personal global
config); the repo fixes only the artifacts.

### Runtime output language (voice + chat) — BINDING

Supported locales are equal — `de`, `en`, `es`, any future one; never a
German- or English-only bias. This governs what Jarvis speaks/writes back and
does NOT weaken the English-artifact rule.

1. **One resolver decides a turn's output language** —
   `jarvis/core/turn_language.py::resolve_output_language`. Precedence:
   `brain.reply_language` pin → conversation stickiness (a one/two-word
   interjection must NOT flip it; only a substantive turn switches) → detected
   input language → `DEFAULT_LOCALE`.
2. It applies to **ALL user-facing output**: deep reply, ack preamble, spawn
   announcements, every canned status/error/clarify phrase, deterministic
   readbacks, AND the TTS voice/BCP-47 pick. A mid-session flip between
   layers is a bug.
3. **No layer re-derives the language** — no `_looks_german()`-style
   heuristics (drops `es`), no de/en-only phrase table, no per-layer default.
   New phrase tables carry ALL supported languages and resolve through the
   one resolver.
4. A layer that genuinely can't tell falls back to `DEFAULT_LOCALE`. Durable
   pin: `brain.reply_language` (`auto` | `de` | `en` | `es`), editable in the
   desktop **Languages** view. Guards: `tests/unit/core/test_turn_language.py`,
   `tests/unit/speech/test_phrase_language.py`,
   `tests/unit/brain/test_*language*.py`.

---

## 2. GitHub — ONE public repo, fail-closed credential protection (BINDING)

The ONE project repo is the public flagship
**`https://github.com/PersonalJarvis/PersonalJarvis`**; every maintainer
"commit / push / save to GitHub / sichere den Stand" targets it — never ask
"which repo". <!-- i18n-allow: quoted German maintainer trigger phrase -->
Lowercase `personal-jarvis` (remote `origin`) is a silent local backup, never
the deliverable. The former depersonalization/snapshot gate is RETIRED
(maintainer directive 2026-07-17): ONE shared git history, every machine
pushes and pulls it normally.

**A push is `git push`.** This repo is an ordinary open-source project and
ships like one. Forbidden as part of pushing (maintainer directive
2026-08-05, after a measured ~440k tokens per push): building a parallel
"clean" staging tree, cloning the target repo to reconcile into, running
`npm ci`/a frontend build, and above all dispatching a sub-agent to read
every file in the tree looking for personal data. That audit re-read 5,481
already-published files on every push; a regex scan, `.gitignore`, and
GitHub's own push protection already cover it. If you find yourself
preparing a snapshot instead of pushing a commit, stop — that is the
retired gate growing back. The same ban covers review sub-agents
(maintainer directive 2026-08-12): a push or release ships commits that
already exist, and that code was reviewed when it was written — spawning
`code-reviewer` (or any reviewer) at push time is the retired ceremony in
a different costume. Review happens when code is authored, never when it
is published.

What remains, fail-closed:

1. **`.gitignore` first** — `data/`, `.env`, `jarvis.toml`, the Vault, and
   key material are never tracked. This is the load-bearing layer: nothing
   personal is in the tree, so nothing personal can be pushed.
2. **Never commit credentials** — API keys, tokens, private keys (incl.
   `*.key.enc`, AP-29), passphrases. `check_no_private_keys.py` + the secret
   sweep stay wired in pre-commit and pre-push.
3. **GitHub secret scanning + push protection stay ON.** A blocked push is a
   real finding: stop and fix — never bypass or allowlist around it.
4. **Trademark/brand review is a human release checkpoint** — flag concerns
   to the maintainer instead of shipping them.

**Gate placement is a rule, not a preference.** A check that reads the whole
tree belongs in CI (`.github/workflows/ci.yml`), never in `pre-push`. Only a
check that (a) judges the pushed change alone and (b) guards something
unrecoverable — a leaked credential — may block a push. Everything else
reports on GitHub's runners, where a red result costs a notification rather
than a debugging session. Adding a gate to `.githooks/pre-push` requires the
maintainer's explicit say-so; adding one to CI does not.

**Two volumes:** *Update (DEFAULT)* — "push", "sichere den Stand" → normal
push, no bump/tag/release. *Release (explicit only)* — "Mach ein Release",
"mach eine neue Version" → SemVer bump + tag + CHANGELOG, shipping the ENTIRE
current local state (reconcile with `public/main` first; never knowingly ship
a release lacking local fixes without saying so). <!-- i18n-allow: quoted German maintainer trigger phrases -->
When ambiguous, update. **Pre-tag gate:** run
`python scripts/ci/check_release_completeness.py` before tagging and again
with `--verify-release` after publishing — a pushed tag without a *published*
GitHub Release updates no managed install.

**Do not PUSH unless the maintainer asks** (local auto-commit per §9).
Releases are cut from the dev-machine line; a session on another machine
ports its work back to `main` instead of cutting its own releases. Doctrine:
[`CLOUD.md`](../CLOUD.md).

---

## 3. Open-source universality — the maintainer's config is NEVER the baseline (BINDING)

**Assume an arbitrary downloader, never the maintainer.** This governs the
whole product surface and the entire credential/integration surface — brains,
STT, TTS, Vision, Wake, Telephony, Channels, Marketplace plugins (OAuth +
MCP), and credential STORAGE itself. Every one must:

- **Work with WHATEVER single key/login the user has** — no provider, model,
  or integration is load-bearing; a missing / depleted / 429 / 402 /
  unreachable one degrades or crosses to a **different family** with an
  honest message, never bricking a core path. Primary AND fallback in the
  same family = a single-provider brick (AP-22); gate on **capability**,
  never a provider name/model id (AP-21).
- **Work on EVERY OS, incl. a headless `python:3.11-slim` VPS** with no
  keyring, GPU, audio, or Windows APIs — base `pip install` + boot must
  succeed there. Use `pathlib` + capability probes + UTF-8; never hardcode
  `C:\Users\...` or assume cp1252. **Base install stays torch-/GPU-free**,
  enforced by `check_requirements_sync.py` + `check_lockfile_universal.py`
  (GPU/torch deps live only in the opt-in `[local-voice]` extra); regenerate
  the lock only with `uv pip compile --universal`. The ONE advertised install
  path is the `[full]` profile; the torch-free base is the internal floor for
  CI and tiny servers (`--headless`).
- **Be recoverable IN-APP** — entering/switching/recovering a credential
  never requires hand-editing `jarvis.toml` or exporting ENV vars.
- **Store credentials portably** — OS keyring → ENV/.env → local-file
  fallback (`config._ensure_keyring_backend`); a save/connect must never 500
  on a host without a Secret Service.

### OS feature parity — macOS and Linux are first-class (BINDING)

Every feature ships working on **all three OSes in the SAME change**, never
as a "later" follow-up. OS-specific backends live behind ONE capability probe
(Win32/UIA ↔ AppleScript/Quartz/AXUIElement ↔ xdotool/AT-SPI/D-Bus);
`sys.platform == "win32"` with silent nothing elsewhere is a defect. Where a
backend is genuinely impossible (headless), degrade to a clearly-messaged
English no-op. A Windows-only implementation lands ONLY with the capability
gate + honest degradation + a tracked entry in
[`docs/os-parity.md`](os-parity.md).

**Definition of done** for any change touching config, credentials, a
provider/integration, or OS-specific code — verify the FOUR non-maintainer
paths (test or honest manual trace):

1. **Fresh install, ONE arbitrary key** reaches a working path (chat + voice
   + Jarvis-Agent + the touched feature), entirely in-app.
2. **Headless Linux** — base install + boot + the feature on
   `python:3.11-slim`; local-only parts degrade to a logged no-op.
3. **macOS** — works there, or degrades honestly + `docs/os-parity.md` entry.
4. **Cross-family fallback** — a dead/absent configured provider crosses to
   whatever the user actually has, or degrades honestly.

"It works on my machine" is the *defect* — the maintainer's box is <0.1 % of
the install base. Doctrine: [`CLOUD.md`](../CLOUD.md) +
[`docs/PHILOSOPHY.md`](PHILOSOPHY.md) (doctrine wins). See AP-21/22/23.

### Local-model currency (BINDING)

Every Ollama or llama.cpp model used as a default, recommendation, managed
profile, or current setup example must be checked against the official live
catalog when it is added or changed. Select the newest capable generation and
the strongest quantization that fits the target hardware honestly; never keep
an older id merely because it was once tested on the maintainer's machine. An
artifact whose official catalog entry is shown as one year old or older is
forbidden in those surfaces. A benchmark-specific compatibility pin may remain
only outside the general defaults, with its measured reason and the test or
condition that removes it. The live Ollama catalog guard is
`tests/integration/test_ollama_catalog_is_current.py`.

### Device-parity triage ritual (BINDING)

"Works on the dev box, broken on device X" has THREE independent causes —
check in order, name the layer (runbook:
[`docs/device-parity-debugging.md`](device-parity-debugging.md)):

1. **Version lag** — compare `GET /api/update/status` → `current` vs the dev
   box; uncommitted/unpublished work exists nowhere else, and the in-app
   updater only moves between **published** GitHub Releases.
2. **Setup divergence** — config, keys, and data never travel with the code;
   key-aware fallbacks (AP-22) degrade QUIETLY, so "broken" is usually
   "downgraded for lack of a key". Align providers, mode, and wake word
   in-app before debugging.
3. **OS gap** — assumed only after 1+2 match; then the parity rules above
   apply.

---

## 4. Naming — internal "Jarvis-Agents", user-visible brand is DYNAMIC (BINDING)

1. **Internal name** (code identifiers, files, docs, comments, commits, log
   lines, i18n KEYS, API paths): **Jarvis-Agents** / singular
   **Jarvis-Agent**. No other internal names.
2. **User-visible brand** (UI labels, TTS output, transcript labels, API
   strings the UI displays, tool-schema prose spoken by the router): derived
   from the configured wake word — "Hey Nova" → **"Nova-Agent(s)"**, any
   wake word likewise, neutral fallback **"Assistant-Agent"**. NEVER hardcode
   a fixed name in a user-visible string. Plumbing: i18n token `{name}-Agent`;
   `agentBrand`/`useAgentBrand` (`src/lib/agentBrand.ts`);
   `agent_brand`/`agent_brand_from_name`
   (`jarvis/brain/assistant_name.py`). Tests pin an arbitrary brand (e.g.
   "Nova-Agent"), never the host's live wake-word config.

**Glossary:** the retired internal codenames ("Subagents" / "sub_jarvis" /
the old bridge codename) stay dead repo-wide. The old bridge codename
survives ONLY where functional: (a) the external `openclaw` npm worker
binary — an outside project; invocation strings, env vars, and install
commands stay literal; (b) read-time back-compat aliases that keep old
installs booting (`[brain.worker]`/`[harness.jarvis_agent]` still accept
`[brain.sub_jarvis]`/`[harness.openclaw]`; legacy `openclaw_state` dirs are
recognized) plus their pinning tests. Do not reintroduce it elsewhere — and
do not "clean up" those aliases or literal binary strings either.

---

## 5. Architecture essentials (respect on every change)

Full model + module catalog:
[`docs/architecture-overview.md`](architecture-overview.md).

- **8-layer dependency rule:** higher layers reach lower ones only via
  protocols (`jarvis/core/protocols.py`); lateral communication only via
  typed `frozen=True` events on `EventBus` (`jarvis/core/bus.py`) carrying
  `trace_id` + `timestamp_ns`. A broken subscriber is logged in
  `_safe_dispatch`, never propagated (AP-18).
- **Plugins are structural:** `jarvis/plugins/<group>/<name>.py`, registered
  via `pyproject.toml` entry-points, no `jarvis.*` import in the plugin
  module. After editing entry-points: `pip install -e . --no-deps`. Groups:
  wakeword, stt, tts, brain, harness, tool, channel.
- **Streaming first:** Brain/STT/TTS/Harness methods return
  `AsyncIterator[...]` (non-streaming yields one element).
- **Secrets** only via `jarvis.core.config.get_secret` (keyring → ENV →
  `.env` → local file). Never in code / `jarvis.toml` / commits /
  `.claude/`. Installer signing PRIVATE keys live ONLY in GitHub Actions
  secrets (AP-29). Voice/chat must never accept secrets (AP-2).
- **Brain is multi-provider** — never hardcode Anthropic/Claude (AP-6); gate
  on capabilities (`supports_vision`, `supports_tools`, `can_call_tools()`),
  never provider/model ids (AP-21). Persona via
  `jarvis/brain/persona_loader.py`, never hardcoded.
- **Router discipline (ADR-0011):** the router-tier brain is a pure
  dispatcher over the `ROUTER_TOOLS` frozenset (`jarvis/brain/factory.py`);
  no spawn tool ever enters a worker set (AP-5/AP-14). Extending
  `ROUTER_TOOLS` → amend ADR-0011 + `tests/unit/brain/test_routing.py`.
- **Voice scrub:** brain→TTS goes through `scrub_for_voice`
  (`jarvis/brain/output_filter.py`) — regex only, no LLM call (AP-11,
  ADR-0010).
- **Atomic config writes:** mutate `jarvis.toml` only via
  `jarvis/core/config_writer.py` (lock + tempfile + BOM-safe, AP-7). The
  self-mod pipeline (Allowlist → Pre-Validate → Backup → replace → sync
  reload-test → Rollback → Audit) is non-negotiable (AP-13/14).
- **CLI-first feature contract (mandate 2026-07-11):** every user-facing
  capability ships its actions as REST routes under
  `jarvis/ui/web/*_routes.py`, mounted + tagged (enforced by
  `scripts/ci/check_cli_coverage.py`) — each action becomes a
  `jarvis api <tag> <op>` CLI command automatically. UI-only / internal-only
  / brain-tool-only features are NOT done. On top: voice/agent-relevant
  actions add a Command-Registry entry (`jarvis/commands/registry.py`,
  drift-gated by `gen_commands_reference.py --check`); destructive routes
  declare `openapi_extra={"x-jarvis-dangerous": True}`
  (`check_danger_metadata.py`); high-value routes get a curated
  `jarvis <group> <command>` (checklist: the `generate-cli-command` skill).
- **Multi-layer enum drift:** any value crossing Python ↔ SQL ↔ Pydantic ↔
  TS ↔ UI uses the five-layer pattern
  (`docs/anti-drift-three-layer.md`) + a parity test, preemptively (BUG-008
  recurred 4×).
- **Worker isolation:** every mission worker runs in a fresh `git worktree`
  under `<repo_parent>/jarvis-agent-outputs/` (legacy `sub-agents-outputs/`
  read as fallback) with kill-on-crash containment (Job Object on Windows,
  process-group reaper on POSIX). Headless installs keep outputs + per-user
  `HOME` under `JARVIS_DATA_DIR` (ADR-0027). `MAX_CRITIC_LOOPS = 3` is fixed.
- **Worker tool broker:** tools delegated to mission workers use a
  short-lived, mission-scoped supervisor grant (ADR-0025); tool objects and
  credentials stay in the supervisor; every call runs through `ToolExecutor`.
  Recursive/skill/secret/config-mutation tools are never exported; an
  unattended ask-tier action never becomes an implicit yes.
- **Native Windows Codex workers:** keep `--ignore-user-config`, use the
  ACL-bounded `unelevated` sandbox, recover rejected file-change tools only
  through BOM-free UTF-8 writes inside the current worktree (ADR-0026).
- **Platform gotchas:** UTF-8 stdout (Windows defaults cp1252); every
  subprocess passes `NO_WINDOW_CREATIONFLAGS` (AP-1); WASAPI audio, WDM-KS
  forbidden (BUG-014); no Windows Service (SYSTEM has no mic); UAC
  `asInvoker`, elevate per-action.

---

## 6. Safety / risk tiers

Four tiers `safe` / `monitor` / `ask` / `block`, priority **blacklist >
whitelist > tool default** (`jarvis/safety/risk_tier.py`); whitelist
downgrades to `safe` (anti-confirmation-fatigue contract). Direct
`Tool.execute()` is a bug — only `ToolExecutor.execute()` is authorized
(AP-3). Generated skills land as `state="draft"`, never auto-activated
(AP-15).

---

## 7. Critical anti-patterns (do not do this)

| # | If you do this... | ...you get this bug |
|---|---|---|
| AP-1 | `subprocess.Popen` without `NO_WINDOW_CREATIONFLAGS` | BUG-012 flicker storm under `pythonw.exe` |
| AP-2 | Accept API keys via voice/chat | STT log leak — credential exfiltration |
| AP-3 | Call `Tool.execute()` directly | Risk-tier/whitelist/plausibility skipped |
| AP-4 | Add a `hangup_reason`/mission-status string in one site only | BUG-008: HTTP 500, empty UI |
| AP-5 | Put a spawn/`dispatch-with-review`/`run-skill` tool in a worker tool set | Recursion: worker spawns supervisor |
| AP-6 | Hardcode the `Claude`/`Anthropic` API client | Breaks `cfg.brain.primary` for non-Anthropic users |
| AP-7 | Write `jarvis.toml` without `_WRITE_LOCK` + tempfile + BOM handling | BUG-018: corrupted TOML, backend won't boot |
| AP-8 | Skip `scripts/preflight.ps1` in a new worktree | BUG-006/014: edits go where live Python doesn't import from |
| AP-9 | Run awareness/wiki code in the voice critical path | Latency regression — awareness stays off the hot path |
| AP-10 | Write a worker without `git worktree` + Job Object | Races + zombie processes on crash |
| AP-11 | Add an LLM call inside `scrub_for_voice` | TTS latency tank |
| AP-12 | Encode API keys in `jarvis.toml` or commit `.env` | Credential leak, bypasses keyring audit |
| AP-13 | Block on watchdog reload for atomic-write verification | Race: file half-applied, no sync rollback |
| AP-14 | Re-add a sub-tier or `SUB_TOOLS` set | Breaks the agent-harness bridge contract |
| AP-15 | Auto-activate generated skills (`state` ≠ `draft`) | Skills run without review |
| AP-16 | Add `[phase6.*]`/`[memory.wiki.*]` keys without `ConfigDict(extra="allow")` | Pre-validate rejects → boot fails after self-mod |
| AP-17 | Run Jarvis as a Windows Service | SYSTEM has no mic/headset access |
| AP-18 | Propagate a subscriber exception from `EventBus._safe_dispatch` | One handler kills the pipeline |
| AP-19 | Reuse a process-global progress counter in a stall watchdog without per-unit reset | BUG-032: watchdog fires between units, aborts a fresh answer |
| AP-20 | `continue` a WS receive loop on a non-`WebSocketDisconnect` error | Loop spins on a dead socket; catch `RuntimeError` and `break` |
| AP-21 | Pin a feature to a provider name/model id instead of a capability | Breaks for every other provider; fix the capability flag instead |
| AP-22 | Give a tier a primary AND fallback in the SAME provider family, or hardcode provider names in a chain | Single-provider brick; resolve every tier through one key-aware, family-crossing chain that degrades honestly |
| AP-23 | Build or test only against the maintainer's config/keys/OS and claim done | Whole surface bricked for every other downloader; verify §3's paths |
| AP-30 | Catch an exception and neither log, re-raise, nor say why silence is right | The feature just does nothing and nobody can tell it failed — the single largest source of "it only half works" (gate: `check_silent_exception_handlers.py`) |
| AP-31 | Add a config field nothing reads, or leave a switch whose value is ignored | `jarvis.toml` and the settings screen promise behaviour that does not exist (gate: `check_config_switches_wired.py`) |

### AP-24 — Never share a native inference engine between concurrent callers

ctranslate2/faster-whisper and ONNX/torch sessions are not thread-safe; a
concurrent call wedges them permanently, and a timeout only BOUNDS a hung
`to_thread`, never recovers it. Fix: a non-blocking per-instance inference
lock (second caller skips) + a `recover()` that rebuilds a FRESH model after
N failures (BUG-036). Never "recover" by re-polling the same wedged engine.

### AP-25 — Never enable GPU wake on CUDA *presence* or a hardware name

CUDA presence and CUDA *usability* diverge (a present-but-broken runtime once
left wake permanently deaf). Gate the GPU wake upgrade ONLY on the
out-of-process **inference probe**
(`jarvis.plugins.stt._wake_gpu_inference_verified`: one real turbo/cuda
transcribe in a killable subprocess, stdout-marker verdict — never the exit
code; cached per ctranslate2 version in `data/wake_gpu_probe.json`). Keep the
probe off the boot path (background hot-swap only), a FIXED `cpu_threads` on
the CPU floor, and the live backstop (a wedge swaps back to the retained
cpu fallback + `mark_wake_gpu_bad()`). `[stt].wake_high_accuracy=false` is
the hard opt-out. Truly instant custom-word wake needs a trained neural KWS
model, not transcription. Detail:
`docs/local-wakeword/WAKE-RELIABILITY-DEEPDIVE.md`.

### AP-26 — Never put a feature's init/import on the startup critical path

Nothing initializes before `APP_INTERACTIVE` / `VOICE_USABLE`: no sync load
in `_run_backend`, the `WebServer` ctor, or `_start_speech_and_orb`; no
module-level heavy import. New subsystems hook into `_heavy_backend_bg`, a
deferred registry scan, or a post-ready task; heavy imports stay lazy; routes
answer 503/None while warming. Measured by the BOOT BUDGET harness
(`scripts/ci/check_boot_budget.py`: window ≤ 8 s, voice-usable +
app-interactive ≤ 20 s). It is an **on-demand local command**, not a push
gate (2026-08-05): it boots the whole application, so as a pre-push hook it
cost a full cold boot per push and counted the inevitable failed boot of a
dependency-less fresh worktree as a budget violation. It has no CI home
either — a GitHub runner has no audio device, so the harness cannot measure
`VOICE_USABLE` there and would either self-skip or false-fail. Run it by
hand after touching the startup path:
`python scripts/ci/check_boot_budget.py`. Doctrine:
`docs/diagnostics/BOOT-TTU-NOTES.md`.

### AP-27 — Never gate a wake word on transcript CONTENT

This is a property of EVERY wake engine that verifies via a transcript, not
an engine quirk: a bias-primed model hallucinates the phrase on silence AND
garbles it on real speech, and every wake word is out-of-vocabulary for some
installed language model — so any content rule that suppresses ghosts also
rejects genuine wakes ("fires on silence" and "goes deaf" share ONE root),
and no spelling threshold can separate them. Verify only on WORD-AGNOSTIC
properties: raw audio ENERGY at the match site (`stt_match`:
`RollingWhisperWake._match_min_rms`; keep the bias-echo confirm PERMISSIVE
and skip it on a loud window) and the SHAPE of the candidate span
(`vosk_kws`: `candidate_shape_ok` — duration, word count, free-decoder
confidence, all derived from the configured phrase, never its spelling). A
spelling match may only ever ACCEPT (bonus path), never reject. Guards:
`tests/unit/speech/test_rolling_whisper_wake_silence_ghost.py`,
`tests/unit/plugins/wake/test_vosk_wake_word_agnostic.py`. Measurements +
history: `docs/local-wakeword/WAKE-RELIABILITY-DEEPDIVE.md`, BUG-037.

### AP-28 — Never gate CI checks on `isinstance` against an unpinned third-party lib

CI installs newest; the lib's next release changes internals and the gate
false-fails in CI while green locally (Typer 0.26 vendored its own Click,
fix 621f837a). Discriminate by CAPABILITY (`.commands`, Click's stable
`param_type_name`), never concrete type. Before anything goes public: CI must
be green and every red cause understood — reproduce against the SAME unpinned
versions CI installs, not your local pins.

### AP-29 — Never commit a signing PRIVATE key, its passphrase, or a `*.key.enc` copy

Installer signing private keys (Wave 2 Ed25519, Wave 4 ML-DSA-65) live ONLY
in GitHub Actions secrets (`WAVE2_OFFLINE_KEY_B64` / `WAVE4_MLDSA65_KEY_B64`),
backup in the maintainer's password manager; the repo holds ONLY public keys
(`install/keys/*.pub*`) + inlined verifier copies. (An earlier in-repo
"encrypted at rest" scheme leaked its passphrase into 14 permanent public
snapshots.) Rotation = new keypair → `gh secret set` → swap public keys +
verifier blocks + fingerprints. Enforced by
`scripts/ci/check_no_private_keys.py` (pre-commit + pre-push). Doctrine:
`docs/supply-chain/wave2-key-ceremony.md`.

---

## 8. Recurring bug classes (signal → defense)

Detail in [`docs/BUGS.md`](BUGS.md):

1. **Restore trap** (BUG-006/014/015) — fix "works in tests" but behavior
   unchanged after restart → `pwsh scripts/preflight.ps1` +
   `python -c "import jarvis; print(jarvis.__file__)"`.
2. **Enum drift** (BUG-008) — empty UI list while the DB has rows, Pydantic
   `literal_error` → five-layer pattern + parity test.
3. **Config drift** (BUG-010) — parallel sessions silently rolling back
   `jarvis.toml` switches → `scripts/drift-guard-daemon.ps1` + ENV overrides
   + BOM-safe writer.
4. **Console flicker** (BUG-012) — import `NO_WINDOW_CREATIONFLAGS` from
   `jarvis.core.process_utils`.
5. **Audio host-API trap** (BUG-014) — WDM-KS auto-picked, PortAudio crashes
   → `_FORBIDDEN_OUTPUT_HOSTAPIS` + shortest-unique-token matching.
6. **Stale watchdog counter** (BUG-032) — watchdog fires between units →
   reset the counter per unit of work (AP-19).
7. **Socket loop on teardown error** — treat any read error as terminal,
   `break` (AP-20).
8. **Wedged native inference** (BUG-036) — non-blocking lock + rebuild a
   fresh model (AP-24).
9. **Wake transcript traps** (BUG-037) — "fires on silence" / "goes deaf on
   its own wake word" share one root; word-agnostic verification only
   (AP-27).

---

## 9. Operational reality & Git workflow

- **The working tree is frequently SHARED** — several agent sessions edit it
  at once. Never assume the staged diff is only yours; commit hunk-isolated
  (`git add -p` / pathspec-scoped). A large uncommitted diff is normal. On a
  corrupted-looking index/HEAD, recover via temp-index commit + `update-ref`
  CAS + safety branch (`git-rescue` on repo-wide disorder).
- **Desktop lifecycle actions are maintainer-gated.** Coding agents must not
  restart, quit, kill, or relaunch the desktop app unless the maintainer
  explicitly authorizes that exact lifecycle action in the current
  conversation. A task such as "finish", "verify", or "fix it" is not restart
  approval, and `--yes` is not human presence. Before an authorized action,
  verify the exact Personal Jarvis PID and command line with read-only checks;
  target only that process and never a name-wide Python/process set. Prefer the
  supported in-app relauncher. If the UI is absent, an authorized agent may
  terminate only the verified process and relaunch `jarvis.ui.web.launcher`
  with the same interpreter and repository working directory. Verify the new
  PID, API reachability, and desktop window afterward. Without explicit
  approval, state why a fresh process is needed and let the maintainer click
  Restart in the desktop UI. The server still rejects Control-API/Bearer
  restart requests so unattended automation remains fail-closed.
- **Jarvis is used as a DESKTOP APP, not a browser tab.** The window is an
  embedded WebView (`jarvis/ui/desktop_app.py`) with no address bar, no
  reload button and no dev tools, so "press Ctrl+R / F5", "hard-refresh",
  "clear the browser cache" and "open the console" are instructions that
  cannot be followed — never end a frontend change with one. A frontend fix
  reaches the user in exactly ONE step: `npm run build` in
  `jarvis/ui/web/frontend/` (the app serves the built `dist/`, never Vite).
  Every open window then picks the new bundle up by itself within seconds —
  `src/lib/bundleWatch.ts` compares the hashed assets the server would hand a
  fresh window against the ones this one is running, and reloads once the
  change has settled and the user has stopped typing. **Do not end a frontend
  change by asking for a restart**: restarting stops the voice stack and the
  live terminal panes to replace files the window fetches on its own. Running
  from a browser against `--dev` is a contributor path, not the maintainer's.
- **Frontend changes ship in BOTH themes.** The app has a light and a dark
  mode, and the Agentic IDE's terminal panes carry their own light/dark
  appearance on top that may disagree with the app theme. A UI change is done
  only when it reads its colours from the theme system — the app's CSS tokens
  (`--background`, `--primary`, …) for app-level surfaces, the per-appearance
  tables in `jarvis/ui/web/frontend/src/components/agentic/terminalThemes.ts`
  (`PANE_BRAND` / `PANE_CHROME`) for anything on a pane's own ground — and has
  been checked legible in both modes. Hardcoding one mode's colours is a
  defect, not a default. (Full rule: `CLOUD.md` → "Frontend theming".)
- **New worktree:** run `pwsh scripts/preflight.ps1` before writing code;
  non-zero exit → fix first (BUG-006/014).
- **Memory:** check `MEMORY.md` (`~/.claude/projects/.../memory/`) before
  larger decisions.

### Git workflow

*(Also pinned in the maintainer's personal global config; restated here so
every contributor and agent follows it.)*

- **Auto-commit after each completed logical step**, staging only YOUR files
  by explicit path or `git add -p` — never `git add -A` / `git add .`
  (sweeps another session's in-flight, possibly secret-bearing work in).
- **Conventional-Commit messages** (`feat:`, `fix:`, `refactor:`, `docs:`,
  `chore:`).
- **Never push automatically** — only when the maintainer explicitly says so
  (still honoring §2).
- **Never commit secrets / `.env` / keys / tokens.** If any appear
  untracked: stop, flag, don't commit.
- **Pushing is one command.** `git push <remote> <branch>` — nothing is built,
  copied, cloned, or audited on the way (§2). The hooks take about a second
  and a half; if a push turns into a work session, something regrew that
  belongs in CI. When a hook does block, it found a credential: fix the
  finding, never reach for `--no-verify` as the first move.

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
pytest tests/integration/ -v       # phase-level E2E (self-skips when prereqs missing)
pytest tests/missions/ -v          # Phase 6
pytest -m "not slow"               # fast subset
# Markers: phase5, skip_ci, e2e, voice_latency, eval, slow, integration.
# Targeted guards: test_routing.py (router), test_output_filter.py (scrubber),
# test_hangup_reason_parity.py (enum drift). New STT/Brain/Tool/Channel
# providers must pass tests/contract/.
```

---

## 11. Pointers

- **Architecture + module catalog:**
  [`docs/architecture-overview.md`](architecture-overview.md).
- **Doctrine:** [`CLOUD.md`](../CLOUD.md),
  [`docs/PHILOSOPHY.md`](PHILOSOPHY.md).
- **Bug register:** [`docs/BUGS.md`](BUGS.md). **Anti-drift:**
  [`docs/anti-drift-three-layer.md`](anti-drift-three-layer.md).
  **Self-Mod:** [`docs/self_mod.md`](self_mod.md).
- **ADRs:** `docs/adr/0001..0023` (ADR-0001 superseded by ADR-0020).
  **Phase docs:** `docs/phase{0,1,1a,1c,2,4,5,6}-*.md`.
- **Operational scripts:** `scripts/preflight.ps1`,
  `scripts/drift-guard-daemon.ps1`, `scripts/README-auto-push.md`.
- **Jarvis control CLI** (drive a running Jarvis from a terminal/agent):
  `docs/jarvis-cli.md` + generated `docs/jarvis-cli-reference.md`
  (`jarvis/cli_ctl/`, binaries `jarvis`/`jarvisctl`/`jctl`). New REST routes
  must stay mounted — enforced by `scripts/ci/check_cli_coverage.py`.
