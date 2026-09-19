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
| 4 | Memory (Japanese search + say-do write) | PARTIAL: Japanese save/recall live-verified across restart; tiers/embeddings not done |
| 5 | PC control | PARTIAL: local brain opened Notepad via run_shell (live) |
| 6 | Screen understanding | DONE, live-verified: "look at the screen" in chat -> screenshot -> local Qwen3.5-4B vision (~35 s) |
| 7 | Double-clap wake | DONE, live-verified via speaker playback (real claps: user to confirm) |
| 8 | Model switch (normal 4B / developer 9B) | DONE for brain modes, live-verified; vision model not done |
| 9 | Developer Mode | PARTIAL: project-chat loop live-verified on a sandbox; Unreal/Unity/Blender build loops not done |
| 10 | Teacher Mode | DONE via chat (plan/start/silent record/summary/end), voice path partially verified |
| 11 | Hand tracking | PARTIAL: open-palm stop (MediaPipe, opt-in); landmarker verified, real-hand camera test open |
| 12 | iPhone client | DONE (web client): HTTPS on the LAN + one-time QR pairing; verified with curl, real iPhone scan open |
| 13 | Self-check / recovery | PARTIAL: doctor reports the local stack; llama-server watchdog; git tag rollback point |

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
  Japanese whole-utterance stop ("Jarvis, teishi", tomatte, yamete, sutoppu, ...) added.
  Classic voice pipeline: a bare stop publishes KillRequested and skips the brain.
  Doctor: `python -m jarvis --doctor` now reports the local stack (llama.cpp, models, VOICEVOX, whisper, disk).
  Holding ESC 1.5 s (Windows) = tray emergency stop (`control/esc_hold.py`). Open-palm stop: `vision/hand_gesture.py` (opt-in `[trigger] palm_stop_enabled`, MediaPipe via `scripts/install_hand_tracking.py`).

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

## Phase 4 — Memory, Japanese (2026-09-18)

Existing store kept (SQLite + markdown wiki vault + FTS5). What was broken for
Japanese and is fixed:
- **Save**: "...と覚えておいて" had no remember cue (de/en/es only) -> the 4B
  model said "Noted." and stored nothing. Japanese cues added
  (`contact_intent._MEMORY_VERB_JA_RE`); a mandated tool now forces a tool call
  on the first round for compact local brains (`BrainRequest.tool_choice =
  "required"`, OpenAI-compatible field; hosted brains unaffected).
- **Search**: FTS5 `unicode61` made a Japanese sentence ONE token. New
  `jarvis/memory/wiki/cjk.py`: index gets a `cjk` column with CJK
  unigrams+bigrams (old index auto-dropped and rebuilt at boot); a CJK query
  becomes content kanji + katakana bigrams (particles dropped).
- **Relevance gate**: Japanese was "too short" (word count) and had no
  personal/lookup markers -> strict bar. Now CJK counts characters, and
  Japanese "watashi no ... nan datta" style is a personal lookup.
- Live: "私の好きな色は翡翠色だと覚えておいて" -> wiki-ingest wrote
  entities/user.md; app restart; "私の好きな色は何だった？" ->
  "あなたの好きな色はエメラルドグリーン（翡翠色）です。" (5.1 s).
- Not done: separate session/short/long/project/task/error tiers, embedding
  search (multilingual-e5) — cross-lingual recall (English page, Japanese
  question) still depends on shared kanji.

## Phase 5 — PC control (partial, 2026-09-18)

- Live: chat "メモ帳を開いて" -> local brain called run_shell
  `start notepad.exe` -> Notepad process confirmed (7.1 s).
- Existing: UIA tree, pywinauto, click/type/hotkey tools, risk tiers. Not yet
  exercised with the local brain: UIA-driven apps, VS Code/Unity/Unreal/Blender.

## Phase 7 — Double-clap wake (2026-09-18)

- `jarvis/speech/clap_detector.py`: deterministic, model-free, nothing stored.
  Per 16 ms frame: adaptive noise floor; onset (8x floor, 4x the quieter of
  the two previous frames, >=0.005 FS); shape (peak within 2 frames, >=2
  frames above 30% of peak — rejects keystrokes —, decay to 25% within ~130
  ms — rejects speech/music); spectrum (centroid >=1.2 kHz, >=20% energy in
  2-8 kHz — rejects knocks/voice); pair gap 0.2-0.8 s, peak ratio <=3, no
  third clap within 0.35 s (applause -> 1 s quiet period), 2 s cooldown.
- Offline (200 seeds each): double clap 100%, keystrokes/speech/knocks 0%
  false triggers; a clap only ~6x above the noise floor is mostly missed.
- Wired as a wake-mic detector next to the wake word ("Hey Jarvis" kept),
  `[trigger].clap_enabled` (default false; true on this box).
- Flow: wake -> UI -> chime -> "はい。" -> listening. The spoken ack is the
  new opt-in `[voice].wake_ack_phrase` (default empty = visual-only, as
  before); input captured while it plays is dropped (echo-safe).
- Live (speaker playback of synthetic claps, the WORST case — the laptop
  speaker has no highs): detected 2 of 3 plays; full flow verified through a
  Japanese command and spoken reply. Real hand claps not yet tested by a
  person.

## Phase 8 — Local model modes (2026-09-18)

- Normal = smallest installed GGUF (Qwen3.5-4B, full GPU, 32K ctx);
  developer = largest (Qwen3.5-9B Q4_K_M, 2026-02, Apache-2.0).
  Qwen2.5-Coder-7B was rejected: 2024 (repo rule: no defaults a year old).
- One model resident at a time (router `--models-max 1`); switching unloads.
- Switch: `jarvis/brain/local_mode_gate.py` (deterministic, ja/en/de, a mode
  name AND a switch cue — "what is developer mode?" does not switch) ->
  `jarvis/local_models/modes.py` (persists
  `[brain.providers."local-openai"].model`, live-applies, prewarms). A tool
  was not used: brain tools are the ADR-0011 ROUTER_TOOLS list.
- Partial offload sized in layers from the GGUF header (`block_count`) and a
  measured per-layer cost: 9B -> 21 GPU layers at 24K ctx. Measured 9B gen
  speed on this box: llama.cpp `--fit` 4.1 tok/s, 22 layers 7.0 tok/s, 28
  layers 5.6 tok/s (spills). Prefill ~200 tok/s -> ~50 s first turn.
- Live: "開発モードに切り替えて" -> "開発モードに切り替えました。モデルは
  Qwen3.5-9B-Q4_K_M です。" (0.1 s); mode survived an app restart.
- RAM: with the 9B loaded free RAM dropped to ~0.4-1.6 GB on 16 GB. Workable
  but tight; close other apps for developer work.

## Phase 9 — Developer Mode (partial, 2026-09-18)

- Uses the existing folder chat (agent-chat session with a cwd); the local
  brain now sees the chat's folder tools (Read/Edit/Glob/Grep/RunCommand).
- Live on a sandbox repo (unittest, pending TODO): "このプロジェクトの続きを
  やって…git commitまでして" -> read TODO, ran tests (fail), edited calc.py,
  re-ran (3/3 OK), git status/add/commit (27d25e9), reported in Japanese.
  ~6.4 min incl. manual approvals of each command (accept-edits mode).
- Bugs found and fixed on the way:
  - `run_shell` ignored the chat's working directory (ran in the app dir).
  - Package installs (pip/npm/winget/choco/Install-Module/...) ran without a
    prompt; now escalated to ask like deletes. The first test run had
    pip-installed 7 packages into the system Python unprompted — all 7 were
    fresh installs and were uninstalled again.
  - A small model announcing "I'll check the files first." ended the turn;
    project chats now nudge it to act (max 2).
- Not done: Unreal/Unity/Blender build+run+log loops (no .uproject exists on
  this PC), "same fix max 3 times" guard, voice-only "Unrealの続き".

## Phase 10 — Teacher Mode (2026-09-18)

- `jarvis/teacher/`: gate (explicit ja/en phrases; "matomete" only counts
  while a lesson runs), `lesson.py` (text-only transcript with timestamps,
  prompts, files), `replies.py`. Wired in `BrainManager.generate()` before
  the LLM. During a lesson every other utterance is recorded and NOT
  answered (`_last_turn_suppressed`, so the pipeline asks no clarifying
  question) and the voice session does not idle-hang-up.
- Files: `%LOCALAPPDATA%/Jarvis/lessons/<stamp>-{plan,summary-N,report,transcript}.md`.
  No audio is stored.
- Live (chat, local 4B): plan for "中学2年の音楽 ヴィヴァルディ「春」25分"
  (objectives, intro/development/summary, teacher lines, questions, expected
  answers, board, slides, time table, assessment); start; 4 student remarks
  recorded silently; "ジャービス、まとめて" -> common/different opinions,
  questions, key terms, per musical element, next questions, time left; end
  -> report (record, real timing, opinions by element, improvements, next).
- 4B quality issues seen: a Chinese heading glyph, "pianist" for a violin
  piece. Music element names are now forced to the Japanese terms.
- Voice: wake + commands through the laptop speaker->mic loop were
  misrecognised ("授業を始めて" -> "重要を始めて" / "授業を集めてください");
  the same audio files transcribe correctly directly. STT dictionary now
  carries the classroom phrases. Voice path with a real speaker: to confirm.
- Not done: live slide/screen window for summaries (text is shown in the
  chat/voice transcript and saved as Markdown), speaker separation.

## Phase 6 — Screen understanding (2026-09-18)

- Qwen3.5 is natively multimodal: `mmproj-<model>.gguf` next to the model
  (installer: `install_local_brain.py`, default model only) is added to the
  llama-server preset with `mmproj-offload = false` (projector on CPU; the
  4 GB card has ~600 MB free with the 4B loaded).
- `LocalOpenAIBrain` declares vision when its projector exists, keeps the
  `screenshot` tool through the tool trim, and caps images at 768 px.
  Measured: 1280 px 56 s, 768 px 17 s (correct), 512 px 7 s (misread).
- `brain/screen_intent.py`: an explicit screen request (ja/en/de/es) mandates
  the screenshot tool on a seeing brain; the 4B otherwise answered from
  window titles. Also feeds `vision_gate.has_visual_marker`.
- VERIFIED live: chat "gamen wo mite ..." -> "Looking at the screen" -> answer
  from the image, 37 s end to end.
- Not done: the 9B developer model has no projector (text only).

## Phase 11 — Hand tracking (partial, 2026-09-18)

- `vision/hand_gesture.py`: MediaPipe hand landmarker (optional install +
  model via `scripts/install_hand_tracking.py`), open palm held 1 s ->
  KillRequested. Opt-in `[trigger] palm_stop_enabled` (keeps the camera on).
- Verified: landmarker loads and runs on this box; logic unit-tested.
  Not verified: a real hand in front of the camera (needs the user).

## Phase 12 — iPhone (2026-09-18)

- `[ui] lan_access` (Settings -> API Keys -> Jarvis Key -> Phone access):
  a second HTTPS listener on the private LAN IP, port 47843, self-signed
  cert in `<user_data>/lan-tls`. Takes effect after a Jarvis restart.
- "Pair a phone" shows a QR with a one-time `#pair=` link; AuthGate
  exchanges it for a session and strips it from the address bar.
- VERIFIED (curl on the LAN address): no session 401, pairing exchange 204,
  then 200; replayed token 401; pairing requested from the LAN 403; plain
  HTTP and the desktop port are unreachable from the LAN.
- Open: a real iPhone scan (first visit shows a certificate warning).

## Copilot features: Material / Teacher / Workflow (2026-09-19)

Minimal, additive. Backup: tag `pre-three-features` (313e517).

- **Wiring**: one deterministic gate in `BrainManager._generate_untagged`
  next to the teacher gate (`jarvis/copilot/gate.py`), handler
  `_handle_copilot_command`. No new router tool, no new service; any failure
  becomes a reply, never a crashed turn (gate import is guarded too).
- **Material** (`jarvis/copilot/material.py`): "... shiryou wo tsukutte" /
  "make a slide deck ...". Active brain writes a JSON outline -> python-pptx
  (new optional extra `[material]`, ~0.5 MB, on existing lxml/Pillow) ->
  PDF printed by the local Edge/Chrome headless (no new dependency; temp dir
  auto-deleted) -> 9 deterministic checks (count vs order, titles, empty,
  bullets/slide, text length, duplicates, PPTX re-opens, PDF written).
  Output `<user_data>/materials/<stamp>/`. Nothing is sent (human delivers).
  VERIFIED live: 5-slide Japanese science deck, 35 s on the local 4B, 9/9,
  PDF with Yu Gothic embedded.
- **Teacher**: existing `jarvis/teacher` (plan -> live silent record +
  summaries -> report); tests pass, unchanged.
- **Workflow** (`jarvis/copilot/workflow.py`): "sagyou no kansatsu wo
  kaishi/shuuryou" / "start/stop observing my work". Foreground app + title
  every 3 s (awareness PrivacyFilter drops blocked/browser titles), no keys,
  no screenshots, in memory only, auto-stop after 8 h. Stop -> time per app +
  repeated app sequences as automation candidates with a suggested approach;
  report only (ja/en) to `<user_data>/workflow/`. Nothing is automated.
  VERIFIED live: 1-min observation reported real apps.
- Not done: revising a deck by page ("make page 7 shorter"), building/running
  an automation (would need the approval flow), mode dashboard UI.

## Changelog

- 2026-09-19: Japanese UI/reply language; Copilot Material + Workflow (Teacher reused).

- 2026-09-18: Emergency stop (ja voice, ESC hold, open palm), doctor local-stack check, Phase 6 local vision.
- 2026-09-18: Teacher Mode (plan / in-class listening + summary / report) verified via chat.
- 2026-09-18: local model modes (4B/9B) + developer project-chat loop verified; run_shell cwd + install-confirmation fixes.
- 2026-09-18: Phase 7 double-clap wake + opt-in spoken wake ack; install_voicevox.py syntax fix.
- 2026-09-18: Japanese long-term memory save+recall verified across restart; PC op (Notepad) verified.
- 2026-09-18: Phase 2 (ja STT) and Phase 3 (VOICEVOX TTS) live-verified; 3 pre-existing bugs fixed (vosk deadlock, STT busy drop, CJK sentence split).
- 2026-09-18: first-turn latency 100 s -> 5 s; Japanese reply pin.
- 2026-09-18: Phase 1 local brain implemented and live-verified (see above).
- 2026-09-18: Phase 0 analysis written. No code modified.
