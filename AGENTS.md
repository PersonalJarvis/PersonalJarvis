# CLAUDE.md

Compressed index of the binding agent rules. The FULL contract (same section
numbers, unabridged) is [`docs/agent-contract.md`](docs/agent-contract.md) —
read it before deep work.

## 0. Mirror rule (BINDING)
`CLAUDE.md` ≡ `AGENTS.md` (byte-identical); `.claude/{agents,commands,skills}/`
≡ `.agents/{...}`. Auto-synced by hook + pre-commit + CI. Write for EVERY
coding agent, never Claude Code alone.

## 1. Language (BINDING, HIGHEST PRIORITY)
Every committed artifact is ENGLISH. German ONLY on the closed product surface
(runtime voice/chat output, i18n files, speech-input vocabulary, tests quoting
them); register `scripts/ci/german-allowlist.txt` / inline `i18n-allow`;
translate legacy German you touch. Runtime output language: ONE resolver
(`jarvis/core/turn_language.py::resolve_output_language`) decides each turn
for ALL layers; no layer re-derives it; all locales (de/en/es/…) equal.

## 2. GitHub (BINDING)
ONE public repo: `github.com/PersonalJarvis/PersonalJarvis`. **A push is
`git push`** — no staging trees, clones, builds, file audits, or review
sub-agents (review happens when code is written, never at push time). `.gitignore`
first (data/, .env, jarvis.toml, Vault, keys never tracked); never commit
credentials; secret scanning ON; whole-tree checks live in CI, never pre-push.
Default = plain push; Release ONLY when explicitly asked (SemVer + tag +
CHANGELOG + published GitHub Release; `check_release_completeness.py` before
and after). Never push unless the maintainer asks.

## 3. Open-source universality (BINDING)
Assume an arbitrary downloader, never the maintainer: ANY single key works
(capability-gated, cross-family fallback, honest degradation, AP-21/22); every
OS incl. headless `python:3.11-slim` (base stays torch-free; GPU deps in
`[local-voice]`); credentials recoverable IN-APP (keyring → ENV → file).
macOS/Linux ship in the SAME change behind one capability probe or degrade
honestly (+ `docs/os-parity.md`). Done = the four non-maintainer paths (§3).
Device triage: version lag → setup divergence → OS gap.
Ollama/llama.cpp defaults, recommendations, and examples must be verified
against the official live catalog when changed and use a current,
hardware-fitting generation/quantization; an artifact shown as one year old
or older must never be a default or recommendation. A measured compatibility
pin may remain only as a documented non-general exception with a removal test.

## 4. Naming (BINDING)
Internal: **Jarvis-Agents**. User-visible brand is DYNAMIC from the wake word
(`{name}-Agent`, fallback "Assistant-Agent") — never hardcode. Retired
codenames stay dead; `openclaw` binary strings + back-compat aliases stay AS-IS.

## 5. Architecture essentials (+ §6 safety)
8-layer rule (protocols down, frozen `EventBus` events laterally); plugins via
entry-points, no `jarvis.*` import inside (then `pip install -e . --no-deps`);
streaming-first; secrets only via `get_secret`; brain multi-provider +
capability-gated; router = pure dispatcher over `ROUTER_TOOLS` (ADR-0011);
`scrub_for_voice` regex-only; `jarvis.toml` only via `config_writer.py`;
CLI-first contract (feature = REST route → auto-CLI + registry + danger
metadata); five-layer enum pattern; workers in fresh git worktrees w/
kill-on-crash + tool broker (ADR-0025/26); UTF-8 + `NO_WINDOW_CREATIONFLAGS`
on every subprocess. Safety tiers safe/monitor/ask/block, blacklist >
whitelist > default; only `ToolExecutor.execute()`; skills stay `draft`.

## 7. Anti-patterns AP-1..31 + bug classes (BINDING)
Full register: `docs/agent-contract.md` §7–8. Essence: no keys via voice/chat;
enum strings in ALL five layers; no spawn tools in worker sets; atomic TOML
writes only; preflight every new worktree (restore trap!); nothing heavy on
the boot critical path; no LLM in the voice scrubber; native inference = lock
+ fresh-model recover; GPU wake gates ONLY on the inference probe; wake
verification word-agnostic, never transcript content; no `isinstance` gates
on unpinned libs; signing private keys ONLY in GH Actions secrets; no silent
`except`; no unread config switch. Bug classes → [`docs/BUGS.md`](docs/BUGS.md).

## 9. Operational reality & git
Working tree is SHARED: stage only YOUR files (`git add -p`/pathspec, never
`git add -A`/`.`); auto-commit each logical step (Conventional Commits); never
push automatically; never commit secrets. Desktop lifecycle actions are
maintainer-gated: agents may restart, quit, kill, or relaunch the desktop app
ONLY after the maintainer explicitly authorizes that exact action in the current
conversation. Generic "fix/finish/verify" approval is insufficient. Verify the
exact Personal Jarvis PID + command first and never affect unrelated processes.
Without explicit approval, explain why a restart is needed and let the maintainer
click Restart in the desktop UI. The desktop WebView
has no F5/console — frontend fix = `npm run build` in
`jarvis/ui/web/frontend/`, nothing else: open windows reload themselves
(`src/lib/bundleWatch.ts`). Never end a frontend change by asking for a restart.
Every frontend change works in BOTH light and dark mode (and the terminal
panes' own appearance): colours come from theme tokens or the per-appearance
tables in `terminalThemes.ts`, never one hardcoded mode (CLOUD.md →
"Frontend theming"). Check `MEMORY.md` before larger decisions.

## 10. Run & test
Install `pip install -e . --no-deps` + `-r requirements.txt` + `".[dev]"`;
launch `run.bat` (`--headless` = API only); lint `ruff check jarvis/ && ruff
format jarvis/ && mypy jarvis/`; tests `pytest tests/` (fakes, not mocks;
fast `-m "not slow"`); guards `test_routing.py`, `test_output_filter.py`,
`test_hangup_reason_parity.py`; new providers pass `tests/contract/`.

## 11. Pointers
[`docs/agent-contract.md`](docs/agent-contract.md) (FULL contract) ·
[`docs/architecture-overview.md`](docs/architecture-overview.md) ·
[`CLOUD.md`](CLOUD.md) · [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) ·
[`docs/BUGS.md`](docs/BUGS.md) · `docs/adr/` ·
[`docs/jarvis-cli.md`](docs/jarvis-cli.md).
