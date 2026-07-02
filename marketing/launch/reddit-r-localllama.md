# r/LocalLLaMA draft

> **Status: DRAFT — do not post without maintainer review.**
> r/LocalLLaMA cares about local inference above all — this draft is upfront
> that the brain is API-based, because pretending otherwise would get torn
> apart in the first comment. Flair: "Resources".

---

**Title:**

Personal Jarvis – open-source voice agent with local wake word + local Whisper STT (MIT). The brain is API-based — honest breakdown inside

**Body:**

I built an open-source voice assistant that actually operates your computer —
you speak, it delegates the work to coding agents / MCP tools / a computer-use
loop, a critic reviews the result, and it talks back. Before you ask the one
question this sub always (rightly) asks, here is the honest local/cloud split:

**Runs locally:**

- Wake word: openWakeWord (ONNX, CPU) — including custom trained wake phrases,
  so you're not stuck with someone else's brand name in your living room.
- STT: faster-whisper as an opt-in extra (`--with-voice-local`, ~1.5 GB) —
  no audio has to leave your machine. Important lesson from months of debugging:
  keep the always-on wake model on CPU with pinned threads; shared GPU
  inference engines (ctranslate2) can wedge unrecoverably when two callers hit
  them concurrently. The bug register in the repo documents this in detail.
- Everything stateful: conversations, the Obsidian-compatible knowledge wiki,
  contacts, config — plain local files. No telemetry.

**Cloud (by deliberate choice):**

- The brain tier. It's a plugin interface (Gemini / Claude / OpenAI /
  OpenRouter / CLI-subscription logins), and there used to be an Ollama
  provider — it was removed, because a voice loop needs a sub-second ack model
  plus a router plus workers plus a critic running concurrently, and the
  latency/quality budget didn't work out on consumer hardware at the time.
- TTS is cloud today.

If you want to prove the local-brain case wrong, the plugin contract is small
(`AsyncIterator` streaming interface + a contract test suite) and a solid PR
would absolutely be considered — the interface never assumes a specific
provider by design.

Architecture in one line: lean router-brain → dispatches to agent harnesses
(Claude Code / Codex CLI / MCP / computer-use) → each mission isolated in its
own git worktree → critic loop (max 3 rounds) → signed controller decides what
gets spoken. Base install is torch-free and runs headless on a €5 VPS with the
browser as mic/speaker.

Repo: https://github.com/PersonalJarvis/PersonalJarvis (MIT)

Happy to go deep on the wake-word war stories — custom KWS training, AGC
amplifying breathing noise into false wakes, and why "just transcribe
everything" is a trap for custom wake phrases.
