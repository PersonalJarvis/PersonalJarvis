<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/banner.png" alt="Personal Jarvis" width="860" />
</p>

<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-666666?labelColor=333333" alt="Apache 2.0 license" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/releases/latest"><img src="https://img.shields.io/github/v/release/PersonalJarvis/PersonalJarvis?label=release&labelColor=333333&color=666666" alt="latest release" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/stargazers"><img src="https://img.shields.io/github/stars/PersonalJarvis/PersonalJarvis?labelColor=333333&color=666666&logo=github" alt="GitHub stars" /></a>
  <a href="https://x.com/PersonalJarvis"><img src="https://img.shields.io/badge/follow-%40PersonalJarvis-000000?logo=x&logoColor=white" alt="follow @PersonalJarvis on X" /></a>
</p>

**Personal Jarvis is an open-source AI assistant that lives on your desktop and gets real work done.** You call it with a wake phrase of your own or type to it, and it answers, uses your browser and your apps, and hands longer jobs to AI agents that keep working while you do something else. It is also where your coding agents live: Claude Code, Codex, OpenCode and others run side by side in one workspace that Jarvis can see and steer. One app for Windows, macOS and Linux, on your laptop or on a server.

**Yours, with no catch.** Conversations, memory, agents and history stay on your computer, and your keys sit in the system keychain. Models are swappable: one key from any supported provider, your Claude Code or Codex subscription, or local models through Ollama. Your prompts go only to the providers you connect. There is no account with us, no paid tier and no analytics in the code. Every tool call passes a risk policy, and anything that could change your system waits for your approval.

[Website](https://personaljarvis.ai) · [Docs](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/welcome-to-personal-jarvis.md) · [Getting started](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/first-run-setup.md) · [How it works](#how-it-works) · [Discord](https://discord.gg/x7USduHxbc) · [X](https://x.com/PersonalJarvis)

https://github.com/user-attachments/assets/bd2d5f3c-c601-475e-a37e-f532ea6ef6ea

<p align="center"><sub>A real, unedited click-through of the app, sped up only where agents are thinking.</sub></p>

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

- Personal Jarvis is a desktop app with a local server behind it. The same server can run on its own, headless on a VPS, and you use it from the browser.
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
