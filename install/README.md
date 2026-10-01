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

On macOS, every launch path enters through the same app bundle so privacy
grants stay attached to one identity. The source installer builds a native
py2app alias launcher, signs it with a per-user code-signing certificate it
creates on the first run (macOS asks for the login password once to trust
it; grants then survive every rebuild and update), verifies the identity from
inside a LaunchServices process, and preserves the bundle unchanged across
ordinary updates. Without that certificate the launcher is ad-hoc signed and a
rebuild costs one round of re-granting. A separately distributed binary still requires the release
pipeline's Developer-ID signing and notarization. Apple does not permit an installer to
silently grant Microphone, Screen Recording, Accessibility, Input Monitoring,
or input-control access. The app therefore presents one explicit button per
permission during first launch, uses only Apple's native prompt/System Settings
flows, and remains fully usable for text when the user declines. The installer
stops instead of claiming success if the full profile or app-bundle registration
fails.

## Current cryptography on every architecture

The installer uses cryptography 50.0.2 or newer and AsyncSSH 2.24.0 or newer
on every supported architecture. Intel macOS 13+ and Windows ARM64 receive
hash-pinned native wheels from the project's supplemental package index;
Apple Silicon, Windows x64 and Linux use upstream PyPI wheels. Users do not
need Rust or OpenSSL development tools. The full profile and frozen browser
helper use the same reviewed builds.

For a manual source checkout, use `python scripts/pip_install.py -e '.[full]'`
to select the supplemental index automatically. A raw `pip` or `pipx` install
on Intel macOS or Windows ARM64 needs
`--find-links https://personaljarvis.github.io/PersonalJarvis/native-crypto/50.0.2-1/simple/cryptography/`
(pass it through `pipx --pip-args` when using pipx).

Maintainers build both native wheels with the **Native cryptography wheels**
workflow, which validates source hashes, native architecture, linked system
libraries and security/compatibility contracts. Record the successful artifacts'
SHA256 values in `packaging/native-crypto.json`. Generate the static index with
`python scripts/native_crypto_index.py --artifacts <downloaded-wheels> --output <pages-checkout>`
and push it to the `native-crypto-index` branch, whose Pages workflow verifies
the artifact hashes again before deployment. Wait for deployment before regenerating
the application locks. Preserve older version directories; never replace a
published wheel. A rebuilt wheel needs a new `build_revision`, even when the
upstream version is unchanged. Keep the explicit uv index URL and pipx example
in sync, then run the portable matrix and native runtime CI jobs before merging.

Compile from project metadata so uv applies the **explicit** cryptography source.
The output uses a flat `--find-links` page for pip; a generic extra index would
query Pages for every unrelated dependency. The browser sidecar project mirrors
its `requirements.in`, enforced by `check_requirements_sync.py`.

```bash
uv pip compile --universal --generate-hashes --emit-find-links \
  --python-version 3.11 --output-file=requirements.txt pyproject.toml \
  --find-links https://personaljarvis.github.io/PersonalJarvis/native-crypto/50.0.2-1/simple/cryptography/ \
  --config-file packaging/native-crypto-uv.toml \
  --default-index https://pypi.org/simple --keyring-provider disabled --no-progress --color never
uv pip compile jarvis/assets/browser/pyproject.toml --universal --generate-hashes --emit-find-links \
  --python-version 3.12 --no-header --output-file=jarvis/assets/browser/requirements.lock \
  --config-file packaging/native-crypto-uv.toml \
  --find-links https://personaljarvis.github.io/PersonalJarvis/native-crypto/50.0.2-1/simple/cryptography/ \
  --default-index https://pypi.org/simple --keyring-provider disabled
uv lock --config-file packaging/native-crypto-uv.toml --default-index https://pypi.org/simple
```

## File layout

| File              | Stage | Responsibility |
|-------------------|-------|----------------|
| `install.ps1`     | 1     | Windows bootstrap: Python+Git detect/install/re-check, clone, venv, install `rich`, exec `installer.py`. |
| `install.sh`      | 1     | macOS/Linux bootstrap: same flow through native package managers, POSIX bash. |
| `installer.py`    | 2     | Python orchestrator: full-profile install, model prefetch, worker CLI, desktop registration, launch (last). |
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

## Uninstalling

The normal path is the uninstaller the installer put on disk. It removes the
install folder, the autostart entry, and the keychain entries. `--dry-run`
previews, `--yes` skips the confirmation:

```powershell
# Windows (PowerShell)
& "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1"
```

```bash
# macOS / Linux
bash ~/.personal-jarvis/install/uninstall.sh
```

If that script is missing or refuses to start, the app uninstalls itself. This
is the same job without the bootstrap wrapper, and it is the path to use on
installs from 1.1.0 and 1.1.1, which shipped an uninstaller that could not run
on macOS at all:

```bash
# macOS / Linux
~/.personal-jarvis/.venv/bin/python -m jarvis --uninstall
```

```powershell
# Windows (PowerShell)
& "$env:USERPROFILE\.personal-jarvis\.venv\Scripts\python.exe" -m jarvis --uninstall
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
