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

> The one-liner only works once the repo is public. While the repo is
> private the URLs return 404 and the manual install (see root README)
> stays the canonical path.

## File layout

| File              | Stage | Responsibility |
|-------------------|-------|----------------|
| `install.ps1`     | 1     | Windows bootstrap: Python+git preflight, clone, venv, install `rich`, exec `installer.py`. |
| `install.sh`      | 1     | macOS/Linux bootstrap: same shape as `install.ps1`, POSIX bash. |
| `installer.py`    | 2     | Python orchestrator: pip install, optional extras, wizard, launch. All cross-platform logic lives here. |
| `README.md`       | docs  | This file. |

## Why two stages?

Stage 1 is the smallest amount of shell we can get away with while still
being trustworthy when piped into `iex` / `bash`. Each shell script is
under 200 lines and reads top-to-bottom in under a minute.

Everything that needs branching logic (platform detection, optional
extras, error recovery, rich progress UI) lives in `installer.py`, where
we get unit tests, exceptions with tracebacks, and a real argument
parser. The trade-off: an extra `python install/installer.py` step at the
end of stage 1.

## End-user flags

All flags are forwarded from stage 1 to `installer.py`:

```powershell
irm https://.../install.ps1 | iex                  # full install + wizard + launch
irm https://.../install.ps1 | iex -- --no-launch   # install only, no app start
irm https://.../install.ps1 | iex -- --no-wizard   # install only, no wizard
irm https://.../install.ps1 | iex -- --headless    # VPS mode (no GUI deps, no launch)
irm https://.../install.ps1 | iex -- --with-voice-local   # also pull faster-whisper (~1.5 GB)
irm https://.../install.ps1 | iex -- --dry-run     # print plan, do nothing
```

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
| `JARVIS_INSTALL_NO_PIP`   | Skip the pip steps (re-run only the wizard / launch). |

## Local development of the installer

```bash
# Syntax-check both shells
bash -n install/install.sh
pwsh -NoProfile -Command "Get-Content install/install.ps1 | Out-Null"

# Lint the Python orchestrator
ruff check install/installer.py
python -m py_compile install/installer.py

# Dry-run end-to-end (no pip, no wizard, no launch)
python install/installer.py --dry-run --no-wizard --no-launch
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
- `pyproject.toml` extras split (base / `[desktop]` / `[voice-local]`) so the headless Linux path doesn't drag in `pywin32`.
