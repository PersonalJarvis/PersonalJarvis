# X launch thread draft (@PersonalJarvis)

> **Status: DRAFT — do not post without maintainer review.**
> Attach a demo video to tweet 1 — upload the MP4 natively (X re-encodes, so
> use the highest-quality master you have).
> House rule: no emojis in post text. Post the tweets as one thread.

---

**Tweet 1 — hook + video**

I wanted an assistant that actually runs my computer by voice. So I built one — and open-sourced it.

You talk. Your computer does the work: it delegates to coding agents, reviews
their output, opens the pull request, and tells you when it's done.

MIT. Private by design. Runs on a 5 euro server.

github.com/PersonalJarvis/PersonalJarvis

[ATTACH: demo video]

**Tweet 2 — how it works**

Under the hood it's not one giant prompt. A lean router-brain listens and
delegates: small talk gets answered instantly, real work becomes a background
mission — an isolated worker does it, a critic reviews it (up to 3 rounds),
and only approved results are spoken back.

**Tweet 3 — provider freedom**

No vendor lock-in, structurally. The brain is a plugin: Gemini, Claude,
OpenAI, OpenRouter — or run the heavy agents on the flat-rate subscription you
already pay (Claude CLI / Codex CLI login). A dead key or a 429 never bricks
it: every tier falls back across provider families.

**Tweet 4 — privacy**

Wake word and speech recognition can run fully local — your audio never has
to leave the machine. No telemetry, no account, no cloud middleman: your
conversations, memory wiki, and keys stay in local files and your OS keychain.

**Tweet 5 — CTA**

It runs headless on a cheap VPS (your browser is the microphone) or as a full
desktop app on Windows, macOS, and Linux.

Star it, break it, file issues — contributions welcome, good first issues are
labeled:

github.com/PersonalJarvis/PersonalJarvis
