# CLOUD.md — Cross-Platform & Cloud-First Charter

> **Binding top-level charter — established 2026-05-29.**
> This is the short, loud manifesto. The full doctrine lives in [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).
> On conflict over hardware/OS assumptions, this charter and `PHILOSOPHY.md` win over any ADR, plan, or phase doc.

---

## Canonical repository

The one project repository is the public flagship
[`PersonalJarvis/PersonalJarvis`](https://github.com/PersonalJarvis/PersonalJarvis).
Normal development branches, `main`, tags, and GitHub Releases all share that
history. A lowercase `personal-jarvis` remote may exist as a local backup, but it
is never the deliverable and is not a second project.

Every contributor must keep local configuration and credentials outside Git:
`.env`, `jarvis.toml`, `data/`, Vault content, private keys, and signing secrets
must never be tracked. GitHub secret scanning and push protection remain
fail-closed. An ordinary update pushes reviewed commits without creating a tag;
an explicit release additionally updates SemVer and the changelog, creates the
tag, publishes the GitHub Release, and verifies its signed assets.

Do not rename the Python package or keyring service `personal-jarvis`; those are
stable technical identifiers and are independent of the repository's display
name.

---

## Rule #1 — Everything we build must run on Linux, macOS, AND Windows. (NON-NEGOTIABLE)

**Every feature, module, dependency, default, and PR must work end-to-end on all three desktop platforms — Linux, macOS, and Windows — plus a headless Linux server.**

This is the first and highest rule. It sits above every other consideration in this repo.

Why this rule exists: the project was bootstrapped on a single Windows 11 workstation, and a lot of code silently encoded "Windows" as the baseline. **That is now treated as a defect, not a default.** As of 2026-05-29 we are actively shifting the codebase back toward true cross-platform parity.

Concretely, Rule #1 means:

1. **No platform may be a second-class citizen.** Linux, macOS, and Windows are all first-class targets. A feature that only works on one of them is **incomplete**, not "done with a known limitation".
2. **The base `pip install` must succeed and the app must boot on a fresh `python:3.11-slim` Linux container, on macOS, and on Windows** — with no GPU, no audio hardware, no native OS API, and only a network connection.
3. **OS-specific code is allowed only when:**
   - (a) it lives behind a runtime capability check (not an `import` at module top level that crashes on the wrong OS), **and**
   - (b) it sits inside an optional extras group (`[desktop]`, `[local-stt]`, `[vision-local]`, …), **and**
   - (c) it degrades to a graceful, clearly-messaged no-op (in English) on the platforms where it is unavailable.
4. **No new hard dependency on an OS-bound package.** `pywin32`, `pywinauto`, `pyautogui`, `sounddevice`, `faster-whisper`, `onnxruntime-gpu`, `openwakeword`, `global-hotkeys`, `mss`, `pywebview` and friends go into an extras group — never the base install.
5. **Use cross-platform primitives by default:** `pathlib.Path` over hand-built `\`/`/` paths, `sys.platform` / capability probes over assuming Windows, `subprocess` flags guarded per-OS, UTF-8 everywhere (never assume cp1252), and config/data dirs resolved via a platform-aware helper rather than hardcoded `C:\Users\...`.
6. **CI must prove it.** Cross-platform parity is verified, not assumed — the test matrix should exercise Linux, macOS, and Windows (at minimum: import + boot + base test suite on each).

**The maintainer's Windows + RTX workstation is a power-user profile, not the baseline.** Tray app, Orb overlay, global-hotkey wake, local Whisper, in-process Silero-VAD, drift-guard daemon, Computer-Use harness — all opt-in extras, all degrade gracefully when absent.

**Grandfather clause:** pre-existing Windows-only code is grandfathered *until touched*. Any *touch* of a violating path is an opportunity to migrate it toward cross-platform, not extend the violation.

---

## The rest of the doctrine (summary — full text in `docs/PHILOSOPHY.md`)

- **All five provider classes (Brain, STT, TTS, Vision, Wake) have a fully cloud-reachable default path** — no required local GPU, model, microphone, speaker, or OS API.
- **Headless VPS + browser UI is a first-class runtime.** Browser `getUserMedia` → WebSocket, or a channel adapter (Telegram, Discord, SMS, webhook), reaches the full Router-Brain → Worker-Critic → Mission-Manager experience with zero native installs.
- **Defaults in `jarvis.toml` cannot assume CUDA, local audio, or Windows paths.** STT/TTS default to cloud providers; wake defaults to a server-side gate (browser PTT / channel / webhook).
- **Local-model recommendations stay current.** Every Ollama/llama.cpp default, recommendation, managed profile, and current setup example must be verified against the official live catalog when changed and use a hardware-fitting current generation/quantization. An artifact shown as one year old or older is forbidden as a default or recommendation; a measured compatibility pin is allowed only as a documented non-general exception with a removal test.
- **Docs, defaults, install instructions, and onboarding lead with the cloud + cross-platform path.** Windows-desktop instructions are a footnote in an "Optional power-user extras" section.
- **Maintainer dev tooling under `scripts/` may stay Windows-PowerShell-only.** The line is the boundary between `scripts/` (developer tools — may stay Windows-only) and the importable `jarvis/` package (runtime — must run on Linux, macOS, and Windows).

---

## Decision lens for any PR

> *Would this PR work, end-to-end, on a fresh `python:3.11-slim` Linux container, on macOS, and on Windows — with no GPU, no audio hardware, no native OS API, and only a network connection?*

If **yes** → merge.
If **no** → either (a) the OS/hardware-specific portion is correctly gated behind an extras group with a graceful no-op fallback in the base install, or (b) split the PR.

---

## Repo hygiene — no stray screenshots or binary scratch (added 2026-05-30)

**Screenshots and ad-hoc image scratch do not belong in the repo.** UI captures, GitHub screenshots, `Screenshot *.png` dumps, debug frames, and design-reference images are throwaway artifacts: they bloat the working tree, inflate clone size, and are never load-bearing. Keep them outside the repo (e.g. `~/Downloads`).

- **Never commit** UI / debug / GitHub screenshots or `Screenshot *.png`-style captures. If one ever appears in `git status`, delete it — do not commit it.
- The **only** images that belong in the repo are load-bearing assets: shipped frontend assets under `jarvis/ui/web/frontend/public/` and `jarvis/ui/web/dist/`, app icons under `assets/icons/`, the chosen mascot art, and `OS-Level/overlay-ui/.../mascot-fallback.png`. Anything else is suspect.
- Runtime telemetry under `data/flight_recorder/blobs/` is gitignored and can grow to tens of GB (the Vision flight-recorder writes one screenshot per frame). Purge it periodically — `FlightRecorder` recreates the directory on boot (`recorder.py`, `mkdir(parents=True, exist_ok=True)`), so deleting it is safe and loses only old replay history.
- `.gitignore` enforces this: root-level `*.png/*.jpg/*.jpeg/*.gif/*.webp/*.bmp` are ignored, plus `Screenshot *.png` anywhere in the tree.

---

## In-app updates — every release reaches users via the "Update available" button (added 2026-07-03)

**The desktop app self-updates.** Since 2026-07-03 the top bar carries an in-app updater (`jarvis/ui/web/update_routes.py` + `jarvis/ui/web/frontend/src/components/layout/TopBar.tsx`): when a newer version is published, an **"Update available · vX.Y.Z"** button appears next to Restart; one click pulls the new code and restarts, so an end user never re-runs the installer from a terminal. This is now **the** way a new version reaches everyone, so the release process must keep it working:

1. **A user-visible update = a real GitHub Release (a `vX.Y.Z` tag).** The button detects the newest *published Release* (`api.github.com/repos/PersonalJarvis/PersonalJarvis/releases/latest`), NOT raw `main`. A **DISCREET** snapshot (no tag) deliberately does NOT surface an update — only the **RELEASE** ceremony does (bump + tag + GitHub Release; see the "Canonical repositories" section above). So "ship an update our users actually see" means run the RELEASE ceremony, not a discreet snapshot. The Release notes (`body`) are what the button shows on hover — write them for users, not for maintainers.
2. **The pull needs the prebuilt frontend + the marker.** Each public release must ship a freshly built `jarvis/ui/web/dist/` (the release skill already rebuilds it) so the update's `git fetch` + `git reset --hard origin/main` refreshes the UI with no Node/npm on the user's machine. The updater is active **only** on an installer-managed checkout — proven by BOTH a `.jarvis-managed-install` marker (written by `install/installer.py`, gitignored so it never ships) AND an `origin` resolving exactly to `PersonalJarvis/PersonalJarvis`. A dev tree, a manual clone, or a look-alike fork never shows the button and can never be self-reset. That guard is load-bearing — do not weaken it.
3. **Cross-platform, like everything else (Rule #1).** The updater rides git + the existing detached relauncher, so it behaves identically on Windows/macOS/Linux and degrades honestly on a headless host (the restart step returns 503 → "update installed, please restart manually"). It adds no OS-bound dependency.

---

## Frontend theming — every UI change ships in light AND dark mode (added 2026-08-12)

**Neither theme is "the" theme.** The web app has a light and a dark theme
(`jarvis/ui/web/frontend/src/index.css` tokens), and the Agentic IDE's terminal
panes additionally carry their own light/dark appearance that may differ from
the app theme. Any frontend change — component, colour, chart, notice — must:

1. **Read its colours from the theme system, never hardcode one mode.**
   App-level surfaces use the CSS tokens (`--background`, `--primary`, …);
   anything drawn on a terminal pane's own ground keys off the pane's
   `TerminalAppearance` (the per-appearance tables in
   `src/components/agentic/terminalThemes.ts`), because an app token lands on
   the wrong ground exactly when pane and app disagree.
2. **Be checked legible in BOTH modes before it is called done.** The brand
   accent is signal-yellow `#FFD60A` on dark grounds and gold `#A86B00` on
   light paper — never the same hex on both.

---

## Pointer network

- Full doctrine: [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md)
- Binding agent guidance: [`CLAUDE.md`](CLAUDE.md) §"Cloud-First Philosophy"
- Recurring maintainer-machine bugs that prove the rule: [`docs/BUGS.md`](docs/BUGS.md) (BUG-009, BUG-014, BUG-026, BUG-027)
