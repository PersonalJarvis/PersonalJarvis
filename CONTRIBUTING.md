<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis">
    <img src="assets/brand/banner.png" alt="Personal Jarvis" width="520" />
  </a>
</p>

<h1 align="center">Contributing</h1>

<p align="center">
  Thanks for being here. Small PRs, friendly review, same-day help on Discord.<br />
  Everything you need to get started fits on this screen.
</p>

<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/pulls"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-FFD60A?style=flat-square&labelColor=0A0A0A" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22"><img alt="Good first issues" src="https://img.shields.io/badge/good%20first%20issues-open-FFD60A?style=flat-square&labelColor=0A0A0A" /></a>
  <a href="https://discord.gg/x7USduHxbc"><img alt="Discord" src="https://img.shields.io/badge/Discord-join-FFD60A?style=flat-square&logo=discord&logoColor=0A0A0A&labelColor=0A0A0A" /></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-FFD60A?style=flat-square&labelColor=0A0A0A" /></a>
</p>

---

## Your first PR in five steps

1. **Fork and clone**, then install:
   ```bash
   pip install -e . --no-deps && pip install -r requirements.txt && pip install -e ".[dev]"
   ```
2. **Pick something.** A [good first issue](https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22), or simply the thing that annoyed you.
3. **Change one thing** and run `pytest -m "not slow"` (UI work: `npm run test` in `jarvis/ui/web/frontend/`).
4. **Open the PR.** The template has one short block per kind of change — tick yours, skip the rest. A typo fix owes nothing else.
5. **Stuck?** Ask on [Discord](https://discord.gg/x7USduHxbc). Someone usually answers the same day.

That's it. You don't need to read the rest of this page to open a PR, and you don't need to
understand the whole system to fix one piece of it.

> [!TIP]
> **A first PR doesn't have to be perfect.** If CI fails on a rule you've never heard of,
> that's normal and not a rejection — say so in the PR and we'll walk you through it.

---

## Two things worth knowing

- **We write in English.** Code, comments, docs, commit messages and PR text are English so
  that everyone can read them. Talk to us in any language you like — the repo itself stays
  English, and CI checks that for you so you don't have to think about it.
- **Keep each PR to one change.** Small PRs get reviewed fast and merged fast.

---

## What we'd love help with

| | |
|---|---|
| **Bug fixes** | Always first. Crashes, wrong behaviour, lost data, anything that made the voice path worse. |
| **Cross-platform work** | Linux, macOS, Windows and headless servers are equal here. Making something work on a platform it didn't before is a great contribution. |
| **Security hardening** | Prompt injection, the line between what the user said and what a tool observed, path traversal. See [`SECURITY.md`](SECURITY.md). |
| **Speed and robustness** | Voice latency, retries, honest degradation, the rule that a conversation never blocks. |
| **New providers and plugins** | Across all seven groups (wake, STT, TTS, brain, harness, tool, channel). Provider-agnostic, passing the contract suite. |
| **Docs** | Any time. Unclear sentence, missing step, outdated screenshot — fix it. |

> [!TIP]
> Planning something bigger than a fix? Open an issue first so we can agree on the approach.
> It keeps you from writing a PR that was never going to land.

---

## Dev setup

```bash
git clone https://github.com/PersonalJarvis/PersonalJarvis ~/personal-jarvis
cd ~/personal-jarvis

python -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1

pip install -e . --no-deps            # activates the plugin entry-points
pip install -r requirements.txt       # runtime dependencies
pip install -e ".[dev]"               # pytest, ruff, mypy

python -m jarvis --wizard             # interactive first-run setup
python -m jarvis.ui.web.launcher      # run it (add --headless for a server)
```

Frontend: `npm install` in `jarvis/ui/web/frontend/`, then `npm run dev` / `npm run build` /
`npm run test`.

> [!NOTE]
> After editing entry-points in `pyproject.toml`, re-run `pip install -e . --no-deps`. That is
> what activates new plugins, and skipping it is the #1 reason a plugin "does not exist".

### Running the checks

```bash
pytest -m "not slow"          # fast subset — enough for most PRs
pytest tests/                 # full suite
pytest tests/contract/ -v     # for new STT/Brain/Tool/Channel providers

ruff check jarvis/ && ruff format --check jarvis/
mypy jarvis/

cd jarvis/ui/web/frontend && npm run test && npm run build
```

Tests use fakes from `tests/fakes/`, not mocks.

---

## Before you open the PR

The PR template scales with what you touched. Find your row, ignore the rest:

| Your change | What it owes |
|---|---|
| Docs, comments, translations | Nothing beyond English |
| Web UI | `npm run test` + `npm run build`, works in light **and** dark mode, a screenshot. Don't commit `jarvis/ui/web/dist/` — the maintainer rebuilds the bundle |
| Bug fix, ordinary Python | `pytest -m "not slow"` green, a test for the fixed behaviour, `ruff` clean |
| New provider | The above plus `pytest tests/contract/`, no `import jarvis.*` in the module, honest degradation without a key |
| A shared contract (capability, config schema, turn-taking, credentials, OS backend) | The above plus: which platforms you touched and what the others do now, a boot on headless `python:3.11-slim`, `CHANGELOG.md` |

Unsure which row you're in? Open the PR and ask. We'd rather tell you in review than have
you guess high and waste an evening.

Describe what changed and why, link the issue it closes. By contributing you agree your work
is licensed under the [MIT License](LICENSE) for the 1.x line, and under the
[Apache License 2.0](LICENSE-APACHE-2.0) from version 2.0 on — see
[`docs/licensing.md`](docs/licensing.md).

---

## Going deeper

Reference for when you want it. None of this is required for a first PR.

<details>
<summary><b>The architecture in four rules</b></summary>

<br />

Personal Jarvis is an 8-layer system. Four rules matter before you write anything:

1. **Layers talk through protocols.** Higher layers reach lower ones only through
   `jarvis/core/protocols.py`. Anything sideways goes over the EventBus as a typed,
   immutable event.
2. **Everything streams.** `Brain`, `STT`, `TTS` and `Harness` methods return an
   `AsyncIterator`; a provider that cannot stream yields exactly one element.
3. **No vendor is load-bearing.** Never hardcode one brain provider; `cfg.brain.primary`
   decides.
4. **The router dispatches rather than doing.** Heavy work becomes a mission in an isolated
   `git worktree`, under a worker-and-critic loop.

For the deep version read [`docs/LLM-CONTEXT.md`](docs/LLM-CONTEXT.md) (a dense
engineering snapshot) and [`CLAUDE.md`](CLAUDE.md) (the binding conventions, including the
cross-platform doctrine in section 2).

</details>

<details>
<summary><b>Plugin, tool, or skill?</b></summary>

<br />

The most common design question. Pick the smallest thing that fits:

| | Plugin | Tool | Skill |
|---|---|---|---|
| What it is | A swappable provider | A brain-callable action inside one turn | An authored, multi-step workflow |
| Where it lives | `jarvis/plugins/<group>/`, one of 7 groups | Registered tool, run via `ToolExecutor` | Authored skill; generated ones start as `draft` |
| The one rule | No `import jarvis.*` in the module; new STT/Brain/Tool/Channel must pass `tests/contract/` | Never call `Tool.execute()` directly | Never auto-activated |

A swappable backend is a plugin. A single action the brain can call is a tool. A multi-step
workflow somebody wrote down is a skill.

**Marketplace plugins** (the connectors in the app's Plugins store — GitHub, Notion, Slack, …)
are a separate, fourth thing. New submissions are packaged per the vendor-neutral
[Agent Plugins standard v1.0.0](https://agent-plugins.org/): a directory with a
`plugin.json`, an `mcp.json` when the service has an MCP server, and everything
Jarvis-specific under the `io.github.personaljarvis` extension namespace. Field mapping and
the migration tracker: [`docs/marketplace/agent-plugins-standard.md`](docs/marketplace/agent-plugins-standard.md).

</details>

<details>
<summary><b>Adding a TTS provider: four registrations, not one</b></summary>

<br />

The entry point in `pyproject.toml` makes a TTS provider discoverable, but the config path
builds providers through `_build_provider()` in
[`jarvis/plugins/tts/__init__.py`](jarvis/plugins/tts/__init__.py) — that is where each
family's own voice, model and sub-table handling lives, and where leftover values from a
previous provider get scrubbed. A provider with an entry point but no branch there hits the
`Unknown TTS provider` fallback: Gemini speaks instead of you, with only a log line to say so.

The four places:

1. The entry point in `pyproject.toml`.
2. A branch in `_build_provider()`.
3. An alias set wired into `_canonical_tts_name()`, so every spelling of your provider
   resolves to one family.
4. An entry in `_TTS_SECRET_CANDIDATES` if it needs a key — without it the credential gate
   treats the provider as always available.

</details>

<details>
<summary><b>Conventions CI enforces</b></summary>

<br />

You'll find out either way; better to know first:

| Area | The rule |
|---|---|
| Language | English artifacts only (`language-policy` gate) |
| Risk tier | `ToolExecutor.execute()` is the only authorized execution path |
| Router | `ROUTER_TOOLS` is a frozenset; no spawn tool in a worker tool set |
| Enum drift | Strings crossing module boundaries use the five-layer pattern plus a parity test |
| Config writes | Mutate `jarvis.toml` only via `config_writer` (lock, tempfile, BOM-safe) |
| Subprocess | Always pass `NO_WINDOW_CREATIONFLAGS` |
| Secrets | Only via `get_secret()`; never in code, config, or commits |
| Dependencies | No Windows-only or GPU-only dependency in the base install; extras only |

The full anti-pattern register lives in [`docs/LLM-CONTEXT.md`](docs/LLM-CONTEXT.md).

</details>

---

## Community

<p align="center">
  <a href="https://discord.gg/x7USduHxbc"><img alt="Discord" src="https://img.shields.io/badge/Discord-join_the_server-FFD60A?style=for-the-badge&logo=discord&logoColor=0A0A0A&labelColor=0A0A0A" /></a>
  <a href="https://x.com/Ruben_Luetke"><img alt="X" src="https://img.shields.io/badge/X-follow-FFD60A?style=for-the-badge&logo=x&logoColor=0A0A0A&labelColor=0A0A0A" /></a>
</p>

<p align="center">
  <a href="https://discord.gg/x7USduHxbc">Discord</a> ·
  <a href="https://x.com/Ruben_Luetke">X</a> ·
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/issues">Issues</a> ·
  <a href="CODE_OF_CONDUCT.md">Code of conduct</a>
</p>
