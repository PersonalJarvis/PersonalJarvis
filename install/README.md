# `install/` — Quick-install bootstrap

This directory ships the one-liner installer for Personal Jarvis. Users
never read these files; they just paste the URL into their shell.

## End-user one-liner

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
```

When Python 3.11+ and Git are already present, the installer is fully
**non-interactive**. If either is missing, Stage 1 lists both missing items and
asks once before using the host's native package manager. It waits, refreshes
the current process environment, re-checks both commands, and continues the
same install run; no second one-liner is needed. All other setup questions
remain in the app's one-time first-launch onboarding.

The installer installs the **full profile** (design 2026-07-07): everything in
the repository — desktop app, telephony, chat channels, local voice models —
skipping only what the OS cannot run. It explains each step and launches the
app as its last action. Re-running the one-liner updates in place and never
re-runs setup. `--headless` keeps the minimal torch-free base (the tiny-VPS /
advanced path).

GUI installs also register Personal Jarvis with the current desktop shell:
Windows Search and Installed Apps, Spotlight via a per-user macOS app bundle,
or the Linux application menu. The in-app updater and first desktop launch
repair these artifacts, and uninstall removes them again. Developer checkouts
and headless Linux hosts are deliberately not registered.

## File layout

| File              | Stage | Responsibility |
|-------------------|-------|----------------|
| `install.ps1`     | 1     | Windows bootstrap: Python+Git detect/install/re-check, clone, venv, install `rich`, exec `installer.py`. |
| `install.sh`      | 1     | macOS/Linux bootstrap: same flow through native package managers, POSIX bash. |
| `installer.py`    | 2     | Python orchestrator: pip install, optional extras, model prefetch, worker CLI, desktop registration, launch (last). |
| `README.md`       | docs  | This file. |

## Why two stages?

Stage 1 is shell-native because it must work before Python or Git exists. Its
prerequisite state machine stays explicitly marked and is exercised directly
by unit tests, while Stage 2 remains in testable Python.

Everything that needs branching logic (platform detection, optional
extras, error recovery, rich progress UI) lives in `installer.py`, where
we get unit tests, exceptions with tracebacks, and a real argument
parser. The trade-off: an extra `python install/installer.py` step at the
end of stage 1.

## End-user flags

All flags are forwarded from stage 1 to `installer.py`:

```powershell
irm https://.../install.ps1 | iex                  # full profile + launch (setup runs in-app)
irm https://.../install.ps1 | iex -- --no-launch   # install only, no app start
irm https://.../install.ps1 | iex -- --headless    # minimal server mode (torch-free base, no launch)
irm https://.../install.ps1 | iex -- --dry-run     # print plan, do nothing
```

(`--no-wizard` and `--with-voice-local` are still accepted as deprecated
no-ops — the installer never runs a terminal wizard anymore, and the full
profile already includes the local voice extras.)

The shell syntax for forwarding (`--` vs. no separator) depends on the
shell and PowerShell version. The safest pattern for ad-hoc testing is to
clone manually and call `installer.py` directly:

```bash
git clone https://github.com/PersonalJarvis/PersonalJarvis ~/.personal-jarvis
cd ~/.personal-jarvis && python -m venv .venv
. .venv/bin/activate           # Windows: .\.venv\Scripts\Activate.ps1
pip install rich packaging
python install/installer.py --dry-run
```

## Environment overrides

| Variable                  | Effect |
|---------------------------|--------|
| `JARVIS_INSTALL_REPO`     | Clone from a fork instead of the upstream repo. |
| `JARVIS_INSTALL_REF`      | Use a branch/tag/SHA other than `main`. |
| `JARVIS_INSTALL_DIR`      | Install to a directory other than `~/.personal-jarvis`. |
| `JARVIS_INSTALL_PREREQS`  | `ask` (default), `auto` (explicit unattended consent), or `never`. |
| `JARVIS_PYTHON`           | Use one explicit Python interpreter; the pin is authoritative. |
| `JARVIS_INSTALL_NO_PIP`   | Skip the pip steps (re-run only prefetch / launch). |

## Local development of the installer

```bash
# Syntax-check both shells
bash -n install/install.sh
pwsh -NoProfile -Command "Get-Content install/install.ps1 | Out-Null"

# Lint the Python orchestrator
ruff check install/installer.py
python -m py_compile install/installer.py

# Dry-run end-to-end (no pip, no launch)
python install/installer.py --dry-run --no-launch
```

## Pre-public-release checklist

The one-liner URLs become live the moment the repo flips to `public`.
Before that flip:

1. Secret scan: `git log --all --full-history -p | grep -iE "api[_-]?key|secret|token|bearer"` returns no hits in commit content (commit *messages* are OK if they're abstract).
2. Strip Maintainer-only paths: `data/_final_verdict_runtime/`, `data/workspace/`, `data/sessions.db` removed from history (consider `git filter-repo`).
3. Confirm `.env` is in `.gitignore` and never committed.
4. Smoke-test the one-liner on a clean VM (Win11 fresh box, Ubuntu 22.04 server, macOS).
5. Flip visibility: `gh repo edit PersonalJarvis/PersonalJarvis --visibility public --accept-visibility-change-consequences`.

## Future work (not in this PR)

- CI smoke workflow `.github/workflows/install-test.yml` — fresh runner per platform, fakes API keys, asserts exit code 0.
- `personal-jarvis update` console script — opencode parity.
- Vercel shortener `jarvis-install.vercel.app` → raw.githubusercontent.com.
