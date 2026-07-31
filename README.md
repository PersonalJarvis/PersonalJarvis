<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis">
    <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/banner.png" alt="Personal Jarvis: an open-source voice agent for your computer" width="860" />
  </a>
</p>

<p align="center">
  <a href="https://pypi.org/project/personal-jarvis/"><img alt="PyPI: personal-jarvis" src="https://img.shields.io/pypi/v/personal-jarvis?style=for-the-badge&labelColor=242424&color=e7c46e" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/PersonalJarvis/PersonalJarvis?style=for-the-badge&labelColor=242424&color=e7c46e" /></a>
  <a href="https://discord.gg/x7USduHxbc"><img alt="Discord" src="https://img.shields.io/badge/Discord-Join-5865F2?style=for-the-badge&logo=discord&logoColor=white&labelColor=242424" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-e7c46e?style=for-the-badge&labelColor=242424" /></a>
  <img alt="Windows, macOS, and Linux" src="https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-242424?style=for-the-badge&labelColor=242424&color=242424" />
</p>

<p align="center">
  <strong>Say it. Personal Jarvis takes the screen and gets it done.</strong>
</p>

<p align="center">
  Personal Jarvis is an open-source voice agent that answers aloud, operates your apps, and remembers useful context.<br/>
  For larger jobs, it starts an isolated AI worker and has a critic review the result.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#see-it-work">Demo</a> ·
  <a href="https://personaljarvis.ai/">Website</a> ·
  <a href="#documentation">Docs</a> ·
  <a href="https://discord.gg/x7USduHxbc">Discord</a>
</p>

## See it work

<p align="center">
  <strong><a href="https://www.youtube.com/watch?v=6xoxgNu5fd8">Watch the Personal Jarvis demo</a></strong>
</p>

## Install

The installer checks for Python 3.11+ and Git, creates an isolated environment, opens the
app, and hands setup over to the interface. You choose the language, wake word, and provider
there. Bring one supported API key or subscription login. Nothing is bundled.

### Windows

Run in PowerShell:

~~~powershell
irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
~~~

### macOS and Linux

~~~bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
~~~

Re-run the same command when you want to update. Keys are entered in the app and stored in
the operating system credential manager.

<details>
<summary><b>Package manager and manual install fallback</b></summary>

<br/>

Install the cloud-first server with pipx:

~~~bash
pipx install personal-jarvis
jarvis serve
~~~

Install the complete desktop profile with pip:

~~~bash
pip install "personal-jarvis[full]"
jarvis
~~~

Or clone the repository:

~~~bash
git clone https://github.com/PersonalJarvis/PersonalJarvis
cd PersonalJarvis
python -m venv .venv
# macOS and Linux: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[full]"
jarvis
~~~

The <code>--headless</code> installer flag keeps the base install small and leaves out local
audio, desktop UI, Node.js workers, and GPU packages.

</details>

## What you can ask

| You say | Personal Jarvis does |
|---|---|
| "Open display settings and switch to light mode." | Takes the mouse and keyboard, opens Settings, and makes the change on screen. |
| "Research vector databases and give me a report." | Starts an isolated worker, reviews the result, and puts the finished file in Outputs. |
| "Remember that Alex prefers Signal over email." | Saves the fact in the local Knowledge Wiki for later sessions. |
| "Switch the voice to Cartesia." | Changes the speech provider live and reads the change back. |
| "Show me which brain provider is active." | Reads the running configuration through the same guarded API used by the app and CLI. |

Computer use needs a desktop with a screen. Cloud providers need their own credentials.
Optional phone calls need the telephony extra, a Twilio account, a number, and public HTTPS
webhooks.

## Why it is different

| | |
|---|---|
| **Voice becomes action** | The fast Router chooses a safe tool or a larger mission. You hear a grounded acknowledgement as soon as that choice is made. |
| **Larger jobs are reviewed** | Mission workers run in isolated Git worktrees. A critic checks the result before it reaches you, with up to three correction rounds. |
| **The provider is your choice** | Gemini, Claude, OpenAI, and OpenRouter can power the brain. Subscription logins and pay-per-token keys are both supported. |
| **Memory stays readable** | Long-term memory is an Obsidian-compatible Markdown vault on your disk. You can inspect, edit, or sync it yourself. |
| **Safety is part of execution** | Every action passes through safe, monitor, ask, or block policy before a tool can run. |

## The desktop app

<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/screenshots/app-desktop.png" alt="The Personal Jarvis desktop app" width="860" />
</p>

The desktop combines voice and chat with missions, Outputs, the Knowledge Wiki, settings,
provider switching, run inspection, and the command surface. The same backend also serves a
browser UI for headless machines.

## How it works

<img
  src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/how-personal-jarvis-works.png"
  width="1064"
  height="568"
  alt="Personal Jarvis routes voice and chat to guarded tools or reviewed mission workers"
/>

Voice and chat enter one router. Small actions go through the risk policy and tool executor.
Larger jobs become missions with their own worktree, worker, critic loop, and downloadable
output. Responses return through the language resolver and voice scrubber before speech.

<details>
<summary><b>The 8-layer architecture</b></summary>

~~~text
L7  UI/UX           Desktop app, browser UI, tray, Orb overlay
L6  Orchestrator    State machine, Router, BrainManager, missions, workers
L5  Harness         Python scripts and computer use
L4  Brain           Provider plugins and the acknowledgement brain
L3  Intent/Risk     Classification, policy, approval, rate limits
L2  Speech          Wake, VAD, STT, TTS, realtime voice
L1  Audio I/O       Device routing and feedback
L0  OS/Hardware     Microphone, speakers, hotkeys, optional GPU
~~~

Higher layers reach lower ones through protocols. Lateral communication uses typed,
immutable events. Providers and harnesses remain replaceable because those boundaries stay
stable.

</details>

## Core capabilities

| Area | What is included |
|---|---|
| Voice | Custom wake word, voice activity detection, cloud or local speech recognition, cloud or local speech output, and optional speech-to-speech mode. |
| Computer use | Opens apps, clicks, types, and navigates with a visible action border on supported desktops. |
| Missions | Isolated workers, critic review, progress updates, crash containment, and downloadable outputs. |
| Memory | Knowledge Wiki, session awareness, profile facts, and plain Markdown storage. |
| Channels | Desktop, browser, Telegram, and Discord share the same brain and memory. |
| Automation | Scheduled and event-driven tasks can speak, run a tool, dispatch a harness, or run an agent turn. |
| Telephony | Optional outbound calls through Twilio. |
| CLI and API | Every mounted REST action is available through the generated CLI, with curated commands for common workflows. |

## Privacy and safety

- Wake-word listening stays on your machine. Audio reaches a cloud speech provider only
  after you address Personal Jarvis, and only when you selected a cloud provider.
- API keys live in the operating system credential manager, with portable fallbacks for
  machines that do not have a keyring.
- Memory is stored as files on your disk rather than in a hosted Personal Jarvis database.
- Destructive actions ask first. A blacklist outranks every other rule, and approved
  routines can be whitelisted to avoid repeated prompts.
- Self-modification uses validation, backup, atomic replacement, verification, rollback,
  and an audit record. It cannot change secrets or bypass the safety policy.

## Run it your way

~~~bash
jarvis          # desktop app, voice, chat, and Orb
jarvis serve    # API, WebSocket, and browser UI
~~~

On a server, open <code>http://localhost:47821</code>. Text works over ordinary HTTP.
Browser microphone access needs localhost or HTTPS. Local-only features report an honest
no-op when the machine has no screen, audio device, or compatible backend.

## Drive it from the terminal

The CLI controls a running instance through the same local API and safety checks as the app:

~~~bash
jarvis system status
jarvis --json brain status
jarvis api <tag> <operation>
~~~

The command is also available as <code>jarvisctl</code> and <code>jctl</code>. See the
[CLI guide](docs/jarvis-cli.md) and the generated
[command reference](docs/jarvis-cli-reference.md).

## Configuration

The setup wizard covers normal use. Advanced configuration lives in one optional
[jarvis.toml](jarvis.toml.example) file. Environment variables can override any setting.
Secrets do not belong in that file.

~~~toml
[profile]
language = "auto"

[trigger.wake_word]
phrase = ""
engine = "auto"

[stt]
provider = "groq-api"

[tts]
provider = "gemini-flash-tts"
fallback = "grok-voice"
~~~

## Extend it

Brain, speech, wake-word, realtime voice, harness, tool, and channel integrations are Python
entry points. A plugin implements the matching protocol, registers one entry point, streams
its output, and passes the contract suite. Core edits are not required.

Start with [the architecture overview](docs/architecture-overview.md) and
[the contributor guide](CONTRIBUTING.md).

## Documentation

| Document | Use it for |
|---|---|
| [Architecture overview](docs/architecture-overview.md) | Components, layers, data flow, and module map |
| [CLI guide](docs/jarvis-cli.md) | Operating a running instance from a terminal |
| [Configuration example](jarvis.toml.example) | Providers, voice, channels, and advanced settings |
| [Security policy](SECURITY.md) | Trust boundaries and private vulnerability reports |
| [Philosophy](docs/PHILOSOPHY.md) | Cross-platform and provider-independent design rules |
| [ADRs](docs/adr/) | Architecture decisions and their tradeoffs |
| [Bug register](docs/BUGS.md) | Recurring failure modes and their defenses |

## Update and uninstall

Re-run the installer to update in place. The installed uninstaller removes the application,
autostart entry, and stored credentials. Add <code>--dry-run</code> to preview its work.

<details>
<summary><b>Uninstall commands</b></summary>

### Windows

~~~powershell
& "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1"
~~~

### macOS and Linux

~~~bash
bash ~/.personal-jarvis/install/uninstall.sh
~~~

If an older installed uninstaller cannot start, use the app module directly:

~~~bash
~/.personal-jarvis/.venv/bin/python -m jarvis --uninstall
~~~

</details>

## Community

Questions, feature ideas, and early previews land in
[Discord](https://discord.gg/x7USduHxbc). You can also follow the project on
[X](https://x.com/PersonalJarvis) and
[Instagram](https://www.instagram.com/personaljarvis/).

Pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before larger changes,
and report security issues privately through [SECURITY.md](SECURITY.md).

## License

Personal Jarvis is available under the [MIT License](LICENSE). Third-party names and logos
belong to their owners; see [TRADEMARK.md](TRADEMARK.md).
