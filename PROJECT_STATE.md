# PROJECT_STATE

Living state of the "keyless local JARVIS" upgrade. Updated after every phase.
Target box: Windows 11, RTX 3050 Laptop (4 GB VRAM), 16 GB RAM, Python 3.13 venv.

## Phase status

| Phase | Topic | Status |
|---|---|---|
| 0 | Full analysis | DONE (this file) — no code changed |
| 1 | Local Brain (llama.cpp + Qwen3-4B Q4_K_M) | NOT STARTED |
| 2 | Local Japanese STT | NOT STARTED |
| 3 | Local Japanese TTS (VOICEVOX / Piper) | NOT STARTED |
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

## Proposed Phase 1 plan (awaiting go)
- Add a managed llama.cpp `llama-server` runtime (download official release
  binary, CUDA build, into the app data dir) + GGUF download (Qwen3-4B
  Q4_K_M) with a VRAM-aware `--n-gpu-layers` calculator and `-c 8192`.
- Point `local-openai` at it automatically; set `local_fallback` to it so any
  cloud 429/quota/outage/offline falls back locally.
- Model selector already exists on the provider card; extend with GGUF list.
- Verify: boot with no keys, Japanese chat turn, forced 429 fallback test.

## Changelog
- 2026-09-18: Phase 0 analysis written. No code modified.
