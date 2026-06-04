# Cross-Platform Full-App Philosophy

> **Binding architectural doctrine — established 2026-05-18, re-scoped 2026-06-02.**
> This document sits **above** ADRs, plans, and phase docs.
> On conflict with anything written before its dates, **this wins**.
> Future ADRs must cite this document when proposing a feature that only works
> on one OS, and must justify why a cross-platform equivalent or graceful
> per-OS fallback is impossible.

---

## 1. The Pivot

Personal Jarvis was bootstrapped on a single workstation: Windows 11 Pro, an RTX 5070 Ti, 32 GB RAM, CUDA 12.8, a wired headset, a tray icon, a global hotkey, an Orb overlay. Many design decisions silently encoded *that one machine* as the baseline — a specific microphone, a specific audio driver, a specific Hugging Face cache path, a specific multi-monitor layout.

**That single-machine assumption is the defect this doctrine exists to kill.**

The original 2026-05-18 pivot over-corrected: it made a headless €5 VPS the *baseline* and relegated the whole desktop experience to an opt-in extra. The **2026-06-02 re-scope** sets the real target:

> **Personal Jarvis ships as a full, downloadable desktop app — one product, three
> native faces (Linux, macOS, Windows). You download it like any normal app, the
> installer pulls every feature in one step, you enter your own API keys on first
> run, and you talk to it.**

What survives from the original doctrine is the part that was always right: **no single machine is the baseline.** The evidence that motivated it still holds — `BUG-009` (wake threshold tuned to one microphone), `BUG-014` (audio host-API picked from one driver set), `BUG-026` (an STT model name only meaningful on one local cache), `BUG-027` (an overlay relying on one monitor layout). Those are *cross-platform-robustness* bugs: they argue for code that runs anywhere, not for stripping the product down to a headless core.

So the maintainer's machine is still the *exception, not the default* — but the answer is "make the full app run natively everywhere", not "make the default a featureless server".

---

## 2. The Doctrine

Every architectural decision is evaluated against **a normal user downloading the full app on Linux, macOS, or Windows** — and, secondarily, against the user who deliberately chooses the headless/server deployment.

Concretely:

1. **The standard install is the full app.** `pip install -e .[full]` (or the native per-OS installer) pulls the desktop GUI, local voice models, telephony, and chat channels, plus the companion packages the app imports at boot (`board-backend`, `OS-Level` → `overlay`, `skillbook`). One command, every feature, every OS — platform markers route each OS to its own native packages.
2. **Cross-platform parity is non-negotiable (Rule #1).** A feature that works on only one OS is *incomplete*. OS-specific code lives behind a capability check, is selected by a platform marker, and degrades to a clearly-messaged English no-op where the capability is genuinely absent (e.g. Wayland global-hotkey).
3. **You bring your own keys.** The first-run wizard collects the user's *own* cloud API keys (Brain / STT / TTS / Vision / Wake) and stores them in the OS credential manager (Windows Credential Manager / macOS Keychain / Secret Service). Keys are never committed, never bundled in the installer, never accepted over voice or chat.
4. **Local models are an installed-by-default upgrade, not a hard requirement.** The full app installs the local-voice stack (faster-whisper, Silero-VAD, openWakeWord); the large model weights download lazily on first use, not at install time, and not bundled into the installer. A machine with no GPU still runs — it simply falls back to the cloud provider for that class.
5. **Every provider class keeps a cloud-reachable default path.** Brain, STT, TTS, Vision, and Wake each have a cloud-API default so a keyless-local / GPU-less machine still works. Local is the upgrade; cloud is the floor. Brain has been provider-agnostic from day one (OpenRouter, OpenAI, Gemini, Grok, Claude-Max-OAuth inside workers; no Anthropic-API hard dependency) and stays that way.
6. **The base `import jarvis` stays clean on a bare `python:3.11-slim` container.** This is an engineering floor that keeps the codebase honestly cross-platform (no module-top OS imports that crash on the wrong OS) and is enforced in CI. It is a code-hygiene guarantee — the *shipped product* is the full app, not this bare floor.
7. **Headless / server mode is a fully supported secondary deployment.** `--headless` (browser UI over `getUserMedia` → WebSocket, or a channel adapter — Telegram, Discord, SMS, webhook) reaches the full Router-Brain → Worker-Critic → Mission-Manager experience for users who want a server install. It is documented as the alternative, below the desktop download — not as the lead.
8. **Documentation, defaults, and onboarding lead with the desktop-app download.** README quick-install opens with the one-click installer per OS; the headless/server path is a clearly-labelled section beneath it.

---

## 3. Per-Layer Mapping

| Layer | Full app (default, all three OSes) | Cloud fallback / headless-server mode |
|---|---|---|
| L0 OS / Hardware | Native install on Linux, macOS, Windows; per-OS packages via markers | Any Python 3.11+ host; no native binaries required |
| L1 Audio I/O | Local mic/speaker via `sounddevice`; per-OS device routing | Browser `getUserMedia` → WebSocket; channel adapters (Telegram, Discord, SMS, webhook) |
| L2 Speech | Local faster-whisper STT + Silero-VAD + openWakeWord (auto-downloaded), with cloud STT/TTS as fallback | Cloud STT (Deepgram / Google STT / Whisper-API), cloud TTS (Gemini Flash / Grok Voice / ElevenLabs), server-side push-to-talk |
| L3 Intent / Risk | Pure Python logic — identical everywhere | Identical |
| L4 Brain | Five cloud providers + Claude-Max-OAuth-inside-workers; user-supplied keys | Identical |
| L5 Harness-Adapter | OpenClaw subprocess, MCP-Remote, Codex CLI, computer-use | OpenClaw via cloud subprocess host; MCP-Remote |
| L6 Orchestrator | Identical everywhere | Identical |
| L7 UI / UX | `pywebview` desktop window + tray + Orb overlay (per-OS) | FastAPI + React served as a web app, reachable in any browser |

---

## 4. Honest Gap Statement

This document declares the **target** and the current rebuild direction, not a finished state.

As of the 2026-06-02 re-scope:

- The Windows full-app path is built and live-verified on a real Windows machine (install, boot, desktop window).
- The macOS and Linux native installers are being authored and wired into CI, but are **not yet live-verified on real macOS/Linux hardware** (the maintainer has Windows only). They are honestly labelled `CI-configured`, not `live-verified`, in [`docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md`](plans/cross-platform-mac-linux/SIGNOFF-LOG.md) until a real device signs them off.
- The hash-pinned `requirements.txt` lockfile is Linux-generated and is not used for the default cross-platform install (it fails `--require-hashes` on Windows/macOS); the default resolves from `pyproject.toml`. Per-platform locked files are future supply-chain work.

**These are the remediation backlog, not bugs in this document.** This document is the **North Star**; future PRs steer toward it.

**Pre-existing single-platform code is grandfathered until touched.** Any *touch* of a violating path is an opportunity to migrate it toward cross-platform, not extend the violation.

**Maintainer dev tooling may stay Windows-PowerShell-only.** The line is the boundary between `scripts/` (developer tools — fair game to stay Windows-only) and the importable `jarvis/` package + the shipped installers (runtime — must run on Linux, macOS, and Windows).

---

## 5. Three Non-Negotiable Rules

1. **A feature that works on only one OS is incomplete.** It needs a cross-platform equivalent or a graceful, clearly-messaged English no-op where the capability is absent.
2. **The full app must install on all three OSes, and the base `import jarvis` must stay clean on a fresh `python:3.11-slim` container.** A dependency that breaks either is a bug — fix the marker or the import, don't ship the breakage.
3. **A doc that describes the desktop experience must keep all three OSes first-class, and must keep the user's own-keys + cloud-fallback story honest.** No "Windows-only" instruction without the macOS/Linux equivalent; no claim that a local capability is required when a cloud fallback exists.

---

## 6. What Stays The Maintainer's

These are explicitly **maintainer-only**, will never become part of the shipped user experience, and may stay Windows-PowerShell-only without violating the doctrine:

- `scripts/preflight.ps1` — worktree drift recovery (BUG-006 / BUG-014 / BUG-015 defense).
- `scripts/check-working-tree.ps1` — pre-boot drift restore invoked by `run.bat`.
- `scripts/drift-guard-daemon.ps1` — `jarvis.toml` self-healing daemon (BUG-010 triple defense).
- `scripts/auto-push-eod.ps1` + `scripts/install-auto-push-task.ps1` — nightly tag-and-push safety net.
- `scripts/jarvis-config-drift-guard.ps1` — config drift defense.
- The Claude-Max OAuth token sync (`.credentials.json` refresh) used by the GitHub bot on private repos.
- The `~/.claude/` private configuration directory (memory, plans, brand guidelines).

These are the maintainer's *operational rituals* — they keep the maintainer's workstation healthy but have no place in a shipped user's runtime.

---

## 7. Pointer Network

This document is referenced from:

- [`README.md`](../README.md) §1 (top of file)
- [`CLAUDE.md`](../CLAUDE.md) §"Cross-Platform Full-App Philosophy" (binding doctrine block)
- [`CLOUD.md`](../CLOUD.md) — the short top-level charter
- [`docs/BUGS.md`](BUGS.md) — recurring bug classes (restore-trap, subprocess flicker, audio host-API) are single-machine bugs that argue for cross-platform robustness and serve as evidence for Rule #1

---

## 8. Decision Lens For Any PR

> *Does this PR keep the full app working, end-to-end, on Linux, macOS, AND Windows — each OS getting its native packages, the base `import jarvis` still clean on a bare `python:3.11-slim` container, the user's own-keys + cloud-fallback story intact, and a graceful fallback where a local capability is absent?*

If **yes** — merge.
If **no** — either (a) the OS-specific portion is correctly scoped by a platform marker / capability check with a graceful fallback, or (b) the PR is split.

That single question is the operational expression of this doctrine.
