# Local realtime diagnosis and reliability repair

Date: 2026-10-02. Scope: local speech only; GPT-Live providers, their model
selection, delegation, authentication and transport are unchanged.

## What failed

The checkout contains two different local voice implementations. Their names
make it easy to select the wrong one:

- `local-realtime`: the older managed `speech-to-speech` server, combining
  Parakeet, an Ollama language model and Qwen3/Pocket speech synthesis behind
  an OpenAI-compatible WebSocket.
- `local-voice`: the newer Jarvis-owned engine from ADR-0037. One isolated
  child process loads CPU speech models, calls the local Ollama model directly,
  and exchanges framed audio/control messages with the app over pipes.

Neither implementation is a single end-to-end speech model. The second already
has an in-app setup that downloads its dependencies and selects an installed
language model according to the machine class. A user does not need to manage
three separate servers or use GPT-Live to operate it.

Observed in the existing local logs:

- At 19:27 and 19:28, calls to the old server were refused because it was still
  starting. Switching to `local-voice` also produced loading refusals, including
  two reports of 0% progress more than a minute apart.
- At 19:30:56, the old Qwen3 path logged 34.70 seconds of generation for
  3.40 seconds of speech. Its response accounting recorded 0.00 seconds of
  delivered audio. The log establishes slow generation and failed delivery
  accounting; it does not establish what was audible at the speakers.
- The old server repeatedly reported an unready pool and recovery deferrals.
  Later status reads showed the new engine ready while GPT-Live subscription
  was selected. A ready engine is not evidence that a call used that engine.
- The machine initially had roughly 13 GB of its 16 GB GPU memory occupied,
  with the old GPU speech server and an Ollama model both resident. Memory
  pressure and contention are plausible contributors, not an isolated causal
  measurement. The old server was gone by the later cleanup check; stop requests
  reported no owned server. No unrelated process was terminated.

The old log cannot uniquely identify why the new engine reported 0% on those
particular calls. Code inspection did establish the lifecycle defects below.

## Repair implemented

1. **Readiness now requires actual inference.** Worker startup synthesizes and
   re-transcribes a short phrase in each configured language, then requires a
   nonempty answer from the local language model. Failed synthesis, recognition
   or reasoning cannot publish `ready`. This checks the software audio path,
   not the physical microphone, speakers or wake word.
2. **Startup failures release their resources.** A failed/cancelled handshake,
   incompatible worker protocol or broken output pipe closes the worker and
   its log. Concurrent cleanup callers join one cleanup task; cancellation
   does not abandon it. Replacing settings also cancels an unfinished start.
3. **Loading cannot remain pending forever.** The adapter limits loading to
   180 seconds after handshake and shuts down a stalled worker. It identifies
   the loading stage and offers a fresh-process retry through the self-test.
   A failed readiness test also releases its unusable worker before retry.
4. **Live audio and control messages preserve wire order.** Previously they
   passed through separate queues/tasks, allowing `response.done` or an
   interruption to overtake previously received audio. The live adapter now
   consumes one ordered stream. Existing measurement clients retain their
   separate-queue interface.
5. **Status is more truthful.** The initial handshake is shown as starting.
   Loading refusals no longer repeatedly promise an unmeasured 15-second ETA.
   Stored self-tests become stale after changes to languages, voice options
   or the local model-server address, as well as model/voice/version changes.

This repairs the installed engine path. It does not remove the legacy provider
or automatically switch the user's active provider. Removing the old stack
remains gated by the remaining ADR-0037 acceptance work.

## Can one downloaded model replace the cascade?

Current publisher documentation was checked, rather than treating text-language
support, speech recognition and speech output as interchangeable:

| Model | Relevant publisher support | Fit for the default German desktop path |
| --- | --- | --- |
| [LFM2.5-Audio 1.5B](https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B) | End-to-end audio/text; English; GGUF CPU inference; LFM Open License | Small and attractive for an English experiment. No supported German voice or demonstrated Jarvis tool contract. |
| [PersonaPlex 7B](https://huggingface.co/nvidia/personaplex-7b-v1) | Full-duplex speech; English; gated model-license acceptance | Interesting natural conversation, but unsuitable as a zero-setup German default. |
| [MiniCPM-o 4.5](https://huggingface.co/openbmb/MiniCPM-o-4_5) | 9B; full-duplex; official speech conversation in English and Chinese; local quantized deployment | Multilingual text/vision does not establish German speech output. Not qualified for this default. |
| [Qwen3-Omni](https://github.com/QwenLM/Qwen3-Omni) | German speech input/output; 30B-A3B Thinker/Talker | A candidate for a larger-machine experimental backend, not a verified 16 GB default. The published BF16 memory table is for video workloads and must not be misreported as an audio-only minimum. Quantized end-to-end desktop deployment still needs measurement. |

The practical default is one Jarvis-managed local voice package, with a local
reasoning model, CPU speech components, and hardware-dependent model selection.
This is one setup action, but honestly remains a cascade internally. A native
audio-model backend should be added only after proving the required spoken
languages, cold/warm latency, interrupt behavior, tool results, cancellation,
memory limits and installation on each supported platform. Spoken text that
looks like JSON must never be interpreted as an authorized action.

## Verification in this session

Real measurements used the installed engine environment, existing model files,
`qwen3.5:4b`, Pocket TTS and the changed production provider adapter. Speech
input was synthetic German streamed in 20 ms frames. No cloud model was used.

| Check | Observed result |
| --- | --- |
| German arithmetic question from audio | Correct transcription and spoken answer; 218,446 PCM bytes; clean turn completion |
| Final transcript to first audio, conversational run | 318 ms; this excludes speech capture and endpoint detection |
| Cold startup including de/en speech proof | 31.93 s in that run |
| Explicit request to use an arithmetic tool | Exactly one `add_numbers(a=2, b=3)` through the actual `ToolExecutor`; correct spoken result; 76,722 PCM bytes |
| Tool-run startup and response latency | 37.14 s startup; 18.30 s final-transcript-to-audio under concurrent machine load: functionally successful, too slow for a realtime performance claim |
| Instrumented repeat of the explicit tool request | 752 ms final-transcript-to-audio; 1,234 ms speech-end-to-audio; tool requested after 480 ms; tool execution below 1 ms; exactly one call and 79,470 PCM bytes |
| Bare arithmetic question with a tool available | Model answered directly; not counted as tool-execution proof. Explicit action wording was used for the tool test. |
| Worker shutdown | Probe-owned workers closed after every run |
| Focused tests after integration | 329 passed / 3 skipped in the main checkout; 315 passed / 3 skipped on current remote main plus this patch. Counts differ because the shared checkout contains other integration changes. |
| Desktop cold-boot guard | Window 1,795 ms; voice usability 9,088 ms; app interaction 10,907 ms; all within existing budgets |
| Static checks | Ruff passed for changed Python files |
| Publication checks | All 19 static gates passed. The initial shell parse failure was caused by CRLF files retained across the rebase; exact committed LF bytes passed without a source change. The exact base checkout also passed. |

The voice-engine tests cover failed/cancelled handshake cleanup, protocol
mismatch, pipe failure, ordered audio/completion, startup timeout, readiness
failure/retry, settings replacement and stale self-test evidence. The adjacent
tests retain the existing tool-confirmation and shared provider contracts.

The main checkout contains the committed repair. Its existing desktop process
has not been restarted by this session: the application explicitly restricts
restarts to a desktop-user action because active agent terminals would be lost
(`jarvis/ui/web/settings_routes.py`, `restart_app`). The probe used fresh
processes with the repaired code. A running desktop session is therefore not
claimed to have adopted all adapter changes yet.

## Remaining qualification

- A real microphone-to-speaker conversation, wake word and acoustic echo/
  interruption acceptance have not been measured in this session.
- The slow tool result needs an isolated latency investigation before claiming
  consistently fast local tool conversations. One fast reply is not a latency
  distribution or a long-session reliability proof.
- Fresh installation was not repeated; this run reused the installed models
  and environment. macOS, Linux and weaker hardware were not run in this
  session. The existing CPU-only tool-quality caveat in the rebuild plan stays.
- The legacy-provider migration, 500-turn soak and physical offline acceptance
  from the rebuild plan remain open. A new single-model speech backend has not
  been claimed or enabled by this repair.

Related: [ADR-0037](../adr/0037-jarvis-owned-local-voice-engine.md) and
[the local voice rebuild plan](../local-live-voice-rebuild.md).
