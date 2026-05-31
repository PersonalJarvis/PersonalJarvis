# Quick-Install One-Liner — Design Doc

**Date:** 2026-05-26
**Author:** Personal Jarvis maintainers
**Status:** Wave 1 in flight (this PR)

## Goal

Replace the four-line manual install (`pip install -e .` × 3 + `python -m
jarvis --wizard`) with a single shell line that fully sets up Personal
Jarvis, runs the first-run wizard, and launches the Desktop App — the
same UX that `opencode`, `rustup`, and `uv` ship.

## Why now

The current README "Quick install" section is 4 commands long and assumes
the user has already cloned the repo. Every public-facing demo (landing
page, screenshots, docs) needs to compare to those tools' one-liner
experience. Until we have one, "Personal Jarvis" looks heavier than it
actually is.

## Non-goals

- A signed PyInstaller binary — 50 MB, macOS notarization, slow release
  pipeline. Defer.
- Auto-installing Python or git — admin-rights and platform detection
  rabbit hole. We print a clear error with a link instead.
- `personal-jarvis update` console script — opencode parity, but it's a
  separate (smaller) PR.
- A custom domain (`jarvis.sh`). We start with raw.githubusercontent.com;
  a Vercel 302 redirect is W5+.
- `pyproject.toml` extras refactor (base vs. `[desktop]` vs.
  `[voice-local]`). Touched in the installer (it _passes_ `--with-desktop`
  to pip), but the actual split happens in a follow-up PR so this one
  stays focused.

## Architecture

Two-stage bootstrap, mirroring `rustup` and `uv`:

```
USER PASTES                                         STAGE 1                  STAGE 2
─────────────────                                 ───────────────         ──────────────────
irm raw.github.../install.ps1 | iex   ─────►    install.ps1 (~180 LOC)  ─►  installer.py (~250 LOC)
                                                  Python+git preflight       pip install + wizard + launch
curl -fsSL raw.github.../install.sh   ─────►    install.sh   (~150 LOC)  ─┘
                                                  clone, venv, rich
                                                  exec installer.py
```

**Why two stages.** Shells are bad at error handling, branching, progress
UI, and unit testing. The minimum we need in shell is "verify Python,
clone, make a venv, install enough deps to run Python — then hand off."
Anything more belongs in `installer.py` where it can be tested and
trace-back'd.

## Files added

| Path                  | Lines | Purpose |
|-----------------------|-------|---------|
| `install/install.ps1` | ~180  | Windows Stage 1 |
| `install/install.sh`  | ~150  | macOS/Linux Stage 1 |
| `install/installer.py`| ~250  | Cross-platform Stage 2 |
| `install/README.md`   | ~100  | Maintainer doc (file layout, flags, env vars, pre-public checklist) |
| `docs/superpowers/specs/2026-05-26-quick-install-one-liner-design.md` | this file | spec record |

Plus a `README.md` rewrite of section "1. Quick install & first-run".

## Stage 1 contract (shell side)

Steps performed by both `install.ps1` and `install.sh`:

1. Print branding banner.
2. Verify Python 3.11+ on `PATH`. Bail with `python.org`/Homebrew link if
   missing.
3. Verify `git`. Bail with install link if missing.
4. Decide install dir: `$JARVIS_INSTALL_DIR` env var, else
   `~/.personal-jarvis` (Windows: `$env:USERPROFILE\.personal-jarvis`).
5. If install dir contains `.git`: `git fetch && reset --hard
   origin/<branch>` (update path).
   If install dir exists but isn't a repo: bail (don't clobber).
   Otherwise: `git clone --depth 1`.
6. Create `.venv` (idempotent) and install `rich` + `packaging` into it.
7. `exec` the venv's Python on `install/installer.py`, forwarding any
   extra args.

Both scripts honor the same env vars: `JARVIS_INSTALL_REPO`,
`JARVIS_INSTALL_REF`, `JARVIS_INSTALL_DIR`.

## Stage 2 contract (Python side)

`installer.py` argparse:

```
--no-wizard         skip the first-run wizard
--no-launch         don't launch the Desktop App at the end
--headless          VPS mode: no GUI extras, no launch
--with-desktop      force [desktop] extras (default: auto by platform)
--with-voice-local  install faster-whisper + Silero + openWakeWord (~1.5 GB)
--dry-run           print plan, run nothing
```

Steps:

1. **Pre-flight** — re-assert Python 3.11+, repo root sanity check.
2. **Pip install** — three layers:
   - `pip install -e . --no-deps` (entry-points activation)
   - `pip install -r requirements.txt` (full runtime)
   - `pip install -e .[desktop]` if `--with-desktop` or platform is
     Windows/macOS with a display server.
   - `pip install -e .[voice-local]` if `--with-voice-local` (off by
     default; opt-in only).
3. **Wizard** — `python -m jarvis --wizard` (interactive: API keys, mic,
   hotkey). Skip on `--no-wizard`. Non-zero exit prints a "rerun"
   pointer; we exit 3.
4. **Launch** — `subprocess.Popen` of:
   - Windows: `run.bat`
   - Headless or `--headless`: `python -m jarvis.ui.web.launcher --headless`
   - macOS/Linux GUI: `python -m jarvis.ui.web.launcher`

The installer returns to the user's shell as soon as the App is spawned.

## End-user UX

```powershell
PS> irm https://raw.githubusercontent.com/personal-jarvis/personal-jarvis/main/install/install.ps1 | iex

  Personal Jarvis — Quick install (Windows)

  [1/5] Checking prerequisites...
        Python OK (python)
        git OK
  [2/5] Preparing repo at C:\Users\Foo\.personal-jarvis ...
        repo ready
  [3/5] Creating Python virtual environment...
        venv OK
  [4/5] Installing bootstrap dependencies (rich, packaging)...
        bootstrap deps OK
  [5/5] Handing off to the Python installer...

  ╭─────── Pre-flight ───────╮
  │ Platform   Windows 11 ...│
  │ Python     3.12.1 ...    │
  │ ...                      │
  ╰──────────────────────────╯

  ╭───── Installing Personal Jarvis ─────╮
  · editable install (entry-points)
  · runtime dependencies
  · desktop extras
  ╰───...───╯

  ╭───── First-run wizard ─────╮
  (interactive: API keys → mic → hotkey → ...)

  ╭───── Launch ─────╮
  Starting Desktop App via run.bat...

  ╭───── Personal Jarvis is installed. ─────╮
```

## Error handling

- Each pre-flight failure prints what's missing, where to get it, and
  exits with a non-zero code. No half-installed state.
- `pip install` failures for **required** layers → exit 2.
- `pip install` failures for **extras** → warn, continue.
- Wizard non-zero → exit 3, with hint to re-run.
- Launch failure → exit 4 (rare; app may have started anyway).

## Update path

Re-running the same one-liner detects `~/.personal-jarvis/.git`, does
`git fetch + reset --hard origin/main`, re-installs deps. Same UX as
`opencode update` without us shipping a separate `personal-jarvis
update` command (that follows in a later PR).

## Hosting

Wave 1 ships the files. They are not callable from a browser until the
repo flips to public (Wave 4 in the project plan).

Pre-public gate (Wave 3 in the project plan):

1. Secret scan against full history.
2. Strip Maintainer-only data (`data/_final_verdict_runtime/`,
   `data/workspace/`).
3. Confirm `.env` was never committed.
4. Smoke-test the one-liner on a clean Win11 box, Ubuntu 22.04, macOS.
5. `gh repo edit ... --visibility public`.

## Testing

This Wave 1 PR includes:

- `bash -n install/install.sh` (CI-able shell syntax check).
- `pwsh -NoProfile -Command "Get-Content install/install.ps1"` parse
  check.
- `python -m py_compile install/installer.py`.
- `python install/installer.py --dry-run --no-wizard --no-launch`
  smoke-test (verifies all steps wire up without doing anything
  destructive).

A full CI workflow that runs the one-liner end-to-end on fresh runners
is deferred (W5 in the plan).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| One-liner URL is dead until repo goes public. | The README says so explicitly; the manual install path stays documented. |
| `installer.py` lives _inside_ the repo (chicken-and-egg). | Stage 1 always clones before exec'ing it. There is no scenario where the user runs `installer.py` without having the repo. |
| `pip install -r requirements.txt` is heavy on slow connections. | Honest 5-minute install time; no spinner deception. Future PR can split base/extras to shrink the base. |
| User has `python` on `PATH` but it's 2.7 or pre-3.11. | Stage 1 checks `python --version` and `py --version`; both are gated to `>= 3.11`. |
| Existing `~/.personal-jarvis` directory that isn't a repo. | Stage 1 detects this and bails — never clobbers. |
| Stash dust from prior maintainer sessions exposed to public. | Wave 3 (pre-public audit) catches this; explicitly out of Wave 1 scope. |

## Waves and what's _not_ in Wave 1

| Wave | Scope | Status |
|---|---|---|
| **W1** | install.ps1 + install.sh + installer.py + README update + spec doc | **this PR** |
| W2 | `pyproject.toml` extras split (`base` / `[desktop]` / `[voice-local]`) | follow-up PR |
| W3 | Pre-public audit (secret scan, history strip, smoke on clean VMs) | manual, off this PR |
| W4 | `gh repo edit --visibility public` + smoke-test the live one-liner | manual, after W3 |
| W5 | CI install-test workflow + Vercel shortener + `personal-jarvis update` | follow-up PR |
