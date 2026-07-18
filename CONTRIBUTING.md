<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis">
    <img src="assets/brand/banner.png" alt="Personal Jarvis" width="520" />
  </a>
</p>

<h1 align="center">Contributing</h1>

<p align="center">
  Thank you for helping build Personal Jarvis.<br/>
  This guide covers dev setup, the architecture, what to build, and getting your PR merged.
</p>

<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/pulls"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-FFD60A?style=flat-square&labelColor=0A0A0A" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22"><img alt="Good first issues" src="https://img.shields.io/badge/good%20first%20issues-open-FFD60A?style=flat-square&labelColor=0A0A0A" /></a>
  <a href="https://discord.gg/x7USduHxbc"><img alt="Discord" src="https://img.shields.io/badge/Discord-join-FFD60A?style=flat-square&logo=discord&logoColor=0A0A0A&labelColor=0A0A0A" /></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-FFD60A?style=flat-square&labelColor=0A0A0A" /></a>
</p>

> [!IMPORTANT]
> **All artifacts in this repo are English** — code, comments, docstrings, docs, commit
> messages, and PR text. You can talk to us in any language (the assistant itself speaks
> de/en/es at runtime), but everything written *into the repo* is English. CI enforces this.

---

## Contents

- [Contribution priorities](#contribution-priorities)
- [Before you start](#before-you-start)
- [Development environment](#development-environment)
- [Architecture in 60 seconds](#architecture-in-60-seconds)
- [Plugin, Tool, or Skill?](#plugin-tool-or-skill)
- [Conventions](#conventions)
- [Testing](#testing)
- [Opening your PR](#opening-your-pr)
- [Community](#community)

---

## Contribution priorities

We value contributions in this order:

| # | Priority | What it covers |
|:---:|---|---|
| 1 | **Bug fixes** | Crashes, incorrect behavior, data loss, voice-path regressions. Always top priority. |
| 2 | **Cross-platform** | Linux, macOS, Windows, headless servers. A feature that only works on one OS is *incomplete* — see [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md). |
| 3 | **Security hardening** | Prompt injection, the instruction-source boundary, the risk-tier policy, path traversal, privilege escalation — see [`SECURITY.md`](SECURITY.md). |
| 4 | **Performance & robustness** | Voice latency budgets, retry logic, graceful degradation, the "conversation never blocks" contract. |
| 5 | **New providers / plugins** | Wake · STT · TTS · brain · harness · tool · channel. Provider-agnostic, and must pass the contract suite. |
| 6 | **New skills** | Broadly useful ones only. Generated skills land as drafts and are never auto-activated. |
| 7 | **Documentation** | Fixes, clarifications, new examples. |

## Before you start

> [!TIP]
> For anything non-trivial, **open an issue first** so we can agree on the approach — it
> saves you from a merged-PR-shaped surprise.

- Search existing issues, and say hi on [Discord](https://discord.gg/x7USduHxbc).
- Keep each PR focused — one logical change is far easier to review and merge.

## Development environment

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

> [!NOTE]
> After editing entry-points in `pyproject.toml`, re-run `pip install -e . --no-deps` — that
> is what activates new plugins. The frontend lives in `jarvis/ui/web/frontend/`
> (`npm install`, then `npm run dev` / `npm run build` / `npm run test`).

## Architecture in 60 seconds

Personal Jarvis is an 8-layer system. The rules that matter for contributors:

- **Higher layers reach lower layers only through protocols** (`jarvis/core/protocols.py`); lateral communication is only via typed, immutable events on the **EventBus**.
- **Streaming-first** — `Brain`/`STT`/`TTS`/`Harness` methods return `AsyncIterator`; non-streaming providers yield exactly one element.
- **Provider-agnostic** — never hardcode one brain vendor; `cfg.brain.primary` selects.
- **Router-Brain dispatches** to interchangeable harnesses; heavy work runs as **missions** in isolated `git worktree` branches under a Worker-Critic loop.

For the deep dive, read [`docs/LLM-CONTEXT.md`](docs/LLM-CONTEXT.md) (a dense engineering snapshot), [`CLAUDE.md`](CLAUDE.md) (binding conventions), and [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) (the cross-platform, cloud-first doctrine).

## Plugin, Tool, or Skill?

The most common design question — pick the smallest thing that fits:

| | **Plugin** | **Tool** | **Skill** |
|---|---|---|---|
| **What it is** | A swappable *provider* | A brain-callable *action* within a turn | An authored, multi-step *workflow* |
| **Where it lives** | `jarvis/plugins/<group>/` in one of 7 groups | Registered tool, run via `ToolExecutor` | Authored skill; generated ones start as `draft` |
| **Golden rule** | No `import jarvis.*` inside the module; new STT/Brain/Tool/Channel must pass `tests/contract/` | **Never** call `Tool.execute()` directly | **Never** auto-activated |

**Rule of thumb:** a swappable backend → **plugin**; a single brain-callable action → **tool**; an authored multi-step workflow → **skill**.

## Conventions

These keep the project coherent — most are enforced in CI:

| Area | The rule |
|---|---|
| Language | English artifacts only (CI `language-policy` gate) |
| Risk tier | `ToolExecutor.execute()` is the **only** authorized execution path |
| Router | `ROUTER_TOOLS` is a frozenset; no spawn-tool in a worker tool set |
| Enum drift | Strings crossing module boundaries use the five-layer pattern + a parity test |
| Config writes | Mutate `jarvis.toml` only via `config_writer` (lock + tempfile + BOM-safe) |
| Subprocess | Always pass `NO_WINDOW_CREATIONFLAGS` |
| Secrets | Only via `get_secret()`; never in code, config, or commits |
| Dependencies | No new Windows-/GPU-specific dependency in the base install — `[desktop]` extras only |

The full anti-pattern register lives in [`docs/LLM-CONTEXT.md`](docs/LLM-CONTEXT.md).

## Testing

```bash
pytest tests/                 # full suite (asyncio_mode=auto)
pytest -m "not slow"          # fast subset
pytest tests/contract/ -v     # mandatory for new STT/Brain/Tool/Channel providers

ruff check jarvis/ && ruff format --check jarvis/
mypy jarvis/

cd jarvis/ui/web/frontend && npm run test && npm run build
```

Tests use fakes (in `tests/fakes/`), not mocks. New providers must pass the contract suite.

## Opening your PR

Run through this checklist before you open it:

- [ ] Tests pass (`pytest`), including `tests/contract/` for new providers
- [ ] `ruff` and `mypy` are clean; the frontend builds and `vitest` is green
- [ ] All new/changed artifacts are English (the CI language-policy gate is required)
- [ ] New wire-format enums use the five-layer pattern + a parity test
- [ ] No new base-install dependency on Windows-/GPU-specific packages (extras only)
- [ ] User-facing changes update the docs / `CHANGELOG.md`

Open a PR with a clear description of *what* changed and *why*, and link the issue it closes.
By contributing, you agree your contribution is licensed under the [MIT License](LICENSE).

## Community

<p align="center">
  <a href="https://discord.gg/x7USduHxbc"><img alt="Discord" src="https://img.shields.io/badge/Discord-join_the_server-FFD60A?style=for-the-badge&logo=discord&logoColor=0A0A0A&labelColor=0A0A0A" /></a>
  <a href="https://x.com/Ruben_Luetke"><img alt="X" src="https://img.shields.io/badge/X-follow-FFD60A?style=for-the-badge&logo=x&logoColor=0A0A0A&labelColor=0A0A0A" /></a>
</p>

<p align="center">
  <a href="https://discord.gg/x7USduHxbc">Discord</a> ·
  <a href="https://x.com/Ruben_Luetke">X</a> ·
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/issues">Issues</a>
</p>
