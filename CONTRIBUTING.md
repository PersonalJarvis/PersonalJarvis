# Contributing to Personal Jarvis

Personal Jarvis is a cross-platform desktop application for voice, agents, and
connected tools. We welcome focused fixes, tests, accessibility work, platform
reports, and documentation improvements. The useful first step is a problem you
can reproduce and explain; familiarity with the whole repository is not required.

## Choose a first contribution

| Starting point | A useful, reviewable result |
|---|---|
| [Open bugs](https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue%20is%3Aopen%20label%3Abug) | Confirm an issue still reproduces on current `main`, add a focused regression test, and fix it. Comment on the issue before starting if its scope is unclear. |
| Installation or product docs | Follow one documented path on a named OS, then correct the exact step that differs from reality. Include the command, observed result, and working replacement. |
| Desktop accessibility | Improve one keyboard, focus, or screen-reader interaction in `jarvis/ui/web/frontend/src/`. Show the before/after behavior in light and dark mode. |
| Platform parity | Test one existing feature on Windows, macOS, Linux, or a headless host, then submit a reproducible report or a narrow fix. Check [OS parity](docs/os-parity.md) first. |

Use [GitHub Issues](https://github.com/PersonalJarvis/PersonalJarvis/issues/new/choose)
for a concrete bug or task. For a larger design, use
[Discussions](https://github.com/PersonalJarvis/PersonalJarvis/discussions) to
agree on scope before implementing it. You can open a small, self-contained PR
without an issue. The `good first issue` label is used when a specific task has
been scoped; browse open bugs or propose a small task if that list is empty.

Interested in helping maintain an area over time? Name the subsystem and the
kind of testing or review you can provide in a Discussion. A series of scoped
contributions gives everyone a concrete basis for deciding how to collaborate;
repository owners handle review, merge, and access decisions.

## Your first contribution in 10 minutes

Pick one path below based on what your issue needs. Both end with your fork
ready and a passing check, so you know your setup works before you touch
code.

### Path A: a docs or frontend text change (Markdown, `.md`, `.json` locale files)

1. Fork the repo and clone your fork.
2. Find a task: browse [issues labeled `good first issue`](https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).
   Comment on it to claim it if you plan to start right away.
3. Make your edit directly in the file the issue points to. No install needed
   for a Markdown-only change.
4. If the change touches `docs/`, sanity-check it:
   ```bash
   python scripts/ci/check_public_docs.py
   ```
5. If the change touches `jarvis/ui/web/frontend/`, install once and run the
   frontend tests:
   ```bash
   cd jarvis/ui/web/frontend
   npm install
   npm run test
   ```
6. Commit, push to your fork, and open a pull request. Use the
   [PR template](.github/PULL_REQUEST_TEMPLATE.md); for a docs or one-view
   change it only asks you to show the rendered result (a screenshot or the
   `check_public_docs.py` output is enough).

You do not need the Python backend running for this path.

### Path B: a Python change (a fix, a new test, a small feature)

1. Fork the repo and clone your fork.
2. Find a task: browse [issues labeled `good first issue`](https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).
   Comment on it to claim it if you plan to start right away.
3. Set up once (Python 3.11+ and Git required):
   ```bash
   python -m venv .venv
   # macOS/Linux:
   source .venv/bin/activate
   # Windows PowerShell:
   .\.venv\Scripts\Activate.ps1

   python -m pip install -e . --no-deps
   python -m pip install -r requirements.txt
   python -m pip install -e ".[dev]"
   ```
4. Confirm setup worked before writing any code:
   ```bash
   python -c "import jarvis; print(jarvis.__file__)"
   ```
   The printed path must point inside your cloned checkout. If it points
   somewhere else (e.g. a different Python install), your `pip install -e`
   went to the wrong environment: re-check `python -m venv` picked up the
   interpreter you expect.
5. Make your change, then run the focused test the issue names, e.g.:
   ```bash
   pytest tests/path/to/relevant_test.py
   ```
6. Before opening the PR:
   ```bash
   ruff check jarvis/
   ruff format --check jarvis/
   mypy jarvis/
   ```
7. Commit, push to your fork, and open a pull request using the
   [PR template](.github/PULL_REQUEST_TEMPLATE.md). State which command you
   ran and that it passed.

You only need `run-dev.bat` (Windows) or `./run-dev.sh` (macOS/Linux) if your
change needs to be seen running in the actual desktop app; most focused fixes
and new tests do not.

### If you get stuck

Ask in [Discord](https://discord.gg/x7USduHxbc) or on the issue itself;
partial progress and questions are welcome, you do not need a finished PR to
say hello.

## Find your way around

| Area | Start here |
|---|---|
| Core contracts and events | `jarvis/core/protocols.py`, `jarvis/core/events.py` |
| Desktop and web UI | `jarvis/ui/web/frontend/`; backend routes in `jarvis/ui/web/` |
| Realtime voice | `jarvis/realtime/` and its focused tests |
| Providers and plugins | `jarvis/plugins/`, entry points in `pyproject.toml`, `tests/contract/` |
| Tests and fixtures | `tests/`, especially `tests/fakes/` |

Read the [architecture overview](docs/architecture-overview.md) for the system
map, [architecture decisions](docs/adr/) for past choices, and
[AGENTS.md](AGENTS.md) for the binding engineering rules. In particular, new
capabilities must work across supported operating systems and must not depend
on one model provider or one developer's machine.

## Development setup

Use Python 3.11+ and Git. From your fork or a local checkout:

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -e . --no-deps
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
python -c "import jarvis; print(jarvis.__file__)"
```

The import path must point into this checkout. Re-run
`python -m pip install -e . --no-deps` after changing plugin entry points.
For an isolated desktop test, use `run-dev.bat` on Windows or `./run-dev.sh`
on macOS/Linux. Both launch a development instance with its own data and ports.
`--headless` runs the API without a desktop shell. The desktop and voice paths
need the relevant OS permissions and hardware; you can work on tests and docs
without them.

For frontend work, run `npm install` in `jarvis/ui/web/frontend/`, then
`npm run test` and `npm run build` there. The desktop loads built frontend
files and reloads them automatically; do not commit generated
`jarvis/ui/web/dist/` files.

## Test and review the change

Keep a PR to one logical change. Describe the user-visible behavior, why it
changes, and how you verified it. Include the OS and provider when they matter.
Use the [PR template](.github/PULL_REQUEST_TEMPLATE.md) for the evidence that
matches your change:

| Change | Verification to include |
|---|---|
| Docs or one local view | Check rendered links and media, or show the view in light and dark mode. |
| Existing backend, provider, or OS adapter | Run the focused tests for that family; state which supported cells you exercised and how the others behave. |
| Shared capability or contract | Add contract tests, cover Windows/macOS/Linux behind capability checks, update `docs/os-parity.md`, and verify a fresh install with one supported key. See the T3 rules in `AGENTS.md`. |

Useful local commands, selected by the change:

```bash
pytest tests/path/to/relevant_test.py
pytest tests/contract/
pytest tests/
ruff check jarvis/
ruff format --check jarvis/
mypy jarvis/
```

Tests use `tests/fakes/` rather than `unittest.mock`. Some repository-wide
checks may have existing failures; report the exact command and failure instead
of claiming a full pass from focused tests. For code that runs at startup, also
run `python scripts/ci/check_boot_budget.py`.

AI-assisted contributions are welcome when the author can explain the diff,
verify its behavior, and respond to review. Write committed code, comments,
documentation, logs, and commit messages in English. Keep secrets and personal
data out of the repository and PR. Contributions are licensed under
[Apache 2.0](LICENSE); see [licensing details](docs/licensing.md).

For private security reports, follow [SECURITY.md](SECURITY.md). For conduct
concerns, see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Usage questions belong
in [Discord](https://discord.gg/x7USduHxbc) or
[Discussions](https://github.com/PersonalJarvis/PersonalJarvis/discussions).
