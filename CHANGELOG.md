# Changelog

All notable changes in Personal Jarvis.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning per [SemVer](https://semver.org/).

---

## [v0.0.0] — 2026-06-04

Initial public release of **Personal Jarvis** — a voice-driven, cross-platform
meta-orchestrator. You download it like a normal desktop app, bring your own
cloud API keys, and talk to it; the core pattern is a Supervisor-Agent that
dispatches work to interchangeable harnesses.

### Highlights

- **Voice pipeline** — wake word → VAD → STT → Brain → TTS, with a sub-second
  acknowledgement tier and a regex-only voice-output filter.
- **Multi-provider Brain** — Claude, OpenRouter, OpenAI, Gemini, and Grok, with
  a smart fallback chain and runtime provider switching. Bring your own keys.
- **Supervisor-Agent orchestration** — a router-tier dispatcher plus a
  self-healing Worker-Critic mission system with git-worktree isolation.
- **Cross-platform desktop app** — native faces on Linux, macOS, and Windows;
  a headless/server mode is a fully supported secondary deployment.
- **Memory & Knowledge Wiki**, **vision / computer-use**, a **risk-tier
  executor**, a **CLI catalog + terminal**, and a **FastAPI + React** desktop UI.

See the README for the full feature surface and install instructions.
