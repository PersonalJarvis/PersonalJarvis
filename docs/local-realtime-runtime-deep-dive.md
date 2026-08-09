# Local Realtime Runtime Deep Dive and Recovery Plan

**Date:** 2026-08-08

**Status:** Proposed; diagnosis and implementation plan only

**Scope:** Managed local realtime voice, its model stack, process lifecycle,
connection path, latency, resource planning, protocol boundary, and release
evidence.

## Executive decision

The local realtime feature is on the wrong production boundary, but not on the
wrong fundamental model architecture.

The deployed system is not a single native speech-to-speech model. It is a
cascade exposed through an OpenAI-Realtime-shaped WebSocket:

1. Silero VAD and turn detection;
2. Parakeet TDT STT on CPU;
3. a local Ollama brain on GPU — now `qwen3.5:4b`; the measurements below
   used the retired `qwen2.5:7b` baseline;
4. Qwen3-TTS on GPU;
5. a one-slot threaded pipeline in `speech-to-speech==0.2.12`.

A cascade remains the best default for this product today because it preserves
live transcription, deterministic tool calling, exact scripted speech, model
swaps, and auditability. LiveKit's current production guidance reaches the same
conclusion: use a cascade for most production agents, and use a native realtime
model when raw latency and expressiveness outweigh control. It also identifies
sub-one-second speech-end-to-response as the natural-conversation target
([pipeline comparison](https://docs.livekit.io/agents/models/pipelines/)).

The mistake is treating a patched, model-owning application server as if it
were a production runtime manager. Jarvis currently asks this process to be all
of the following at once: installer, model loader, WebSocket gateway, scheduler,
session pool, health signal, crash boundary, and restart unit. It does not have
the control plane required for those responsibilities.

**Recommendation:** keep the cascade and the OpenAI Realtime edge protocol, but
put both behind a Jarvis-owned local realtime runtime contract. The gateway must
start immediately, model workers must be shared and supervised, readiness must
mean a completed protocol/model probe, and provider switching must prepare the
new runtime before handing a live call to it. Keep Hugging Face
`speech-to-speech` as a replaceable engine during this work, not as the source of
truth for process state.

## Local model inventory and boundary assessment

Jarvis already has several distinct local-model surfaces. They should not be
treated as one interchangeable “local AI” process because they have different
latency, memory, isolation, and readiness contracts.

| Surface | Current local choices | Runtime boundary | Audit assessment |
| --- | --- | --- | --- |
| Brain | Ollama, or any OpenAI-compatible local server such as llama.cpp, LM Studio, vLLM, TGI, or Transformers Serve | External HTTP process | Correct replaceable-provider shape, but Ollama context/residency must join the shared VRAM budget. |
| Pipeline STT | Faster-Whisper `large-v3`; Nemotron 3.5 streaming through sherpa-onnx | Jarvis local voice runtime | Has fail-closed engine-plus-weights readiness and language-aware model inventory. This is the stronger readiness pattern. |
| Pipeline TTS | Piper voices through sherpa-onnx for German, English, and Spanish | Jarvis local voice runtime | Small, predictable, CPU-capable fallback; lower naturalness but valuable as an always-available local safety path. |
| Wake/verification | Separate lightweight and upgraded Whisper/Vosk/openWakeWord paths | Dedicated wake lifecycle | Correctly separate from utterance STT; must not share mutable native inference instances. |
| Managed realtime | Parakeet TDT + Ollama + Qwen3-TTS inside a dedicated managed virtual environment | Third-party threaded server plus Jarvis wrapper supervisor | Duplicates model discovery, readiness, lifecycle, and resource ownership instead of using one runtime contract. This is the unstable boundary. |

The pipeline providers determine readiness from two independent facts—runtime
importability and complete model presence—and fail closed when either is
unknown (`jarvis/speech/local_models.py:1-34` and `64-87`). Managed realtime
does not reuse that contract; its install marker and TCP probe form a second,
weaker definition of “ready”. The long-term runtime should generalize the
existing fail-closed local-model contract to all engines while keeping native
model execution in isolated workers. It should not move CUDA/ONNX models into
the main Jarvis process.

This also identifies the safest cold-start fallback. Piper plus an already
available local STT and brain may be less expressive than Qwen3-TTS, but it can
offer an immediate, fully local conversation while the premium realtime worker
warms. The fallback decision remains capability- and policy-driven; a missing
component must degrade honestly rather than being assumed present.

## What was measured

The audit used the current configuration and live logs on one 16 GB NVIDIA
development system. Values below are observations, not estimates.

| Signal | Observed result | Interpretation |
| --- | ---: | --- |
| Cold session start to ready | 80.4 s in one trace | The user waits through model loading and repeated connection attempts. |
| Other cold server boots | 42.6-75 s | Cold time varies with imports, STT load, Ollama residency, TTS load, and CUDA graph capture. |
| Warm local session ready | commonly 2.3-3.4 s | Still far too slow for a loopback WebSocket. |
| Raw WebSocket via `localhost` | about 2.06 s | IPv6 is attempted while the server accepts IPv4, then falls back. |
| Raw WebSocket via `127.0.0.1` | about 2.4 ms | The transport itself is effectively instant. |
| SDK session via `localhost` | about 2.58 s | SDK/import cost plus the same address-family delay. |
| Warm SDK session via `127.0.0.1` | about 3 ms | Confirms that seconds of “connect” time are avoidable client overhead. |
| Failed retry interval | about 8.4 s | Failed `/v1/models`, failed WebSocket, then a two-second sleep. |
| Warm final STT | 0.58-1.44 s | STT plus turn finalization already consumes much of a one-second budget. |
| Warm TTS TTFA | 0.25-0.45 s | TTS is acceptable once resident. |
| TTS startup warm-up | 5.1-7.3 s | It is a material part of cold start. |
| Server capacity | one pipeline slot | A close/reconnect race produced “all 1 pipeline slots in use”. |
| GPU snapshot | 15.4 of 16.3 GB used | Only about 0.6 GB remained; there was no safe fault or context-growth margin. |
| Managed server trees | two complete trees | One tree had failed its port bind but retained loaded models indefinitely. |

`RT-SPAWN first_audio` ranged from roughly 12 to 58 seconds in local traces,
with an older 67-second sample. That metric includes user/session behavior and
is not a clean speech-end latency measurement. The lack of stage-normalized
latency histograms is itself an observability defect; the logs cannot currently
distinguish listening time, turn detection, STT, LLM, TTS, queueing, and mission
work reliably at p50/p95/p99.

## Root-cause analysis

### 1. Heavy initialization happens before the port exists

The pinned server constructs handlers and synchronously loads/warm-ups models
before Uvicorn binds. A healthy cold process is therefore indistinguishable
from a dead process for 40-80 seconds. Extending client retries to 120 seconds
does not solve this; it turns infrastructure warm-up into a blocked call.

The Jarvis provider explicitly encodes that wait in
`jarvis/plugins/realtime/openai_realtime.py:1243-1261`, then retries the entire
open path in `1631-1676`.

### 2. Reachability, liveness, readiness, and ownership are conflated

`supervisor.status()` calls a TCP port probe, and `ensure_running()` returns
`already-running` for either any listener or a recorded parent process
(`jarvis/realtime/local_server/supervisor.py:224-267`). Neither answer proves
that the Realtime protocol works, a pipeline slot is available, or STT/LLM/TTS
are ready.

The install's `ready` state only means files, patch, and smoke marker exist
(`install.py:210-248`). The smoke test stops when a TCP port opens
(`install.py:370-432`); it never creates a realtime session, sends audio, or
receives audio. `/v1/models` is not implemented by the pinned server and returns
404, while the client calls it on every unconfigured-model attempt
(`openai_realtime.py:1453-1488`).

### 3. Process ownership is incomplete and a bind failure becomes a zombie

The live failure was deterministic:

1. a second complete stack loaded STT, LLM, and TTS;
2. Uvicorn failed to bind with Windows error 10048;
3. only the server thread exited;
4. handler threads kept the process and models alive;
5. a later valid server created two resident stacks and nearly exhausted VRAM.

Jarvis records one wrapper PID. `stop()` returns immediately after killing that
verified tree and only scans the managed install root when no owned PID is live
(`supervisor.py:347-369`). On Windows `_kill_pid_tree()` reports success without
checking `taskkill`'s result (`372-408`). The existing unit test explicitly
expects only the recorded PID to be targeted
(`tests/unit/realtime/local_server/test_supervisor.py:187-201`); there is no
test for a surviving child, port-bind failure, or duplicate managed tree.

There is also no continuous supervisor. Start/revive is called by prewarm,
connect, or a REST action; nothing owns a heartbeat, crash budget, persisted
backoff, or automatic failover after that call returns.

### 4. `localhost` adds seconds to every attempt

The managed card stores `http://localhost:8765`, and URL normalization maps
`0.0.0.0` back to `localhost` (`openai_realtime.py:1316-1346`). The server is
IPv4-only in this deployment. On this Windows host, `localhost` first follows a
non-serving address-family path; `127.0.0.1` connects in milliseconds. This
single defect explains almost all warm “connecting” time and multiplies every
cold retry.

### 5. Provider selection races model preparation

Boot warming waits for voice usability, adds a delay, and warms only providers
that were selected at that moment (`jarvis/ui/desktop_app.py:2941-3004` and
`jarvis/realtime/factory.py:220-253`). Selecting local realtime later persists
the choice and immediately reconnects the active call
(`provider_routes.py:4408-4454`); it does not first start the server and wait for
full readiness. The user therefore becomes the warm-up probe. The separate
managed “start” route returns after spawn and brain ping, not after realtime
readiness (`provider_routes.py:2748-2775`).

### 6. The resource model measures labels, not the active workload

The preflight selects a tier from total accelerator memory, not free/reservable
memory under the proposed combination. Brain fit uses Ollama's on-disk model
size plus a fixed 6 GB allowance and permits unknown sizes
(`brain_link.py:64-108`). It does not budget TTS weights, CUDA graphs, KV cache,
driver allocations, another process, or transient peaks.

The current 16 GB profile ran Ollama at a 32,768-token context and left roughly
0.6 GB free. Ollama's own documentation says larger contexts increase memory
use and defaults sub-24-GB GPUs to 4K
([context guidance](https://docs.ollama.com/context-length)). A voice turn does
not justify a global 32K default without a measured requirement.

The tier table correctly marks larger target classes as unmeasured, but it
still describes a future 30B-class target for 16 GB
(`tiers.py:53-103`). That cannot be promised while the present 7B brain plus TTS
already saturates the device.

### 7. One pipeline slot is a single global failure domain

The installer hardcodes `--num_pipelines 1` and a Torch Qwen3-TTS backend
(`install.py:251-281`). A slow reclaim, stale session, reconnect overlap, or
handler failure therefore rejects every new user. Simply changing the count to
two is unsafe because the pinned implementation constructs another set of model
handlers, multiplying memory rather than sharing inference runtimes.

The Hugging Face maintainers have now approved an architecture that separates
pipeline capacity from model replicas, adds bounded admission, shared STT/TTS
runtimes, liveness/readiness/metrics, capability negotiation, deadlines, and
quarantine semantics. It is an open implementation plan, not something this
deployment can assume is shipped
([upstream design issue](https://github.com/huggingface/speech-to-speech/issues/363)).
Its direction independently validates this audit.

### 8. The pinned protocol and dependency surface have drifted

Jarvis pins `speech-to-speech==0.2.12` and patches its service code
(`patching.py:3-30`). The current upstream interface has moved to `serve`, binds
to `127.0.0.1` by default, uses a GGML Qwen3-TTS path by default on non-macOS,
and deprecates `--mode`
([current upstream README](https://github.com/huggingface/speech-to-speech)).
The managed command remains on the deprecated mode, all-interface bind, and
Torch/CUDA-graph backend. An upgrade may materially improve startup and memory,
but must pass the full bake-off; replacing one unverified pin with `main` is not
a reliability strategy.

Only selected top-level packages are pinned. Native audio, CUDA, Torch,
torchaudio, Transformers, WebSocket, OpenAI SDK, and Uvicorn compatibility is
not proven as one lock set. The running environment also emits avoidable
startup work such as an NLTK download check.

### 9. The local endpoint is exposed more broadly than intended

The running server binds `0.0.0.0` and has no application authentication. That
turns a keyless local voice endpoint into a LAN-visible service. The managed
default must be `127.0.0.1`; remote access must be an explicit authenticated,
TLS-protected configuration. Current upstream now follows this safer loopback
default.

### 10. Tests prove components, not the user experience

The existing tests are useful unit and route tests, but they mostly replace
processes and network calls. There is no release gate that boots the real pinned
environment, verifies a unique process tree, completes an audio round-trip,
checks German and English, measures SLOs, forces an OOM/bind conflict/worker
crash, or runs a long reconnect soak. A green suite can therefore coexist with
the exact failures reported by the user.

## Target architecture

### A. A lightweight Jarvis-owned control gateway

Start a small gateway before heavy models, without putting model initialization
on the application boot critical path. It binds loopback quickly and exposes:

- `GET /health/live`: gateway event loop and watchdog are alive;
- `GET /health/ready`: required workers are warm, protocol-compatible, within
  memory budget, and have usable capacity;
- `GET /v1/models`: protocol version, model revisions, audio formats,
  languages, voices, tool support, and capacity;
- `GET /metrics`: stage latency, queueing, capacity, restarts, memory, and
  terminal outcomes;
- the Realtime WebSocket only when admission can succeed.

Use an explicit state machine:

`STOPPED -> STARTING(stage, progress, ETA) -> WARM -> BUSY -> DEGRADED -> CRASHED`

TCP-open is never a state transition. A ready transition requires a real
session handshake plus a short synthetic STT/LLM/TTS probe, cached by exact
engine/model/dependency fingerprint.

### B. Shared, isolated model workers

Run STT, LLM, and TTS as separately supervised worker processes behind bounded
request interfaces. Pipeline sessions share one loaded model runtime instead of
duplicating it. The gateway owns sessions, ordering, cancellation, tools, turn
state, and protocol events; workers own model execution only.

On Windows, put the full tree in a Job Object with kill-on-close. On POSIX, use
a dedicated process group. Maintain an atomic instance lease containing parent
and descendant identities, creation times, executable fingerprints, and model
fingerprints. On any gateway bind failure, parent death, or shutdown, terminate
the whole owned tree and verify exit. A periodic watchdog detects stuck workers
and moves readiness to false before attempting a bounded restart.

This design contains native CUDA/ONNX failures: a TTS crash can restart TTS
without silently orphaning the gateway or creating another complete model
stack.

### C. Prepare-then-handover session UX

Provider selection becomes a two-phase operation:

1. prepare the candidate runtime and show stage/progress;
2. switch the active provider only after its readiness contract passes.

An active call remains on its current provider until the new one is ready. If a
local runtime is cold at call time, the call must not wait 40-120 seconds. Use
an explicitly configured fallback policy: preferably the existing fully local
classic pipeline, otherwise a user-approved hosted provider. Never introduce a
silent billed fallback. A local-only user sees an honest warming state and ETA,
not a false connected state.

Once resident, provider connection should be only a loopback handshake; no
model discovery HTTP request, process launch, dependency import, or model load
belongs in that path.

### D. A runtime resource governor

Before loading, reserve a measured budget for every selected component and
transient peak. During operation, sample actual accelerator residency, system
RAM, model context, queue size, and worker health. Refuse an unsafe combination
before OOM, with an actionable recommendation.

For the 16 GB tier, immediately benchmark a 4K-8K voice context, current
upstream's GGML Qwen3-TTS backend, and lighter TTS options. Require at least 20%
VRAM headroom after steady-state warm-up unless a platform-specific study
proves a lower bound safe. Higher model tiers remain unavailable until measured
on their target hardware.

### E. A replaceable engine adapter

Do not couple Jarvis state to one upstream server's process or endpoint quirks.
Define a `LocalRealtimeEngine` contract for install fingerprint, capabilities,
start, ready probe, open session, metrics, drain, and stop. The current Hugging
Face cascade is adapter one. This permits a controlled upstream upgrade or an
engine replacement without rewriting provider/session UX.

Pipecat and LiveKit are useful references for turn handling, metrics, transport,
and fallback, but neither removes the need for local model supervision. A
framework swap alone would not fix duplicate processes, VRAM admission, or
readiness.

## Model and server options

| Candidate | Decision | Reason |
| --- | --- | --- |
| Optimized cascade behind the new runtime | **Primary path** | Best German/tool/auditability fit; smallest migration; component-level bake-offs remain possible. |
| Current Hugging Face `speech-to-speech` engine | **Upgrade candidate, not blind replacement** | Upstream has safer loopback defaults, new CLI/backend choices, and an aligned coordinator design, but the latter is not fully shipped. |
| Kyutai Unmute | **Bake-off candidate** | Strong streaming cascade and OpenAI-Realtime-like protocol, but requires at least 16 GB and officially supports Linux/WSL rather than native Windows or macOS ([official repository](https://github.com/kyutai-labs/unmute)). |
| Moshi | **Reject as default for this tier** | Excellent full-duplex latency, but official PyTorch guidance calls for substantial/24 GB-class memory and lacks native Windows support ([official repository](https://github.com/kyutai-labs/moshi)). |
| Qwen3-Omni 30B-A3B | **Research tier only** | German speech input/output is attractive, but official BF16 minimums are roughly 79 GB for the full instruct model, far beyond 16 GB ([official repository](https://github.com/QwenLM/Qwen3-Omni)). |
| MiniCPM-o 4.5 | **English/Chinese experiment only** | It can run on lower-resource GPUs, but realtime speech is bilingual English/Chinese and the project documents unstable/mixed speech limitations; it does not satisfy the German product path ([official repository](https://github.com/OpenBMB/MiniCPM-V)). |

The engine winner must be selected by measured German/English quality,
tool-call correctness, cancellation, barge-in, latency, memory, crash recovery,
and OS parity—not by demo smoothness.

## Delivery plan

### P0 — Contain the current failure (1-2 engineering days)

1. Make the managed endpoint and client use `127.0.0.1`; bind loopback only.
2. Add a cross-process unique-instance lease before any model load.
3. Treat bind/server-thread failure as fatal to the entire owned tree.
4. Make stop sweep and verify all descendants from the managed install, even
   after the recorded parent was killed; check Windows termination results.
5. Replace TCP readiness with a protocol/pool probe; stop calling unsupported
   `/v1/models` or implement a versioned response.
6. Rotate logs and persist bounded restart/crash state.
7. Reduce Ollama voice context to a measured 4K-8K profile and require safe
   steady-state headroom.
8. Change provider selection to start preparation without tearing down the
   current session.

**Exit gate:** 100 warm connects with p95 below 150 ms; one managed process
tree; no LAN listener; bind-conflict drill leaves zero model-owning orphans.

### P1 — Build the control plane (3-5 engineering days)

1. Introduce the runtime state machine, `/health/live`, `/health/ready`,
   capability versioning, and stage progress.
2. Add Job Object/process-group ownership, atomic metadata, watchdog heartbeats,
   persisted exponential backoff, and a restart budget.
3. Make install smoke complete a real WebSocket/audio round-trip and fingerprint
   the full dependency/model set.
4. Emit structured metrics for connect, queue, VAD, STT, LLM first token, TTS
   first audio, cancellation, reclaim, memory, and restart.

**Exit gate:** every accepted session reaches an explicit terminal outcome;
unready capacity rejects within 100 ms; crash detection is under one second.

### P2 — Remove cold start from the call path (3-5 engineering days)

1. Implement prepare-then-handover provider switching.
2. Start the lightweight gateway immediately and warm selected heavy workers in
   the background.
3. Add explicit fully-local/hosted fallback policy and atomic recovery handover.
4. Keep workers resident under a configurable, actually-read idle policy; show
   warm-up stage and ETA when local-only mode cannot yet accept a call.

**Exit gate:** a user can begin a usable call immediately during a cold worker
start; a resident local provider becomes active in under 500 ms.

### P3 — Shared inference and latency bake-off (5-10 engineering days)

1. Separate pipeline slots from model replicas with bounded, fair scheduling.
2. Benchmark current pin versus a reviewed upstream revision and GGML/Torch TTS
   variants; benchmark suitable local STT/TTS alternatives.
3. Tune turn detection, partial transcription, preemptive LLM generation,
   streaming text-to-TTS chunking, context size, and cancellation.
4. Run the same German/English corpus across 12/16/32/64/128 GB tiers. Publish
   quality, p50/p95/p99, RTF, peak memory, and failure results for each tier.

**Exit gate:** on the 16 GB reference tier, speech-end-to-first-audio p50 is at
most 800 ms and p95 at most 1.2 s, with at least 20% steady-state VRAM headroom.
If the quality target makes this physically impossible, publish the measured
trade-off and move that model profile to a higher tier.

### P4 — Production reliability gate (3-5 engineering days)

Automate cold boot, warm boot, app restart, provider switch, rapid reconnect,
port conflict, parent death, child death, GPU OOM, Ollama eviction, stuck slot,
cancel/barge-in, missing model, damaged install, and dependency mismatch. Run a
500-turn multilingual soak, 100 consecutive connection cycles, and repeated
fault injection on Windows, macOS, Linux, and headless Linux.

**Release gate:** zero orphan processes, zero silent terminal losses, bounded
memory/queues/logs, p99 warm connect below 250 ms, automatic degradation within
two seconds, and recovery without application restart.

## Required SLO dashboard

| SLO | Target |
| --- | ---: |
| Resident loopback connection | p50 <= 50 ms, p95 <= 150 ms, p99 <= 250 ms |
| Prepared-provider activation | p95 <= 500 ms |
| Speech end to first audio | p50 <= 800 ms, p95 <= 1.2 s |
| Barge-in to audio stop | p95 <= 250 ms |
| Saturation/unready rejection | p95 <= 100 ms |
| Worker crash detection | p95 <= 1 s |
| Usable fallback after failure | p95 <= 2 s |
| Model-worker recovery | <= 60 s, never blocking an existing fallback call |
| Steady-state accelerator headroom | >= 20% on each certified profile |
| Process integrity | exactly one owned tree; zero orphans after every drill |

## What not to do

- Do not increase the 120-second retry window.
- Do not call a TCP-open port “ready”.
- Do not add a second pipeline while it duplicates model instances.
- Do not load heavy models synchronously on first call or application boot.
- Do not switch frameworks before fixing ownership, readiness, and resource
  admission; those defects would follow the migration.
- Do not promise a 30B or native omni model on 16 GB before a measured,
  multilingual, tool-capable profile passes the same gates.
- Do not silently fail over to a billed provider.

## Final acceptance statement

The work is complete only when “connect” means a short local protocol handshake,
not process launch; when cold models warm outside the user's call; when every
owned child is killed or recovered deterministically; and when the certified
hardware tiers have published quality, memory, latency, and fault-injection
evidence. Until those conditions hold, the managed local realtime card should
be presented as experimental rather than API-grade.
