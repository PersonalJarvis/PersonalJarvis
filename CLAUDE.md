# CLAUDE.md

Binding rules for every coding agent — compressed index. The FULL contract
(same section numbers, unabridged wording + rationale) lives in
[`docs/agent-contract.md`](docs/agent-contract.md); read it before deep work.

## 0. Mirror rule (BINDING)
`CLAUDE.md` ≡ `AGENTS.md` (byte-identical twins); `.claude/{agents,commands,skills}/`
≡ `.agents/{...}`. Sync engines run as hook, pre-commit, and CI gate. Everything
in them addresses EVERY coding agent, never Claude Code alone.

## 1. Language (BINDING, HIGHEST PRIORITY)
Every committed artifact is ENGLISH — code, comments, docs, commits, tests, CLI
help, UI source strings. German ONLY on the closed product surface (runtime
voice/chat output, i18n files, speech-input vocabulary, tests quoting them);
register: `scripts/ci/german-allowlist.txt` / inline `i18n-allow`. Translate
legacy German you touch. Runtime output language: ONE resolver
(`jarvis/core/turn_language.py::resolve_output_language`) decides each turn for
ALL layers (reply, acks, canned phrases, TTS voice); no layer re-derives it;
all locales (de/en/es/…) are equal.

## 2. GitHub (BINDING)
ONE public repo: `github.com/PersonalJarvis/PersonalJarvis`. **A push is
`git push`** — no staging trees, clones, builds, or file-by-file audits.
Protection: `.gitignore` first (data/, .env, jarvis.toml, Vault, keys never
tracked); never commit credentials; secret scanning stays ON; whole-tree checks
belong in CI, never pre-push. Default = plain push; Release ONLY when
explicitly asked (SemVer + tag + CHANGELOG + published GitHub Release;
`check_release_completeness.py` before tagging and `--verify-release` after).
Never push unless the maintainer asks.

## 3. Open-source universality (BINDING)
Assume an arbitrary downloader, never the maintainer: ANY single key/provider
works (capability-gated, cross-family fallback, honest degradation, AP-21/22);
every OS incl. headless `python:3.11-slim` (base install stays torch-free; GPU
deps only in `[local-voice]`); credentials recoverable IN-APP, stored keyring →
ENV → file. macOS/Linux ship in the SAME change behind one capability probe or
degrade honestly (+ `docs/os-parity.md` entry). Definition of done = the four
non-maintainer paths (§3 detail). Device triage order: version lag → setup
divergence → OS gap (`docs/device-parity-debugging.md`).

## 4. Naming (BINDING)
Internal: **Jarvis-Agents**. User-visible brand is DYNAMIC from the wake word
(`{name}-Agent`, fallback "Assistant-Agent") — never hardcode a name in a
user-visible string. Retired codenames stay dead; the external `openclaw`
binary strings and the read-time back-compat aliases stay AS-IS.

## 5. Architecture essentials
8-layer rule (protocols downward, frozen events on `EventBus` laterally);
plugins via entry-points, no `jarvis.*` import inside (after entry-point edits:
`pip install -e . --no-deps`); streaming-first (`AsyncIterator`); secrets only
via `get_secret`; brain is multi-provider + capability-gated (never hardcode a
provider/model); router = pure dispatcher over `ROUTER_TOOLS` (ADR-0011);
`scrub_for_voice` is regex-only; `jarvis.toml` only via `config_writer.py`;
CLI-first contract (every feature ships REST routes → auto-CLI + registry entry
+ danger metadata); five-layer enum pattern for any cross-layer value; mission
workers in fresh git worktrees with kill-on-crash + tool broker (ADR-0025/26);
UTF-8 + `NO_WINDOW_CREATIONFLAGS` on every subprocess. Safety: tiers
safe/monitor/ask/block, blacklist > whitelist > default; only
`ToolExecutor.execute()`; generated skills stay `state="draft"`.

## 7. Anti-patterns AP-1..AP-31 (BINDING)
Full register with the bug each causes: `docs/agent-contract.md` §7. Essence:
no subprocess without `NO_WINDOW_CREATIONFLAGS`; no keys via voice/chat; enum
strings land in ALL five layers; no spawn tools in worker sets; atomic TOML
writes only; preflight every new worktree; nothing heavy on the boot critical
path; no LLM in the voice scrubber; native inference engines get a per-instance
lock + fresh-model recover; GPU wake gates ONLY on the inference probe; wake
verification is word-agnostic, never transcript content; no `isinstance` gates
on unpinned libs; signing private keys ONLY in GitHub Actions secrets; no
silent `except`; no config switch nothing reads.

## 8. Recurring bug classes
Restore trap (`pwsh scripts/preflight.ps1` + `python -c "import jarvis;
print(jarvis.__file__)"`), enum drift, config drift, console flicker, WDM-KS
audio, stale watchdog counters, socket teardown loops, wedged native
inference, wake transcript traps → [`docs/BUGS.md`](docs/BUGS.md).

## 9. Operational reality & git
The working tree is SHARED by parallel sessions: stage only YOUR files
(`git add -p` / pathspec, never `git add -A` / `git add .`); auto-commit each
logical step with Conventional-Commit messages; never push automatically;
never commit secrets. App restart = `POST /api/settings/restart-app` (never
`Stop-Process`); the app is a desktop WebView without F5/console — a frontend
fix = `npm run build` in `jarvis/ui/web/frontend/` + the in-app Restart
button. Check `MEMORY.md` before larger decisions.

## 10. Run & test
Install: `pip install -e . --no-deps` + `pip install -r requirements.txt` +
`pip install -e ".[dev]"`. Launch: `run.bat` (`--headless` = API/WS only).
Lint: `ruff check jarvis/ && ruff format jarvis/ && mypy jarvis/`. Tests:
`pytest tests/` (fakes in `tests/fakes/`, not mocks; fast: `-m "not slow"`);
guards `test_routing.py`, `test_output_filter.py`,
`test_hangup_reason_parity.py`; new providers pass `tests/contract/`.

## 11. Pointers
[`docs/agent-contract.md`](docs/agent-contract.md) (FULL contract) ·
[`docs/architecture-overview.md`](docs/architecture-overview.md) ·
[`CLOUD.md`](CLOUD.md) · [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) ·
[`docs/BUGS.md`](docs/BUGS.md) · `docs/adr/` ·
[`docs/jarvis-cli.md`](docs/jarvis-cli.md).
