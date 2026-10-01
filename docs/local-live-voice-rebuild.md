# Local Live Voice — Rebuild Plan

- **Status:** Proposed. Analysis and plan only; nothing is implemented yet.
- **Date:** 2026-10-01
- **Decision record:** [ADR-0037](adr/0037-jarvis-owned-local-voice-engine.md)
- **Supersedes:** the recovery plan in
  [`local-realtime-runtime-deep-dive.md`](local-realtime-runtime-deep-dive.md)
  (2026-08-08) and the backlog in
  [`local-realtime-supervisor-assessment.md`](local-realtime-supervisor-assessment.md)
  (2026-08-11). Both stay as history.
- **Scope:** the `local-realtime` provider (the "Local voice" card): live,
  full-duplex voice with every model on the user's machine. GPT-Live, Gemini
  Live, OpenAI Realtime and the classic pipeline are out of scope, except where
  they share the `jarvis/live` seam.

## 1. Summary

Local live voice does not work, and the evidence says it cannot be repaired in
place. No local call has produced audio since 2026-08-27. The managed engine
(Hugging Face `speech-to-speech==0.2.12`, patched) loads every model before it
opens its port, needs the network on every boot, serves one call at a time,
stops listening while it speaks, and needs at least 12 GB of accelerator memory.
Its installer cannot resolve on macOS, has never run on Linux, and refuses every
machine without a large NVIDIA card or Apple unified memory. Jarvis wraps it in
5,770 lines of supervision code that exist only to manage that process's failure
modes.

We replace the engine, not the live seam. A Jarvis-owned voice engine runs as
one child process with its own Python environment, speaks a small private
protocol over stdin/stdout, and implements the existing
`RealtimeProvider`/`RealtimeSession` contract. `NativeLiveVoiceSession`, the
tool layer and the browser audio path therefore stay. Inside the engine a small
cascade runs: Silero VAD, Smart Turn v3.2, Parakeet TDT 0.6B v3, a streaming
local LLM through Ollama, and a three-step TTS ladder (Piper, Pocket TTS,
Qwen3-TTS). Speech models run on the CPU by default; a GPU (CUDA or Apple MLX)
is used only where a measured self-test shows it helps. That makes a usable
core tier possible on every machine, including CPU-only Linux, and leaves the
GPU to the language model.

## 2. Evidence: what fails today

Sources: `data/local_realtime_server.log` and `.log.1` (2026-08-07 to 09-26),
`data/local_realtime_server.boot.json`, `data/flight_recorder/*.jsonl`, the
managed venv, and the working tree on 2026-10-01. Numbers are observations on
the maintainer's Windows box (RTX 5070 Ti, 16 GB) unless marked otherwise.

### 2.1 Usage

| Signal | Observed |
| --- | --- |
| Local sessions 2026-08-07 to 08-28 | 108; 45 (42 %) produced any audio; 49 completed no turn |
| Last session with audio | 2026-08-27 20:12 (first audio after 12.5 s) |
| Last attempt | 2026-08-28 15:33, "realtime handshake exceeded 135.0s provider budget" |
| Sessions on the native path (since 2026-09-18) | 0; the path has never carried a real local call |

### 2.2 Boot

| Signal | Observed |
| --- | --- |
| Server generations | 174, of which 93 (53 %) never became ready |
| Why never ready | 47 offline: Hugging Face HEAD requests and an NLTK download on every boot, five retries per file, killed at the budget, respawned every ~5 min. 27 LLM warm-up timeouts: the engine aborts Ollama's model load after ~20 s. 6 Ollama not running. 13 stopped without an error line. |
| Spawn to ready (supervisor statistics) | 68 / 110 / 150 / 178 / 251 s (median 150 s); one boot on 09-26 took ~418 s |
| Idle release | `[voice].local_idle_release_minutes = 15` stops the server; the warm loop re-arms only after a call ends, so most calls after a pause start cold |

### 2.3 Latency (speech end to first audio)

| Signal | Observed |
| --- | --- |
| First audio per user turn (n = 82) | p50 4.63 s, p90 12.9 s; none at or below 1.2 s |
| Stage p50 / p90 | STT 1.03 / 4.15 s, LLM 1.11 / 6.08 s, TTS first audio 0.35 / 1.00 s |
| Fixed waits in the launch flags | +800 ms "speculative reopen grace" on every complete turn; +2,000 ms when Smart Turn says "incomplete" |
| Worst turn under GPU contention | 49.8 s (09-26: STT 13.7 s, LLM 15.0 s, TTS first audio 19.8 s) |

### 2.4 Capacity, duplex, resources

- One pipeline slot: 12 "all 1 pipeline slots in use" rejections and 3 sessions
  that never drained.
- Half duplex: the engine's VAD drops audio while it answers, and Jarvis sends
  `interrupt_response=false`. The user cannot interrupt.
- VRAM: the spawn gate asks for 6 GB free. The stack needs about 12–14 GB
  (brain with a 32K context, Qwen3-TTS 1.7B with CUDA graphs, Parakeet) on a
  card that already carries ~9.4 GB of other GPU users. Under that pressure the
  TTS warm-up took 77 s to its first audio.
- On Windows every spawn opens a visible terminal window: `DETACHED_PROCESS`
  overrides `CREATE_NO_WINDOW` for the console-script launcher
  (`supervisor.py::_server_creationflags`). Closing that window kills the
  server.
- Health and proof drift: the install proof dates from 2026-08-08 and a
  different brain; a pinned `pocket-tts` is missing from the venv while the
  status says ready; `data/state/local_models_health.json` reports "voice ok"
  with fresh timestamps while nothing runs.

### 2.5 Platform reach

Only Windows was ever run. The rest comes from code and wheel metadata.

| Platform | Today |
| --- | --- |
| macOS, Apple Silicon | The install cannot resolve. `speech-to-speech 0.2.12` on Darwin requires `transformers==5.6.2`, `torch==2.11.0` and MLX packages; Jarvis pins `qwen-tts==0.1.1` (`transformers==4.57.3`), `faster-qwen3-tts==0.3.2` (`transformers<5`) and `torch==2.13.0`. |
| Linux + NVIDIA | Below 12 GB refused. At 12 GB and above it needs a CUDA-13 driver, sm_75 or newer and the native `qwentts-cpp-python` package. Never run. |
| AMD/Intel GPU, CPU-only, headless | Refused by design ("no supported accelerator"). |

### 2.6 The call path since 2026-09-18

Commit `8f43cfc98` gave `LocalRealtimeProvider` `native_tool_orchestration=True`.
`build_realtime_session` (`jarvis/realtime/factory.py:484`) therefore hands
local calls to `NativeLiveVoiceSession` (`jarvis/live/native.py`). That path:

- never calls `can_open_duplex_session()`. The 0.75 s honest refusal with a
  boot ETA is gone; `audio_start` blocks inside the provider's 120 s connect
  loop while the desktop waits up to 140 s;
- ends a failed start with a generic, unspoken `provider_error`;
- uses only `providers[0]`: configured fallbacks are ignored, and the free
  classic local pipeline is never offered;
- skips the 5 s voice tool budget, the per-provider declaration budget (it
  declares up to 52 tools, 24 KB of JSON, to a 4–9B model), the scrub gate and
  output-language validation;
- maps `speech_started`/`interrupted` only to a browser flush. Nothing cancels
  the engine's response;
- opens the local socket with `[brain.realtime].model` first, which after a
  switch from GPT-Live still reads `gpt-live-1`.

### 2.7 Why not repair in place

| # | Cause | Kind |
| --- | --- | --- |
| 1 | Models load serially before the port binds; a healthy boot looks dead for minutes | Structural (inside the third-party engine) |
| 2 | Network on every boot; offline means a crash loop | Fixable, but every engine upgrade brings it back |
| 3 | One model copy per pipeline slot; deaf while answering | Structural (upstream redesign #363 is open, not shipped) |
| 4 | Serial stages behind one lock, fixed grace waits, every tool call a full extra LLM pass | Structural and fixable |
| 5 | Memory budgeted by label (12 GB floor), not by the measured workload | Structural (the stack really needs ~12 GB) |
| 6 | Mac, Linux and CPU unreachable | Structural (dependency set) |
| 7 | Native seam gaps (2.6) | Fixable, and fixed by this plan |
| 8 | Visible console window, stale health and install proofs | Fixable |

Fixing the fixable items leaves 1, 3, 5 and 6. They are properties of the
engine and its dependency set. Upstream `speech-to-speech 1.0.0` (2026-09-06)
improves macOS and Linux, but its smoke tests cover only Linux and Apple
Silicon on Python 3.11, its fast GGML TTS path has no Windows wheel, and it
still binds one model copy per pipeline. It is the runner-up (section 5), not
the answer.

## 3. Requirements

- **R1 Platforms.** macOS 14+ on Apple Silicon; Windows and Linux with NVIDIA
  (Pascal to Blackwell, 6 GB and up); any x86-64 or arm64 machine on the CPU,
  including headless Linux (audio then comes from a browser tab on another
  device, as today). Every platform either works or says in one sentence why
  not and what to do.
- **R2 Languages.** German and English first; Spanish next (Piper voices
  exist). A language without a local voice says so.
- **R3 Tools.** The live model calls Jarvis tools through
  `LiveTools`/`ToolExecutor`; results arrive within `VOICE_TOOL_BUDGET_S` or as
  an honest pending result.
- **R4 Turn-taking.** Automatic end of turn by default (`turn_pause_ms=None`).
  An explicit Settings pause is folded into the engine's own detection, never
  layered on top. Barge-in works while Jarvis speaks.
- **R5 Latency.** Speech end to first audio p50 ≤ 0.8 s and p95 ≤ 1.2 s on
  NVIDIA with 8 GB or more and on Apple M-Pro/Max; measured and shown
  everywhere else.
- **R6 Readiness.** Once the user selects local voice, no model loads on the
  call path. A call during warm-up gets an honest spoken and visible state
  within 1 s.
- **R7 Process integrity.** One engine per app instance; it dies with Jarvis;
  no orphans, no port, no LAN listener.
- **R8 Offline.** After setup nothing touches the network at runtime.
- **R9 Install.** One click in the app, resumable, with progress; no hand-edited
  config, no tokens, no gated models, no GPL or non-commercial weights.
- **R10 Ownership.** Jarvis owns the real-time loop. Third-party code is limited
  to model runtimes behind small interfaces.

## 4. Target architecture

### 4.1 Overview

```
WebView (mic 48 kHz with echo cancellation, playback)
   |  /ws/audio (unchanged)
   v
Jarvis app process
   NativeLiveVoiceSession -- LiveTools -- ToolExecutor     (seam kept, fixes in 4.4)
   |  RealtimeProvider / RealtimeSession
   LocalVoiceProvider adapter (pure Python, any app interpreter 3.11-3.14)
   |  stdin/stdout, length-prefixed frames (protocol v1, 4.5)
   v
Voice engine worker (child process, own uv venv, CPython 3.12)
   audio 16 kHz -> Silero VAD -> Smart Turn v3.2 -> Parakeet v3 (speculative)
                                                    | final transcript
                                     Jarvis decides the language, requests a response
                                                    v
                                LLM stream (Ollama, OpenAI-compatible, tools)
                                   | text deltas      | tool_call -> Jarvis -> tool_result
                                   v
                           clause chunker -> TTS ladder -> audio 24 kHz
   barge-in: VAD stays live while speaking -> cancel LLM + TTS -> interrupted
   |  HTTP on loopback
   v
Ollama (separate service, managed by jarvis/brain/ollama_runtime.py), voice profile resident
```

### 4.2 Process model

- One worker per Jarvis instance, started by the adapter with
  `asyncio.create_subprocess_exec` and `NO_WINDOW_CREATIONFLAGS` (AP-1) inside
  a `jarvis/core/process_tree.py` tree (Windows Job Object with kill-on-close,
  POSIX process group).
- The worker exits on stdin EOF, so it dies with Jarvis. There is no pidfile,
  no lease, no port and no adoption logic. Two app instances (live and dev) get
  two workers; the memory gate (4.9) refuses a second premium stack honestly.
- Stdout carries only protocol frames. Logs go to a rotated file under the
  data dir and to stderr.
- The LLM stays in Ollama, a separate long-lived service. An app restart
  reloads only the speech models (seconds), not the language model. This
  replaces the old reason for letting the server outlive the app.
- Why not in the app process: CUDA and ONNX in the desktop process is what the
  dictation lane moved out (os-parity P-41, AP-24); a native crash would take
  the app down; and the latency-critical loop must not share the app's event
  loop, which already stalls under load.

### 4.3 Engine internals

Per session: `listening -> user_speaking -> turn_pending -> responding
(thinking | speaking | tool_wait) -> listening`.

1. **Audio in.** PCM16 at 16 kHz from the adapter (the session already
   resamples the browser's 48 kHz).
2. **VAD.** Silero VAD v6 as an ONNX file loaded directly (no torch), 32 ms
   frames.
3. **Turn decision.** After ~200 ms of trailing silence, Smart Turn v3.2
   (ONNX, int8, CPU) scores the last ≤ 8 s. "Complete" ends the turn;
   "incomplete" keeps listening for a bounded extension tuned in the bake-off
   (8.5). An explicit `turn_pause_ms` replaces the silence threshold and biases
   toward "incomplete"; `None` adds nothing.
4. **STT.** Parakeet TDT 0.6B v3 int8 (sherpa-onnx offline recognizer) decodes
   as soon as the silence starts; if the user resumes, the result is dropped.
   The final transcript therefore costs almost nothing after the turn decision
   (RTFx 31–35 measured by onnx-asr on a desktop CPU).
5. **Response.** The engine emits `input_transcript(final)`. Jarvis resolves
   the reply language once (`jarvis/core/turn_language.py`) and calls
   `request_response()` (`creates_responses_automatically=False`). The pipe
   round trip costs milliseconds.
6. **LLM.** Streaming chat completion on the voice profile (4.6). Text deltas
   feed a clause chunker: sentence end, or a clause boundary after a minimum
   length; aware of numbers and abbreviations in German and English.
7. **TTS.** The first clause goes to TTS at once; later clauses queue. Audio
   leaves as PCM16 at 24 kHz, with output transcript deltas aligned to the
   clauses.
8. **Tools.** A `tool_call` pauses the LLM round and goes to Jarvis; the
   `tool_result` resumes it. Heavy work classes get the ADR-0033 instant
   acknowledgment, spoken by the same engine voice.
9. **Barge-in.** VAD keeps running while the engine speaks (the browser's echo
   cancellation removes Jarvis's own voice). Confirmed user speech (≥ 250 ms,
   optionally a non-empty partial transcript) cancels the LLM stream and the
   TTS queue, emits `speech_started` and `interrupted`, and truncates the
   assistant turn in the history to the audio actually sent.
10. **Cancellation.** One token per response; every stage checks it between
    units of work (AP-19).

### 4.4 The seam: adapter contract and `jarvis/live` fixes

Adapter `jarvis/plugins/realtime/local_voice.py` (entry point
`jarvis.realtime`, no `jarvis.*` import at module import):

- Class attributes: `supports_realtime=True`, `native_tool_orchestration=True`,
  `browser_audio=True`, `implicit_usage_fallback_allowed=False`,
  `credential_candidates=()`, `input_sample_rate=16000`,
  `output_sample_rate=24000`, `external_login_ready(cfg)` (engine installed),
  `from_runtime_config(cfg)`, `prespawn_transport` (start the worker, load
  models), `warm_transport` (await ready), `handshake_budget_s` ≤ 5 s.
- `can_open_duplex_session()` answers from the worker's state in under 50 ms;
  `duplex_unavailable_reason` carries the user sentence and the ETA.
- `open_session(cfg)` sends `session.open` with instructions, language, tools
  and history. The session implements `send_audio`, `receive`,
  `request_response`, `send_tool_result`, `send_text`, `interrupt`, `truncate`,
  `update_session` and `close`, with `creates_responses_automatically=False`,
  `isolates_response_generations=True` and `supports_tool_results=True`.
  `send_image` exists only when the selected LLM accepts images.

Fixes in `jarvis/live/native.py`. Gemini Live shares this file, so each fix
gets a regression test on both providers:

1. Call `can_open_duplex_session()` before `open_session()`; refuse within 1 s
   with the provider's reason, spoken and visible (reuse
   `_announce_start_failure`).
2. Pass the provider's own model setting, never another provider's
   `[brain.realtime].model`.
3. On `speech_started` while Jarvis speaks, call `connection.interrupt()` and
   flush the browser.
4. Apply the 5 s `VOICE_TOOL_BUDGET_S` deadline with an honest pending result
   (port the logic from `jarvis/realtime/session.py`).
5. Respect a provider's `tool_declaration_budget_tokens`. For the local engine,
   declare a curated direct set plus `discover_tools`/`call_tool` (4.6).
6. Forward the per-turn language decision with `update_session(language=...)`
   before `request_response()`.
7. When local voice is not ready and a fully local classic pipeline exists,
   offer it. It is keyless, so the billing rule behind
   `allow_classic_fallback=False` does not apply. Never fall back to a billed
   provider.

`LiveTools`, `LiveLedger`, recovery, runtime ownership, `/ws/audio` and the
browser client stay unchanged.

### 4.5 Protocol v1 (worker and adapter)

Frame: 4-byte big-endian length, 1-byte kind (`J` = UTF-8 JSON, `A` = PCM16
audio with an 8-byte header for session and sequence), payload. The version is
negotiated in `hello`.

Adapter to worker: `hello{protocol, app_version}`,
`configure{profile, models, voices, llm{base_url, model, options}, limits}`,
`session.open{id, instructions, language, tools, history, turn_pause_ms}`,
audio frames, `response.request{language}`, `tool.result{call_id, result}`,
`text{content, kind}`, `interrupt`, `truncate{audio_end_ms}`,
`session.update{instructions?, tools?, language?}`, `session.close`,
`selftest{suite}`, `shutdown`.

Worker to adapter: `hello{protocol, capabilities, engines}`,
`state{phase, stage, progress, eta_s, reason}`, `speech_started`,
`transcript.input{partial|final, text, language, voiced_ms}`,
`transcript.output{delta}`, audio frames, `tool.call{call_id, name, args}`,
`response.done{status}`, `interrupted{self_initiated}`,
`usage{input_text, output_text}`, `metrics{turn_id, stage timings}`,
`error{code, message, recoverable}`.

The adapter maps these one to one onto `RealtimeEvent`. The protocol does not
depend on the transport; a loopback WebSocket for a remote GPU machine would be
an added transport, not a redesign.

### 4.6 LLM

- **Runtime.** Ollama by default: Jarvis already installs, starts and stops it
  on all three OSes. llama.cpp `llama-server` is a later alternative because of
  its prompt-cache control and prebuilt binaries for CUDA 12/13, Vulkan, ROCm,
  Metal and CPU.
- **Voice profile.** A dedicated alias with `num_ctx=8192` (4K is too small
  once a tool result arrives; more only costs prefill time), thinking off
  (`reasoning_effort="none"` on the OpenAI endpoint), kept resident while local
  voice is selected (`keep_alive` refreshed by the engine), low temperature for
  tool rounds.
- **Prompt layout.** Static system prompt and tool declarations first, history
  after, the per-turn directive last, so the prefix cache hits. The bake-off
  measures cache hits per runtime, because hybrid and sliding-window models
  (Qwen3.5) have regressed here before.
- **Tools.** A curated direct set of 8–12 frequent tools with compact schemas,
  plus `discover_tools`/`call_tool` for the rest, within about 2K tokens. Every
  discovery step costs a full LLM round, so the direct set matters more locally
  than in the cloud.
- **Model per tier** comes from the bake-off (8.2). Candidates: Qwen3.5
  4B/9B, Ministral 3 3B/8B, Granite 4.2 3B/8B, Gemma 4 E4B/12B. No public
  measurement of German tool calls with thinking off exists; this is the
  largest open risk.
- **Coupling.** Selecting local voice marks Ollama as in use, so
  `jarvis/local_models/autostart.py` starts it at boot even when the Local
  models switch is off. During a local call, background work must not queue new
  generations on the same Ollama host.

### 4.7 Components

Defaults, to be confirmed or replaced by the bake-off.

| Role | Default | Runtime per platform | Size | Licence |
| --- | --- | --- | --- | --- |
| VAD | Silero VAD v6 | onnxruntime CPU, all platforms | 2 MB | MIT |
| Turn | Smart Turn v3.2 (23 languages incl. German) | onnxruntime CPU, all platforms | 8 MB | BSD-2 |
| STT | Parakeet TDT 0.6B v3 (25 European languages) | sherpa-onnx int8 on CPU, all platforms; optional parakeet-mlx on Mac | 0.64 GB | CC-BY-4.0 |
| TTS floor | Piper (German thorsten, English, Spanish) | sherpa-onnx CPU, all platforms | ~60 MB per voice | Apache-2.0 runtime; per-voice cards |
| TTS natural | Pocket TTS (Kyutai, 100M; de, en, fr, es, it, pt, nl) | CPU torch, all platforms | 100M parameters | code MIT, weights CC-BY-4.0 (non-cloning repo) |
| TTS premium | Qwen3-TTS 0.6B / 1.7B (10 languages incl. German) | faster-qwen3-tts on CUDA (cu130 for Blackwell, cu126 for Pascal); mlx-audio on Mac | 2.5 / 4.5 GB + 0.7 GB tokenizer | Apache-2.0 |
| LLM | per tier (4.8) | Ollama (CUDA, ROCm, Metal, CPU) | 1.3–7 GB | per model (Apache-2.0 candidates) |

Excluded: Kokoro (no official German); XTTS-v2, Voxtral TTS, Fish/OpenAudio,
F5-TTS base weights and Higgs (non-commercial or research licences); the
LiveKit turn detector and TEN VAD (use restrictions); GPL `piper1`; gated
repositories (they need a user token); Supertonic (archived in 2026-09, pinned
fallback at most); native speech-to-speech models (as of 2026-10 none is
German-capable, small and cross-platform at once).

### 4.8 Capability tiers chosen by a measured self-test

Hardware detection only decides what to try. Setup then runs a 30–60 s
self-test: STT real-time factor, TTS first audio and real-time factor per voice,
LLM first token and tokens per second on the voice profile, and one synthetic
round trip. It picks the highest tier that meets the latency target, and the
card shows the measured expected response time.

| Machine class | Core (always) | Natural | Premium | Expected first audio (estimate) |
| --- | --- | --- | --- | --- |
| CPU only, incl. headless | Parakeet CPU + Piper; LLM 2–4B on CPU | Pocket if its real-time factor ≥ 1.5 | — | 2–5 s, labelled slow |
| Mac 8 GB | Parakeet CPU + Piper; LLM 2B | Pocket | — | 1.5–3 s |
| Mac 16 GB, base chip | as above, LLM 4B | Pocket | — | 1.2–2.5 s |
| Mac Pro/Max | as above | Pocket | Qwen3-TTS 0.6B (MLX); 1.7B and a 9–12B LLM from 24 GB | 0.8–1.2 s |
| NVIDIA 6–8 GB | LLM 4B on GPU, speech on CPU | Pocket | Qwen3-TTS 0.6B only if ≥ 2.5 GB stay free after the LLM | 0.8–1.2 s |
| NVIDIA 12 GB | LLM 8–9B | Pocket | Qwen3-TTS 0.6B | 0.7–1.0 s |
| NVIDIA 16 GB and up | LLM 9–12B | Pocket | Qwen3-TTS 1.7B | 0.6–0.9 s |
| AMD or Intel GPU | speech on CPU; LLM through Ollama ROCm or llama.cpp Vulkan | Pocket | — | measured |

Premium keeps at least 20 % accelerator headroom at steady state. The gate
reads free memory (NVML on NVIDIA; available unified memory on macOS, a new
read in `jarvis/hardware/detection.py`), never the card's label.

### 4.9 Lifecycle, readiness and failure behaviour

- **States:** `not_installed -> installing(progress) -> stopped ->
  starting(stage, progress, eta) -> ready -> degraded(reason) ->
  failed(reason, log tail)`.
- **Ready** means every selected engine passed a real inference in this process
  run: synthesize a phrase, transcribe it back, generate one LLM token. An open
  socket, files on disk or an old marker never count.
- **Start:** on selection (prepare, then hand over), at boot when selected
  (after the boot gate, AP-26), and lazily on a call. Targets: core ready
  within 15 s from a warm disk, premium within 60 s.
- **A call during warm-up** gets an answer within 1 s: "Local voice is still
  loading (about N s)", spoken and shown. It never waits 140 s in silence.
- **Crash:** worker exit becomes `error(recoverable=False)`; the session ends
  with a spoken reason; the worker restarts with backoff 1 s, 5 s, 30 s; three
  failures within 10 min mean `failed`, with the log tail on the card.
- **Degrade, don't die:** if premium TTS fails to load or runs out of memory,
  the worker drops to Natural or Core and reports `degraded` instead of failing
  the call.
- **Idle:** speech models stay resident while local voice is selected (core
  needs about 1.5 GB of RAM). Premium VRAM may be released after a configurable
  idle time; the worker reads that setting (AP-31).
- **Offline:** models live in a Jarvis model store with checksums. The worker
  runs with `HF_HUB_OFFLINE=1` and makes no NLTK or other network calls.

### 4.10 Install and packaging

- **Environment:** `uv venv --python 3.12` under the data dir, using the uv
  bootstrap that `jarvis/society/browser/bootstrap.py::ensure_uv` already
  provides. It is independent of the app's interpreter and absent from the
  base install.
- **Locks:** one hashed lock per platform profile, generated in CI:
  `win-x64-cpu`, `win-x64-cuda13`, `win-x64-cuda12` (Pascal), `mac-arm64`,
  `linux-x64-cpu`, `linux-x64-cuda13`, `linux-arm64-cpu`. Core needs only
  `onnxruntime`, `sherpa-onnx` and `numpy` (~40 MB). Natural adds CPU torch
  (0.12–0.2 GB download). Premium adds CUDA torch (~2 GB on Windows) and
  `faster-qwen3-tts`, or `mlx` and `mlx-audio` on Mac.
- **Worker code:** a self-contained pure-Python package (`jarvis/voice_engine/`,
  no `jarvis.*` imports). The installer copies it into the engine venv from the
  app's own files, so frozen builds work and no Jarvis code is downloaded.
- **Models:** downloaded once with progress and resume, checksummed, from
  ungated repositories. Core ≈ 0.8 GB; Pocket adds a small checkpoint;
  Premium adds 3.2–5.2 GB.
- **Proof:** setup ends with the self-test. Its result is bound to the exact
  engine version, lock and model checksums; any change invalidates it.
- **Uninstall** removes the venv and the models and offers to delete the old
  `data/local_realtime/` tree (several GB).

## 5. Decisions and alternatives

Each decision lists the recommended option first, then the runner-up and why it
loses.

1. **Engine: a Jarvis-owned cascade (recommended).** HF `speech-to-speech
   1.0.0` behind a new supervisor loses: untested on Windows, no Windows wheel
   for its fast TTS path, still one model copy per pipeline. Native
   speech-to-speech models lose: none is German-capable, small and
   cross-platform.
2. **Process: one child worker over stdin/stdout (recommended).** A loopback
   WebSocket server with a per-start token loses: a port brings back bind
   conflicts, firewall prompts and orphaned servers, three failures the old
   stack actually had, while the pipe ends the worker when Jarvis dies. An
   in-process engine loses on AP-24, P-41 and crash isolation.
3. **Real-time loop: our own, about 2,000 lines (recommended).** Pipecat 1.12
   (pinned) loses: its API changes roughly every two weeks, its frame and
   interruption model would again put third-party behaviour on our critical
   path, and it solves none of the hard parts here (install, readiness,
   memory). It stays a reference for turn and interruption handling.
4. **Speech on CPU by default, GPU by measurement (recommended).** GPU-first
   loses: ONNX Runtime GPU on Blackwell needs custom builds, CUDA 12 and 13
   split by GPU generation, and the GPU is the scarce resource the LLM needs.
5. **LLM through Ollama (recommended).** An in-worker LLM loses:
   `llama-cpp-python` ships no wheels, and the model would reload on every app
   restart.
6. **Provider id: new `local-voice` during the build, `local-realtime` aliased
   to it at cutover (recommended).** Reusing `local-realtime` from day one
   loses: old and new engines must run side by side for the bake-off.

## 6. Delivery plan

A phase is done when its gate passes, not when its code is merged. Day counts
are rough planning estimates.

| Phase | Content | Gate |
| --- | --- | --- |
| P0 Measurement bench (2–3 d) | Worker skeleton with a `bench` CLI that needs no Jarvis: de/en corpus with hesitations, numbers and tool requests; synthetic audio; every measurement in section 8 that needs no UI | Numbers for 8.1–8.5 and 8.7 on Windows NVIDIA, the maintainer's Mac and a CPU-only `python:3.11-slim` container; tier defaults chosen from them |
| P1 Engine core (4–6 d) | Protocol v1; VAD, turn and speculative STT; LLM streaming with tools; clause chunker; Piper and Pocket; barge-in; cancellation; self-test; metrics; logs | Standalone p50 ≤ 1.0 s on Windows NVIDIA (core tier); 500-turn soak without memory growth; kill drill leaves zero processes; boot with the network unplugged |
| P2 Jarvis integration (3–4 d) | `local-voice` adapter; `jarvis/live/native.py` fixes (4.4); card with setup progress, self-test, tier and expected latency; prepare-then-handover switch; Ollama coupling | 20 real calls, 0 silent failures; barge-in p95 ≤ 250 ms; GPT-Live to local and back without restart; Gemini Live regression tests green |
| P3 Installer and platforms (4–6 d) | uv venv, per-platform locks in CI, model store, uninstall; Mac and Linux runs | Fresh-install drill on Windows, macOS (real Apple Silicon) and the CPU container; Linux NVIDIA stays marked unverified until run on hardware |
| P4 Premium voice (3–5 d) | Qwen3-TTS on CUDA and MLX; free-memory governor; degrade path | First audio p50 ≤ 300 ms on 12–16 GB NVIDIA; ≥ 20 % headroom; an out-of-memory drill degrades instead of failing |
| P5 Cutover and removal (2–3 d) | Alias `local-realtime` to `local-voice`; delete the old stack (section 9); update `docs/os-parity.md` (P-28, P-40) and `docs/BUGS.md`; mark old docs superseded | CI gate green; no reference to `speech-to-speech` left outside history |

## 7. Release SLOs

| SLO | Target |
| --- | --- |
| Speech end to first audio | p50 ≤ 0.8 s, p95 ≤ 1.2 s on NVIDIA ≥ 8 GB and Mac Pro/Max; measured and shown elsewhere |
| Barge-in to silence | p95 ≤ 250 ms |
| Open session when ready | p95 ≤ 150 ms |
| Honest refusal when not ready | ≤ 1 s, spoken and visible |
| Ready after selection, warm disk | core ≤ 15 s, premium ≤ 60 s |
| Boot success, offline included | ≥ 99 % |
| Orphans after kill and crash drills | 0 |
| Worker crash to restarted | ≤ 5 s |
| Tool calls, curated set, de/en | ≥ 90 % right tool with valid arguments on the bake-off corpus |
| Accelerator headroom, premium, steady state | ≥ 20 % |

## 8. Bake-off protocol

1. **Latency per tier**, split into turn decision, STT final, LLM first token,
   first clause, TTS first audio and playback; p50/p95, warm, one device per
   class; de/en corpus with hesitations.
2. **Tool-call accuracy** in German and English with thinking off, on the real
   Jarvis catalog, at least 200 utterances per language: right tool, valid
   JSON, no invented results; direct set against discover-then-call;
   candidates from 4.6.
3. **Second-turn first-token time** with the prefix cache, Ollama against
   llama-server, 8K profile, hybrid and sliding-window models included.
4. **German TTS quality:** ASR round-trip error rate on numbers, dates,
   abbreviations and anglicisms; blind A/B with native speakers; first audio,
   real-time factor and underruns per device, for Qwen3-TTS 0.6B/1.7B, Pocket
   and Piper.
5. **Turn-taking:** cut-off rate and late-answer rate on German thinking
   pauses; Smart Turn thresholds; with and without a Settings pause.
6. **Barge-in** through each WebView's echo cancellation (WebView2, WKWebView,
   WebKitGTK): speech start to silence p95; false interruptions per minute from
   echo and noise.
7. **Memory:** steady-state and peak VRAM or unified memory with every component
   resident plus typical desktop load; cold start to ready; provider switch
   time.
8. **Soak:** 500 turns in German and English with interruptions, tool calls and
   injected out-of-memory and crash faults; leaks, orphans, recovery.

## 9. Keep, rewrite, delete

| Area | Lines | Fate |
| --- | --- | --- |
| `jarvis/realtime/local_server/` (supervisor 2,752, install 1,024, model_catalog 468, brain_link 336, boot_progress 309, preflight 241, patching 191, configure 176, tiers 129, smoke 119, `__init__` 25) with `patches/` and `pins/` | 5,770 | Delete at P5. Ideas kept: transactional model switch (configure), stage and ETA reporting (boot_progress), round-trip probe (smoke) |
| `LocalRealtimeProvider` in `jarvis/plugins/realtime/openai_realtime.py` | ~920 | Delete at P5; the cloud `OpenAIRealtimeProvider` and `_OpenAIRealtimeSession` stay |
| Managed-server routes in `jarvis/ui/web/provider_routes.py` | ~460 | Replace with `local-voice` setup, status and self-test routes; `realtime_switch` gets prepare-then-handover |
| `ManagedServerPanel` in `ProviderTierSection.tsx` and its types in `useProviders.ts` | ~1,080 | Replace with a smaller card that works in light and dark mode |
| Local-realtime writers in `jarvis/core/config_writer.py` | ~160 | Replace with a structured config block instead of a launch-command string |
| Tests: `tests/unit/realtime/local_server/`, `test_local_realtime.py`, `test_local_output.py`, `test_brain_link.py` | 4,248 + 1,344 | Delete with the code; new tests use `tests/fakes/` plus a real-engine smoke job |
| `jarvis/live/*`, `jarvis/realtime/{protocol,factory,audio}.py`, `/ws/audio`, browser client | — | Keep; `native.py` gets the fixes in 4.4 |
| `jarvis/brain/ollama_*`, `jarvis/local_models/autostart.py`, `jarvis/hardware/detection.py`, `jarvis/speech/local_models.py`, the Piper, Nemotron and faster-whisper plugins, `jarvis/core/process_tree.py` | — | Keep and reuse. `local_models/health_monitor.py` stops refreshing a stale voice block; `detection.py` gains free unified-memory reads |

Estimated new code: worker about 2,000–2,500 lines, adapter about 300,
installer about 600, card about 400, against about 8,400 lines of product code
removed.

## 10. Risks

1. **German tool calls of small models with thinking off are unmeasured.**
   Reliable tools may need an 8B+ model, which would leave 6–8 GB machines with
   weaker tools. Mitigation: bake-off first; a small curated tool set.
2. **Prefill on base Apple Silicon** (118–221 tokens/s for a 7B model on M1 to
   M4 base chips) puts 0.8 s out of reach there; the card shows the measured
   value instead.
3. **CUDA split:** Blackwell needs CUDA 12.8/13 or newer, and CUDA 13 dropped
   Maxwell, Pascal and Volta. That means two GPU lock lines, and the Windows
   CUDA torch wheel weighs about 2 GB.
4. **Prompt-cache regressions** for hybrid and sliding-window models in
   llama.cpp and Ollama would re-process the whole prompt on every turn; measure
   per runtime version.
5. **Echo cancellation for barge-in** is proven only in WebView2. WKWebView and
   WebKitGTK need measuring; the fallback is half duplex with a headphones hint.
6. **Licences and maintenance:** a third-party ONNX export of Pocket TTS is
   marked non-commercial (we use the torch package instead); Supertonic is
   archived. Every model is pinned by revision and checksum.
7. **GPU sharing** with background local work; mitigated by the call-time gate
   in 4.6.
8. **Parallel work on the old stack:** uncommitted changes to the old engine's
   timeouts and boot ETA exist in the shared tree while this plan is written.
   That work is superseded at P5.

## 11. Not verified yet

- Every Mac and Linux statement comes from documentation, wheel metadata or
  code reading; nothing was run there.
- End-to-end latency of the new stack is an estimate built from the stage
  measurements in the cited sources.
- The context-size estimate (8K) comes from reading the code, not from counted
  tokens.

## Sources

- Silero VAD: https://github.com/snakers4/silero-vad
- Smart Turn: https://github.com/pipecat-ai/smart-turn and
  https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/
- Parakeet TDT 0.6B v3: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3;
  sherpa-onnx export:
  https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html;
  onnx-asr benchmarks: https://istupakov.github.io/onnx-asr/benchmarks/
- Pocket TTS: https://github.com/kyutai-labs/pocket-tts
- Qwen3-TTS: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice;
  faster-qwen3-tts: https://github.com/andimarafioti/faster-qwen3-tts; MLX:
  https://github.com/Blaizzy/mlx-audio
- HF speech-to-speech 1.0.0: https://pypi.org/project/speech-to-speech/1.0.0/;
  shared-runtime design: https://github.com/huggingface/speech-to-speech/issues/363
- Pipecat: https://github.com/pipecat-ai/pipecat
- Ollama context length: https://docs.ollama.com/context-length
- llama.cpp throughput on Apple and CUDA:
  https://github.com/ggml-org/llama.cpp/discussions/4167 and
  https://github.com/ggml-org/llama.cpp/discussions/15013; prompt-cache issue:
  https://github.com/ggml-org/llama.cpp/issues/21831
- CUDA 13 architecture support:
  https://docs.nvidia.com/cuda/archive/13.0.1/cuda-toolkit-release-notes/index.html
