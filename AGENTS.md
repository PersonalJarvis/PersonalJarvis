# CLAUDE.md

The binding rules for every coding agent in this repo — Claude Code, Codex,
Gemini CLI, whichever. This is the whole rulebook; there is no longer a fuller
version to read first. Write everything here so it addresses ANY agent.

**Twin:** `AGENTS.md` is byte-identical to this file. `.claude/{agents,skills}/`
↔ `.agents/{…}`, and `.codex/agents/*.toml` is a generated projection of
`.claude/agents/*.md` — never hand-edit it. Three sync engines hold all of it
(`sync_agents_md.py`, `sync_agents_dir.py`, `sync_codex_agents.py`); a hook,
pre-commit and CI run them. Edit the canonical side and let them work.

---

## 1. Traps no one guesses

These cost real bugs. Nothing catches them but you.

- **Restore trap.** A fix works in tests and changes nothing after restart:
  live Python imports from elsewhere. New worktree → `pwsh scripts/preflight.ps1`,
  then `python -c "import jarvis; print(jarvis.__file__)"`. (AP-8)
- **The working tree is SHARED** with other agent sessions. Stage only YOUR
  paths — `git add -p` or an explicit pathspec, and `git commit --only -- <paths>`.
  `git add -A` sweeps someone else's half-finished work into your commit.
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

**Proportionality — the evidence a change owes scales with what it touches.**
Name the tier in one line, then owe only that tier.
**T1 local** (styling, copy, one view, a test, a doc, a refactor behind one call
site) → nothing extra. A matrix here is noise, not diligence.
**T2 one surface** (an existing adapter, transport, channel or OS backend,
shared contract unchanged) → name the cells you touched and what the others do
(unchanged / emulated / degraded), plus tests for that family.
**T3 contract** (a capability, shared interface, turn-taking, credentials,
config schema, a NEW provider/transport/OS backend) → the full treatment: all
three OSes in the same change behind one capability probe, `tests/contract/`
per family, `docs/os-parity.md` updated, and a fresh install with ONE arbitrary
key verified end to end. Unsure between T2 and T3 → it is T3. Over-tiering is
also a defect.

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
losing it (AP-32). Detail and history for any of them: `docs/BUGS.md`.

## 4. How work ships

Commit each finished step (Conventional Commits). Pushing is NOT automatic —
push only when explicitly asked. `git pull --rebase --ff-only` first if
origin moved. Never `--force`, never `--no-verify`, never push from a linked or
mission worktree — the parent lands that work. **A push is `git push`:** nothing
is built, cloned, audited, or reviewed on the way. Review happens when code is
written, never when it is published. A check that reads the whole tree belongs
in CI, never in `pre-push`. A release (SemVer + tag + CHANGELOG + published
GitHub Release) happens ONLY when explicitly asked.

Every frontend change works in BOTH light and dark mode, and on the terminal
panes' own appearance — colours come from theme tokens or the per-appearance
tables in `terminalThemes.ts`, never one hardcoded mode.

**Maintainer-gated:** restarting, quitting or killing the desktop app needs
explicit approval for that exact action in the current conversation. "Fix it"
or "verify" is not approval. Explain why a restart is needed and let them click
Restart.

Local model defaults (Ollama/llama.cpp) are checked against the live catalog
when changed; nothing a year old or older ships as a default.

## 5. Run & test

`pip install -e . --no-deps` + `-r requirements.txt` + `".[dev]"`; launch
`run.bat` (`--headless` = API only); `ruff check jarvis/ && ruff format jarvis/
&& mypy jarvis/`; `pytest tests/` with fakes from `tests/fakes/`, never
`unittest.mock`. New providers pass `tests/contract/`. Four guards must stay
green and have their own blocking CI step: `test_routing`, `test_output_filter`,
`test_hangup_reason_parity`, `test_turn_language`.

Everything else is enforced by a gate in `scripts/ci/` via pre-commit and CI —
German, private keys, bundle consistency, CLI coverage, danger metadata, unwired
switches, silent handlers, import cleanliness, the dependency/lockfile matrix,
and the three mirrors. Don't spend attention re-checking them by hand; a failure
fails the build. The one exception with no gate: run `check_boot_budget.py`
yourself after touching startup.

**Pointers:** [`docs/architecture-overview.md`](docs/architecture-overview.md) ·
[`docs/BUGS.md`](docs/BUGS.md) (symptom → cause) · `docs/adr/` ·
[`docs/os-parity.md`](docs/os-parity.md) ·
[`docs/jarvis-cli.md`](docs/jarvis-cli.md).
