# Local voice: model-first redesign

Assessment date: 2026-09-23. Status: investigation and first regression fix;
the replacement runtime is **not implemented or qualified**.

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
| 2: Runtime contract | Typed model/adapter selection; invalid or incompatible custom weights rejected; CPU/Metal/CUDA contract tests; no boot-time heavy imports. | Manifest, eligibility, verified acquisition/import and inspection CLI implemented. Actual engine adapter and application wiring remain pending. |
| 3: Inference integration | Real audio in/out, tool request/result, interrupt, cancellation and two-turn context through the selected native model or agreed package. | Pending model/language/tool decision and real engine qualification. |
| 4: App workflow | Choose/download/use/customize without server setup; progress/cancel/retry; settings migration and rollback; light/dark browser verification and frontend build. | Pending. |
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
