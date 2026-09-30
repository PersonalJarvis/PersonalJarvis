<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/banner.png" alt="Personal Jarvis" width="860" />
</p>

<h3 align="center">A voice assistant that runs on your own computer<br />and runs your coding agents for you.</h3>

<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/releases/latest"><img src="https://img.shields.io/github/v/release/PersonalJarvis/PersonalJarvis?label=release&labelColor=333333&color=666666" alt="latest release" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-666666?labelColor=333333" alt="Apache 2.0 license" /></a>
  <a href="https://discord.gg/x7USduHxbc"><img src="https://img.shields.io/badge/chat-Discord-5865F2?labelColor=333333&logo=discord&logoColor=white" alt="Discord" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/stargazers"><img src="https://img.shields.io/github/stars/PersonalJarvis/PersonalJarvis?labelColor=333333&color=666666&logo=github" alt="GitHub stars" /></a>
  <a href="https://x.com/PersonalJarvis"><img src="https://img.shields.io/badge/follow-%40PersonalJarvis-000000?logo=x&logoColor=white" alt="follow @PersonalJarvis on X" /></a>
</p>

<p align="center">
  <a href="https://personaljarvis.ai">Website</a> ·
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/welcome-to-personal-jarvis.md">Docs</a> ·
  <a href="https://personaljarvis.ai/blog">Blog</a> ·
  <a href="https://discord.gg/x7USduHxbc">Discord</a> ·
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/releases/latest">Download</a>
</p>

Personal Jarvis is an open-source desktop assistant for Windows, macOS and Linux. You say a wake phrase you picked yourself, and it answers out loud, operates your browser and desktop apps, and hands coding work to the agents you already pay for: Claude Code, Codex, OpenCode, Kimi Code and others, side by side in one workspace.

It brings no model and no subscription of its own. It runs on your Claude Code or Codex plan, one API key from any supported provider, or a local model through Ollama. There is no account and no analytics, and your keys, memory and history stay on your machine.

https://github.com/user-attachments/assets/bd2d5f3c-c601-475e-a37e-f532ea6ef6ea

<p align="center"><sub>A real, unedited click-through of the app, sped up only where agents are thinking.</sub></p>

## Install

Download the installer for your system from the [latest release](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest):

| System | File |
| --- | --- |
| Windows 10/11 (x64) | [`PersonalJarvis-Setup-x64.exe`](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-Setup-x64.exe) |
| macOS, Apple Silicon | [`PersonalJarvis-macOS-arm64.dmg`](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-macOS-arm64.dmg) |
| macOS, Intel | [`PersonalJarvis-macOS-x64.dmg`](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-macOS-x64.dmg) |
| Linux (x86_64) | [`PersonalJarvis-Linux-x86_64.AppImage`](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-Linux-x86_64.AppImage) |

Or install from the terminal. This is the right choice if you want to follow `main` or read the code you run.

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
```

```bash
# macOS and Linux
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
```

The script checks for Python 3.11+ and Git, offers to install whatever is missing, sets up the app and opens it. Run it again to update. `pipx install personal-jarvis` works too. More options, including uninstalling, are in the [install guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/install/README.md).

You don't need a GPU. Speech recognition and local models run on a normal machine, just slower. A microphone helps if you want to talk to it; a keyboard shortcut works without one.

## Your first five minutes

1. **Pick a language and a wake phrase.** Any phrase works, for example "Hey Nova". It is detected on your machine, so nothing is sent anywhere until you say it.
2. **Connect a model.** Add a key under **Settings › API Keys**, connect your Claude Code or Codex subscription, or set up a local model under **Local models**.
3. **Say your wake phrase and ask for something.** For example: "Plan a small project with me and ask me what you need to know."
4. **Open a project folder in the Agentic IDE** and start two coding agents next to each other.

The [first-run guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/first-run-setup.md) walks through each step with screenshots.

## What you can do with it

### Talk to it like a person, not a chat box

Jarvis opens a live, two-way audio session. You can interrupt it mid-sentence. It answers right away and keeps talking to you while the actual work runs behind it, and the work keeps going after you hang up.

You choose where the voice runs. [GPT-Live](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/gpt-live.md) (experimental) uses OpenAI's realtime voice over WebRTC for the conversation and a separate model for reasoning and tool calls, both on one OpenAI key. Gemini Live works the same way. If your voice should not leave the house, Jarvis installs and manages a local realtime server on your own GPU (12 GB of VRAM and up) that listens, thinks through Ollama and speaks without a cloud call.

Dictation, phone calls, and chat through Telegram and Discord use the same assistant.

### Run a room full of coding agents

The [Agentic IDE](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/agentic-ide.md) holds up to 16 coding agents per workspace. Each pane is the real CLI: Claude Code, Codex, OpenCode, Kimi Code, GLM Coding Plan, Grok Build, Antigravity, DeepSeek Harness or a plain shell. You can mix them in one workspace and give each its own git worktree.

Jarvis keeps track of all of them, so you can ask by voice:

- "Tell T1 to run the tests."
- "What is T2 working on?"
- "Have T1 and T2 split the accessibility audit."

For the last one, Jarvis writes a separate assignment for each agent and tells you which ones received it and which it could not reach. The Spend view reads the session logs the CLIs write to disk and shows what each agent did and what it cost.

Agents don't stop when you close the app. They run in their own background process, and after a reboot Jarvis resumes each one on its own conversation. So far this is verified live on Windows with the terminal install. We wrote up how that works, and what broke on the way, in [Agents outlive the app](https://personaljarvis.ai/blog/agents-outlive-the-app/).

### Build a team that works while you don't

Behind Jarvis you can set up [agents](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/jarvis-agents.md) of your own. Each has a name, instructions, memory and a browser. They run routines on a schedule, write down procedures that worked so they can reuse them, and stop to ask you a multiple-choice question when they are not sure instead of guessing. An agent can also work on another machine over SSH, such as a VPS, while its chat and approvals stay on yours.

### Stay in control of what it touches

A small router only decides where a request goes: an answer, a tool, or an agent. Every action then passes through one executor with four risk levels: **safe**, **monitor**, **ask** and **block**. Anything that could change your system waits for your approval, and every run is recorded, so you can check afterwards what happened. [Safety and approvals →](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/safety-and-approvals.md)

## Works with

| | |
| --- | --- |
| **Models** | OpenAI, Anthropic Claude, Google Gemini and Vertex AI, xAI Grok, OpenRouter, NVIDIA, Ollama and any OpenAI-compatible local server |
| **Subscriptions** | Claude Code, Codex, Antigravity |
| **Speech to text** | Local Whisper, local Nemotron, OpenAI, Gemini, Vertex AI, Groq, Deepgram, OpenRouter |
| **Text to speech** | Piper (local), ElevenLabs, Cartesia, Inworld, Gemini, Vertex AI, Grok, OpenRouter |
| **Coding agents** | Claude Code, Codex, OpenCode, Kimi Code, GLM Coding Plan, Grok Build, Antigravity, DeepSeek Harness |
| **Extensions** | [Plugins](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/plugins.md), [MCP servers](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/mcp-connections.md), Telegram, Discord |

Any single key is enough to start. The first-run guide offers starter plans that set up every part from one provider, and you can change each part later on its own.

## How it works

<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/how-personal-jarvis-works-v2.png" alt="Desktop, browser, voice, CLI and chat channels feed the Jarvis core, which answers, calls protected tools or delegates to agents, and records the result" width="720" />
</p>

Personal Jarvis is a Python core with a React desktop app in front of it, both running locally. The same core runs headless on a server, and you use it from the browser ([server guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/headless-vps-deployment.md)). Scripts and other agents drive it through the [`jarvis` CLI](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/jarvis-cli.md) and a local API. The full design is in the [architecture overview](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/architecture-overview.md).

## From the blog

We build Personal Jarvis with Jarvis, and we write down what we measure along the way.

- [How many coding agents do you actually run at once?](https://personaljarvis.ai/blog/how-many-coding-agents-at-once/) Five months of our own logs: two or more agents were working 69% of the time, and what broke as the number grew.
- [Agents outlive the app](https://personaljarvis.ai/blog/agents-outlive-the-app/) How closing Jarvis or restarting the PC stopped costing us a single agent.

## Documentation

| I want to… | Read |
| --- | --- |
| Set it up the first time | [First-run setup](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/first-run-setup.md) |
| Talk to it or dictate | [Voice conversations](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/voice-conversations.md) · [Dictation](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/dictation.md) |
| Run coding agents | [Agentic IDE](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/agentic-ide.md) |
| Build my own agents | [Jarvis agents](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/jarvis-agents.md) |
| Choose a provider or run models locally | [Providers and API keys](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/personalize-and-connect/providers-and-api-keys.md) · [Local models](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/personalize-and-connect/local-ai-providers.md) |
| Run it on a server | [Headless deployment](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/headless-vps-deployment.md) |
| Fix something | [Troubleshooting](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/troubleshooting.md) |

## Contributing

Pick a [good first issue](https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and follow [your first contribution in 10 minutes](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/CONTRIBUTING.md#your-first-contribution-in-10-minutes). Questions and ideas go to [Discussions](https://github.com/PersonalJarvis/PersonalJarvis/discussions) or [Discord](https://discord.gg/x7USduHxbc). Report security issues as described in [SECURITY.md](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/SECURITY.md), not in a public issue.

If you are an AI agent working on this repository, read [`AGENTS.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/AGENTS.md) first.

If Personal Jarvis is useful to you, a star helps other people find it. Sponsors are listed in [SPONSORS.md](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/SPONSORS.md).

<!-- contributors:start -->

<a href="https://github.com/rubenluetke10-beep"><img src="https://avatars.githubusercontent.com/u/226271791?v=4&s=48" width="48" height="48" alt="rubenluetke10-beep"></a>

<!-- contributors:end -->

## License

[Apache 2.0](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE). Releases through 1.6.0 keep their original MIT license. See [NOTICE](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/NOTICE) and the [trademark guidance](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/TRADEMARK.md).
