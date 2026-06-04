# CLOUD.md — Cross-Platform & Full-App Charter

> **Binding top-level charter — established 2026-05-29, re-scoped 2026-06-02.**
> This is the short, loud manifesto. The full doctrine lives in [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).
> On conflict over hardware/OS assumptions, this charter and `PHILOSOPHY.md` win over any ADR, plan, or phase doc.

> **2026-06-02 re-scope.** The product goal is now a **full, downloadable desktop
> app** that installs *every* feature in one step and runs the same on Linux,
> macOS, and Windows — you download it like any normal app, enter your own API
> keys on first run, and talk to it. The earlier framing that made a headless
> €5-VPS the *baseline* and the desktop experience an *opt-in extra* is retired.
> What is **kept** from the original charter is Rule #1: true cross-platform
> parity. What **changed** is the default product: the complete desktop app,
> not a stripped headless core.

---

## Rule #1 — Everything we build must run on Linux, macOS, AND Windows. (NON-NEGOTIABLE)

**Every feature, module, dependency, default, and PR must work end-to-end on all three desktop platforms — Linux, macOS, and Windows.**

This is the first and highest rule. It sits above every other consideration in this repo.

Why this rule exists: the project was bootstrapped on a single Windows 11 workstation, and a lot of code silently encoded "Windows" as the baseline. **That is treated as a defect, not a default.** The full app is one product with three native faces, not a Windows app with ports bolted on.

Concretely, Rule #1 means:

1. **No platform may be a second-class citizen.** Linux, macOS, and Windows are all first-class targets. A feature that only works on one of them is **incomplete**, not "done with a known limitation".
2. **The full app (`pip install -e .[full]`) must install and boot on all three platforms.** OS-specific packages are selected by environment markers, so the *same* install command resolves the correct native wheels per OS (e.g. the Windows torch wheel that bundles CUDA, never the Linux-only `nvidia-*` packages). As an engineering floor, the **base `import jarvis` must also stay clean on a fresh `python:3.11-slim` container** (no module-top OS imports that crash on the wrong OS) — this keeps the codebase honestly cross-platform and is enforced in CI. The base floor is a guarantee about code hygiene; the **shipped product is the full app**.
3. **OS-specific code is allowed only when:**
   - (a) it lives behind a runtime capability check (not an `import` at module top level that crashes on the wrong OS), **and**
   - (b) it is selected by a platform marker or capability probe (so it installs on the OS that supports it and is skipped elsewhere), **and**
   - (c) it degrades to a graceful, clearly-messaged no-op (in English) on the platforms where it is unavailable.
4. **OS-bound packages carry an explicit platform marker.** `pywin32`, `pywinauto`, `global-hotkeys`, `pywinpty`, `pycaw` (Windows), `pyobjc-*` (macOS), `ptyprocess` (POSIX) etc. are marked so they install only where they apply. They ship as part of the full app via the marked `[desktop]` / `[desktop-macos]` groups — they are not "excluded from the product", they are "scoped to their OS".
5. **Use cross-platform primitives by default:** `pathlib.Path` over hand-built `\`/`/` paths, `sys.platform` / capability probes over assuming Windows, `subprocess` flags guarded per-OS, UTF-8 everywhere (never assume cp1252), and config/data dirs resolved via a platform-aware helper rather than hardcoded `C:\Users\...`.
6. **CI must prove it.** Cross-platform parity is verified, not assumed — the test matrix exercises Linux, macOS, and Windows (at minimum: import + boot + base test suite on each), and the release pipeline builds a native installer for each.

**The full feature set is the default, not a power-user add-on.** Tray app, Orb overlay, global-hotkey wake, local Whisper, in-process Silero-VAD, Computer-Use harness — these install with the standard `[full]` product on the platforms that support them, and degrade gracefully where a capability is genuinely absent (e.g. Wayland global-hotkey).

**Grandfather clause:** pre-existing single-platform code is grandfathered *until touched*. Any *touch* of a violating path is an opportunity to migrate it toward cross-platform, not extend the violation.

---

## The rest of the doctrine (summary — full text in `docs/PHILOSOPHY.md`)

- **The standard install is the full app.** `pip install -e .[full]` (or the native installer) pulls the desktop GUI, local voice models, telephony, and chat channels. One step, all features, every OS. The companion packages the app imports at boot (`board-backend`, `OS-Level` → `overlay`, `skillbook`) are installed alongside it.
- **You bring your own keys.** The first-run wizard collects the user's own cloud API keys (Brain / STT / TTS / Vision / Wake) and stores them in the OS credential manager. Keys are never committed, never bundled, never accepted over voice/chat.
- **Cloud-reachable defaults remain the safe fallback.** Every provider class (Brain, STT, TTS, Vision, Wake) still has a cloud-API default path so the app works on a machine with no GPU. Local models are an installed-by-default *upgrade* (auto-downloaded on first use), not a hard requirement — a keyless or GPU-less machine still runs, just via cloud providers.
- **Headless / server mode is a fully supported secondary deployment, not the baseline.** `--headless` (browser UI over `getUserMedia` → WebSocket, or a channel adapter — Telegram, Discord, SMS, webhook) reaches the full Router-Brain → Worker-Critic → Mission-Manager experience for users who want a server install. It is documented as the alternative, not the lead.
- **Docs, defaults, install instructions, and onboarding lead with the desktop-app download.** The one-click installer per OS is the headline; the headless/server path is a clearly-labelled section below it.
- **Maintainer dev tooling under `scripts/` may stay Windows-PowerShell-only.** The line is the boundary between `scripts/` (developer tools — may stay Windows-only) and the importable `jarvis/` package + the shipped installers (runtime — must run on Linux, macOS, and Windows).

---

## Decision lens for any PR

> *Does this PR keep the full app working, end-to-end, on Linux, macOS, AND Windows — with each OS getting its native packages, the base `import jarvis` still clean on a bare `python:3.11-slim` container, and a graceful cloud/no-op fallback where a local capability is absent?*

If **yes** → merge.
If **no** → either (a) the OS-specific portion is correctly scoped by a platform marker / capability check with a graceful fallback, or (b) split the PR.

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
- Binding agent guidance: [`CLAUDE.md`](CLAUDE.md) §"Cross-Platform Full-App Philosophy"
- Recurring maintainer-machine bugs that prove Rule #1: [`docs/BUGS.md`](docs/BUGS.md) (BUG-009, BUG-014, BUG-026, BUG-027)
