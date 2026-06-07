# Cloud-First Philosophy

> **Binding architectural doctrine — established 2026-05-18.**
> This document sits **above** ADRs, plans, and phase docs.
> On conflict with anything written before 2026-05-18, **this wins**.
> Future ADRs must cite this document when proposing a feature that depends on local hardware and must justify why a cloud fallback is impossible (or accept the feature being relegated to an opt-in `[desktop]` extras group).

---

## 1. The Pivot

Personal Jarvis was bootstrapped on a single workstation: Windows 11 Pro, an RTX 5070 Ti, 32 GB RAM, CUDA 12.8, a wired headset, an Obsidian vault on the desktop, a tray icon, a global hotkey, an Orb overlay. Many design decisions silently encoded that environment as the baseline — `faster-whisper-large` on CUDA, `openWakeWord` on the CPU, `Silero-VAD` in-process, WASAPI audio routing, a `pywebview` desktop window, a PowerShell drift-guard daemon polling `jarvis.toml` every five minutes.

**That baseline is no longer the project's target.**

Personal Jarvis is being released as **open source on GitHub**. The realistic profile of a future contributor or user is one of:

- A **€5 / month VPS** — 1 vCPU, 1–2 GB RAM, no GPU, no audio hardware, headless Linux.
- A **mid-range laptop** — integrated graphics, 8–16 GB RAM, Windows / macOS / Linux, browser-based interaction.

The maintainer's RTX 5070 Ti / 32 GB Windows workstation represents **fewer than 0.1 %** of the install base we should design for. Treating it as the default has already cost the project real pain — `BUG-009` (wake threshold tuned to one specific microphone), `BUG-014` (audio host-API picked from one specific Realtek + Sennheiser driver set), `BUG-026` (an STT model name only meaningful on the maintainer's local Hugging Face cache), `BUG-027` (an Orb overlay relying on a specific multi-monitor layout). **Each of those would not occur on a cloud-first deployment.**

The maintainer's machine is the *exception*, not the *default*.

---

## 2. The Doctrine

From **2026-05-18 forward**, every architectural decision is evaluated against the **€5 VPS user**, not the maintainer.

Concretely:

1. **All five provider classes — Brain, STT, TTS, Vision, Wake — must have a fully cloud-reachable default path.** No required local GPU. No required local model download. No required Windows API. No required microphone. No required speaker. If a provider class only works locally today, it gets a cloud-default *before* it ships any new feature.
2. **No new hard dependency on Windows-specific or GPU-specific packages.** Anything that imports `pywin32`, `pywinauto`, `pyautogui`, `sounddevice`, `faster-whisper`, `onnxruntime-gpu`, `openwakeword`, `global-hotkeys`, `mss`, `pywebview`, or any other OS- or device-bound library belongs in an **optional install extra** (e.g. `pip install personal-jarvis[desktop]`, `[local-stt]`, `[vision-local]`), never in the base `pip install personal-jarvis`.
3. **The maintainer's setup is a power-user profile, not a baseline.** Tray icon, `pywebview` window, Orb overlay, global-hotkey wake, local Whisper, in-process Silero-VAD, PowerShell drift-guard daemon, Computer-Use harness — **all opt-in extras**. They light up when the relevant extras are installed; they degrade gracefully (with a clear, English-language message) when they are not.
4. **The headless VPS path is a first-class runtime.** A user pointing a browser at the FastAPI / WebSocket interface, talking through the browser's microphone and speakers (or via a channel adapter — Telegram, Discord, SMS, webhook), must reach the **full** Router-Brain → Worker-Critic → Mission-Manager experience without ever installing a Windows binary, a CUDA toolkit, or a native audio driver.
5. **Defaults in `jarvis.toml` cannot assume CUDA, local audio, or Windows paths.** STT defaults to a cloud provider (Deepgram / Google Cloud STT / Whisper-API). TTS defaults to a cloud provider (Gemini Flash TTS / Grok Voice / ElevenLabs). Wake defaults to a server-side gate (browser PTT button, channel adapter, webhook) — never a local KWS model. Brain has been provider-agnostic from day one and stays that way.
6. **Documentation, defaults, install instructions, and onboarding lead with the cloud path.** README quick-install, CLAUDE.md, every phase doc and ADR opens with the VPS / laptop story and treats the workstation profile as a footnote inside an "Optional power-user extras" section. Re-order the doc when in doubt.

---

## 3. Per-Layer Mapping

| Layer | Cloud-First Default (base install) | Local-Optional (power-user extras) |
|---|---|---|
| L0 OS / Hardware | Any Python 3.11+ host (Linux container, macOS, Windows); no native binaries required | Win32 hotkeys, CUDA, WASAPI, headset detection |
| L1 Audio I/O | Browser `getUserMedia` → WebSocket; channel adapters (Telegram, Discord, SMS, webhook) | `sounddevice` + WASAPI, shortest-token device matching, drift-guarded `jarvis.toml` |
| L2 Speech | Cloud STT (Deepgram / Google STT / Whisper-API); cloud TTS (Gemini Flash / Grok Voice / ElevenLabs); **server-side push-to-talk replaces local wake** | `faster-whisper` + Silero-VAD + openWakeWord on local GPU; SAPI5 fallback TTS |
| L3 Intent / Risk | Unchanged — pure Python logic | Unchanged |
| L4 Brain | Unchanged — already five cloud providers (OpenRouter, OpenAI, Gemini, Grok, Claude-Max-OAuth-inside-workers); no Anthropic-API hard-dep | Future Ollama-local optional path |
| L5 Harness-Adapter | OpenClaw via cloud subprocess host; MCP-Remote; Codex CLI on a worker container | POAV computer-use, local PowerShell, in-tree subprocess |
| L6 Orchestrator | Unchanged | Unchanged |
| L7 UI / UX | FastAPI + React **served as a web app** — reachable in any browser, no native window | `pywebview` window, tray, Orb overlay, global hotkey |

---

## 4. Honest Gap Statement

This document declares the **target**, not the **current state**.

As of 2026-05-18 the codebase still hardcodes several local-hardware assumptions:

- `jarvis.toml` defaults STT to `faster-whisper` on `device='cuda'`.
- `pyproject.toml` lists `faster-whisper`, `openwakeword`, `pywin32`, `sounddevice`, `pywinauto`, `pyautogui`, `global-hotkeys` as **hard** runtime dependencies.
- `README.md` §1 ("Quick install & first-run") and `CLAUDE.md` §"Windows specifics" assume Windows 11 Pro with a tray, a headset, and an `asInvoker` UAC manifest.
- ADR-0001 (Named-Pipe HMAC), ADR-0010 (`MsgWaitForMultipleObjects`), ADR-0015 (Obsidian registry probing) bake in Windows APIs without declaring them optional.
- `scripts/preflight.ps1`, `run.bat`, `scripts/drift-guard-daemon.ps1`, `scripts/check-working-tree.ps1`, `scripts/auto-push-eod.ps1` are PowerShell-only.

**These are not bugs in this document.** They are the **remediation backlog**. This document is the **North Star**; future PRs steer toward it.

**Pre-existing code is grandfathered until touched.** A code path that already violates the doctrine does not need to be rewritten in a panic. But any new change from 2026-05-18 forward is evaluated against this doctrine. Any *touch* of a violating code path is an opportunity to migrate it toward the cloud-first default, not extend the violation.

**Maintainer dev tooling may stay Windows-PowerShell-only.** That is the *developer's* environment, not the *user's* runtime. The line is drawn at the boundary between `scripts/` (developer tools — fair game to stay Windows-only) and the importable `jarvis/` package (runtime — must run on a Linux VPS).

---

## 5. Three Non-Negotiable Rules

Three rules supersede everything else in this repo:

1. **A new feature that only works on the maintainer's hardware is incomplete.** It needs either a cloud-equivalent or a graceful no-op (with a clear English-language message) when the local dependency is absent.
2. **A new hard dependency that does not `pip install` on a fresh Linux VPS is a bug.** Move it into an extras group. The base install must succeed on `python:3.11-slim` with nothing but a network connection and the standard tooling.
3. **A doc paragraph that describes "the user's mic", "the user's GPU", or "the Windows tray" without **first** describing the VPS / browser path is a doc bug.** Re-order the doc.

---

## 6. What Stays The Maintainer's

These are explicitly **maintainer-only**, will never become part of the open-source user experience, and may stay Windows-PowerShell-only without violating the doctrine:

- `scripts/preflight.ps1` — worktree drift recovery (BUG-006 / BUG-014 / BUG-015 defense).
- `scripts/check-working-tree.ps1` — pre-boot drift restore invoked by `run.bat`.
- `scripts/drift-guard-daemon.ps1` — `jarvis.toml` self-healing daemon (BUG-010 triple defense).
- `scripts/auto-push-eod.ps1` + `scripts/install-auto-push-task.ps1` — nightly tag-and-push safety net.
- `scripts/jarvis-config-drift-guard.ps1` — config drift defense.
- The Claude-Max OAuth token sync (`.credentials.json` refresh every 3 h) used by the GitHub bot on 29 private repos.
- The `~/.claude/` private configuration directory (memory, plans, brand guidelines).

These are the maintainer's *operational rituals* — they keep the maintainer's workstation healthy, but they have no place in an open-source user's runtime, and they would not work there either.

---

## 7. Pointer Network

This document is referenced from:

- [`README.md`](../README.md) §1 (top of file)
- [`CLAUDE.md`](../CLAUDE.md) §"Cloud-First Philosophy" (binding doctrine block)
- [`docs/openclaw-bridge.md`](openclaw-bridge.md) — preamble (planned: OpenClaw harness must support a cloud-subprocess-host transport, not only local Windows subprocesses)
- [`docs/BUGS.md`](BUGS.md) — recurring bug classes 1, 4, and 5 (restore-trap, subprocess flicker, audio host-API) are explicitly **maintainer-machine bugs** that would not occur on a cloud-first deployment, and serve as evidence for this doctrine

---

## 8. Decision Lens For Any PR

Before merging any PR from 2026-05-18 forward, the reviewer asks **one question**:

> *Would this PR work, end-to-end, for a user on a fresh `python:3.11-slim` Linux container with 1 vCPU, 1 GB RAM, no GPU, no audio hardware, no Windows APIs, and only a network connection?*

If **yes** — merge.
If **no** — either (a) the local-only portion is correctly gated behind an extras group with a graceful no-op fallback in the base install, or (b) the PR is rejected and asked to split.

That single question is the operational expression of this doctrine.
