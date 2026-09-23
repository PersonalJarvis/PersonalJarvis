# Local voice: model-first redesign

Assessment date: 2026-09-23. Status: diagnosis, model acquisition and a native
pipe-worker prototype implemented. The application migration and full voice
qualification are **not complete**.

Scope: local reasoning, speech input/output, wake activation, realtime voice,
model installation, custom models, hardware selection, and tool execution.
The intended replacement is a T3 contract change. The initial Apple Silicon
classification fix is T2: simulated macOS/Metal coverage, existing Windows
and Linux detection retained.

## Product contract

The requested experience is: choose a local realtime model, download it in the
app, and speak after the configured wake phrase. Users should not configure a
server, port, launch command, Python environment, or separate tool model.
Jarvis must retain its existing permission and ToolExecutor execution boundary.

There are two different interpretations of "one model": one native
audio-input/audio-output checkpoint, or one user-visible voice package that
may contain separate speech and reasoning components. They are not equivalent.
The request favors the native interpretation; adopting a cascade as the default
requires resolving the language/tool tradeoff with the user.

No finite model can guarantee realtime latency on every device. Cross-platform
support means capability-based selection, tested resource requirements,
CPU operation when supported, and an actionable unsupported state otherwise.
It must not mean claiming that every model runs on every machine.

## Current local surfaces

| Surface | Code boundary | What it actually does |
| --- | --- | --- |
| Local reasoning | `jarvis/plugins/brain/ollama.py`, `local_openai.py` | Tool-capable text reasoning through local HTTP providers; Ollama owns text-model downloads. |
| Dictation and pipeline STT | `jarvis/plugins/stt/fwhisper.py`, `nemotron_local.py`, `jarvis/speech/local_models.py` | Local transcription with separate runtime/weight checks. Dictation remains a separate product surface from voice conversation. |
| Pipeline speech output | `jarvis/plugins/tts/piper_local.py` | Local voice bundles through sherpa-onnx; separate from managed realtime TTS. |
| Wake activation | `jarvis/plugins/wake/openwakeword_provider.py`, `jarvis/speech/pipeline.py` | Lightweight activation and verification; must not share a mutable native inference instance with conversation STT. |
| Realtime adapter | `LocalRealtimeProvider` in `jarvis/plugins/realtime/openai_realtime.py` | OpenAI-shaped WebSocket client with managed-process revival, warm-up and readiness probes. |
| Managed voice engine | `jarvis/realtime/local_server/` | Separate venv, pinned third-party server, vendored patches, speech models, Ollama reasoning, ownership/leases, readiness and recovery. |
| Model management UI | `LocalModelsView.tsx`, `ProviderTierSection.tsx`, `local_models_routes.py`, `provider_routes.py` | Ollama inventory and a separate managed-server panel. Voice selection currently means selecting a brain and a TTS profile. |
| Residency | `jarvis/local_models/autostart.py`, `local_server/supervisor.py`, `realtime/factory.py` | Multiple entry points coordinate warm-up, Ollama residency and idle release. |

The managed engine is a **cascade**, not a native realtime model:
Parakeet transcription -> Ollama reasoning -> Qwen3-TTS or Pocket TTS.
`model_catalog.py` lists speech-output models; those entries cannot hear audio,
reason and call tools by themselves. Changing a TTS checkpoint cannot satisfy
the requested native-model replacement.

## Findings verified against current code

1. **Apple Silicon can lose eligibility after Ollama starts.**
   `_probe_accelerator_gb()` returned the generic `ollama-runtime` source when
   Ollama supplied a Metal memory budget. `_usable_accelerator_gb()` only
   accepts `nvidia-smi` and `apple-unified`, reducing that supported Mac to
   `(0, "none")`. The fix retains the Apple family while using Ollama's budget.
   Both the detector and its realtime-preflight consumer failed before the fix.
2. **A global 12 GiB accelerator floor prevents smaller/CPU runtimes.**
   `tiers.py` blocks below 12 GiB before model-specific capabilities are considered.
   All five current tiers select the same `qwen3.5:4b` brain and explicitly have
   no completed voice bake-off. The tier labels are not measured performance.
3. **Installation is tied to a particular application server.**
   `install.py` owns venv creation, Torch flavor, core packages, two source patches,
   smoke boot and a persisted command. `configure.py` edits command flags to
   switch models and restarts the whole stack. A runtime should consume typed
   configuration instead of recovering its configuration from shell strings.
4. **Installed is different from ready to hear a wake phrase.**
   `server_status().ready` describes install artifacts and historical smoke proof;
   `supervisor.status().ready/available` describe a live pool. A read-only check
   during this investigation found an installed/smoke-proven engine but no live
   listener. The active voice provider was different, so this observation is
   not evidence of a crash. The user interface needs separate states.
5. **Instant wake and idle unloading conflict.**
   The runtime supports configurable idle release. A model that has been
   unloaded must be loaded again; it cannot truthfully be marked wake-ready.
   The replacement must make the residency policy explicit and must not lose
   the user's first utterance while preparing a selected engine.
6. **There is already valuable lifecycle protection to retain.**
   The supervisor has a cross-process lease, generation-bound ownership,
   readiness probes, crash backoff and transactional model replacement. Older
   assessments describe defects subsequently fixed here; do not rewrite those
   historical claims as present-day findings.
7. **A local engine can currently resolve to cloud reasoning.**
   `brain_link.resolve_brain()` offers OpenAI reasoning if Ollama is unavailable.
   The installer has an explicit brain-kind confirmation, so this is not proof
   of an unannounced cloud switch. The new fully-local profile must nonetheless
   reject remote reasoning rather than treating it as a successful local setup.
8. **Memory figures need runtime evidence.**
   Total accelerator memory alone does not prove free capacity, working kernels,
   sufficient system RAM or realtime throughput. The reference NVIDIA host was
   heavily occupied at inspection time; a new model benchmark must use a
   controlled resource state and must not terminate unrelated GPU applications.

## Native-model feasibility, checked against upstream sources

These are candidates, not a list of Jarvis-supported models. Upstream claims
are not Jarvis end-to-end acceptance evidence.

| Candidate | Relevant capability | Constraint before making it the default |
| --- | --- | --- |
| MiniCPM-o 4.5 / llama.cpp-omni | Native full-duplex audio; GGUF runtime advertises Windows, Linux and macOS with CUDA/Metal paths. | Model documentation specifies English/Chinese speech. The inspected omni server exposes custom prefill/decode endpoints; no structured tool-call endpoint was found in that server file. German speech and Jarvis tool/result handling are unqualified. |
| LFM2.5-Audio-1.5B | Small native audio model, interleaved output, GGUF CPU path. | Model card specifies English. A reliable Jarvis tool/result protocol and interrupt semantics still need proof. Small size alone is not proof of full duplex. |
| PersonaPlex 7B | Full-duplex speech and configurable persona. | Upstream has an open tool-support request. Do not equate spoken intent with an executable function call. |
| Qwen3-Omni | Multilingual native audio candidate. | vLLM-Omni tracks realtime tool calling, turn handling and interruption as integration work. Backend availability and memory requirements need separate Apple/NVIDIA/CPU evaluation. |

Sources inspected:

- [MiniCPM-o official model documentation](https://github.com/OpenBMB/MiniCPM-V#minicpm-o-45)
- [llama.cpp-omni runtime](https://github.com/tc-mb/llama.cpp-omni)
- [Inspected omni server revision](https://github.com/tc-mb/llama.cpp-omni/blob/64d092c60db4b4ee45768476bd752f03fdcc98ea/tools/server/server-omni.cpp)
- [LFM2.5-Audio model card](https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B)
- [PersonaPlex tool-support request](https://github.com/NVIDIA/personaplex/issues/93)
- [Qwen3-Omni realtime integration tracker](https://github.com/vllm-project/vllm-omni/issues/7055)
- [Current speech-to-speech upstream](https://github.com/huggingface/speech-to-speech)

Do not replace the pinned server with upstream 1.0 merely because it is newer.
Its changed dependencies and protocol still require adapter contract tests.
Likewise, different Torch and TorchAudio version numbers are not by themselves
a defect: [TorchAudio 2.11 documents a stable ABI](https://docs.pytorch.org/audio/stable/installation.html)
supporting later Torch releases.

## Replacement architecture

The following boundaries apply to either product interpretation:

- **One local-voice selection.** Pick a voice model/package and optionally an
  advanced custom source. Language, download size, hardware fit and tested
  capabilities are shown before download. TTS-only models are labeled as such.
- **Typed model manifest.** Identity, immutable artifact revision/digests,
  adapter family, audio formats, supported languages, memory estimates,
  tool/result and interruption support. A custom GGUF/Hugging Face ID is
  accepted only by an adapter that understands its architecture. No arbitrary
  commands or automatic remote model-code execution.
- **Application-owned runtime.** Jarvis installs and starts a bundled engine
  worker. There is no server configuration in the normal workflow. A private
  IPC channel or app-managed loopback transport is an implementation detail;
  downloading model weights alone does not eliminate the need for inference.
- **One lifecycle owner.** Explicit missing/downloading/verifying/loading/
  ready/busy/recovering/error states, generation-bound leases, one model session
  owner, cancellation and verified process cleanup. Readiness follows inference
  and protocol probes, not an open port or a previous success file.
- **Wake-ready residency.** Warm the selected engine after app boot, outside
  the boot critical path. Keep it resident while wake-ready mode is selected.
  An optional memory-saving policy honestly reports cold readiness. Wake
  detection stays lightweight and independent of the conversation engine.
- **Existing tool boundary.** Model outputs normalized structured tool requests;
  Jarvis validates/authorizes them through ToolExecutor and returns results to
  the same conversation. Speech text is never parsed as an implicit command.
  Pending confirmations, cancellation, exactly-once effects and unknown
  completion states survive recovery without replaying uncertain operations.
- **Hardware selection per adapter.** Probe actual runtime availability and
  inference, select CUDA, Metal/MLX or CPU, reserve memory headroom, then measure.
  Never select MPS for an arbitrary non-NVIDIA source. Never sum separate GPUs
  unless the chosen engine supports distributing this model across them.
- **Transactional migration.** Keep the existing selection usable until the
  replacement passes a real audio/tool round-trip. Preserve external endpoints
  as an expert path. Failed downloads/model changes retain prior configuration
  and conversation; the old managed installation is removed only explicitly.

Fine-tuning weights is a separate experiment requiring a suitable dataset,
license, training budget and held-out tool/speech evaluation. Runtime tuning
and prompt changes must not be described as having fine-tuned a model.

## Acceptance and execution waves

| Wave | Acceptance | Current state |
| --- | --- | --- |
| 1: Diagnosis | Reproducible findings, local-surface map and upstream feasibility assessment; regression for Mac rejection. | Initial assessment and Mac fix complete. |
| 2: Runtime contract | Typed model/adapter selection; invalid or incompatible custom weights rejected; CPU/Metal/CUDA contract tests; no boot-time heavy imports. | Manifest, eligibility, verified acquisition/import, inspection CLI and resident process controller implemented. Application wiring and runtime distribution remain pending. |
| 3: Inference integration | Real audio in/out, tool request/result, interrupt, cancellation and two-turn context through the selected native model or agreed package. | Windows CPU pipe-worker proof covers synthetic English audio, context and cancellation. Tool execution and application integration remain pending. |
| 4: App workflow | Choose/download/use/customize without server setup; progress/cancel/retry; settings migration and rollback; light/dark browser verification and frontend build. | Preview package library in Local models: catalog, download, verification, cancellation and custom manifest import. Voice activation, runtime installation and configuration migration remain pending. |
| 5: Wake and qualification | Cold app launch, first utterance preserved, warm wake latency measured, long-session recovery, real NVIDIA and Apple Silicon, CPU/headless base install and existing provider regression checks. | Pending; no Apple Silicon execution environment has been established. |

Terminal evidence must include targeted pytest and `tests/contract/` checks,
frontend Vitest/build, the boot-budget check when startup changes, and recorded
audio/tool scenarios. A fake engine passing the contract proves orchestration,
not native-model accuracy, German output or device performance. Report every
hardware cell as measured, simulated, unavailable or unsupported. The overall
goal remains open until required acceptance is met or its scope is explicitly
changed.

## First verification result

`tests/unit/hardware/test_accelerator_probe.py` first reproduced two failures:
Metal budget classification and its realtime-preflight consequence. After the
fix, that suite plus `tests/unit/realtime/local_server/test_engine.py` produced
88 passed and one skipped. Ruff passed for the changed code and tests.
This is simulated cross-platform regression evidence on a Windows host, not
a real Apple Silicon voice test or qualification of the replacement runtime.

## Native package foundation

`jarvis/realtime/local_runtime/` now contains the replacement's data-only model
contract, adapter/memory eligibility checks, and verified model acquisition.
It accepts pinned Hugging Face artifacts and own local weights; neither source
can provide a shell command or executable model code. The package fingerprint
includes artifact hashes, language/capability claims and memory profiles.

Acquisition uses a cross-process lock, separate content-addressed directories,
temporary files, size/SHA-256 checks and atomic publication. Cancellation and
failed model changes preserve previously verified versions. Retries reuse
verified files, rather than redownloading the entire package. The returned
weights still need real inference and tool qualification before activation.

The first catalog entry is an **experimental candidate**, LFM2.5 Audio Q4, using
four official GGUF files from the immutable Hub revision
`7d525f883a077e20afb782f2ff618edcae0e39e4`. Its declared language is English; tool
calling, full duplex and measured hardware memory profiles are deliberately
absent. It cannot satisfy the default Jarvis voice requirements yet. The
[upstream runner integration](https://github.com/ggml-org/llama.cpp/pull/18641)
was still an unmerged draft when checked; the official model repository lists
Mac/Linux/Android runners but no Windows runner. A packaged Windows engine
must be built and verified before advertising Windows inference support.

Developer inspection commands, run from the repository root:

```text
python -m jarvis.realtime.local_runtime catalog
python -m jarvis.realtime.local_runtime schema
python -m jarvis.realtime.local_runtime inspect lfm2.5-audio-1.5b-q4
python -m jarvis.realtime.local_runtime acquire MODEL.json --store MODEL_STORE --source OWN_WEIGHTS
```

These commands are the implementation/verification surface for the new package
layer, not the finished user workflow. In-app selection, download progress,
runtime installation, wake activation and tools remain required work.

A real acquisition of the catalog candidate completed on the Windows test host:
all four GGUF files matched their published sizes and SHA-256 digests. The CLI
reported `package_verified: true` and `runtime_qualified: false`. This proves
the download/verification path, not inference, speech quality or tool accuracy.

The combined new package/selection/acquisition contracts and existing hardware
and installer regressions passed with 149 tests and two skips. The skips are
the host's unavailable symlink-creation capability and an existing optional
installer test. Ruff and focused mypy checks passed. No native audio test,
frontend build, fresh-install qualification or physical Mac/Linux test was
performed for this package-only stage.

## Native inference prototype

The new C++ `native/worker.cpp` links the pinned LFM runner directly and keeps
one model resident. The parent communicates over stdin/stdout; there is no HTTP
server to configure. Requests have generation IDs, one in-flight inference slot,
explicit cancellation and terminal events. Aborting a generation invalidates
its context, and the next turn must reset. The worker never executes tools.

The build produced a native Windows x64 CPU executable. A real run through
`scripts/verify_local_native_audio.py` returned `LOCAL_NATIVE_AUDIO_OK`:

- The model generated an English question as audio, consumed that WAV and
  answered the arithmetic question with matching text and PCM.
- A subsequent conversation retained the requested color across two turns.
- Cancelling an active response produced `cancelled`; continuation with stale
  context was refused, and a reset conversation produced speech again.
- The owned process shut down and was reaped. The verifier checks that it opens
  no network listener and records the runner SHA-256 with the evidence.

The public, path-free record is
[`reports/local-native-audio-2026-09-23.json`](reports/local-native-audio-2026-09-23.json).
These are individual synthetic CPU observations with cached model files, not a
PC cold-start benchmark, a microphone/wake test, a latency percentile or a
semantic-accuracy benchmark. CUDA and Metal execution remain unverified.

An earlier Linux-container run used the official upstream binary over HTTP.
It established the audio format and exposed a compatibility difference:
the pinned legacy binary emits float32 `audio_chunk` events while the newer
source emits PCM16 `audio` events. `lfm.py` is the explicit-version diagnostic
adapter for those upstream streams; the production direction is the private
pipe worker. Unknown formats, truncated streams and unsolicited acoustic
output for a text-only request are not silently accepted.

The current model still declares neither native structured tool/result support
nor full duplex. Producing one syntactically valid weather-call JSON in a probe
is not enough to grant either capability. The remaining application work must
connect a qualified tool protocol to ToolExecutor, preserve confirmations and
receipts, then integrate model selection, runtime acquisition, wake residency
and recovery. The main desktop still uses its existing voice provider.

## Resident controller and tool-model investigation

The application-owned `LocalVoiceRuntime` now verifies an actual speech response
before reporting audio readiness, keeps one warm worker, and leases it to one
conversation. Failed or cancelled model changes restore the previous selection
using a fresh process. Crash recovery retains the application conversation owner
but discards uncertain native context; it never replays audio or tool effects.
The process controller bounds protocol messages, validates generation identities,
uses an interprocess ownership lock and reaps stalled children. A downloaded
model or loaded engine is still never labelled Jarvis-qualified.

The live verifier now uses this controller, rather than talking directly to the
worker. Its Windows CPU run passed synthetic audio input/output, two-turn context,
cancellation, stale-context refusal, warm process reuse and shutdown. The path-free
record is [the controller report](reports/local-native-controller-2026-09-23.json).
The targeted package/runtime/hardware/installer suite passed **194 tests with two
skips**. Ruff, focused mypy and the silent-exception gate passed. These checks
do not qualify application startup, wake activation or a physical Mac.

A second experimental package pins the community Q4 conversion of
[NVIDIA NemotronLabs VoiceChat 11B](https://huggingface.co/nvidia/NVIDIA-NemotronLabs-VoiceChat-11B).
The upstream model has a dedicated function channel and English speech. The
official optimized deployment and the community GGUF runner are different runtime
profiles; upstream capability claims do not certify the community transport.
The [conversion](https://huggingface.co/hoidhxd/NVIDIA-NemotronLabs-VoiceChat-11B-GGUF/tree/89883a05a031557729771f94abb9998e4facdd45)
contains four verified files totalling 6,520,209,856 bytes. No measured memory
profile or default recommendation is assigned.

A real Windows CUDA probe with
[llama-voicechat.cpp](https://github.com/sansamour/llama-voicechat.cpp/tree/f45001fc3d8013c72beb6753d3eb0b976b6a9fff)
loaded its speech and function heads and returned spoken English. It exposed a
Windows long-path defect: the function-head sidecar exceeded the legacy path
limit. Extended native paths restored its loading, now covered by a regression.
The weather prompt produced a clarification instead of the required tool call;
no real tool ran and the probe **failed tool qualification**. This one observation
does not establish a model-wide accuracy rate. The current upstream CLI also
accepts whole WAV turns and publishes audio at turn completion, so its underlying
model's full-duplex claim is not a streaming microphone transport implementation.

The clarified first milestone uses English and keeps a single native audio model
with a simple application-owned lifecycle. German is not an initial acceptance
criterion. Completing the request still requires the in-app workflow,
ToolExecutor integration, wake residency, runtime distribution and real device
qualification described above. No experimental candidate has been activated as
the user's current voice provider.

## In-app package library

Local models now has a Native voice tab independent of Ollama availability. It
uses the mounted `/api/local-voice` API to list the catalog and stored manifests,
download or import packages, show progress, cancel an operation and retry using
verified files. One operation owns the download slot until cleanup finishes.
Closing the app requests cancellation and waits for filesystem cleanup. Custom
weights use a data-only JSON manifest plus an optional local source directory;
model metadata cannot supply an executable or shell command.

The preview explicitly says activation is unavailable. A package receipt and
file sizes describe storage; activation still requires checksum and inference
verification. The existing voice configuration is not changed by this tab.
Runtime download/installation, provider activation and wake integration remain
separate unfinished acceptance items.

Verification on the Windows development host:

- Targeted React tests pass; the production frontend build completes.
- The mounted API accepts and stores an own five-byte fixture package. Library
  contracts cover persistence, missing files, cancellation/cleanup, retry and
  Python/TypeScript field parity. The CLI dynamically exposes all three API
  operations; `jarvis api local-voice list-voice-models` returned the live list.
- Chrome exercised the actual component against the actual package API in an
  isolated preview: light/dark rendering, real GGUF checksum verification,
  cancellation and refresh. No console or network errors occurred in that pass.
  The browser extension refused automated file selection because file-URL access
  was disabled. File selection/import has component and API evidence, not a
  completed browser upload proof. The entire desktop workflow was not exercised.
- The isolated startup-budget check passed: window 1,801 ms, application
  interaction 12,839 ms and existing voice readiness 12,919 ms. These numbers
  cover boot regression only, not native-model wake latency.

The portable CI matrix now includes the new local-voice contracts on Windows,
macOS, Linux and `python:3.11-slim`. The subprocess fixtures exercise ownership
and failure recovery without model weights. Passing them must not be represented
as physical GPU, microphone or Apple Silicon audio qualification.
