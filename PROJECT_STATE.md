# PROJECT_STATE

Living state of the "keyless local JARVIS" upgrade. Updated after every phase.
Target box: Windows 11, RTX 3050 Laptop (4 GB VRAM), 16 GB RAM, Python 3.13 venv.

## Phase status

| Phase | Topic | Status |
|---|---|---|
| 0 | Full analysis | DONE (this file) — no code changed |
| 1 | Local Brain (llama.cpp + Qwen3.5-4B Q4_K_M) | DONE with known issues (see Phase 1) |
| 2 | Local Japanese STT | DONE, live-verified via speaker->mic loop (see Phase 2) |
| 3 | Local Japanese TTS (VOICEVOX + SAPI5 fallback) | DONE, live-verified; latency open (see Phase 3) |
| 4 | Memory tiers + multilingual embeddings | NOT STARTED |
| 5 | PC control | NOT STARTED (large existing base) |
| 6 | Screen understanding | NOT STARTED (large existing base) |
| 7 | Double-clap wake | NOT STARTED |
| 8 | Model auto-switch / VRAM governor | NOT STARTED |
| 9 | Developer Mode | NOT STARTED |
| 10 | Teacher Mode | NOT STARTED |
| 11 | Hand tracking | NOT STARTED |
| 12 | iPhone client | NOT STARTED |
| 13 | Self-check / recovery | NOT STARTED |

Nothing below is "done" unless it says VERIFIED with evidence.

## Phase 0 findings

### Project & launch
- Personal Jarvis v2.2.1. Backend FastAPI + pywebview desktop shell + React
  frontend (`jarvis/ui/web/frontend`, build with `npm run build`).
- Launch: `run.bat` (`--headless`, `--debug`, `--dev`) →
  `python -m jarvis.ui.web.launcher`. Venv at `.venv`; `jarvis` imports from
  the repo checkout (restore trap clear).
- Live instance VERIFIED: `GET http://127.0.0.1:47821/api/health` →
  `{"ok":true,"version":"2.2.1"}`. Log: `data/jarvis_desktop.log`.
- Config: `jarvis.toml`, written only via `jarvis/core/config_writer.py`.
  Schema in `jarvis/core/config.py`. Secrets via `get_secret` (keyring → ENV → file).
- Rules: `CLAUDE.md` (English-only commits, capability gating, no Windows
  Service, subprocess `NO_WINDOW_CREATIONFLAGS`, tiered evidence T1/T2/T3).

### Brain / providers
- Plugins in `jarvis/plugins/brain/`: claude_api, claude_cli, codex, gemini,
  vertex, openai, openrouter, grok, nvidia, antigravity, **ollama**,
  **local_openai** (any OpenAI-compatible server incl. llama.cpp `llama-server`).
- Fallback chain: `jarvis/brain/resolver.py` (primary → tier fallbacks →
  `brain.local_fallback`). Error classification: `brain/provider_test.py`,
  rate limits: `brain/rate_limit_tracker.py`, quota state `*_quota_state.py`.
- Schema defaults (`config.py:1547`): `primary="claude-api"`,
  `local_fallback="claude-api"` — i.e. the default "local" fallback is a cloud API.
- **Current box**: `[brain].primary = "local-openai"` but NO base_url configured
  and no llama-server installed → log: "No server URL configured for the local
  OpenAI-compatible provider". The brain is currently unusable without a key.
- `brain.computer_use` and `brain.worker` = gemini.
- Local-model infrastructure already exists: `jarvis/local_models/`
  (autostart, health monitor, benchmarks), `brain/hf_gguf.py`,
  `brain/ollama_*` (Ollama-centric). Ollama not installed here.
- Gemini references: 114 Python files (brain 15, tts 9, mission workers 9,
  web 7, core 6 ...). Mostly optional provider code, not hard boot deps.

### Voice
- STT plugins (`jarvis/plugins/stt/`): **fwhisper** (faster-whisper/ctranslate2,
  local, default model distil-large-v3 ~1.5 GB VRAM — too big for this box next
  to an LLM), nemotron_local, gemini/openai/groq/deepgram/openrouter APIs.
  Current: `stt.provider = "gemini-api"`, `language = "ja"` → needs a key.
  Log: "Dictation STT warm-up ended without a ready engine".
- TTS plugins: **piper_local** (sherpa-onnx, keyless), fallback_tts (SAPI5
  opt-in), gemini_flash_tts, elevenlabs, cartesia, inworld, grok, openrouter.
  No VOICEVOX adapter. Current: gemini-flash-tts, `language_code = "de-DE"`
  → log: "No TTS provider has a usable API key". **Voice output is dead now.**
- Wake: `jarvis/plugins/wake/` openwakeword (alive per heartbeat), vosk KWS;
  `speech/rolling_whisper_wake.py`, `wake_verifier.py`. Phrase "Hey Jarvis".
  No clap detector exists.
- Realtime voice: `jarvis/realtime/` + plugins gemini_live, openai_realtime
  (both cloud). `voice.mode = "pipeline"`.
- Installed: faster-whisper 1.2.1, ctranslate2 4.8.2, onnxruntime 1.27,
  sherpa-onnx 1.13.8, openwakeword 0.6, vosk, sounddevice.
  Not installed: llama-cpp / llama-server, whisper.cpp, silero, torch,
  mediapipe, embedding libs.

### Memory
- SQLite, `jarvis/memory/schema.sql`: messages + FTS5, kv_store,
  awareness_frames/episodes + FTS5. Plus markdown wiki (`jarvis/memory/wiki`),
  core_memory, user_profile, soul, people, recall.
- No vector embeddings; search is FTS5 keyword (Japanese tokenization under
  FTS5 default tokenizer is weak — needs trigram or embeddings).
- No explicit tiers for session/short/long/project/task-history/error-solution.

### PC control / screen
- Tools in `jarvis/plugins/tool/`: run_shell, open_app, app_command, click,
  click_element, type_text, hotkey, scroll, drag, switch_window, navigate,
  wait_for_element, read_visible_ui_state, screen_snapshot, computer_use_tool.
- `jarvis/cu/` computer-use engine (capture/actuate/verify/ledger);
  `jarvis/vision/uia_tree.py` (Windows UIA), ax/atspi trees, set-of-marks.
- `jarvis/screen_context/` (uitext, redaction, targeting). pywinauto 0.6.9 and
  comtypes installed. No local OCR engine found; no local vision model.
- MCP: `jarvis/mcp/` client/server/registry; skills: `jarvis/skills/`.

### Safety
- `jarvis/safety/`: risk tiers safe/monitor/ask/block, approval surface,
  command_impact, `ToolExecutor.execute()` is the only authorized path.
- Cancellation: `jarvis/control/cancel.py` (CancelToken registry). Voice
  "stop" intent: `speech/interrupt_intent.py`, `speech/hangup.py`.
  No ESC-long-press or gesture stop.

### Japanese
- UI locales: de, en, es only — **no ja.json**. `[ui].language = "en"`.
- Turn language decided once in `jarvis/core/turn_language.py`.
- TTS `language_code = "de-DE"` must become ja-JP for Japanese output.

### Mobile
- Web channel + discord/telegram channels start at boot. No dedicated mobile
  pairing API yet. Server binds 127.0.0.1 only.

### Hardware usage
- `jarvis/hardware/detection.py` reports VRAM. GPU idle usage 324 MB.
- Budget plan for 4 GB: Qwen3-4B Q4_K_M ≈ 2.5 GB weights → partial GPU offload
  (~24-30 of 36 layers) with 8K ctx; STT must run on CPU (whisper small/base
  int8) to avoid sharing VRAM with the LLM (and AP-24: never share engines).

## Root causes of "doesn't work without keys" today
1. Brain `local-openai` has no server and no local server manager for llama.cpp.
2. STT = gemini-api, TTS = gemini-flash-tts (keys required).
3. `local_fallback` defaults to a cloud provider.

## Phase 1 plan (as proposed; executed below)
- Add a managed llama.cpp `llama-server` runtime (download official release
  binary, CUDA build, into the app data dir) + GGUF download (Qwen3-4B
  Q4_K_M) with a VRAM-aware `--n-gpu-layers` calculator and `-c 8192`.
- Point `local-openai` at it automatically; set `local_fallback` to it so any
  cloud 429/quota/outage/offline falls back locally.
- Model selector already exists on the provider card; extend with GGUF list.
- Verify: boot with no keys, Japanese chat turn, forced 429 fallback test.

## Phase 1 — Local Brain (2026-09-18)

### What was built
- `jarvis/local_models/llama_server.py`: managed llama.cpp `llama-server`
  in router mode (`--models-preset`, `--models-max 1`): every GGUF in
  `%LOCALAPPDATA%/Jarvis/llama/models` shows in `/v1/models`, so the
  local-openai card / chat model picker switches models; only ONE model is
  resident. Per-model offload tier from free VRAM: full GPU -> `--fit`
  (partial, rest in RAM/CPU) -> CPU; a failed warm-up (OOM) steps down and
  restarts. 32K context, q8_0 KV cache, 1 slot, reasoning off. Watchdog
  restarts a dead server (backoff, max 5). Starts in the background at boot
  (AP-26); stops on app shutdown. Opt-out:
  `[brain.providers."local-openai"].managed_server = false`.
- Boot wiring in `jarvis/ui/web/server.py`; base_url pinned through
  `config_writer.set_provider_base_url` (AP-7).
- `jarvis/brain/manager.py`: stage 4 "keyless local floor" appended to every
  fallback chain when a local-openai server URL is configured (capability, not
  a provider name). Brains may declare `tool_budget_tokens` + `core_tools`;
  local-openai declares 4000 tokens and 9 core tools.
- `scripts/install_local_brain.py`: downloads the official llama.cpp release
  (CUDA 12.4 on Windows+NVIDIA, CPU otherwise, Ubuntu/macOS builds) and the
  default GGUF. No account, no key.
- Model: `unsloth/Qwen3.5-4B-GGUF` Q4_K_M (Qwen3.5-4B, 2026-02, Apache-2.0).
  Chosen over Qwen3-4B because the repo forbids defaults a year old or older.
- llama.cpp build: b11026 (b11028 had no Windows assets yet).

### Verified (live, this box)
- Standalone: full offload 3.3-3.5 GB VRAM, 32-42 tok/s generation.
- Jarvis boots with no API keys and starts the server itself:
  log `llama-server: ready on http://127.0.0.1:18181 — Qwen3.5-4B-Q4_K_M on tier full`.
- `jarvis brain test local-openai` -> ok (1.8 s).
- Desktop chat, Japanese: reply received in Japanese. Turn 1: 101 s
  (13.3K-token prompt prefill). Turn 2: 3.0 s (prompt cache).
- 429 fallback over a real HTTP path: stub OpenAI endpoint returning 429 ->
  chain skipped key-less clouds -> local Qwen answered.
- Tests: `tests/unit/local_models/test_llama_server.py`,
  `tests/integration/test_local_floor_fallback.py`,
  `tests/unit/brain/test_context_window_fit.py` (budget case) pass.
  Pre-existing failures (fail identically on the pre-change commit, not
  caused by this work): 9 in ollama/supervisor/stt/realtime tests.

### Known issues
1. ~~First turn slow (~100 s)~~ **FIXED 2026-09-18** — first turn after boot
   now 5.0 s (Japanese reply), follow-ups 2-3 s. Root causes and fixes:
   - Prompt too big for a 4B model: compact prompt for brains declaring
     `compact_prompt` (drops the 9K-char skill catalogue + society lead card;
     the per-turn skill hint still names a matching skill) and a 4000-token
     tool budget with core tools kept first.
   - Boot contention: the app's own boot (voice models, 3D scene) slowed the
     first prefill 5x. The server now prefills the real turn prefix 45 s after
     it is healthy (`BrainManager.prewarm_prompt_cache`).
   - Cache never hit: Qwen3.5's chat template renders TOOLS before the system
     prompt, and its recurrent layers can only rewind to a checkpoint. Any
     per-turn tool change (smalltalk turns with no tools, gates hiding action
     tools, chat build-mode tools, CLIs/MCP attaching later) forced a full
     re-prefill. Fix: a compact brain is ADVERTISED one frozen tool surface
     every turn (`_stable_compact_tools`, frozen at first use); what may
     EXECUTE is still the per-turn gated set — an advertised-but-gated call
     is answered "not used for this request" and nothing runs
     (`ToolUseLoop.advertised_tools`). Checkpoint spacing 1024 tokens.
   - Host-RAM prompt cache capped at 1.5 GB (llama.cpp default is 8 GB).
2. GPU throttling seen once (P3, 712 MHz, reason 0x20); later runs were at
   P0 1.6-1.7 GHz and prefill ran at 600-780 tok/s. Keep an eye on it.
3. VRAM is tight: 3.9 / 4.0 GB with Jarvis + model resident. STT on CPU.
4. Quality of the 4B model: occasional Chinese glyph in Japanese, invented a
   weather report when no weather tool was advertised.
5. Japanese: **reply language FIXED** — `turn_language.is_japanese_text`
   (kana, or Han without Latin words) pins a MANDATORY Japanese reply
   directive. Canned phrases (acks, errors) are still de/en/es: 278 tables in
   41 files have no `ja` key yet — Japanese turns hear English canned phrases.
   To do in Phase 3.
6. Spec deviation: 32K context, not 8K (Jarvis's request cannot fit in 8K).
7. Chat build-mode file tools (Edit/Write/Grep...) are not advertised to the
   4B local brain; coding belongs to Developer Mode (Phase 9, 7B coder).

## Phase 2 — Japanese STT (2026-09-18)

- Engine: existing `faster-whisper` plugin (CTranslate2 Whisper) instead of a
  second whisper.cpp stack — same Whisper models, already integrated, tested
  and wrapped by the STT dictionary. Silero VAD sits in front (existing).
- Model: **base** on CPU int8, language pinned `ja`. Measured on this box
  (5 Japanese test sentences, SAPI-generated): small 3.7-4.1 s/utterance,
  base 1.2-1.4 s; with vocabulary bias BOTH 5/5 correct -> base (spec: "small,
  base if heavy"). `[stt].model = "small"` switches back.
- STT dictionary seeded with Jarvis/app names (+ misheard variants). Fixed:
  misheard replacements never matched in Japanese (word-boundary lookarounds
  require spaces) — kana/Han edges now match without a boundary.
- Fixed a dropped-turn bug: the final transcription gave up after ~1.2 s while
  a cancelled preview decode still held the local engine ("already in flight").
  The final now waits out `TranscribeBusy` for up to 6 s.
- Fixed a GPU grab: the dictation engines chose CUDA when free at boot and took
  ~600 MB, pushing the LLM off full offload. With a managed local LLM installed
  they now run on the CPU (`_local_brain_owns_accelerator`). Dictation final
  model on this box: `[dictation].local_model = "small"` (large-v3-turbo on CPU
  took 66 s to load).
- Fixed an event-loop DEADLOCK (pre-existing): a vosk recognizer `__del__`
  during GC inside `ThreadPoolExecutor.submit` re-entered the executor's global
  lock -> loop frozen 75 s+ right after a wake. Release now goes through a
  `queue.SimpleQueue` + daemon thread.
- Live: wake "Hey Jarvis" -> Japanese command -> transcript
  "こんにちは 自己紹介を一分でお願いします" (ref: ...一文で...), ja, ~1.5 s STT.
- No audio is stored by these changes (existing session recorder unchanged).

## Phase 3 — Japanese TTS (2026-09-18)

- `jarvis/plugins/tts/voicevox_tts.py` + `voicevox_engine.py`: VOICEVOX Engine
  0.25.2 (CPU build, 127.0.0.1:50021), started in the background at boot and
  on first use; speaker chosen by name from `/speakers` (default: a calm male
  fictional character), `[tts].model = "<character>/<style>"` overrides.
  Speed from `[tts].speed`; volume stays in the player (`[tts].volume`).
  Voice ON/OFF: existing voice session controls.
- Fallback: `sapi5` (Windows built-in Japanese voice "Haruka", local). Piper
  has no Japanese voice, so it stays the de/en/es local voice.
- UI card "VOICEVOX (on this machine, Japanese)", defaults entry, contract
  test for the local TTS family, `scripts/install_voicevox.py`.
- Japanese streaming: sentence splitter now splits on 。！？ without a space
  (before: the whole reply was waited for — ~8 s of silence).
- Measured: synthesis ~1.0-1.3 s for a short phrase, roughly real-time for
  longer sentences on this CPU (clock stayed at 2.3 GHz base). Open: GPU TTS
  is blocked by VRAM (LLM uses 3.3 of 4 GB) -> Phase 8.
- Live: voice turn answered in Japanese through VOICEVOX
  ("こんにちは。私はジャルビスです。...").
- Credit note: VOICEVOX characters require "VOICEVOX:<character>" credit when
  audio is published.

## Changelog
- 2026-09-18: Phase 2 (ja STT) and Phase 3 (VOICEVOX TTS) live-verified; 3 pre-existing bugs fixed (vosk deadlock, STT busy drop, CJK sentence split).
- 2026-09-18: first-turn latency 100 s -> 5 s; Japanese reply pin.
- 2026-09-18: Phase 1 local brain implemented and live-verified (see above).
- 2026-09-18: Phase 0 analysis written. No code modified.
