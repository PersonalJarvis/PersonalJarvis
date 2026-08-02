<p align="center">
  <a href="https://github.com/PersonalJarvis/PersonalJarvis">
    <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/banner.png" alt="Personal Jarvis, a voice-driven meta-orchestrator" width="860" />
  </a>
</p>

<p align="center">
  <a href="https://pypi.org/project/personal-jarvis/"><img alt="PyPI: personal-jarvis" src="https://img.shields.io/pypi/v/personal-jarvis?style=for-the-badge&labelColor=242424&color=e7c46e" /></a>
  <a href="https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-e7c46e?style=for-the-badge&labelColor=242424" /></a>
  <a href="https://discord.gg/x7USduHxbc"><img alt="Discord" src="https://img.shields.io/badge/Discord-Join-5865F2?style=for-the-badge&logo=discord&logoColor=white&labelColor=242424" /></a>
  <a href="https://x.com/Ruben_Luetke"><img alt="Follow @Ruben_Luetke on X" src="https://img.shields.io/badge/Follow-%40Ruben__Luetke-e7c46e?style=for-the-badge&logo=x&logoColor=white&labelColor=242424" /></a>
  <a href="https://personaljarvis.ai/"><img alt="Personal Jarvis website" src="https://img.shields.io/badge/Website-personaljarvis.ai-e7c46e?style=for-the-badge&labelColor=242424" /></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-e7c46e?style=for-the-badge&logo=python&logoColor=e7c46e&labelColor=242424" />
  <img alt="Platforms: Linux, macOS, Windows" src="https://img.shields.io/badge/Linux%20%C2%B7%20macOS%20%C2%B7%20Windows-242424?style=for-the-badge&labelColor=242424&color=242424" />
</p>

<p align="center">
  <b>An open-source voice agent that operates your computer instead of just answering questions about it.</b>
</p>

---

A classical voice assistant answers you. Personal Jarvis carries out the request. A small,
fast router brain listens and decides what the request actually needs. Short things it
handles itself. Anything heavy goes to a coding-agent worker (Claude Code, Codex CLI,
Gemini CLI, or an in-process worker running on whatever API key you already have), which
works in isolation, gets checked by a critic, and reports back in the language you spoke.

You choose the provider. Gemini, Claude, OpenAI and OpenRouter are one setting. It can
rewrite its own configuration, and it runs on a headless server as readily as on a desktop
with a microphone.

## What you can say

| You say | What happens |
|---|---|
| *"Research vector databases."* | An isolated agent does the research. The finished report lands in **Outputs** as a file you can download. |
| *"Call the clinic and book the next open appointment."* | A real outbound phone call goes out over the optional Twilio line. |
| *"Remember: Alex prefers Signal over email."* | Written to the Knowledge Wiki, and still known in every later session. |
| *"Switch the voice over to Cartesia."* | The speech provider changes while you talk, and Jarvis reads the change back to you, old then new. |
| *"Tell Nova to run the tests."* | The instruction lands in that agent's terminal in the Agentic IDE workspace. |
| *"Open the browser and pull up the weather."* | Jarvis takes the mouse and keyboard and does it on your screen. |

All six run on shipped code. None of them is a roadmap item. Two carry a setup cost that
is not out of the box: the phone call needs the optional `[telephony]` extra plus your own
Twilio account, a number, and a publicly reachable HTTPS URL for the webhooks. Computer use
needs a desktop install with a screen, not the headless one.

When-then triggers are a real feature too, but you cannot arm them by voice. You create
them in the Tasks view or with `jarvis tasks create`, not by saying "when X, do Y". They
fire on a clock, on an interval, or on one of Jarvis's own internal events, such as a
mission finishing or a message being sent. They do not fire on arbitrary things happening
elsewhere on your PC. What they can do is speak, run one tool, dispatch a harness, or run
an agent turn.

## Demo

<p align="center">
  <a href="https://www.youtube.com/watch?v=6xoxgNu5fd8">
    <img src="https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/assets/demo/personal-jarvis-demo.gif" alt="Animated demo showing the spoken prompt, Jarvis opening Windows Settings, and switching display mode from dark to light" width="860" />
  </a>
</p>

<p align="center">
  <sub>One voice command, and the router takes the screen and does it live &middot; <a href="https://www.youtube.com/watch?v=6xoxgNu5fd8">watch the full demo on YouTube</a></sub>
</p>

## What it does differently

The router is deliberately small. It works out what you said, picks a tool or a worker,
and gets out of the way. There is no single giant prompt trying to be everything. Anything
non-trivial runs as a mission in an isolated worktree and gets reviewed by a critic before
you ever hear the result.

While that runs you are not left listening to silence. The moment the router picks an
action, Jarvis says one line about that specific action, not a generic "working on it".

Providers are interchangeable, which matters most on the day one of them fails. If the
configured provider is unreachable or out of quota, Jarvis crosses to a different provider
family instead of leaving you stuck. Workers run on a subscription login or on a
pay-per-token key, whichever you have. Speech and voice providers can be switched by voice;
the brain provider cannot, on purpose, because that one stays yours to change in the app or
the CLI.

It remembers. A Knowledge Wiki of plain Markdown files, plus an awareness layer, build up a
picture of you across sessions. And it can change its own settings through a pipeline that
validates, backs up, applies, verifies, and rolls back when something fails.

## How it works

<img
  src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/how-personal-jarvis-works.png"
  width="1064"
  height="568"
  alt="How Personal Jarvis works: routing voice and chat through safe actions or reviewed missions"
/>

Higher layers reach lower ones only through protocols, and everything else talks over a
typed, immutable EventBus. That strict seam is what makes harnesses, providers and plugins
swappable in the first place.

<details>
<summary><b>The 8-layer map</b></summary>

```
L7  UI/UX           Desktop app (FastAPI + React + pywebview), tray, Orb overlay
L6  Orchestrator    State machine, Router, BrainManager, Mission-Manager + workers, Controller
L5  Harness adapter python-script, computer-use  (coding agents are L6 mission workers)
L4  Brain           Gemini · Claude · OpenAI · Grok · OpenRouter  +  sub-second Ack-Brain
L3  Intent / Risk   Classifier, four-tier risk policy, approval, rate-limit tracking
L2  Speech          Wake → VAD → STT → TTS  (cloud or local, your choice)
L1  Audio I/O       Device routing, chime feedback
L0  OS / Hardware   Mic, speakers, global hotkeys, optional GPU
```

A deeper engineering map, with anti-patterns, bug classes, and phase status down to
`file:line`, lives in [`docs/LLM-CONTEXT.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/LLM-CONTEXT.md).

</details>

## Install

One command on Windows, macOS, or Linux. You need Python 3.11 or newer and Git; the
installer checks for both and stops with a download link if one is missing. It asks nothing
in the terminal. It launches the app, and the app walks you through a one-time setup for
language, wake word, and API keys. Bring your own keys, nothing is bundled.

**Windows** (PowerShell)

```powershell
irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
```

**macOS and Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
```

> This is open source, so read the installer before you run it. It creates a venv, installs
> dependencies, prefetches the voice models, and launches the app. Your keys land in your
> operating system's credential manager, never in the repo. Re-running the same one-liner
> updates in place.

**Uninstall** is one command as well. It removes the install folder, the autostart entry,
and the keychain entries. Add `--dry-run` to preview, `--yes` to skip the confirmation:

```powershell
# Windows (PowerShell)
& "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1"
```

```bash
# macOS · Linux
bash ~/.personal-jarvis/install/uninstall.sh
```

Both of those run the uninstaller that is already on your disk. If it is missing or refuses
to start (installs from 1.1.0 and 1.1.1 shipped one that could not run on macOS at all),
skip it and use the app's own uninstall directly. Same job, no bootstrap involved. Add
`--dry-run` first to see what it would remove:

```bash
# macOS · Linux
~/.personal-jarvis/.venv/bin/python -m jarvis --uninstall
```

```powershell
# Windows (PowerShell)
& "$env:USERPROFILE\.personal-jarvis\.venv\Scripts\python.exe" -m jarvis --uninstall
```

<details>
<summary><b>Optional extras, install flags, pipx & manual clone</b></summary>

<br/>

Everything below is optional. Each item unlocks one specific thing:

| Optional | Unlocks |
|---|---|
| A provider API key or subscription login (Gemini, Claude, OpenAI, or OpenRouter) | Actually talking to a brain. The in-app setup stores it in your credential manager. |
| Node.js 18+ | The coding-agent worker CLIs, such as Claude Code and Codex, that heavy missions delegate to. Add it any time. |
| libportaudio *(Linux only)* | Local microphone and speakers (`apt install libportaudio2`). |
| A GPU | Faster fully-offline speech. Everything also runs on CPU. |

| Install flag | Effect |
|---|---|
| `--headless` | Minimal server install: API and WebSocket only, torch-free base, no Node.js. The tiny-VPS path. |
| `--no-launch` | Install only, do not start the app |

**pipx**, isolated, no clone, any OS, straight from PyPI:

```bash
pipx install personal-jarvis && jarvis serve
```

**pip**, into an environment you already have:

```bash
pip install personal-jarvis          # cloud-first base: API + WebSocket + browser UI
pip install "personal-jarvis[full]"  # everything: desktop app, telephony, channels, local voice
```

**Manual**: clone it, read every line, then run:

```bash
git clone https://github.com/PersonalJarvis/PersonalJarvis
cd PersonalJarvis
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e .[full]
jarvis serve
```

</details>

## Run it

```bash
jarvis          # full desktop: window + voice + Orb overlay
jarvis serve    # headless server: API + WebSocket + browser UI, no local audio needed
```

<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/screenshots/app-desktop.png" alt="The Personal Jarvis desktop app" width="860" />
</p>

<details>
<summary><b>Headless / server notes</b></summary>

<br/>

On a server, open **http://localhost:47821**. The full experience lives in the browser,
including voice through the browser microphone. The one-time setup runs there too, and you
can also set a provider key such as `GEMINI_API_KEY` in the environment or a `.env` file.

Browser microphone access needs a secure context. `localhost` works as it is; for a remote
VPS, terminate TLS with an HTTPS reverse proxy such as Caddy or Nginx. Plain
`http://server-ip` stays usable for text, but browsers will block voice.

</details>

## What's inside

### Missions

Anything non-trivial, say "research X and write me a report", spawns a worker in an
isolated `git worktree`. That is a private sandbox copy of the workspace, with crash
containment. A critic reviews the result, for up to three rounds, before you ever hear it,
and deliverables land in **Outputs** as downloadable files.

### Agentic IDE

Pick a folder, choose how many terminals to open and which coding agent runs in each one,
Claude Code or Codex, and you get a grid of real terminals inside the app. Every terminal
carries a spoken call sign (Mika, Nova, Aria), so the whole workspace is addressable by
voice: *"what is Mika doing?"*, *"tell Nova to run the tests"*. A focus mode narrows Jarvis
to that workspace for as long as you want, then switches back cleanly.

<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/demo/agentic-ide-demo.gif" alt="A prompt arriving in one agent's terminal in the Agentic IDE, with the thinking counter running underneath it" width="860" />
</p>

<p align="center">
  <sub>A prompt lands in one agent's terminal, and the counter underneath it shows how long that agent has been thinking</sub>
</p>

### Knowledge Wiki

An Obsidian-compatible Markdown vault that Jarvis reads and writes. Tell it something once
and every future session knows it. Because it is plain files on your disk, you can read,
edit, and sync it yourself.

### Computer use

Jarvis takes the mouse and keyboard when you ask: opening apps, clicking, typing,
navigating. An on-screen action border shows you when it is driving. That border is drawn
by a small Qt sidecar from the `[desktop]` extra; where the sidecar is absent, on a base or
headless install and on aarch64 Linux, it degrades to a logged no-op and the control itself
still works.

### Channels and telephony

The desktop window, the browser, Telegram, and Discord all reach the same brain and share
the same memory. Real outbound phone calls are possible but not out of the box: they need
the optional `[telephony]` extra, your own Twilio account and number, and a publicly
reachable HTTPS URL that Twilio can call back for the voice webhook and the media socket.

### Safety tiers

Every action is classified as safe, monitor, ask, or block before it runs. Destructive
things ask first, whitelisted routines stop nagging you, and the blacklist always outranks
the whitelist.

### Self-modification

Jarvis can change its own settings by voice, through a guarded pipeline that validates,
backs up, applies, verifies, and rolls back on failure, with a full audit trail. Some
things are deliberately out of its reach: secrets and keys, the safety tiers, the review
gates, and the active brain provider, which only you can change from the app or the CLI.
Generated skills always land as drafts for your review. Nothing self-activates.

### Dictation

Hold a key and talk, and what you said goes into whatever text field currently has focus,
in any application. Jarvis writes through the clipboard, sends the paste chord, and puts
your old clipboard back. There is a key you hold while speaking and a separate key that
toggles, and you can have both armed at once.

Filler sounds are stripped by plain pattern matching, per language, with no model call
involved. An optional second pass handles what pattern matching structurally cannot:
punctuation, capitalization, false starts, spoken numbers. It sits behind a hard latency
ceiling, and every one of its failure paths hands back your raw transcript unchanged. A
separate translate pass writes what you said in one fixed target language instead. Words
the recognizer keeps getting wrong go into your own dictionary, and everything dictated is
kept locally in both raw and cleaned form, so a cleanup can always be checked after the
fact.

### Realtime voice

An optional speech-to-speech mode (OpenAI Realtime, Gemini Live) for sub-second
conversational latency, with automatic fallback to the classic wake, STT, brain, TTS
pipeline when it is unavailable.

## Drive it from the terminal

The `jarvis` CLI (aliases `jarvisctl`, `jctl`) controls a running instance. Same actions as
the app, same safety checks, just scriptable. Anything you can click, you or your scripts
or another coding agent can type:

```bash
jarvis system status          # {"reachable": true} when Jarvis is up
jarvis --json brain status    # which provider is live, as machine-readable JSON
jarvis api <tag> <op>         # EVERY REST endpoint, auto-generated from OpenAPI
```

It is a thin client over the local REST API on `127.0.0.1:47821`, so it inherits every
guardrail (risk tiers, atomic config writes, the audit log) instead of going around them.
Full guide: [`docs/jarvis-cli.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/jarvis-cli.md).

## Configuration

You do not need a config file. Every setting has a built-in default, and the one-time
in-app setup covers the rest. For finer control there is one optional, documented file
([`jarvis.toml.example`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/jarvis.toml.example)):

```toml
[profile]
language = "auto"          # "de" | "en" | "auto" (bilingual auto-detect)

[trigger.wake_word]
phrase = ""                # YOUR word; nothing is preset for you
engine = "auto"            # resolves the best engine for your phrase

[stt]
provider = "groq-api"      # or openai-api, openrouter-stt, gemini-api, faster-whisper (local)

[tts]
provider = "gemini-flash-tts"
fallback = "grok-voice"    # cross-provider fallback is the norm everywhere
```

Overrides cascade from `jarvis.toml` to ENV (`JARVIS__SECTION__KEY=…`). Secrets never go in
this file. API keys live in your operating system's credential manager, or in `.env`, and
you enter them in the app.

## Privacy

Your keys stay yours. They are stored in the operating system's credential manager, never
in the repo, and never in a file you could commit by accident.

The always-on part is local. Wake-word listening runs entirely on your machine, and audio
only goes to a cloud speech provider after you have addressed Jarvis, and only if you chose
a cloud provider in the first place. Speech recognition can run fully offline with the
`[local-voice]` extra. Brain and voice output use whichever provider you configure.

Memory is plain files. The Knowledge Wiki is Markdown on your disk, not a hosted database.

## Extend it

Every pluggable part is a Python entry point. Write a class against the protocols in
[`jarvis/core/protocols.py`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/jarvis/core/protocols.py), register one line in
`pyproject.toml`, reinstall. No fork, no core edits.

| Plugin group | What you can add |
|---|---|
| `jarvis.brain` | A new LLM provider |
| `jarvis.stt` / `jarvis.tts` | Speech recognition / synthesis backends |
| `jarvis.wakeword` | Wake-word engines |
| `jarvis.realtime` | Speech-to-speech providers |
| `jarvis.harness` | Harness adapters the router and when-then tasks dispatch to |
| `jarvis.tool` | Actions the router can call directly |
| `jarvis.channel` | New surfaces, such as chat platforms and transports |

Three rules keep it stable: implement the protocol, stream everything (`AsyncIterator`,
where non-streaming yields one element), and pass the contract suite
(`pytest tests/contract/`). The deep engineering map, with anti-patterns, recurring bug
classes, and phase status, lives in
[`docs/LLM-CONTEXT.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/LLM-CONTEXT.md), and is built to be pasted into
an LLM chat whole.

<details>
<summary><b>Project structure</b></summary>

```text
PersonalJarvis/
├── jarvis/          # The application: every core package (brain, speech, missions, memory, UI server…)
├── ui/              # Orb overlay for the desktop; loaded by jarvis at runtime
├── board-backend/   # Standalone federation service (verifies signed Board aggregates)
├── conductor/       # YAML-first agentic-workflow canvas, mounted inside the app
├── wiki/            # Seed knowledge vault (Obsidian-compatible), created on first run
├── install/         # One-line installers + signed-release verification (cosign / TUF)
├── tests/           # Unit, integration, contract, and end-to-end suites
├── docs/            # Architecture docs, ADRs, the philosophy, design specs
├── assets/          # Brand art, banner, screenshots
├── .github/         # CI workflows + issue / pull-request templates
├── scoop-bucket/    # Windows install manifest (Scoop)
├── homebrew-tap/    # macOS install formula (Homebrew)
└── README · LICENSE · CODE_OF_CONDUCT · CONTRIBUTING · SECURITY · CHANGELOG
```

Inside `jarvis/`, the layout mirrors the 8-layer model: `jarvis/brain/` (providers and
router), `jarvis/speech/` (wake, VAD, STT, TTS), `jarvis/missions/` (the worker and critic
loop), `jarvis/memory/wiki/` (long-term memory), `jarvis/ui/web/` (the desktop app).

</details>

## Documentation

| Document | What's in it |
|---|---|
| [`docs/architecture-overview.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/architecture-overview.md) | The full architecture: layers, module catalog, data flow |
| [`docs/LLM-CONTEXT.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/LLM-CONTEXT.md) | Dense project snapshot, built to paste into an LLM chat whole |
| [`CLAUDE.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/CLAUDE.md) | Binding contributor guide: conventions, doctrine, anti-patterns |
| [`docs/PHILOSOPHY.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/PHILOSOPHY.md) | Cross-platform, provider-agnostic design doctrine |
| [`docs/adr/`](https://github.com/PersonalJarvis/PersonalJarvis/tree/main/docs/adr/) | Architecture Decision Records |
| [`docs/BUGS.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/BUGS.md) | The recurring-bug register |
| [`docs/BRAND.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/BRAND.md) | Brand guidelines: colors, typography, the wordmark |

## Community

Development happens in the open. The roadmap and the bug hunts land on Discord before they
land anywhere else, and questions are welcome there.

<p align="center">
  <a href="https://discord.gg/x7USduHxbc"><img alt="Discord" src="https://img.shields.io/badge/Discord-join_the_server-FFD60A?style=for-the-badge&logo=discord&logoColor=0A0A0A&labelColor=0A0A0A" /></a>
  <a href="https://x.com/Ruben_Luetke"><img alt="X" src="https://img.shields.io/badge/X-follow-FFD60A?style=for-the-badge&logo=x&logoColor=0A0A0A&labelColor=0A0A0A" /></a>
</p>

<p align="center">
  <a href="https://discord.gg/x7USduHxbc">Discord</a> ·
  <a href="https://x.com/Ruben_Luetke">@Ruben_Luetke</a> ·
  <a href="https://www.instagram.com/personaljarvis/">Instagram</a> ·
  <a href="https://github.com/PersonalJarvis/PersonalJarvis">GitHub</a>
</p>

## Contributing

Pull requests are welcome, and [`CONTRIBUTING.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/CONTRIBUTING.md) has the full
guide. The short version: artifacts are English, read [`CLAUDE.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/CLAUDE.md)
before larger changes, new providers must pass `pytest tests/contract/`, and security issues
go to [`SECURITY.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/SECURITY.md) privately.

## License

MIT. Free to use, modify, and distribute, including commercially; see
[`LICENSE`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/LICENSE). Third-party names and logos belong to their owners,
see [`TRADEMARK.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/TRADEMARK.md).

<br/>

<p align="center">
  <sub>Created by <b>Ruben Lütke</b> · <a href="https://x.com/Ruben_Luetke">@Ruben_Luetke</a> · © 2026 · MIT</sub><br/> <!-- i18n-allow: maintainer's name, not German prose -->
  <sub><a href="https://discord.gg/x7USduHxbc">Discord</a> · <a href="https://x.com/Ruben_Luetke">X</a> · <a href="https://www.instagram.com/personaljarvis/">Instagram</a></sub>
</p>
