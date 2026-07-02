# Show HN draft

> **Status: DRAFT — do not post without maintainer review.**
> Post from the maintainer's HN account. Best window: Tue–Thu, ~14:00–16:00 UTC.
> HN culture notes: no marketing language, answer every technical question in
> the first two hours, be upfront about limitations.

---

**Title:**

Show HN: Personal Jarvis – open-source voice agent that runs your computer

**URL:** https://github.com/PersonalJarvis/PersonalJarvis

**Text:**

Hi HN — I've spent the last months building the assistant I always wanted:
you say something, and your computer actually does it.

The core is a small, fast "router brain" whose only job is to listen and
delegate. Small talk gets answered directly; real work gets dispatched to
agent harnesses (Claude Code, Codex CLI, MCP servers, or a raw computer-use
loop) that run as background missions. Each mission runs in an isolated git
worktree, a critic model reviews the result (up to three correction rounds),
and a controller decides what is spoken back to you. A sub-second ack model
keeps the conversation flowing while the heavy work happens behind the scenes.

Some design decisions that might interest this crowd:

- Provider-agnostic by architecture, not as an afterthought. The brain is a
  plugin (Gemini, Claude, OpenAI, OpenRouter, or CLI-subscription logins like
  Claude Max / ChatGPT), and every tier has a key-aware fallback chain that
  crosses provider families — one dead key or 429 never bricks the assistant.
- The heavy agents can run on a flat-rate subscription you already pay for
  (Claude CLI / Codex CLI login) instead of a metered API key.
- Speech is hybrid: wake word and STT can run fully locally (openWakeWord +
  faster-whisper as an opt-in extra), so no audio has to leave the machine;
  cloud STT (Groq Whisper) is the low-latency default if you opt in.
- Security is structural: everything observed through tools (web pages,
  emails, screenshots) is data, never instructions; every tool call goes
  through a four-tier risk policy (safe/monitor/ask/block); generated skills
  land as drafts and are never auto-activated.
- It runs headless on a cheap VPS — the browser provides mic and speakers via
  WebSocket — or as a full desktop app (Windows/macOS/Linux) with a tray, an
  overlay, and global-hotkey wake. The base install is torch- and GPU-free.

Honest limitations: the brain itself is cloud-API-based today (local LLMs
were dropped deliberately — voice latency budgets made pure-API chains the
pragmatic call, wake/STT stay local); TTS is cloud; and computer-use is the
least polished path. The repo history is depersonalized, so it reads cleaner
than the real, messier development was.

MIT-licensed. I'd genuinely value feedback on the architecture — especially
the router/worker/critic split and the prompt-injection boundary.

---

**Prepared answers for likely questions:**

- *"Why not a local LLM?"* — Tried it; a voice conversation needs sub-second
  acks and fast first tokens, and the orchestration (router + ack + worker +
  critic) needs several concurrent models. The plugin interface is there; a
  well-done local-brain PR would be welcome.
- *"How is this different from Siri/Alexa?"* — Those answer questions and set
  timers. This one opens a terminal, edits code, files PRs, browses, calls
  people — and shows its work in an auditable mission log.
- *"Prompt injection?"* — Only chat/voice can issue instructions. Tool output
  is data by contract; embedded instructions get surfaced, not executed. Plus
  risk tiers and a whitelist/blacklist with blacklist precedence. See SECURITY.md.
- *"What data leaves my machine?"* — Whatever you configure: audio only to the
  STT provider you chose (or none, if local STT), text to your brain provider.
  No telemetry, no analytics.
