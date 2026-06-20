# Contributing to Personal Jarvis

Thank you for contributing to Personal Jarvis! This guide covers everything you need:
setting up your dev environment, understanding the architecture, deciding what to build,
and getting your PR merged.

> All artifacts in this repo are **English** — code, comments, docstrings, docs, commit
> messages, and PR text. (You can talk to us in any language; the assistant speaks de/en/es
> at runtime — but everything written into the repo is English. CI enforces this.)

## Contribution priorities

We value contributions in this order:

1. **Bug fixes** — crashes, incorrect behavior, data loss, voice-path regressions. Always top priority.
2. **Cross-platform compatibility** — Linux, macOS, Windows, and headless servers. A feature
   that only works on one OS is *incomplete*, not "done with a known limitation". See
   [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).
3. **Security hardening** — prompt injection, the instruction-source boundary, the risk-tier
   policy, path traversal, privilege escalation, secret handling. See [`SECURITY.md`](SECURITY.md).
4. **Performance & robustness** — voice latency budgets, retry logic, graceful degradation,
   and the "conversation never blocks" contract.
5. **New providers / plugins** — wake, STT, TTS, brain, harness, tool, or channel plugins.
   Must be provider-agnostic and pass the contract suite.
6. **New skills** — but only broadly useful ones. Generated skills land as drafts and are
   never auto-activated.
7. **Documentation** — fixes, clarifications, new examples.

## Before you start

- Search existing issues, and say hi on [Discord](https://discord.gg/UPu6pFWrJ).
- For anything non-trivial, open an issue first so we can agree on the approach.
- Keep PRs focused — one logical change per PR is much easier to review and merge.

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

- **After editing entry-points** (`pyproject.toml`), re-run `pip install -e . --no-deps` —
  this is what activates new plugins.
- **Frontend** lives in `jarvis/ui/web/frontend/`: `npm install`, then `npm run dev`
  (`http://localhost:5173`), `npm run build`, `npm run test`.

## Architecture in 60 seconds

Personal Jarvis is an 8-layer system. The rules that matter for contributors:

- **Higher layers reach lower layers only through protocols** (`jarvis/core/protocols.py`).
  Lateral communication is only via typed, immutable events on the **EventBus**.
- **Streaming-first** — `Brain`/`STT`/`TTS`/`Harness` methods return `AsyncIterator`;
  non-streaming providers yield exactly one element.
- **Provider-agnostic** — never hardcode a single brain vendor; `cfg.brain.primary` selects.
- **Router-Brain dispatches** to interchangeable harnesses; heavy work runs as **missions**
  in isolated `git worktree` branches under a Worker-Critic loop.

For the deep dive, read [`docs/LLM-CONTEXT.md`](docs/LLM-CONTEXT.md) (a dense, self-contained
engineering snapshot), [`CLAUDE.md`](CLAUDE.md) (binding conventions), and
[`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) (the cross-platform, cloud-first doctrine).

## Should it be a Plugin, a Tool, or a Skill?

This is the most common design question. Pick the smallest thing that fits:

- **Plugin** — a swappable *provider* in one of the seven groups (`wakeword`, `stt`, `tts`,
  `brain`, `harness`, `tool`, `channel`). Plugins live under `jarvis/plugins/<group>/<name>.py`,
  register via `pyproject.toml` entry-points, and **must not import from `jarvis.*`** inside
  the plugin module — only structural Protocol compatibility. New STT/Brain/Tool/Channel
  providers must pass `tests/contract/`.
- **Tool** — a discrete capability the brain can call *within a turn*. Tools go through the
  `ToolExecutor` and the risk-tier policy. **Never call `Tool.execute()` directly.**
- **Skill** — a higher-level, authored capability or multi-step workflow. Generated skills
  are created as `draft` and are never auto-activated.

Rule of thumb: a swappable backend → **plugin**; a single brain-callable action → **tool**;
an authored multi-step workflow → **skill**.

## Conventions that keep the project coherent

- **English artifacts**, always (CI `language-policy` gate).
- **Risk-tier discipline** — `ToolExecutor.execute()` is the only authorized execution path.
- **Router discipline** — `ROUTER_TOOLS` is a frozenset; no spawn-tool ever enters a worker
  tool set (it creates a recursion vector).
- **Enum-drift prevention** — any string crossing module boundaries (a status, a reason)
  uses the five-layer pattern (Python → SQL → Pydantic → TypeScript → UI) plus a parity test.
- **Atomic config writes** — mutate `jarvis.toml` only through `config_writer` (lock +
  tempfile + BOM-safe).
- **Subprocess hygiene** — pass `NO_WINDOW_CREATIONFLAGS` from `jarvis/core/process_utils.py`.
- **Secrets** — only via `get_secret()`; never in code, config, or commits. Voice/chat must
  never accept secrets.
- **No new hard dependency on Windows- or GPU-specific packages in the base install** — those
  go into the `[desktop]` extras with a graceful no-op fallback elsewhere.

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

## Getting your PR merged

Before you open a PR, run through this checklist:

- [ ] Tests pass (`pytest`), including `tests/contract/` for new providers.
- [ ] `ruff` and `mypy` are clean; the frontend builds and `vitest` is green.
- [ ] All new/changed artifacts are English (the CI language-policy gate is a required check).
- [ ] New wire-format enums use the five-layer pattern + a parity test.
- [ ] No new base-install dependency on Windows-/GPU-specific packages (extras only).
- [ ] User-facing changes update the docs / `CHANGELOG.md`.

Open a PR with a clear description of *what* changed and *why*, and link the issue it closes.
By contributing, you agree your contribution is licensed under the [MIT License](LICENSE).

## Community

- **Discord** — [discord.gg/UPu6pFWrJ](https://discord.gg/UPu6pFWrJ)
- **X** — [@PersonalJarvis](https://x.com/PersonalJarvis)
- **Issues** — [GitHub issues](https://github.com/PersonalJarvis/PersonalJarvis/issues)
