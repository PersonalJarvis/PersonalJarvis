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

<h2 align="center">Your personal AI ecosystem, controlled entirely by voice.</h2>

<p align="center">
  It drives coding agents, runs shell commands, operates your computer, connects anything that speaks MCP, dictates into any app, and remembers everything.<br>
  Open source, and it can run fully on your own hardware.
</p>

---

A typical voice assistant talks back. Personal Jarvis does the thing. At the center of
every voice conversation sits a tool model: it decides how much a request actually needs,
runs shell commands, takes the mouse and keyboard, and reaches any service that speaks
MCP. The short stuff it handles itself. Anything heavier goes to a coding-agent worker
(Claude Code, Codex CLI, Gemini CLI, or an in-process worker on whatever API key you
already have), which runs in isolation, gets checked by a critic, and reports back in the
language you spoke.

Every tier has a keyless local option, so the whole assistant can run on your own hardware
with no cloud account anywhere in the chain — details under **Runs on your own hardware**
below. If you would rather use a hosted model, you pick the provider per tier: Gemini,
Claude, OpenAI, or OpenRouter, one setting for each. It can rewrite its own configuration,
and it runs on a headless server just as well as on a desktop with a microphone.

<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/screenshots/app-chat.webp" alt="The desktop app's chat view: voice history in the sidebar, the ghost mascot over a golden wave wallpaper, and the Ready for commands prompt" width="900" />
</p>

<p align="center">
  <sub>The desktop app, ready for commands. The assistant takes whatever name you pick as your wake word &mdash; this install answers to George.</sub>
</p>

## What you can say

| You say | What happens |
|---|---|
| *"Research vector databases."* | An isolated agent does the research. The finished report lands in **Outputs** as a file you can download. |
| *"Call the clinic and book the next open appointment."* | A real outbound phone call goes out over the optional Twilio line. |
| *"Remember: Alex prefers Signal over email."* | Written to the Knowledge Wiki, and still known in every later session. |
| *"Switch the voice over to Cartesia."* | The speech provider changes while you talk, and Jarvis reads the change back to you, old then new. |
| *"Tell T1 to run the tests."* | The instruction lands in terminal 1 of the Agentic IDE workspace. |
| *"Open the browser and pull up the weather."* | Jarvis takes the mouse and keyboard and does it on your screen. |

All six work today; none of this is a roadmap item. Two need extra setup: the phone call
needs the optional `[telephony]` extra plus your own Twilio account, a number, and a
publicly reachable HTTPS URL for the webhooks, and computer use needs a desktop install
with a screen, not the headless one.

## Which model answers you

Three models sit behind that table, and you never choose between them: the handover
happens mid-sentence, based on what the request needs.

The **realtime model** carries the conversation itself. It hears you and answers in under
a second, built for talking, not for thinking hard. The moment a request needs an actual
tool, that turn hands off to a **second model**, slower and noticeably smarter, the one
that reads your wiki, changes a setting, places the call, or takes the screen. It answers
in the same voice, so from where you're sitting it never stopped being one conversation.
Real work, the kind that takes minutes, goes to a **third**: a coding agent running in its
own isolated copy of the workspace, reviewed by a critic, that comes back with a file
instead of just an answer.

In the app this lives on one screen: API Keys has one tab per tier, each with its own
provider, and you only need keys for the tiers you use. Every provider says how it bills:
a subscription login you already have, or an API key charged per token.

## Runs on your own hardware

Hosted providers are a choice here, not a requirement. Every layer that could reach for a
cloud account has a local option that needs no API key and no signup, so a complete install
can keep your voice, your screen and your files on the machine they started on.

| Layer | Keyless local option | What it costs you |
|---|---|---|
| **Conversation** (realtime) | A self-hosted server speaking the OpenAI Realtime protocol. The install panel checks the machine and sets up a managed one in a click, or you point it at any address of your own. | About 12 GB of GPU or unified memory for a good experience. Still marked experimental. |
| **Brain** (decisions, tools) | Ollama, found automatically at `http://localhost:11434`, or any OpenAI-compatible server. | Pull a tools-capable model, e.g. `ollama pull qwen3.5`. |
| **Speech to text** | Whisper large-v3 on device, or NVIDIA's Nemotron 3.5 streaming model. | Whisper is a one-time 3 GB download and wants a graphics card; Nemotron is ~690 MB, covers 40 languages, and runs several times faster than real time on a plain CPU. |
| **Text to speech** | Piper on this machine, plus Kokoro and Qwen3-TTS voice profiles. | A voice download in the hundreds of megabytes. |

Mixing is normal and expected: a local recognizer with a hosted brain, or a local brain
with a hosted voice. Nothing forces the whole chain one way.

Two capabilities stay outside this promise, and it would be dishonest to imply otherwise.
The outbound phone call goes over Twilio, which is a hosted service by definition, and the
coding-agent workers run on whichever agent CLI or API key you point them at. Everything in
the table above is genuinely local.

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

The router itself stays small. It works out what you said, picks a tool or a worker, and
gets out of the way; there is no single giant prompt trying to be everything. Anything
non-trivial runs as a mission in an isolated worktree and gets a critic's review before you
ever hear the result. You are not left listening to silence while that happens, either: the
moment the router picks an action, Jarvis says one line about that specific action, not a
generic "working on it".

Providers are interchangeable, and that matters most on the day one of them fails. If the
configured provider is unreachable or out of quota, Jarvis crosses to a different provider
family instead of leaving you stuck. Workers run on a subscription login or a pay-per-token
key, whichever you have. Speech and voice providers can be switched by voice mid-
conversation; the brain provider cannot, on purpose, that one stays yours to change from
the app or the CLI.

It also remembers: a Knowledge Wiki of plain Markdown files, plus an awareness layer, build
up a picture of you across sessions. And it can change its own settings through a guarded,
audited pipeline, the full mechanics are under Self-modification below.

## How it works

<img
  src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/brand/how-personal-jarvis-works.png"
  width="1064"
  height="568"
  alt="How Personal Jarvis works: routing voice and chat through safe actions or reviewed missions"
/>

Higher layers can only reach lower ones through protocols; everything else talks over a
typed, immutable EventBus. That's the strict seam that makes harnesses, providers, and
plugins swappable in the first place.

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
language, wake word, and API keys.

**What it costs: nothing to us.** Personal Jarvis is MIT-licensed software you run on your
own machine. There is no subscription for it, no paid tier, no marketplace cut, and no
referral link behind any provider named on this page. What you do need is access to a
model, and that is billed by whoever provides it, straight to you. An AI subscription you
already pay for works, and so does a pay-per-token API key. The same goes for the optional
pieces: a phone call runs on your own Twilio account at Twilio's prices.

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
to start, the app can uninstall itself instead: see
[`install/README.md`](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/install/README.md#uninstalling).

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
isolated `git worktree`: a private sandbox copy of the workspace, with crash containment. A
critic reviews the result, for up to three rounds, before you ever hear it, and
deliverables land in **Outputs** as downloadable files.

### Agentic IDE

Pick a folder, choose how many terminals to open and which coding agent runs in each one,
Claude Code or Codex, and you get a grid of real terminals inside the app. Every terminal
carries a spoken call sign (Mika, Nova, Aria), so the whole workspace is addressable by
voice: *"what is Mika doing?"*, *"tell Nova to run the tests"*. A focus mode narrows Jarvis
to that workspace for as long as you want, then switches back cleanly.

<p align="center">
  <a href="https://youtu.be/wFBdmdOn6EU">
    <img src="https://github.com/PersonalJarvis/PersonalJarvis/raw/main/assets/demo/agentic-ide-demo.gif" alt="Two coding agents side by side in the Agentic IDE, one receiving a prompt with its full mission brief while its thinking counter runs" width="860" />
  </a>
</p>

<p align="center">
  <sub>A prompt lands in terminal 1, carrying the task, the key files and how that part of the code works today. The counter underneath shows how long that agent has been thinking &middot; <a href="https://youtu.be/wFBdmdOn6EU">watch the full Agentic IDE demo on YouTube</a></sub>
</p>

### Knowledge Wiki

An Obsidian-compatible Markdown vault that Jarvis reads and writes. Tell it something once
and every future session knows it. Because it is plain files on your disk, you can read,
edit, and sync it yourself.

### Computer use

Ask for something that has no API, and Jarvis takes the mouse and keyboard: opening apps,
clicking, typing, navigating. There is no scripted path per application. It works the way
you would.

The loop is perceive, act, verify. Jarvis takes a screenshot, a vision model says what to
click next, the click is made through the platform's own input layer, and then it looks
again to check that what it intended actually happened. Two details keep that honest.
Coordinates are resolved against the exact frame the model saw, not against a stale
picture of the screen, so a window that moved between two steps cannot send a click into
nothing. And every action goes through a ledger that refuses duplicates, so a model that
repeats itself does not click Send twice.

While it drives, a border sits around the screen so you can see it is not you. That border
comes from a small Qt companion in the `[desktop]` extra. Where the companion is absent, on
a base or headless install and on aarch64 Linux, it degrades to a logged no-op and the
control itself still works.

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

Hold a key, talk, and the text lands in whatever field has focus, in any application: a
browser, an editor, a chat box, some internal tool's form. Jarvis writes it through the
clipboard, sends the paste chord, and restores your old clipboard afterward; if a paste
doesn't land, it tells you instead of silently losing what you said. One key is a hold, a
second is a toggle, both can be armed together, and a third re-pastes the last thing you
dictated into whatever field just ate it.

<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/releases/download/v1.3.1/readme-demo-dictation.gif" alt="Dictation demo: a spoken sentence arrives as clean text in the chat input" width="860" />
</p>

<p align="center">
  <sub>Speak, release the key, and the cleaned-up sentence lands in whatever field has focus.</sub>
</p>

Recognition runs on whatever speech provider you have configured, local model included, in
which case your voice never leaves the machine. Cleanup happens in two passes: a plain
pattern match strips filler sounds per language with no model involved at all, and an
optional second pass, capped by a hard latency budget, handles punctuation, capitalization,
false starts, and spoken numbers, falling back to your raw transcript unchanged if it
fails. A separate pass translates instead of cleaning, writing what you said in one fixed
target language. Words the recognizer keeps getting wrong go into a dictionary you control.
Everything, raw transcript, cleaned version, and the original audio, stays on disk: you can
see exactly what a cleanup pass changed, revert it, or retry a transcription that came back
empty because a provider was briefly down.

### Realtime voice

An optional speech-to-speech mode (OpenAI Realtime, Gemini Live) for sub-second
conversational latency, with automatic fallback to the classic wake, STT, brain, TTS
pipeline when it is unavailable.

### Wallpaper gallery

501 wallpapers across 21 art styles, from oil painting and pixel art to synthwave and
woodblock, each tagged light or dark so the app can match the wallpaper to your theme.
Filter the grid, preview fullscreen, mark favorites, or add your own image.

<p align="center">
  <img src="https://github.com/PersonalJarvis/PersonalJarvis/releases/download/v1.3.1/readme-demo-wallpapers.gif" alt="Scrolling the wallpaper gallery and opening a fullscreen preview" width="860" />
</p>

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

Inside `jarvis/` the layout mirrors the 8-layer model, so you can usually guess where
something lives from the layer it belongs to:

```text
PersonalJarvis/
├── jarvis/                  # The application
│   ├── core/                #   L6  EventBus, protocols, config + atomic writer
│   ├── orchestrator/        #   L6  State machine and turn control
│   ├── brain/               #   L4  Providers, the router, the Ack-Brain, persona
│   ├── missions/            #   L6  Worker and critic loop, worktree isolation
│   ├── agentic_ide/         #   L6  Terminal grid, call signs, prompt delivery
│   ├── speech/              #   L2  Wake → VAD → STT → TTS
│   ├── dictation/           #   L2  Cleanup, clipboard insert, polish, history
│   ├── realtime/            #   L2  Speech-to-speech providers
│   ├── memory/              #   L6  Knowledge Wiki, awareness, long-term recall
│   ├── cu/                  #   L5  Computer use: see the screen, drive it
│   ├── safety/              #   L3  The four risk tiers and the approval path
│   ├── channels/            #   L7  Telegram, Discord, and the shared brain behind them
│   ├── telephony/           #   L7  Outbound and inbound calls (optional extra)
│   ├── plugins/             #       Every pluggable backend, wired by entry point
│   ├── cli_ctl/             #       The jarvis / jarvisctl / jctl client
│   └── ui/web/              #   L7  FastAPI server + the React desktop app
├── ui/                      # Orb overlay; loaded by jarvis at runtime
├── board-backend/           # Standalone federation service (signed Board aggregates)
├── conductor/               # YAML-first agentic-workflow canvas, mounted in the app
├── wiki/                    # Seed knowledge vault, created on first run
├── install/                 # One-line installers + release verification (cosign / TUF)
├── tests/                   # Unit, integration, contract, and end-to-end suites
├── docs/                    # Architecture docs, ADRs, the philosophy, design specs
├── assets/                  # Brand art, banner, screenshots, demo recordings
├── .github/                 # CI workflows + issue / pull-request templates
├── scoop-bucket/            # Windows install manifest (Scoop)
├── homebrew-tap/            # macOS install formula (Homebrew)
└── README · LICENSE · CONTRIBUTING · SECURITY · TRADEMARK · CHANGELOG
```

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
