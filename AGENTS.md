# Personal Jarvis agent rules

The binding rules for every coding agent in this repo — Claude Code, Codex,
Gemini CLI, whichever. This is the whole rulebook; there is no longer a fuller
version to read first. Write everything here so it addresses ANY agent.

**Source of truth:** `AGENTS.md` is the only instructions file; every agent
reads it directly. Never add a `CLAUDE.md`, `.claude/CLAUDE.md` or
`CLAUDE.local.md`: Claude Code then reads that file INSTEAD of this one
(`scripts/ci/check_agents_md.py` blocks a tracked one). A Claude pane on a
Jarvis account config dir sees `~/.claude/CLAUDE.md` as such a parent-dir file,
so its Project instructions setting must be `claude-md-and-agents-md`.
Edit `.agents/{agents,skills}/`; `.claude/{agents,skills}/` are compatibility
copies because Claude Code does not discover subagents or skills under
`.agents/`, and `.codex/agents/*.toml` is generated from `.agents/agents/*.md`
for Codex. The sync scripts and CI check these copies; never edit a generated
copy directly.

---

## 1. Traps no one guesses

These cost real bugs. Nothing catches them but you.

- **Restore trap.** A fix works in tests and changes nothing after restart:
  live Python imports from elsewhere. New worktree → `pwsh scripts/preflight.ps1`,
  then `python -c "import jarvis; print(jarvis.__file__)"`. (AP-8) Never
  `pip install -e` from a linked worktree: that pin is the user's desktop app,
  and their next restart boots your branch with an empty `data/`. (BUG-219)
- **The working tree is SHARED** with other agent sessions. Stage only YOUR
  paths — `git add -p` or an explicit pathspec, and `git commit --only -- <paths>`.
  `git add -A` sweeps someone else's half-finished work into your commit.
- **A probe against the running app spends the user's real money.** Turns sent
  to it (`/ws`, `jarvis` CLI, Telegram) bill their own keys: five scripted
  test turns once cost $6.82 on a deep model. Send one turn, on a subscription
  or `tests/fakes/`, never a loop against a paid provider.
- **`jarvis.toml` only through `jarvis/core/config_writer.py`** — lock, tempfile,
  BOM-safe. A raw write leaves the backend unbootable. (AP-7)
- **Never share a native inference engine** (ctranslate2, ONNX) between callers.
  It wedges permanently and a timeout does NOT recover it: non-blocking
  per-instance lock + `recover()` that rebuilds a fresh model. (AP-24)
- **Every `subprocess` passes `NO_WINDOW_CREATIONFLAGS`** from
  `jarvis.core.process_utils`, and stdout is UTF-8 (Windows defaults cp1252). (AP-1)
- **Don't "clean up" the `openclaw` strings.** The external binary name and the
  read-time back-compat aliases are load-bearing; retired codenames stay dead
  everywhere else.
- **A stall watchdog resets its counter per unit of work** (AP-19); a WebSocket
  receive loop treats ANY read error as terminal and breaks (AP-20); a subscriber
  exception never leaves `EventBus._safe_dispatch` (AP-18).
- **Every reconnect is jittered and pays the shared connect budget.** A retry
  grid with no jitter, or a wake handler that opens a socket directly, is not
  an app bug: N panes x M windows x 2 instances firing on one
  `visibilitychange` empties the OS ephemeral-port pool and NOTHING on the
  machine can connect for two minutes. Frontend goes through
  `lib/connectBudget.ts`; a poll reuses a client from `jarvis/core/http_pool.py`.
  (AP-33)
- **No Windows Service** — SYSTEM has no microphone. (AP-17)
- **Never gate a CI check on `isinstance` against an unpinned library.** Green
  locally, red in CI on the next release. Discriminate by capability. (AP-28)
- **The desktop app is a WebView** — no F5, no console, no dev tools. A frontend
  fix is `npm run build` in `jarvis/ui/web/frontend/` and nothing else; open
  windows reload themselves (`src/lib/bundleWatch.ts`). Never end a frontend
  change by asking for a restart.
- **Read `MEMORY.md`** (`~/.claude/projects/.../memory/`) before larger decisions.

## 2. What the product is

Assume an arbitrary downloader, never the maintainer — their box is <0.1 % of
the install base, and "works on my machine" is the defect. (AP-23)

- **Any single key must work.** Gate on capability, never a provider name or
  model id (AP-21). A tier whose primary AND fallback share one provider family
  is a brick — every chain crosses families or degrades honestly (AP-22).
- **Nothing the user did not start may bill a key on its own.** Health dots
  come from real calls, never a paid probe. Once a subscription is connected,
  background work runs only on subscriptions or local models
  (`jarvis/brain/background_policy.py`); a failing subscription makes it WAIT,
  never fall back to an API key. An agent's own memory updates run on that
  agent's seat. Idle once burned 5 EUR in three hours on wiki calls.
- **Every OS**, including a headless `python:3.11-slim` with no GPU, audio or
  native API: base install + boot must succeed there. Extras group, environment
  marker or lazy import — whichever fits.
- **Credentials are recoverable IN-APP** (keyring → ENV → file). Never make
  someone hand-edit `jarvis.toml` or export a variable.
- **Enumerate providers from the CODE** (`jarvis/core/config.py`,
  `jarvis/realtime/factory.py`), never from this box's `jarvis.toml`.
- **Everything committed is ENGLISH** — code, comments, docstrings, logs, commit
  messages. German only on the closed product surface: runtime voice/chat output,
  i18n files, speech-input vocabulary, and tests quoting them
  (`scripts/ci/german-allowlist.txt`, inline `i18n-allow`). Translate legacy
  German in files you touch. Runtime output language is decided ONCE per turn by
  `jarvis/core/turn_language.py`; no layer re-derives it, all locales are equal.

**Proportionality.** The agent owns the validation plan and chooses the smallest
set of checks that can detect a plausible regression from the diff. State the
scope, what was run, and what remains unverified in the PR. Use these tiers as
guidance, not as automatic checklists:

- **T1 local:** for copy, styling, docs, one view, or an isolated refactor, run
  focused checks for that surface. A frontend change still needs a production
  build and light/dark inspection when its appearance changes.
- **T2 one surface:** test the affected adapter, transport, channel, or OS
  backend and its nearest callers. Check other platforms when the changed path
  can execute there; state why an unavailable platform is unaffected or how it
  degrades.
- **T3 shared contract:** prove the changed behavior with contract tests and
  exercise the affected providers and OSes. Update `docs/os-parity.md` when OS
  behavior changes. A new provider or credential path needs a fresh-install,
  one-key proof; a schema-only change does not automatically owe that test.

Escalate evidence when risk crosses a boundary, not because a label is
ambiguous. Do not claim unrun platforms, devices, or providers were verified.
If a required check is red on the exact base commit, compare failure identities
and causes under the same environment. A failure already present on base may
be reported as a separate backlog item; a new failure or an unexplained change
in a failing test blocks completion. Counts alone are not a comparison. Do not
silence a required check, raise a baseline, or bypass branch protection to make
a PR appear green.

## 3. Architecture you must respect

Higher layers reach lower ones only through `jarvis/core/protocols.py`; lateral
traffic is `frozen=True` events on `EventBus` carrying `trace_id`. Plugins live
under `jarvis/plugins/<group>/`, register via entry-points, import no `jarvis.*`
(then `pip install -e . --no-deps`). Brain/STT/TTS/Harness are streaming-first.
Secrets only via `get_secret` — never in code, `jarvis.toml`, or a commit, and
voice/chat must never accept one (AP-2). Signing private keys live only in
GitHub Actions secrets (AP-29). The router is a pure dispatcher over
`ROUTER_TOOLS` (ADR-0011) and no spawn tool ever enters a worker set (AP-5/14);
extending it means amending the ADR and `test_routing.py`. `scrub_for_voice` is
regex-only, never an LLM call (AP-11). Any value crossing Python ↔ SQL ↔ Pydantic
↔ TS ↔ UI uses the five-layer pattern plus a parity test (AP-4). Mission workers
run in a fresh `git worktree` with kill-on-crash containment (AP-10). Nothing
initializes on the boot critical path (AP-26). Risk tiers are safe / monitor /
ask / block with blacklist > whitelist > default; only `ToolExecutor.execute()`
is authorized (AP-3), and generated skills stay `draft` (AP-15).

Two the gates catch but models still write: never swallow an exception without
logging, re-raising, or saying why silence is right (AP-30), and never add a
config field nothing reads (AP-31).

Marketplace plugin auth follows the browser-auth standard
(`docs/marketplace/browser-auth-standard.md`): a normal user connects with one
browser approval and zero developer setup; the publisher provisions the shared
OAuth client (`publisher_<family>_oauth_*` secrets), never the end user. A new
auth-bearing plugin is not releasable while its default path needs pasted
tokens, user-supplied client IDs/secrets, or self-registered developer apps —
own-client stays an expert override only. Every catalog change keeps
`docs/marketplace/plugin-auth-audit.md` and `scripts/ci/check_plugin_auth_contract.py`
green; provider error bodies never reach logs, UI, or storage (AP-34).
Keep `docs/marketplace/plugin-e2e-audit.json` backed by real browser evidence;
new built-ins require PASS and release qualification uses the auth gate's
`--require-e2e-pass` option. BLOCKED never means provider-verified or complete.

The rest of the register, one line each, because code comments cite these
numbers: never hardcode an Anthropic/Claude client (AP-6); keep awareness and
wiki code off the voice critical path (AP-9); never put a key in `jarvis.toml`
or commit `.env` (AP-12); never block on a watchdog reload to verify an atomic
write (AP-13); never reintroduce a sub tier or `SUB_TOOLS` set — Wave 4 deleted
it (AP-14); new `[phase6.*]` / `[memory.wiki.*]` keys need
`ConfigDict(extra="allow")` or pre-validate rejects them (AP-16); gate the GPU
wake upgrade only on the out-of-process inference probe, never on CUDA presence
(AP-25); verify a wake word on audio energy and candidate shape, never on
transcript content (AP-27); a WebGL scene releases its context and survives
losing it (AP-32); a reconnect without jitter and without a shared connect
budget is an outage of the whole machine, not an app bug (AP-33). Detail and
history for any of them: `docs/BUGS.md`.

## 4. How work ships

**World and character art:** follow `docs/agent-society/game-art-pipeline.md`
and the `game-art-pipeline` skill before creating/redesigning game assets.
Author a small reference in Blender and verify it in the actual runtime before
rolling its style out across asset families; no separate user approval of the
reference is required. Existing runtime contracts stay binding.

Commit each finished step (Conventional Commits). Use the coding agent's
standard Git workflow: do not artificially leave completed work local, and do
not wait for extra PersonalJarvis permission to commit, branch, push, or open
a pull request. Once the scoped review and required checks pass, the agent may
merge an ordinary authorized PR without another confirmation; a draft PR stays
draft until its remaining work is done. `git pull --rebase --ff-only` first if
origin moved. Never
`--force`, never `--no-verify`. Isolated mission workers must not run git
(`add`/`commit`/`branch`/`checkout`/`push`) — the parent runtime captures the
diff and lands it. Never push from a linked mission worktree. **A push is
`git push`:** nothing is built, cloned, audited, or reviewed on the way.
Review happens when code is written, never when it is published. A check that
reads the whole tree belongs in CI, never in `pre-push`. A release (SemVer +
tag + CHANGELOG + published GitHub Release) happens ONLY when explicitly
asked — an ordinary push is not a release.

Every frontend change works in BOTH light and dark mode, and on the terminal
panes' own appearance — colours come from theme tokens or the per-appearance
tables in `terminalThemes.ts`, never one hardcoded mode.

**Runtime restarts:** agents may restart the desktop app and related development
processes when needed to apply or verify authorized work, without asking for
additional approval. Announce the reason, prefer programmatic lifecycle/process
control over desktop UI automation, and verify that the application returns
healthy. This does not authorize stopping
unrelated processes or leaving the desktop app shut down.

Local model defaults (Ollama/llama.cpp) are checked against the live catalog
when changed; nothing a year old or older ships as a default.

## 5. Run & test

`pip install -e . --no-deps` + `-r requirements.txt` + `".[dev]"`; launch
`run.bat` (`--headless` = API only). Choose focused `pytest` and lint targets
for the diff; use fakes from `tests/fakes/`, never `unittest.mock`. Run the
full suite when a change has broad reach or focused tests cannot bound its
risk. New providers pass their `tests/contract/` family. Four guards remain
required in CI: `test_routing`, `test_output_filter`,
`test_hangup_reason_parity`, `test_turn_language`.

The commit and push hooks block confirmed secret, private-key, withheld-path,
language, and broken-bundle additions. CI (`docs/ci-pipeline.md`) has ONE
required check, `CI gate`, over change-classified lanes; a test failure blocks
unless it is listed in `scripts/ci/test-baseline-<os>.json`. Finished work in
your own worktree lands with `python scripts/agent_land.py` (rebase onto main,
auto-resolve generated files, gates, relevant tests, push); a `codex/`,
`claude/`, `agent/` branch or an `auto-merge` label puts a PR on the merge
train, which keeps it current with main and squash-merges it once green.
Triage any red job against the exact base; never add to a baseline to hide a
new failure.
Run `check_boot_budget.py` after touching startup; CI cannot measure the live
voice-ready path.

**Pointers:** [`docs/architecture-overview.md`](docs/architecture-overview.md) ·
[`docs/BUGS.md`](docs/BUGS.md) (symptom → cause) · `docs/adr/` ·
[`docs/os-parity.md`](docs/os-parity.md) ·
[`docs/jarvis-cli.md`](docs/jarvis-cli.md).
