# r/LocalLLaMA draft

> **Status: DRAFT — do not post without maintainer review.**
> Two placeholders must be filled in by the maintainer before posting:
> `[HARDWARE]` and `[MODEL + SPEED]`. This sub does not believe a local
> claim without the box it ran on. Do not post the draft with the brackets
> still in it.
>
> Rewritten 2026-08-06. The previous version of this file said the Ollama
> provider had been removed and the brain was API-only. That stopped being
> true on 2026-07-25 (Ollama re-added as a keyless local card) and finally
> on 2026-08-05, when realtime — the last tier without a keyless option —
> got a self-hosted card. Posting the old draft would have undersold the
> project to the one sub that cares about exactly this.
>
> Suggested flair: check the sidebar before posting; "Resources" or
> "Tutorial | Guide" fit, "New Model" does not.

---

**Title (pick one):**

1. I made my voice assistant run with zero cloud accounts — wake word, STT, brain, TTS and the low-latency voice mode all on my own box (MIT)

2. Personal Jarvis: an open-source voice agent that runs missions on your local model, not just chats with it (MIT)

3. Every tier of my voice assistant now has a keyless local option — including the realtime voice mode. Honest writeup of what still sucks.

---

**Body:**

I've been building an open-source voice agent for about a year. It doesn't
just answer, it does the thing: you speak, it picks a tool or spawns an
isolated worker, a critic reviews the result, and it talks back. Until
recently the honest answer to "can I run this fully local?" was "mostly",
and this sub knows that "mostly local" means "not local".

As of yesterday every tier the voice loop touches has a keyless card, and
there's a test in the repo that fails if a tier ever loses its last local
option:

| Tier | Local option |
|---|---|
| Wake word | openWakeWord (ONNX, CPU) or Vosk KWS, custom phrases included |
| STT | faster-whisper, or Nemotron on-device |
| Brain | Ollama, or any OpenAI-compatible server (llama.cpp, vLLM, LM Studio, `transformers serve`) |
| TTS | Piper |
| Realtime (low-latency voice mode) | any endpoint speaking the OpenAI realtime protocol, address is yours to name |
| Dictation polish | Ollama |

The part I actually care about: **missions run on the local model too.**
A "research X and write it up" request doesn't quietly need a cloud key —
with Ollama or an OpenAI-compatible server selected, the in-process worker
runs the tool-use loop against your server, in an isolated `git worktree`,
and drops a file in Outputs. The model needs tool calling; the current
default hint is `ollama pull qwen3.5`, and the app pulls models for you
instead of sending you to a terminal.

My setup: [HARDWARE], running [MODEL + SPEED]. The router tier is
deliberately small and cheap, so the thing that has to answer in under a
second is not the thing doing the heavy lifting.

**What's still bad, stated up front:**

- The local realtime card shipped yesterday. It is protocol-shaped, not
  product-shaped — it bundles no server and names no project, because I
  didn't want to bless one. If your endpoint speaks the protocol it works;
  if it half-speaks it, you'll find out in the logs. Feedback here is the
  main thing I'm after.
- The strongest agent path is still cloud: delegating a mission to Claude
  Code or Codex CLI needs their credential. Local models get the in-process
  worker, which is real but weaker at long autonomous work.
- Computer use (it takes the mouse and keyboard) needs a vision-capable
  model, and it's the least mature path in the project either way.
- A small local model will route worse than a frontier one. That's the
  trade, and the app doesn't hide which tier is running on what.

Architecture in one line: small router brain → tool, or a mission in an
isolated worktree → critic loop, max 3 rounds → controller decides what
gets spoken. Providers sit behind a plugin contract (`AsyncIterator`
streaming plus a contract test suite), and nothing in the code gates on a
provider name — only on capabilities — so adding a backend is a plugin, not
a fork.

Everything stateful is plain local files: conversations, an
Obsidian-compatible knowledge wiki, contacts, config. No telemetry, no
account, no phone-home. Base install is torch-free and runs headless on a
tiny VPS with the browser as microphone and speaker; local voice models are
an opt-in extra.

Repo: https://github.com/PersonalJarvis/PersonalJarvis (MIT)

Happy to go deep in the comments on the wake-word war stories — custom KWS
training, AGC amplifying breathing into false wakes, why a shared
ctranslate2 engine wedges permanently when two callers hit it at once, and
why gating a wake word on what the transcript *says* breaks in both
directions at once.

---

## Comment prep — the questions this sub will ask

Have answers ready in the first hour; the first three comments decide the
post. Do not argue, and do not answer a hardware question with a link.

- **"Which model, which hardware, how many tok/s?"** → the numbers from
  `[HARDWARE]` / `[MODEL + SPEED]`, plus what falls apart on 8 GB VRAM.
- **"Another LangChain wrapper?"** → no framework; the plugin contract and
  the worktree isolation are the answer, in two sentences, not a paragraph.
- **"Why not <local realtime project>?"** → the card takes any endpoint
  speaking the protocol; name is a config field. Ask them to try theirs.
- **"Why is Whisper not the wake word?"** → transcript-content gating fails
  in both directions; point at the deep-dive doc.
- **"Did an LLM write this README?"** → say plainly how the project is
  built. This sub can smell a dodge.
