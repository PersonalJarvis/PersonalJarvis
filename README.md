# Personal Jarvis

<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/icons/jarvis-gigi-256.png" alt="Personal Jarvis" width="100" />
</p>

<p align="center">
  <a href="https://personaljarvis.ai">personaljarvis.ai</a> · <a href="#install">install</a> · <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/first-run-setup.md">quick start</a> · <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/welcome-to-personal-jarvis.md">docs</a> · <a href="https://discord.gg/x7USduHxbc">discord</a>
</p>

<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-666666?labelColor=333333" alt="Apache 2.0 license" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/releases/latest"><img src="https://img.shields.io/github/v/release/PersonalJarvis/PersonalJarvis?label=release&labelColor=333333&color=666666" alt="latest release" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/stargazers"><img src="https://img.shields.io/github/stars/PersonalJarvis/PersonalJarvis?labelColor=333333&color=666666&logo=github" alt="GitHub stars" /></a>
  <a href="https://x.com/PersonalJarvis"><img src="https://img.shields.io/badge/follow-%40PersonalJarvis-000000?logo=x&logoColor=white" alt="follow @PersonalJarvis on X" /></a>
</p>

---

https://github.com/user-attachments/assets/bd2d5f3c-c601-475e-a37e-f532ea6ef6ea

**Your computer becomes an AI agent.** One desktop app you talk to. It answers, uses your computer, and runs your coding agents and a team of AI helpers for you.

- **talk instead of type**: say your own wake phrase and have a real conversation, or dictate into any app. [voice →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/voice-conversations.md)
- **every coding agent in one window**: Claude Code, Codex, OpenCode, Kimi, GLM and more, side by side, each in its own pane and optional git worktree. Ask Jarvis what T2 is doing. [agentic IDE →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/agentic-ide.md)
- **a team that keeps working**: persistent agents with their own instructions, routines and memory, in an office you can walk through. [agents →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/jarvis-agents.md)
- **hands on your computer**: it uses the browser and your apps, and asks before anything risky. [safety →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/safety-and-approvals.md)
- **any model, or no cloud at all**: one key from any supported provider, or local models through Ollama. [providers →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/personalize-and-connect/providers-and-api-keys.md)
- **plugs into your tools**: plugins, skills, MCP, and a local Markdown wiki as memory. [plugins →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/plugins.md)
- **runs beyond your laptop**: put agents on a VPS or a local VM over SSH, or run the whole app on a server. [headless →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/headless-vps-deployment.md)
- **yours, no catch**: free under Apache 2.0, no account with us, no analytics. Keys stay in your system keychain. [privacy →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/privacy-and-local-data.md)

---

## install

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
```

**macOS and Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
```

The installer checks for Python 3.11+ and Git, offers to install what is missing, sets up the app and opens it. Run it again to update. [install options →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/install/README.md)

**what you need**: Windows, macOS or Linux, and one API key from a supported provider, or a local model. A microphone for voice. No GPU needed; speech recognition and local models can still run on your own machine.

## first steps

1. **finish the one-time setup**: pick a language and a wake phrase of your own, like "Hey Nova", or a keyboard shortcut.
2. **connect one model**: add a provider key under **Settings › API Keys**, or set up a model under **Local models**.
3. **say your wake phrase**: try *"Plan a small project with me and ask what you need to know."* Then open **Agentic IDE** with a project folder, or create your first agent under **Agents**.

## works with

- **models**: OpenAI, Anthropic Claude, Google Gemini and Vertex AI, OpenRouter, NVIDIA, Ollama and any OpenAI-compatible local server, plus your Claude Code and Codex subscriptions.
- **speech**: local Whisper, OpenAI, Gemini, Groq, Deepgram and OpenRouter for listening; Piper (local), ElevenLabs, Cartesia, Inworld, Gemini and OpenRouter voices for speaking.
- **coding agents**: Claude Code, Codex, OpenCode, Kimi Code, GLM, Grok Build and Antigravity.

## how it works

- Personal Jarvis is a desktop app with a local server behind it. Your conversations, agents, memory and history are stored on your computer.
- Each request goes to the model you chose. Jarvis then decides whether to answer, use a tool, or hand the work to an agent.
- Every tool call passes a risk policy (safe, monitor, ask, block). You approve anything that could change your system, and every run is recorded so you can see what happened.
- Scripts and other agents reach the same app through the `jarvis` CLI and a local API. [CLI →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/jarvis-cli.md) · [architecture →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/architecture-overview.md)

## docs

[quick start](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/first-run-setup.md) · [voice](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/voice-conversations.md) · [dictation](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/dictation.md) · [agentic IDE](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/agentic-ide.md) · [agents](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/jarvis-agents.md) · [providers](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/personalize-and-connect/providers-and-api-keys.md) · [local models](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/personalize-and-connect/local-ai-providers.md) · [plugins](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/plugins.md) · [MCP](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/mcp-connections.md) · [CLI](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/jarvis-cli.md) · [server](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/headless-vps-deployment.md) · [architecture](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/architecture-overview.md) · [troubleshooting](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/troubleshooting.md)

## contribute

Start with a [good first issue](https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and [your first contribution in 10 minutes](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/CONTRIBUTING.md#your-first-contribution-in-10-minutes). Questions and ideas go to [discussions](https://github.com/PersonalJarvis/PersonalJarvis/discussions), security reports to [SECURITY.md](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/SECURITY.md).

If you are an AI agent helping with this repository, read [`AGENTS.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/AGENTS.md) first.

## thanks

If Personal Jarvis is useful to you, a star helps other people find it. Sponsors are listed in [SPONSORS.md](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/SPONSORS.md).

<!-- contributors:start -->

<a href="https://github.com/rubenluetke10-beep"><img src="https://avatars.githubusercontent.com/u/226271791?v=4&s=48" width="48" height="48" alt="rubenluetke10-beep"></a>

<!-- contributors:end -->

## license

[Apache 2.0](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE). Releases through 1.6.0 keep their original MIT license. See [NOTICE](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/NOTICE) and [trademark guidance](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/TRADEMARK.md).
