# Local Lightweight Wake-Word — Research & Design Report

**Date:** 2026-05-24
**Author:** Autonomous research + implementation session (4 research Jarvis-Agents + main agent synthesis)
**Status:** Design accepted → implementation in progress on branch `feat/lightweight-local-wakeword`

---

## 1. Mandate & Constraints

Replace the heavy local Whisper model that currently runs permanently as a wake-word
backstop with a **lightweight, locally-bundled wake-word detector** that ships natively
with the app (no multi-GB download as a precondition).

**Two hard constraints (non-negotiable):**

1. ❌ **No continuous audio stream to any cloud** — wake detection must run on the
   user's own device. Cloud (incl. a VPS the user does not physically sit at) is a
   privacy risk and is out.
2. ❌ **No heavy hardware requirement** — no GPU, no multi-GB model. Must run on a weak
   laptop / 1-vCPU box. The model must be small enough to bundle natively.

---

## 2. Executive Summary (TL;DR)

- **The wake word was never the heavy part.** `openWakeWord` — already in the project —
  is CPU-only, ships a pretrained `hey_jarvis` model, and the *entire* stack
  (melspectrogram + embedding + hey_jarvis classifier) is **~3.5 MB of ONNX**. A
  Raspberry Pi 3 core runs 15–20 such models in real time.
- **The actual heavyweight is `RollingWhisperWake`** — a second, parallel wake backstop
  built on local `faster-whisper` (~1 GB, `device="cuda"`). That, plus the faster-whisper
  VAD-stability probe, is what imposes the GPU/RAM/download burden.
- **Decision:** make `openWakeWord` the sole lightweight local wake detector, bundle its
  ONNX models in-repo (no runtime download), and remove `faster-whisper` from the default
  path entirely (it becomes an opt-in power-user extra). This satisfies both hard
  constraints for the self-hosted Python runtime (desktop *and* audio-equipped VPS).
- **Browser-WASM wake (for true VPS-over-browser users) is a documented follow-up**, not
  this session's deliverable, because the frontend currently has **no microphone capture
  at all** — building it is a separate, larger project. The full blueprint is captured in
  §7 so it can be picked up cleanly later.

---

## 3. Research Findings

### 3.1 Landscape of on-device wake-word approaches

| Option | Stack size | CPU-only | Cloud? | Browser (WASM) | Python | Code lic. | Model lic. | "hey jarvis"? | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **openWakeWord** | ~3.5 MB | ✅ | none | community only | ✅ | Apache-2.0 | **CC-BY-NC-SA** | ✅ pretrained | **Pick (Python)** |
| livekit-wakeword | ~8 MB | ✅ | none | ❌ | ✅ | Apache-2.0 | **Apache-2.0** | train (minutes) | Clean-license upgrade path |
| sherpa-onnx KWS | ~18–38 MB | ✅ | none | KWS-WASM unconfirmed | ✅ | Apache-2.0 | Apache-2.0 | via keyword list | Strong, heavier |
| Picovoice Porcupine | ~1 MB | ✅ | **monthly phone-home + AccessKey** | ✅ (mature) | ✅ | Apache-2.0 | proprietary | "Jarvis" builtin | ❌ phone-home/account |
| @ricky0123/vad-web | 2.3 MB (VAD) | ✅ | none | ✅ mature | — | ISC | MIT | (VAD, not wake) | **Pick (browser VAD)** |
| TF.js speech-commands | ~4 MB | ✅ | CDN fetch | ✅ | ❌ | Apache-2.0 | Apache-2.0 | ❌ 18 fixed words | Wrong vocabulary |
| Web Speech API (default) | — | — | **streams to Google** | ✅ | ❌ | — | — | n/a | ❌ violates constraint 1 |
| Web Speech API (Chrome 139 on-device) | **4 GB** | ✅ | none | Chrome-only | ❌ | — | — | scan | ❌ violates constraint 2 |
| Mycroft Precise / Snowboy / Howl | small | ✅ | none | partial | ✅ | Apache/MPL | mixed | ❌ | ❌ abandoned |

### 3.2 Supply-chain / repo due-diligence

| Package | Stars | Last release | License | Verdict |
|---|---|---|---|---|
| `@ricky0123/vad-web` | 2.0k | Nov 2025 | ISC / MIT models | ✅ **SAFE** — 211k dl/mo, 90/100 security, no install scripts, single dep |
| `snakers4/silero-vad` | 9.1k | Feb 2026 | MIT / MIT | ✅ **SAFE** — gold standard, zero telemetry |
| `k2-fsa/sherpa-onnx` | 12.4k | May 2026 | Apache-2.0 / Apache-2.0 | ✅ **SAFE** — most active, fully offline |
| `dscripka/openWakeWord` | 2.3k | Feb 2025 | Apache-2.0 / **CC-BY-NC-SA** | ⚠️ **CAUTION** — NC model license, 15-mo commit gap, bus-factor 1. Custom-trained models are fine. |
| `Picovoice/porcupine` | 4.8k | Dec 2025 | Apache-2.0 / proprietary | ⚠️ **CAUTION** — AccessKey + monthly validation phone-home |
| `dnavarrom/openwakeword_wasm` | 4 | never | none visible | ❌ **AVOID** — anonymous maintainer, binary tarball in git, 0 users. *Build the WASM pattern yourself instead.* |
| `@linto-ai/webvoicesdk` | 36 | — | **AGPL-3.0** | ❌ **AVOID** — viral copyleft, ~0 adoption |
| `@tensorflow-models/speech-commands` | — | **Mar 2021** | Apache-2.0 | ❌ **AVOID** — abandoned 4+ yrs, TFJS-3 lock, wrong vocab |

**License note:** openWakeWord's *pretrained* models are CC-BY-NC-SA 4.0 (non-commercial).
For a non-commercial OSS project this is acceptable but must be disclosed. If Jarvis ever
goes commercial, migrate the model to `livekit-wakeword` (Apache-2.0 on code *and* models;
train a `hey_jarvis.onnx` in minutes via its automated synthetic-TTS pipeline — drop-in
compatible with the openWakeWord ONNX format).

### 3.3 Prior art (how privacy-first assistants do it)

Home Assistant (Wyoming protocol), Willow (ESP-SR on-chip), Rhasspy, OpenVoiceOS all use
the same pattern: **wake fires on-device, and not a single audio byte leaves the device
until after the wake event.** The wake service emits a fire-and-forget `detection` event;
only the *following* utterance is forwarded to STT. This is exactly Jarvis's current
local model — wake + VAD local, only the post-wake utterance crosses to cloud STT (Groq).

### 3.4 Jarvis codebase reality (integration recon)

- **All microphone capture is Python-backend** (`sounddevice`/WASAPI, `jarvis/audio/capture.py`).
  The React frontend has **zero** `getUserMedia`/`AudioContext`/`AudioWorklet` — it is a
  pure event/text terminal. The `/ws` WebSocket carries **JSON only**, no audio frames.
- Two wake detectors run in parallel (`pipeline.py:_run_parallel_wake`):
  `OpenWakeWordProvider` (light, ONNX, threshold 0.15) + `RollingWhisperWake` (heavy,
  local faster-whisper). Both gated by one flag `cfg.trigger.wake_word_enabled`
  (`desktop_app.py:1187-1188`).
- `faster-whisper` is loaded in **three** places: the `RollingWhisperWake` backstop,
  the VAD-stability probe (`pipeline.py:_stt_probe_async`), and as the (now-unused-by-default)
  utterance-STT fallback. Post-wake utterance STT already goes to **Groq cloud**
  (`build_stt_from_config` → `groq-api`).
- Cleanest seam for a *future* browser wake: browser fires → `{action:"wake_detected"}`
  over `/ws` → backend publishes `WakeWordDetected` → `pipeline.py:_call_event.set()`
  (the state loop already listens). Files: `server.py:_route_incoming`, `config.py`
  (`wake_source`), `pipeline.py:_wake_listening_enabled`, `frontend/schema/ws.ts`.

---

## 4. Design Decision

**Build a faster-whisper-free "lightweight local wake" default path.**

- `openWakeWord` becomes the **sole** local wake detector by default.
- Its three ONNX models are **bundled in-repo** (`jarvis/assets/wakeword/`, ~3.5 MB) and
  loaded from disk — no runtime auto-download, works offline on first boot.
- A new config switch gates the heavy faster-whisper path. When off (the new default):
  - `RollingWhisperWake` is **not** constructed,
  - the faster-whisper VAD-stability probe is **disabled** (Silero VAD endpoints on its
    own `silence_ms` timer — the probe was only a music-bleed optimisation),
  - **no `FasterWhisperProvider` is instantiated at all** → no CUDA, no ~1 GB download.
- The heavy path stays available as an **opt-in power-user extra** (set the switch true)
  so the maintainer's existing low-volume-wake backstop is not lost.

This satisfies both hard constraints for any self-hosted Python runtime (desktop or
audio-equipped VPS/laptop): wake is local + tiny + CPU-only; nothing streams to cloud
before wake; the post-wake utterance still goes to cloud STT (Groq), which is the
user-approved status quo.

### Trade-off (honest)

Dropping `RollingWhisperWake` removes the low-volume "safety net" for very quiet wakes
(BUG-009 history). openWakeWord alone at threshold 0.15 is the documented, data-driven
sweet spot. The maintainer can re-enable the heavy backstop via the opt-in switch.

---

## 5. Implementation Plan (TDD)

1. **Bundled-model loading** — `OpenWakeWordProvider` resolves bundled local ONNX paths
   (melspec + embedding + hey_jarvis) before falling back to package auto-download.
   *Tests:* path resolution returns bundled files; graceful fallback when absent.
2. **Config switch** — `TriggerConfig.heavy_local_whisper: bool = False`.
   *Tests:* default is False; pydantic round-trips; `extra="allow"` safe.
3. **Pipeline faster-whisper-optional** — `SpeechPipeline` accepts `stt=None`; when None,
   no probe, no RollingWhisperWake, `_stt`-dependent paths guarded.
   *Tests:* constructing with `stt=None, enable_whisper_wake=False` instantiates no
   FasterWhisperProvider; `_on_vad_probe` is a no-op; warmup skips `_stt._ensure_model`.
4. **Wiring in `desktop_app.py`** — only build `FasterWhisperProvider` when
   `cfg.trigger.heavy_local_whisper`; pass `stt=None` otherwise; `enable_whisper_wake`
   follows the same switch.
5. **Regression guard** — existing `tests/unit/speech/test_wake_threshold.py` stays green.

---

## 6. Files Touched

- `jarvis/assets/wakeword/*.onnx` (new, bundled models)
- `jarvis/plugins/wake/openwakeword_provider.py` (local model paths)
- `jarvis/core/config.py` (`TriggerConfig.heavy_local_whisper`)
- `jarvis/speech/pipeline.py` (faster-whisper optional)
- `jarvis/ui/desktop_app.py` (conditional construction)
- `tests/unit/speech/`, `tests/unit/plugins/wake/` (new tests)

---

## 7. Follow-up: Browser-WASM Wake (not this session)

For a true browser/VPS user with no server-side mic, build client-side wake so no audio
leaves the user's device. Blueprint from the research:

- **Stack:** `@ricky0123/vad-web` (✅ VAD) + self-built openWakeWord WASM via
  `onnxruntime-web` (do **not** depend on `dnavarrom/openwakeword_wasm`; follow the Deep
  Core Labs reference). Models served as static assets (~11 MB, cached in IndexedDB).
- **Pipeline:** `getUserMedia` (HTTPS/localhost) → `AudioWorkletNode` → resample to
  16 kHz (libsamplerate-js; AudioContext is 48 kHz on Windows) → mel/embedding/classifier.
- **Headers:** `onnxruntime-web` threading needs cross-origin isolation
  (`COOP: same-origin` + `COEP: require-corp`, or `coi-serviceworker`).
- **Handoff:** wake fires client-side → Silero VAD gates the one following utterance →
  send only that segment over a new **binary** WS (current `/ws` is JSON-only and would
  crash on binary frames) → backend feeds it into `_handle_utterance`.
- **Pitfalls:** 1.28 s cold-start gap; melspec `(value/10)+2` normalisation; copy ONNX
  output buffers immediately; AGC/echo-cancellation on in getUserMedia; pause on hidden tab.

---

## 8. Sources

openWakeWord: https://github.com/dscripka/openWakeWord ·
hey_jarvis card: https://github.com/dscripka/openWakeWord/blob/main/docs/models/hey_jarvis.md ·
livekit-wakeword: https://github.com/livekit/livekit-wakeword ·
sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx ·
Porcupine: https://github.com/Picovoice/porcupine ·
@ricky0123/vad: https://github.com/ricky0123/vad ·
silero-vad: https://github.com/snakers4/silero-vad ·
Deep Core Labs WASM: https://deepcorelabs.com/open-wake-word-on-the-web/ ·
Home Assistant wake: https://www.home-assistant.io/voice_control/about_wake_word/ ·
ONNX Runtime Web: https://onnxruntime.ai/docs/tutorials/web/deploy.html ·
COOP/COEP: https://web.dev/articles/coop-coep
