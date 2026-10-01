# ADR-0037 — A Jarvis-owned local voice engine replaces the managed speech-to-speech server

**Status:** Proposed
**Date:** 2026-10-01
**Reference:** [Local live voice rebuild plan](../local-live-voice-rebuild.md); ADR-0024, ADR-0033, ADR-0036

## Context

The `local-realtime` provider runs a patched Hugging Face
`speech-to-speech==0.2.12` server behind 5,770 lines of Jarvis supervision code.
Between 2026-08-07 and 2026-09-26, 93 of 174 server generations never became
ready, cold boots took 68–418 s, first audio per user turn had a p50 of 4.6 s,
and the server served one call at a time without listening while it spoke. No
local call has produced audio since 2026-08-27. The install needs at least
12 GB of accelerator memory, cannot resolve on macOS, has never run on Linux
and refuses every CPU-only machine. Since 2026-09-18 local calls go through
`NativeLiveVoiceSession`, which skips the provider's readiness refusal, tool
deadline, declaration budget and barge-in. The evidence is in section 2 of the
rebuild plan.

## Decision

1. Local live voice is served by a Jarvis-owned cascade engine running as one
   child process per app instance, in its own uv-managed CPython 3.12
   environment, connected to Jarvis over stdin/stdout with a versioned framed
   protocol. The worker exits when its stdin closes; it binds no port.
2. A thin provider adapter implements `RealtimeProvider` and `RealtimeSession`
   with `native_tool_orchestration=True` and `browser_audio=True`, so
   `NativeLiveVoiceSession`, `LiveTools` and the browser audio path stay.
   `jarvis/live/native.py` gains the readiness refusal, spoken start failure,
   barge-in interrupt, 5 s tool deadline, declaration budget, per-turn language
   and the provider's own model id.
3. The engine owns the real-time loop: Silero VAD, Smart Turn v3.2, speculative
   Parakeet TDT 0.6B v3 transcription, a streaming LLM on Ollama with tools, a
   clause chunker, a TTS ladder (Piper, Pocket TTS, Qwen3-TTS) and barge-in.
   Speech models run on the CPU by default; CUDA or MLX is used only when the
   setup self-test measures a benefit.
4. Tiers are chosen by a measured self-test on the user's machine, never by a
   memory label. Every machine gets at least the core tier or a one-sentence
   reason why not.
5. After setup the engine makes no network calls. Readiness means a real
   inference in the running process.
6. The old stack (`jarvis/realtime/local_server/`, `LocalRealtimeProvider`, the
   managed-server routes and card) is removed after the new engine passes the
   plan's gates; `local-realtime` then becomes an alias of the new provider.

## Consequences

- Local live voice becomes reachable on macOS, Linux, AMD/Intel GPUs and
  CPU-only machines, with an honest, measured latency label per machine.
- No pidfiles, leases, port conflicts or orphaned servers: the process tree
  dies with the app. An app restart reloads only the speech models; the LLM
  stays resident in Ollama.
- Jarvis maintains about 2,000–2,500 lines of real-time loop code instead of
  adapting to a third-party server; roughly 8,400 lines of product code and
  5,600 lines of tests are removed.
- German tool-call quality of small local models is the largest open risk; the
  bake-off decides the model per tier before defaults ship.
- Echo cancellation for barge-in is proven only in WebView2; macOS and Linux
  WebViews must be measured, with half duplex as the fallback.

## Alternatives considered

- **HF `speech-to-speech 1.0.0` behind a new supervisor:** rejected; smoke
  tests cover only Linux and Apple Silicon, the fast TTS path has no Windows
  wheel, and each pipeline still holds its own model copy.
- **Pipecat inside the worker:** rejected for the core loop; its API changes
  about every two weeks and it does not solve install, readiness or memory.
  It remains a reference implementation.
- **Engine inside the app process:** rejected; it brings native inference and
  CUDA into the desktop process (AP-24, os-parity P-41) and lets an engine
  crash take the app down.
- **Loopback WebSocket server instead of pipes:** rejected for the default; a
  port reintroduces bind conflicts, firewall prompts and orphaned servers. The
  protocol stays transport-neutral, so a socket transport can be added later.
- **Native speech-to-speech models:** rejected for now; none is German-capable,
  small and cross-platform as of 2026-10.
