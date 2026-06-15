# CLOUD.md — Cross-Platform & Cloud-First Charter

> **Binding top-level charter — established 2026-05-29.**
> This is the short, loud manifesto. The full doctrine lives in [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).
> On conflict over hardware/OS assumptions, this charter and `PHILOSOPHY.md` win over any ADR, plan, or phase doc.

---

## Canonical repositories (READ THIS BEFORE ANY PUSH — added 2026-06-07)

This project lives in **two private GitHub repos under the `PersonalJarvis` org**. The org owner was renamed from `personal-jarvis` to `PersonalJarvis` on 2026-06-07, so **both repos now start with `PersonalJarvis/`** — tell them apart by the **second path segment (case-sensitive)**:

| Remote | URL | What it is | Who writes to it |
|---|---|---|---|
| **`origin`** | `github.com/PersonalJarvis/personal-jarvis` — lower-case repo name (the old `personal-jarvis/personal-jarvis` 301-redirects here) | **THE working repo.** All day-to-day development, commits, branches, and `main` live here. | Every dev session — this is where `git push origin` goes. |
| `public` | `github.com/PersonalJarvis/PersonalJarvis` — PascalCase repo name | The **depersonalized public distribution repo** (still private until the public launch, ~end of June). A clean, secrets-/PII-scrubbed snapshot of the working tree. | **Only** the `ship-public-release` skill. |

**Binding rule for every agent (human or cloud-code):**

1. **Normal work pushes to `origin` = `PersonalJarvis/personal-jarvis`.** Before any push, run `git remote -v` and confirm the target is `origin`. The lower-case `…/personal-jarvis` is correct; the PascalCase `…/PersonalJarvis` is NOT a normal push target.
2. **`PersonalJarvis/PersonalJarvis` (the `public` remote) is written EXCLUSIVELY by the `ship-public-release` skill** — never by a manual `git push`, never with raw working state, never with secrets/PII. Pushing day-to-day work there is a defect.
3. **Do NOT rename the local pip package name or the keyring service `personal-jarvis`** — those identifiers are independent of the GitHub org rename and changing them breaks credential lookup.
4. **Flagship framing (the maintainer's standing decision — 2026-06-09).** Treat `PersonalJarvis/PersonalJarvis` as **THE one repository that *is* "the project"** — the public-facing flagship, the OpenClaw / Peter-Steinberger model: a single clean public repo that never contains personal data. `PersonalJarvis/personal-jarvis` (origin) is **not a second project** to reason about — it is the private *backstage*: the working-tree history plus the maintainer's secrets / `jarvis.toml` / `.env` / `data/` / Vault live there and **never** leave it. Do **not** make the maintainer re-explain this or re-pick a target; do not resurrect "which of the two repos" as an open question.
5. **What "push it / ship it / release it / veröffentliche / sichere / bring das raus" from the MAINTAINER means — AND the plain git phrasings "commit to main", "commit this to main", "push", "push to main", "push this", "commit and push", "merge to main", "land it on main", "save to GitHub":** a **depersonalized release to `PersonalJarvis/PersonalJarvis` via the `ship-public-release` skill** — the full *current* working-tree feature set (including uncommitted work, captured via a throwaway `GIT_INDEX_FILE` snapshot index so the working repo is never touched), scrubbed of all personal data. Default to this for any maintainer-initiated "get my current state out there" request; never push raw working state there. **A bare branch name ("main") or a bare git verb ("commit", "push", "merge") from the maintainer is STILL this request — do not silently downgrade it to a plain `git push origin` and call it done (that exact misread happened 2026-06-15).** Automatic per-session dev-safety commits still go to `origin` as infrastructure — that is *not* what the maintainer means by "push". The skill **NEVER** flips visibility private↔public; that stays the maintainer's manual call, made once, outside any skill. <!-- i18n-allow: quoted German maintainer trigger phrases -->

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

## Pointer network

- Full doctrine: [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md)
- Binding agent guidance: [`CLAUDE.md`](CLAUDE.md) §"Cloud-First Philosophy"
- Recurring maintainer-machine bugs that prove the rule: [`docs/BUGS.md`](docs/BUGS.md) (BUG-009, BUG-014, BUG-026, BUG-027)
