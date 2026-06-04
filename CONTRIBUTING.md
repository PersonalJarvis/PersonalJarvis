# Contributing to Personal Jarvis

Thanks for your interest in contributing! This guide is the short, public
contributor reference. (The project's deep internal engineering notes are kept
out of the public tree to reduce noise — this file is the entry point you need.)

## Ground rules

- **Cross-platform first.** Everything must run on Linux, macOS, **and** Windows.
  A feature that only works on one OS is incomplete. OS-specific code is allowed
  only behind a runtime capability check / platform marker, with a graceful
  no-op (and a clear English log message) where the capability is absent. Use
  `pathlib`, capability probes, and UTF-8 by default — never hardcode home paths
  or assume a specific code page. The base `import jarvis` must stay clean on a
  bare `python:3.11-slim` container.
- **English for all repo artifacts.** Code, comments, docstrings, log/exception
  messages, Markdown, tests, and commit messages are written in English.
- **Bring your own keys.** The app uses your own cloud API keys, collected by the
  first-run wizard and stored in your OS credential manager. Never commit secrets,
  never accept keys over voice/chat, and never put keys in `jarvis.toml` or `.env`.
- **Every provider has a cloud-reachable default.** Local models (voice, STT, etc.)
  are an installed-by-default upgrade, not a hard requirement.

## Development setup

```bash
# Editable install (activates plugin entry points)
pip install -e . --no-deps
pip install -r requirements.txt        # full runtime deps
pip install -e ".[dev]"                # pytest, ruff, mypy

# Frontend (jarvis/ui/web/frontend/)
npm install
npm run build                          # → jarvis/ui/web/dist
```

## Running the app

```bash
python -m jarvis --wizard              # first-run setup
python -m jarvis.ui.web.launcher       # desktop app (FastAPI + pywebview + voice)
python -m jarvis.ui.web.launcher --headless   # API + WS only, no window
```

## Quality gates

```bash
ruff check jarvis/
ruff format jarvis/
mypy jarvis/

pytest tests/                          # full suite
pytest -m "not slow"                   # fast subset
npm run test                           # frontend (vitest)
```

New STT / Brain / TTS / Tool / Channel providers must pass the contract suite in
`tests/contract/`.

## Pull requests

- Keep PRs focused. Split unrelated changes.
- Make sure the full app still works end-to-end on Linux, macOS, and Windows —
  each OS getting its native packages — before requesting review.
- Run the quality gates above; CI enforces them (including the English
  output-language policy).

## Security

Found a vulnerability or an accidentally-committed secret? Please open a private
report rather than a public issue. Do not include live credentials in any report.
