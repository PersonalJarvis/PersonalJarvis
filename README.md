<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/banner.png" alt="Personal Jarvis" width="860" />
</p>

<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-666666?labelColor=333333" alt="Apache 2.0 license" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/releases/latest"><img src="https://img.shields.io/github/v/release/PersonalJarvis/PersonalJarvis?label=release&labelColor=333333&color=666666" alt="latest release" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/stargazers"><img src="https://img.shields.io/github/stars/PersonalJarvis/PersonalJarvis?labelColor=333333&color=666666&logo=github" alt="GitHub stars" /></a>
  <a href="https://x.com/PersonalJarvis"><img src="https://img.shields.io/badge/follow-%40PersonalJarvis-000000?logo=x&logoColor=white" alt="follow @PersonalJarvis on X" /></a>
</p>

Personal Jarvis is an open-source AI assistant that runs on your own computer and that you simply talk to. Say your wake phrase and it answers out loud, works your browser and desktop apps, and runs your coding agents for you: Claude Code, Codex, OpenCode, Kimi and others sit side by side in one workspace, and you can tell any of them what to do by voice ("tell T1 to run the tests"). Longer jobs go to persistent agents that keep working in the background. One app for Windows, macOS and Linux, or headless on a server.

**It conducts your tools instead of replacing them.** Most AI apps want you to move into their chat and their subscription. Jarvis sits on top of what you already have: your Claude Code or Codex plan, one API key from any provider, or a local model through Ollama. It turns them into a single assistant you can talk to, and you can swap any of them without touching anything else.

**How it's built.** A Python core and a React desktop app, both running locally. A small router only decides where a request goes (answer, tool, or agent) and every action passes through one executor with a risk policy (safe, monitor, ask, block), so nothing that could change your system happens without your approval, and every run is recorded. Voice streams end to end, so Jarvis starts answering before it has finished thinking. State, memory and keys stay on your machine; there is no account, no paid tier and no analytics. The whole design is in the [architecture overview](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/architecture-overview.md).

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

You need Windows, macOS or Linux, plus one API key from a supported provider or a local model. A microphone helps if you want to talk to it. You don't need a GPU; speech recognition and local models run fine on a normal machine, just slower.

## first steps

1. Go through the short setup in the app. Pick a language and a wake phrase of your own, like "Hey Nova", or use a keyboard shortcut instead.
2. Connect a model. Add a key under Settings › API Keys, or set up a local one under Local models.
3. Say your wake phrase and ask for something, for example "Plan a small project with me and ask what you need to know." After that, open a project folder in the Agentic IDE or create your first agent.

## works with

- Models: OpenAI, Anthropic Claude, Google Gemini and Vertex AI, OpenRouter, NVIDIA, Ollama and any OpenAI-compatible local server, plus your Claude Code and Codex subscriptions.
- Speech: local Whisper, OpenAI, Gemini, Groq, Deepgram and OpenRouter for listening; Piper (local), ElevenLabs, Cartesia, Inworld, Gemini and OpenRouter voices for speaking.
- Coding agents: Claude Code, Codex, OpenCode, Kimi Code, GLM, Grok Build and Antigravity.

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
