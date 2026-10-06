<h1 align="center">Personal Jarvis</h1>

<p align="center">
  <strong>The open-source AI agent super-app for your desktop.</strong><br />
  Run every coding agent side by side, talk to your computer, and keep a team of AI agents working while you are away.
</p>

<p align="center">
  <a href="https://pypi.org/project/personal-jarvis/"><img alt="PyPI" src="https://img.shields.io/pypi/v/personal-jarvis?labelColor=0A0A0A&amp;color=F7F7F4" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE"><img alt="Apache 2.0" src="https://img.shields.io/badge/License-Apache_2.0-F7F7F4?labelColor=0A0A0A" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/PersonalJarvis/PersonalJarvis/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="https://discord.gg/x7USduHxbc"><img alt="Discord" src="https://img.shields.io/badge/Discord-join-F7F7F4?labelColor=0A0A0A&amp;logo=discord&amp;logoColor=white" /></a>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="https://personaljarvis.ai">Website</a> ·
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/welcome-to-personal-jarvis.md">Docs</a> ·
  <a href="https://discord.gg/x7USduHxbc">Discord</a> ·
  <a href="https://x.com/PersonalJarvis">X</a>
</p>

https://github.com/user-attachments/assets/42afcb9f-323b-4a57-9b6d-17aa7a2585b2

<p align="center"><sub>A real, unedited recording of the app with sound: talking to Jarvis by voice, handing work to agents, and following them in the coding workspace.</sub></p>

---

Personal Jarvis is one desktop app for everything AI can do on a computer. It
has a workspace where several coding agents run side by side, AI agents that
keep their own memory, a voice assistant with a wake word, control of your
desktop and browser, and a plugin marketplace. It works with the models you
already pay for, whether hosted, local or through a subscription you have.

You say what you want. Jarvis answers you, does the task on your computer, or
hands it to the agent that fits, and you can watch every step.

It is free and open source, and it runs on your own machine. You do not need an
account, and there is no cloud of ours between you and your models. It runs on
Windows, macOS and Linux, and as a server without a screen.

## What's inside

| | |
|---|---|
| **Agentic IDE** | Run Claude Code, Codex, OpenCode, Kimi Code, GLM, Grok Build, Antigravity, Cursor CLI and DeepSeek Harness side by side in live terminal panes, as many as your machine can hold. You can brief them by voice. |
| **Jarvis Agents** | Build a team of AI agents that stay around. Each one has its own chat, instructions, tools, schedule and memory, and learns from its own work. |
| **Jarvis Verse** | Watch your agents in a 3D world. They walk to their desks, pick up work, and show what they are doing right now. |
| **Voice** | A wake word, live conversations, dictation into any app, and phone calls. You can interrupt it mid-sentence. |
| **Computer use** | Jarvis clicks, types and reads the screen on your desktop and in your browser. You decide what it may do. |
| **Marketplace** | More than 40 plugins, MCP servers, skills and command-line tools you install with one click, including GitHub, Google Workspace, Notion, Slack, Linear, Spotify and Home Assistant. |
| **Any model** | OpenAI, Anthropic, Gemini, OpenRouter, NVIDIA, Ollama and any local server, or your Claude and ChatGPT subscriptions. You can pick a different one for each task. |
| **Memory** | A local wiki that Jarvis reads, writes and cites, so it remembers context across chats, agents and days. |
| **Routines** | Work that repeats on a schedule, such as a morning briefing, an inbox sweep or a nightly report. |
| **Appshots** | Press two keys to show Jarvis the window you are looking at, or just one area of it. You can record clips the same way. |
| **Pets** | A small animated companion on your desktop that reacts while Jarvis listens, thinks and talks. |

## Run every coding agent at once

The Agentic IDE turns one screen into a room full of coding agents. You open a
project, split the screen into panes, and give each pane its own coding agent.
Every pane gets a short call sign such as T1 or T2, so you can steer them
without touching the keyboard. You might say "Tell T1 to write the tests",
"What is T2 working on?" or "Send the fix plan to all panes".

- Jarvis finds the coding tools you already have installed and starts any of
  them in a pane with one key. You keep using the subscriptions you pay for.
- You can send the same task to several panes and compare what comes back.
- You can talk to Jarvis while the agents type, and it passes your brief to the
  right pane by its call sign.
- Every pane is a real terminal, so you can type into it yourself at any time.

The [Agentic IDE guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/agentic-ide.md) has the details.

## A team of agents that keeps working

Jarvis Agents are specialists you create with one click and keep for as long as
you like: a researcher, a release manager, a marketing writer, or whatever your
work needs.

- Each agent has one chat that never ends, so tomorrow you continue where you
  stopped today.
- Agents keep private notes on what they learned from their own work, and they
  get better at your tasks over time.
- Give an agent a routine and it does the work on schedule, without being asked.
- Files, reports and plans they produce land in Artifacts, and you can see every
  tool they used along the way.
- Longer coding missions run in their own copy of your repository (a git
  worktree), can split into parallel workers, and pass a review before you see
  the result.

Read more in the [Agents guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/jarvis-agents.md),
[how agents learn](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/agent-society/self-learning.md)
and [routines](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/routines.md).

## Talk to your computer

You pick your own wake phrase, such as "Hey Nova", "Jarvis" or anything else,
and then you talk.

- Conversations are live and take turns naturally. Mid-sentence you can ask it
  to open a website, check your calendar or start an agent.
- Dictation types what you say into any app and learns your words from a
  personal dictionary.
- Phone calls go through your own number. You can call Jarvis, or let it call
  someone for you once you approve.
- Speech recognition and voices can run fully offline. Hosted voices are
  optional.

Read more in the [voice guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/voice-conversations.md)
and the [dictation guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/dictation.md).

## Connect your tools

- Install plugins from the marketplace with one sign-in in your browser, or add
  any MCP server yourself.
- Jarvis finds command-line tools such as GitHub, Vercel, Supabase, Stripe,
  Docker and the cloud CLIs, and signs you in to them from the app.
- Skills are reusable instructions that Jarvis picks up when your request fits
  one.
- You can reach Jarvis from Telegram and Discord.
- The `jarvis` command and a local API give your own scripts, and other agents,
  the same control you have.

Read more about [plugins](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/plugins.md),
[MCP](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/mcp-connections.md),
[command-line tools](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/cli-connections.md)
and the [Jarvis CLI](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/jarvis-cli.md).

## You stay in control

Every action Jarvis takes passes a risk check with four levels: safe, monitor,
ask and block. Anything that could change your system waits for your approval,
and every run is recorded, so you can see later exactly what happened. Your keys
are kept in your operating system's credential store. Nothing leaves your
machine unless you connect a service that needs it.

Read more about [safety and approvals](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/safety-and-approvals.md)
and [privacy and local data](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/privacy-and-local-data.md).

## Install

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
```

**macOS and Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
```

The installer checks for Python 3.11 or newer and Git, offers to install
whatever is missing, adds the desktop launcher and opens the app. If you run it
again, it updates your existing installation. You choose your language, wake
phrase and models inside the app.
Later, open it from the desktop launcher, or type `jarvis` in a terminal.

If you prefer a regular desktop installer, download one here:

- [Windows](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-Setup-x64.exe)
- [macOS on Apple Silicon](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-macOS-arm64.dmg)
- [macOS on Intel](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-macOS-x64.dmg)
- [Linux AppImage](https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/PersonalJarvis-Linux-x86_64.AppImage)

The macOS disk image is not notarized by Apple yet. The first time you open it
on macOS 15, go to **System Settings > Privacy & Security** and click **Open
Anyway**. Up to macOS 14, right-click the app and choose **Open**. The one-line
installer above does not need this step.

To run Jarvis on a server, use `pip install personal-jarvis` and `jarvis serve`,
then open `http://localhost:47821` in a browser. Using a microphone from another
computer needs HTTPS. The [headless deployment guide](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/headless-vps-deployment.md)
explains the setup. If you install with pip or pipx by hand on an Intel Mac or
on Windows ARM64, you also need the
[native package index](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/install-personal-jarvis.md#install-the-isolated-pypi-package).
The installer sets that up for you.

You need one API key, a subscription or a local model. A microphone helps for
voice. You do not need a GPU.

## First steps

1. Finish the short setup in the app. Pick your language, a wake phrase (or a
   keyboard shortcut instead) and one model.
2. Say your wake phrase and ask for something, for example "Plan a small project
   with me and ask what you need to know."
3. Open a project folder in the Agentic IDE, or create your first agent.

## Works with

- **Models:** OpenAI, Anthropic Claude, Google Gemini and Vertex AI, OpenRouter, NVIDIA, Ollama and any OpenAI-compatible local server, plus your Claude Code and Codex subscriptions.
- **Coding agents:** Claude Code, Codex, OpenCode, Kimi Code, GLM, Grok Build, Antigravity, Cursor CLI and DeepSeek Harness.
- **Speech:** local Whisper, OpenAI, Gemini, Groq, Deepgram and OpenRouter for listening; Piper (local), ElevenLabs, Cartesia, Inworld, Gemini and OpenRouter voices for speaking.

## Docs

- **Getting started:** [quick start](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/first-run-setup.md), [app tour](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/start-here/desktop-app-tour.md)
- **Everyday use:** [voice](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/voice-conversations.md), [dictation](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/everyday-use/dictation.md), [memory](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/knowledge-and-sharing/wiki-and-memory.md)
- **Agents and automation:** [agentic IDE](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/agentic-ide.md), [agents](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/jarvis-agents.md), [computer use](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/computer-use.md), [plugins](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/plugins.md), [MCP](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/extend-and-automate/mcp-connections.md), [CLI](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/jarvis-cli.md)
- **Models:** [providers](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/personalize-and-connect/providers-and-api-keys.md), [local models](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/personalize-and-connect/local-ai-providers.md)
- **Running and fixing:** [server](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/headless-vps-deployment.md), [architecture](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/architecture-overview.md), [troubleshooting](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/product/privacy-safety-and-support/troubleshooting.md)

## Contribute

A good place to start is a [good first issue](https://github.com/PersonalJarvis/PersonalJarvis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and the guide to [your first contribution in 10 minutes](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/CONTRIBUTING.md#your-first-contribution-in-10-minutes). Questions and ideas go to [discussions](https://github.com/PersonalJarvis/PersonalJarvis/discussions), and security reports follow [SECURITY.md](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/SECURITY.md).

If you are an AI agent helping with this repository, read [`AGENTS.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/AGENTS.md) first.

If Personal Jarvis is useful to you, a star on GitHub helps other people find it. Sponsors are listed in [SPONSORS.md](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/SPONSORS.md).

<!-- contributors:start -->

<a href="https://github.com/rubenluetke10-beep"><img src="https://avatars.githubusercontent.com/u/226271791?v=4&s=48" width="48" height="48" alt="rubenluetke10-beep"></a>

<!-- contributors:end -->

## License

[Apache 2.0](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE). Releases up to and including 1.6.0 keep their original MIT license. See [NOTICE](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/NOTICE) and the [trademark guidance](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/TRADEMARK.md).
