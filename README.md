# Personal Jarvis

## Your computer becomes an AI agent.

Personal Jarvis is an open-source desktop app that connects your computer, AI
models, tools, services, and specialized agents. Give it a task in chat or by
voice. Jarvis can use the browser and desktop, call connected tools, delegate
longer work, and bring the result back to one workspace.

<p align="center">
  <a href="https://pypi.org/project/personal-jarvis/"><img alt="PyPI" src="https://img.shields.io/pypi/v/personal-jarvis?labelColor=0A0A0A&amp;color=F7F7F4" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE"><img alt="Apache 2.0" src="https://img.shields.io/badge/License-Apache_2.0-F7F7F4?labelColor=0A0A0A" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/PersonalJarvis/PersonalJarvis/actions/workflows/ci.yml/badge.svg" /></a>
</p>

## See it in action

https://github.com/user-attachments/assets/9930ca95-5015-4ade-9a16-975c77d179fd

<p align="center"><sub>A real click-through of the app: every frame is recorded, waiting is sped up, and a few moments are zoomed in.</sub></p>

Want to try Personal Jarvis later? **Star this repository** to keep it handy.

**Why it is different:** Jarvis coordinates the tools you already have. A request
can move from voice or chat to [computer use](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/computer-use.md),
[Codex or Claude Code](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/agentic-ide.md), [local or hosted models](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/personalize-and-connect/local-ai-providers.md),
[plugins and MCP](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/plugins.md), or a [background agent](#jarvis-agents).
[Memory](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/knowledge-and-sharing/wiki-and-memory.md), [agent learning](#jarvis-agents), and
[routines](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/automations.md) provide context and recurring work.
You can inspect actions, approvals, and output in the app. What runs depends on
your setup, permissions, and the tools you connect.

**Try it:** [Install on Windows, macOS, or Linux](#install) ·
[Watch the real demo](#see-it-in-action) ·
[Read the first-run guide](#first-steps)

## Install

**Desktop downloads:** [Windows](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-Setup-x64.exe) ·
[macOS (Apple Silicon)](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-macOS-arm64.dmg) ·
[macOS (Intel)](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-macOS-x64.dmg) ·
[Linux AppImage](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-Linux-x86_64.AppImage)

Prefer the command line? Use the installer below.

**Windows — PowerShell**

```powershell
irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
```

**macOS and Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
```

The installer checks Python 3.11+ and Git, offers to install missing prerequisites
through the host package manager, installs the applicable desktop components,
registers the desktop launcher, and opens the app. Language, wake phrase, and provider setup happen in the app.
OS permissions and hardware capabilities affect voice and desktop control.
Re-running the installer updates an existing installation.

After installation, open the desktop launcher or start the app from a terminal:

```bash
jarvis          # full desktop
```

For a minimal server installation, use `pip install personal-jarvis` and
`jarvis serve`. Open the local address reported at startup; the default is
`http://localhost:47821`. Remote browser microphone access requires HTTPS.
See the [headless deployment guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/headless-vps-deployment.md).
Manual pip/pipx installs on Intel macOS and Windows ARM64 also need the
[native package index](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/install-personal-jarvis.md#install-the-isolated-pypi-package);
the recommended installer configures it automatically.

The idea is simple: one place on your computer where you say what you need, and it happens. You talk to Jarvis or type to it, and it works out whether to just answer, do something on your computer for you, or pass the job to an agent that keeps at it while you get on with your day. You can always see what it's doing, and it asks before it touches anything that matters.

It runs on your own machine with whichever model you like, local ones included, and it's free. There's no account to create, and nothing sends your data anywhere you didn't connect yourself.

## One request, several ways to get it done

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/how-a-request-runs-dark.svg" />
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/how-a-request-runs-light.svg" alt="You ask by voice or chat. Jarvis picks a route: answer directly, act on your desktop, browser, apps and tools, or hand longer work to an agent. Every action passes a risk check of safe, monitor, ask you, or block, and the result comes back with every step saved in history." width="100%" />
</picture>

| Stage | What Jarvis connects |
|---|---|
| **Ask** | Start in chat or Voice Chat. Jarvis Voice separately dictates into other apps. |
| **Act** | Use the host desktop and browser through Computer Use, or call a connected plugin, skill, CLI, or MCP tool. |
| **Delegate** | Hand longer work to a background mission, a persistent specialist, or a Codex/Claude Code coding session when configured. Bounded parallel workers can split suitable work. |
| **Keep context** | Use the local wiki and private agent lessons; schedule routines for recurring tasks. |
| **Review** | Follow approvals and activity, then open the answer, file, or report in the conversation or Artifacts. |

The app runs on Windows, macOS, and Linux, with headless use for server-capable
features. Desktop control needs a supported graphical session and OS permission.
Local models and speech are optional; hosted providers and connected services
receive the content required for their requests. See [privacy and local data](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/privacy-and-local-data.md).

## Start with chat or voice

[Website](https://personaljarvis.ai) · [Docs](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/welcome-to-personal-jarvis.md) · [Getting started](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/first-run-setup.md) · [How it works](#how-it-works) · [Discord](https://discord.gg/x7USduHxbc) · [X](https://x.com/PersonalJarvis)

## Jarvis Agents

Build a team you can return to. Each agent has an identity, a direct conversation,
standing instructions, and access to the tools you grant it. Pick a connected
model or supported agent account for the work, and keep the conversations in
one workspace.

- **Talk directly to a specialist.** Select an agent from the roster and continue its chat.
- **Give it a standing brief.** Configure its instructions, model access, and tools.
- **Set up recurring work.** Per-agent routines expose instructions, scheduling, and execution history.
- **Inspect its work.** Read messages and tool activity, and open produced files in Artifacts.
- **Explore the world view.** The workspace also has a visual map of the team.

Persistent agents and isolated coding missions have different lifecycles.
Coding missions can use worktree isolation and critic review; an ordinary
agent chat is not a new isolated worktree on every message.
For suitable tasks, a mission can fan out to a bounded group of child workers.
Each child has its own result; this is controlled delegation rather than an
unlimited self-spawning swarm.

[Agent guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/jarvis-agents.md) ·
[Agent learning](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/agent-society/self-learning.md) · [Routines](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/routines.md)

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

Panes have call signs so you can address a particular session through Jarvis:
*"Tell T1 to run the tests"* or *"What is T2 working on?"* Return to the workspace
to inspect output, respond to a prompt, or take over manually. A terminal becoming
idle is not proof that its result is correct; inspect its changes and validation.
You can also send one brief to several coding panes, with a delivery result for
each pane. This is the coding workspace's parallel-agent path.

Sponsors are listed in [SPONSORS.md](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/SPONSORS.md).

<!-- contributors:start -->

<a href="https://github.com/rubenluetke10-beep"><img src="https://avatars.githubusercontent.com/u/226271791?v=4&s=48" width="48" height="48" alt="rubenluetke10-beep"></a> <a href="https://github.com/CodeByPeace"><img src="https://avatars.githubusercontent.com/u/228503734?v=4&s=48" width="48" height="48" alt="CodeByPeace"></a>

<!-- contributors:end -->

## license

[Apache 2.0](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE). Releases through 1.6.0 keep their original MIT license. See [NOTICE](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/NOTICE) and [trademark guidance](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/TRADEMARK.md).
