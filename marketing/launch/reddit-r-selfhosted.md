# r/selfhosted draft

> **Status: DRAFT — do not post without maintainer review.**
> r/selfhosted cares about: runs on my box, no phone-home, easy deploy,
> resource footprint, and "what's the catch". Answer those directly.

---

**Title:**

Personal Jarvis – a self-hosted voice assistant that runs your machine: headless on a small VPS, browser as mic/speaker, MIT

**Body:**

I've open-sourced the assistant I run at home: a voice agent that doesn't just
answer questions but actually does work — dispatches coding agents, drives the
browser/desktop, writes into a local knowledge wiki, and reports back by voice.

The parts this sub usually asks about first:

**Self-hosting story**

- `jarvis serve` runs fully headless: FastAPI + WebSocket + a browser UI. Your
  browser provides microphone and speakers, so a screenless VPS can still hold
  a voice conversation. Tested against a bare `python:3.11-slim` — no GPU, no
  audio stack, no OS keyring required (falls back to ENV/.env or a local file).
- Base install is deliberately torch-free and platform-universal; local
  STT/wake models are an opt-in extra (~1.5 GB) if you want audio to never
  leave the box.
- One-line installer (venv-based — no Docker required; read it before running,
  it's short), or pipx, or a manual clone. Windows/macOS/Linux desktop mode
  exists too if you want the tray + overlay experience.

**Privacy / phone-home**

- No telemetry, no analytics, no account. Outbound traffic goes only to the
  providers you configure (LLM APIs, and STT only if you choose cloud STT).
- Keys live in the OS credential manager (or ENV/.env on servers), never in
  config files or the repo.
- The instruction boundary is structural: content the agent *reads* (web
  pages, mails, documents) is data, never commands — that's the prompt-
  injection defense, documented in SECURITY.md.

**The catch (being honest)**

- The LLM "brain" is bring-your-own-key cloud API (or a flat-rate CLI
  subscription like Claude Max / ChatGPT login for the heavy agents) — there
  is no local-LLM brain today. Wake word + STT can be fully local.
- TTS is cloud. And computer-use is the least mature path.
- It's a young project; expect rough edges and a fast-moving main branch.

Resource footprint on my smallest box: a €5/month VPS runs the headless
server comfortably — the heavy lifting happens at the API providers.

Repo: https://github.com/PersonalJarvis/PersonalJarvis (MIT)
