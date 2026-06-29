# Isolated Jarvis Sandbox — Clean-Room Testing on the Maintainer's Own Machine

- **Date:** 2026-06-24
- **Status:** Draft (design approved, pending spec review)
- **Author:** Maintainer + assistant (brainstorming session)
- **Scope:** A repeatable, throwaway sandbox that runs a fresh clone of the public
  `PersonalJarvis/PersonalJarvis` repo natively on Windows, so the maintainer experiences
  exactly what a stranger pulling from GitHub gets — without touching the maintainer's
  real config, data, or credentials.

---

## 1. Problem & Motivation

The maintainer wants to verify the *fresh-user experience* of the public flagship repo on
their own Windows machine. Today this is unsafe to do casually, because a second Jarvis
instance started from a clone shares three machine-scoped resources with the real install:

1. **Windows Credential Manager** — the keyring service name `personal-jarvis` is hardcoded
   (`jarvis/core/config.py:67`), with no env override. A native second instance would *read*
   the maintainer's real API keys (breaking the "fresh stranger has no keys" fidelity and
   silently spending the maintainer's billing) and *write* into the same namespace
   (contaminating / overwriting the maintainer's real keys).
2. **The editable install pin** — running `pip install -e .` from a clone re-points
   `import jarvis` to that clone for whatever Python ran it (the BUG-006/014/015 restore trap).
3. **Config + data on disk** — writes to the real `jarvis.toml` and `data/` tree.

The goal: an isolated sandbox where all three are sealed off, the sandbox runs the *published*
code (not the maintainer's in-flight working tree), and the result is provably identical to
what any other Windows desktop user would get.

### Target experience (decided during brainstorming)

- **Reproduce:** a Windows-desktop power user (native, with voice + browser UI).
- **Source of truth:** a fresh `git clone` of the public GitHub repo (the truest clean room).
- **Reach:** **voice + browser UI only. Computer-Use is OFF.** The sandbox talks, shows the UI,
  and runs missions, but never touches the physical desktop. (Rationale: on a single physical
  machine, Computer-Use actions — mouse, keyboard, screenshots — are inherently global and
  cannot be sandboxed by a venv; only a VM could isolate them, which the maintainer explicitly
  declined in favor of the simpler, safer voice+UI scope.)

---

## 2. Goals & Non-Goals

### Goals

- One repeatable command provisions a fully isolated sandbox from a fresh public clone and
  launches it natively with voice + browser UI.
- Zero contamination: the maintainer's real `jarvis.toml`, `data/`, and `personal-jarvis`
  Credential Manager namespace are never read or written by the sandbox.
- Faithful: the provisioning runs *exactly* the documented stranger steps, so a failure in any
  step is a real defect a stranger would also hit (e.g. a missing frontend build).
- Provable: isolation is verified by the tooling, not merely asserted.
- Throwaway: a teardown command removes the sandbox and leaves the host untouched.

### Non-Goals

- VM / Hyper-V isolation of Computer-Use actions (explicitly out of scope this iteration).
- Running the sandbox against the maintainer's uncommitted working tree (the source is always
  the published public repo).
- Cross-platform sandbox scripts. This tooling is Windows-PowerShell-only, which the
  cloud-first doctrine permits for `scripts/` developer tooling.
- Any change to the runtime `jarvis/` package. The sandbox is provisioning + launch tooling
  around the *unmodified* published code.

---

## 3. The Four Isolation Seams

| Seam | Risk without isolation | How the sandbox seals it |
|---|---|---|
| **Python import** | `pip install -e .` re-pins the global `import jarvis` to the clone (restore trap) | A dedicated venv inside the sandbox dir. The editable install affects **only** that venv's site-packages and entry-point scripts; the global interpreter's `import jarvis` is untouched. |
| **Credentials** | Hardcoded keyring service `personal-jarvis` → sandbox reads/writes the maintainer's real keys | `PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring` redirects keyring to an isolated, write-safe file backend (installed into the sandbox venv). Reads of the real WinVault namespace become impossible; writes go to a throwaway file; the real Credential Manager is never touched. Sandbox API keys live only in a sandbox-local `.env`. |
| **Config** | Writes to the real `jarvis.toml` | `JARVIS_CONFIG` → `<sandbox>/jarvis.toml`. (Defense-in-depth; the separate clone already makes `PROJECT_ROOT` resolve into the sandbox.) |
| **Data** | Writes to the real DBs, missions, flight-recorder, board | `JARVIS_DATA_DIR` → `<sandbox>/data`. |

### Runtime co-existence

The sandbox is launched with a different port and the single-instance lock disabled, so it can
run alongside the real tray instance:

- `python -m jarvis.ui.web.launcher --no-lock --port <Port>` (both flags exist:
  `jarvis/ui/web/launcher.py:82,88`; `use_lock = not args.no_lock` at line 863).
- **Mic and speakers are shared OS resources** and cannot be split. If the sandbox should own
  voice, stop the real tray app first; otherwise expect device contention. This is documented,
  not engineered around.

### Computer-Use disabled

The seeded sandbox `jarvis.toml` sets `[computer_use].enabled = false`. With that flag off, the
brain factory never builds the Computer-Use context (`jarvis/brain/factory.py:862-863,1022-1023`),
so the sandbox cannot drive the physical desktop. Voice (`JARVIS_VOICE=1`, the default) stays on.

---

## 4. Provisioning Flow (the stranger's steps, isolated)

The provisioner performs the documented fresh-clone sequence, redirected into the sandbox dir:

1. **Preconditions** — assert `git`, a Python ≥3.11 launcher, and `node`/`npm` are present
   (npm is needed because the built frontend is not shipped — see below). Refuse to run if the
   sandbox dir already exists unless `-Force`.
2. **Fresh clone** — `git clone <RepoUrl> <SandboxRoot>` at `<Ref>` (default: the public
   flagship at its default branch).
3. **Isolated venv** — `python -m venv <SandboxRoot>/.venv` using the host Python ≥3.11.
4. **Install (exactly the README steps)** — inside the venv:
   `pip install -e . --no-deps` (activates entry-points / plugin discovery) +
   `pip install -r requirements.txt` (runtime deps) + `pip install keyrings.alt`
   (the isolated keyring backend; sandbox-only, never added to the published deps).
5. **Frontend build** — the public repo does **not** track `jarvis/ui/web/dist` (0 tracked
   files; `dist/` is gitignored). If `dist` is absent, run `npm install && npm run build` in
   `jarvis/ui/web/frontend`. A failure here is a real gap in the public repo's documented
   install story and is surfaced, not hidden.
6. **Secrets + config seed** — per mode (Section 5).
7. **Generate `run-sandbox.ps1`** inside the sandbox — sets `JARVIS_CONFIG`, `JARVIS_DATA_DIR`,
   `PYTHON_KEYRING_BACKEND`, `JARVIS_VOICE=1`, and launches
   `<venv>/Scripts/python -m jarvis.ui.web.launcher --no-lock --port <Port>`.
8. **Verify isolation** (Section 6) and print a PASS/FAIL report **before** the first launch.

---

## 5. Run Modes

A single `-Mode` parameter selects one of three behaviors. **Computer-Use is off in all modes.**

- **`Configured` (default, recommended).** The provisioner writes a sandbox-local `.env` with
  the provider key(s) the maintainer chooses to lend it (sourced from `-EnvFile`, or prompted),
  seeds `jarvis.toml` from `jarvis.toml.example` with `[computer_use].enabled = false`, and
  writes the `data/.setup-complete` marker. The sandbox boots straight into the *running*
  Jarvis — the configured product a stranger has after setup. Least fragile; tests what the
  maintainer actually uses day-to-day.
- **`ColdStart` (`-Mode ColdStart`).** No keys, no `.setup-complete` marker. The maintainer
  walks the exact first-run onboarding a stranger sees; keys typed into the UI persist into the
  throwaway file keyring, never the real namespace. Best for QA'ing the literal first-run UX.
- **`Keyless` (`-Mode Keyless`).** No keys at all; the brain runs on the Mock-Brain fallback.
  A pure boot / UI / onboarding-shell smoke test with no secrets involved.

Rationale for the default: `Configured` exercises the real running product with the least
fragility and zero contamination risk; `ColdStart` is available because "look the same as for
them" includes the very first impression.

---

## 6. Built-in Isolation Proof

Run automatically at the end of provisioning, and on demand via `verify-jarvis-sandbox.ps1`.
Each check prints PASS/FAIL; any FAIL exits non-zero and blocks launch.

1. **Sandbox import origin** — `<venv>/python -c "import jarvis; print(jarvis.__file__)"`
   resolves under `<SandboxRoot>`.
2. **Global import untouched** — the host's global
   `python -c "import jarvis; print(jarvis.__file__)"` still resolves to the maintainer's real
   tree (unchanged from a baseline captured before provisioning).
3. **Keyring redirected** —
   `<venv>/python -c "import keyring; print(type(keyring.get_keyring()).__name__)"` is **not**
   `WinVaultKeyring`.
4. **Real state untouched** — the maintainer's real `jarvis.toml` and `data/` mtimes, and the
   presence/values of the real `personal-jarvis` Credential Manager entries, are unchanged
   before vs. after a sandbox run.

---

## 7. Artifacts

All under `scripts/sandbox/`, English, PowerShell:

- **`new-jarvis-sandbox.ps1`** — provision + launch.
  Parameters: `-SandboxRoot <path>` (default: a `jarvis-sandbox` sibling of the repo),
  `-RepoUrl <url>` (default: the public flagship), `-Ref <branch|tag>` (default: default branch),
  `-Port <int>` (default: 47830), `-Mode <Configured|ColdStart|Keyless>` (default: Configured),
  `-EnvFile <path>` (optional key source for Configured), `-Force`, `-NoLaunch`.
- **`verify-jarvis-sandbox.ps1`** — the Section 6 proofs on demand.
  Parameters: `-SandboxRoot <path>`, `-RepoRoot <path>` (the real tree to compare against).
- **`remove-jarvis-sandbox.ps1`** — delete the sandbox dir and the throwaway keyring file, then
  re-run the "real state untouched" check and print confirmation. Parameter: `-SandboxRoot`.
- **`run-sandbox.ps1`** — generated into the sandbox dir by the provisioner; re-launches an
  already-provisioned sandbox with the correct env.
- **`docs/sandbox-testing.md`** — short English guide: the isolation model, the four seams, the
  three modes, how isolation is proven, teardown, and the runtime-contention notes.

---

## 8. Acceptance Criteria

- `new-jarvis-sandbox.ps1` provisions from a fresh public clone and launches voice + browser UI
  on the alternate port alongside a running real instance.
- All four isolation proofs PASS on a real run.
- After a full provision + launch + teardown cycle, the maintainer's real `jarvis.toml`,
  `data/`, global `import jarvis`, and `personal-jarvis` Credential Manager entries are byte/
  state-identical to a baseline captured before provisioning.
- `Configured` mode reaches a working conversation with a lent key; `Keyless` mode boots to the
  UI on the Mock-Brain; `ColdStart` reaches the first-run onboarding with no real keys present.
- Computer-Use is confirmed disabled (the factory logs the
  "`[computer_use].enabled = false`" disabled path) and no desktop input is emitted.

---

## 9. Risks & Open Questions

- **Shared keyring file (ColdStart).** The `keyrings.alt` plaintext store lives at the per-user
  `%LOCALAPPDATA%\Python Keyring` location, shared across sandboxes but never the real WinVault
  namespace. Harmless throwaway; teardown clears it. (Per-sandbox file redirection was
  considered and rejected as YAGNI.)
- **Frontend build dependency.** Requires Node on the host. If the published repo intends fresh
  clones to need `npm run build`, the README should say so; the sandbox test will surface this.
- **Audio device contention** when both instances run with voice — documented, not engineered.
- **Open:** should the sandbox tooling ship in the public repo (useful to contributors verifying
  their own fork) or stay maintainer-local? Default assumption: ship it in `scripts/sandbox/`.
- **Open:** default `-SandboxRoot` location — a sibling of the repo vs. a fixed path like
  `C:\JarvisSandbox`. Default assumption: sibling of the repo.
