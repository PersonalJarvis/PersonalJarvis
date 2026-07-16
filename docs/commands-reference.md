# App Commands — Reference

_Generated from the Command Registry by `scripts/ci/gen_commands_reference.py` — do not edit by hand._

Every command below is available on four surfaces backed by the SAME endpoint and validation chain:

- **Voice/chat** — Jarvis's `app-command` tool (say it naturally).
- **Desktop UI** — the sidebar section named per command.
- **CLI** — `jarvis commands list` / `jarvis commands show <id>` to browse; execute via the curated command or `jarvis api <tag> <op>`.
- **REST** — the endpoint listed per command (machine-readable catalog: `GET /api/commands`).

Commands marked **requires confirmation** never run on a bare voice request — Jarvis asks first (two-turn confirm); the CLI needs `--yes`.

## `brain-switch` — Switch brain provider

Switch the ACTIVE main brain (LLM) provider, e.g. from openai to claude-api. Reversible; validated against the provider catalog and stored credentials.

- **Endpoint:** `POST /api/brain/switch`
- **Arguments:** `provider` (one of: claude-api, gemini, grok, nvidia, openai, openrouter; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `apikeys`
- **Voice example (EN):** "switch the brain provider to claude"

## `tts-switch` — Switch voice (TTS) provider

Switch the active text-to-speech provider (live, no restart).

- **Endpoint:** `POST /api/tts/switch`
- **Arguments:** `provider` (one of: cartesia, elevenlabs, gemini-flash-tts, grok-voice, inworld, openrouter-tts; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `apikeys`
- **Voice example (EN):** "switch the voice to elevenlabs"

## `stt-switch` — Switch speech-recognition (STT) provider

Switch the speech-to-text provider. Takes effect on the next voice-pipeline start (restart required).

- **Endpoint:** `POST /api/stt/switch`
- **Arguments:** `provider` (one of: groq-api, openai-api, openrouter-stt; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `apikeys`
- **Voice example (EN):** "switch speech recognition to deepgram"

## `realtime-switch` — Switch realtime voice provider

Switch which realtime voice engine (speech-to-speech) is active, e.g. openai-realtime or gemini-live.

- **Endpoint:** `POST /api/realtime/switch`
- **Arguments:** `provider` (one of: gemini-live, openai-realtime; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `apikeys`
- **Voice example (EN):** "switch the realtime model to gemini"

## `computer-use-switch` — Switch Computer-Use provider

Switch the dedicated Computer-Use planner provider (screen control), decoupled from the main brain.

- **Endpoint:** `POST /api/computer-use/switch`
- **Arguments:** `provider` (one of: antigravity, claude-api, codex, gemini, grok, nvidia, openai, openrouter; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `apikeys`
- **Voice example (EN):** "switch the computer use provider to gemini"

## `jarvis-agent-switch` — Switch Jarvis-Agent (worker) provider

Switch the Jarvis-Agent / worker provider used for missions (e.g. codex to openai). Restart required.

- **Endpoint:** `POST /api/jarvis-agent/switch`
- **Arguments:** `provider` (one of: antigravity, claude-api, codex, gemini, grok, nvidia, openai, openrouter; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `agents`
- **Voice example (EN):** "switch the agent provider to openai"

## `providers-list` — List providers

List all configured providers and which ones are active.

- **Endpoint:** `GET /api/providers`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `apikeys`
- **Voice example (EN):** "which providers are configured"

## `provider-test` — Test a provider

Test connectivity and authentication for one provider.

- **Endpoint:** `POST /api/providers/{provider_id}/test`
- **Arguments:** `provider_id` (one of: antigravity, cartesia, claude-api, codex, elevenlabs, gemini, gemini-flash-tts, gemini-live, grok, grok-voice, groq-api, inworld, nvidia, openai, openai-api, openai-realtime, openrouter, openrouter-stt, openrouter-tts; required)
- **Requires confirmation:** no
- **Desktop UI section:** `apikeys`
- **Voice example (EN):** "test the openai provider"

## `reply-language-set` — Set reply language

Pin the language Jarvis answers in (auto follows the spoken language).

- **Endpoint:** `PUT /api/settings/reply-language`
- **Arguments:** `language` (one of: auto, de, en, es; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `languages`
- **Voice example (EN):** "answer in german from now on"

## `voice-mode-set` — Set voice mode (pipeline / realtime)

Choose the voice engine: the classic STT-brain-TTS pipeline or a realtime speech-to-speech model.

- **Endpoint:** `PUT /api/settings/voice-mode`
- **Arguments:** `mode` (one of: pipeline, realtime; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `settings`
- **Voice example (EN):** "switch to realtime mode"

## `wake-word-get` — Show wake word

Show the current wake word and wake-engine settings.

- **Endpoint:** `GET /api/settings/wake-word`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `settings`
- **Voice example (EN):** "what is my wake word"

## `wake-word-set` — Change wake word

Set the phrase that wakes Jarvis up.

- **Endpoint:** `PUT /api/settings/wake-word`
- **Arguments:** `phrase` (string; required)
- **Requires confirmation:** no
- **Desktop UI section:** `settings`
- **Voice example (EN):** "change my wake word to nova"

## `tts-volume-set` — Set voice volume

Set the text-to-speech output volume (0.0 to 1.0).

- **Endpoint:** `PUT /api/settings/tts-volume`
- **Arguments:** `volume` (number; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `settings`
- **Voice example (EN):** "set the voice volume to 50 percent"

## `audio-devices-list` — List audio devices

List available speaker and microphone devices.

- **Endpoint:** `GET /api/settings/audio-devices`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `settings`
- **Voice example (EN):** "list my audio devices"

## `wiki-ingest` — Store a fact in the Wiki

Store one self-contained fact or summary through the guarded Wiki curator. The command succeeds only after a page is written.

- **Endpoint:** `POST /api/wiki/ingest`
- **Arguments:** `text` (string; required); `source` (string; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `memory`
- **Voice example (EN):** "store that in my wiki"

## `session-latest-turn` — Show latest voice turn

Return the latest persisted user transcript and its complete voice turn, optionally restricted to one session.

- **Endpoint:** `GET /api/sessions/latest-turn`
- **Arguments:** `session_id` (string; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `sessions`
- **Voice example (EN):** "read the latest transcript"

## `tools-list` — List effective tools

Return the effective live Brain tool surface, including native, connected CLI, Marketplace, and MCP tools.

- **Endpoint:** `GET /api/tools`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `settings`
- **Voice example (EN):** "list the connected tools mcps and clis"

## `app-restart` — Restart Jarvis

Restart the Jarvis desktop app (voice + UI restart too).

- **Endpoint:** `POST /api/settings/restart-app`
- **Arguments:** none
- **Requires confirmation:** yes
- **Desktop UI section:** `settings`
- **Voice example (EN):** "restart jarvis"

## `missions-list` — List missions

List Jarvis-Agent missions and their status.

- **Endpoint:** `GET /api/missions`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `agents`
- **Voice example (EN):** "show me the missions"

## `mission-result` — Read a mission result

Read the signed summary and actual deliverable contents of one completed Jarvis-Agent mission. Use this after listing missions when the user asks what a mission found or produced.

- **Endpoint:** `GET /api/missions/{mission_id}/result`
- **Arguments:** `mission_id` (string; required)
- **Requires confirmation:** no
- **Desktop UI section:** `agents`
- **Voice example (EN):** "what did the mission find"

## `mission-cancel` — Cancel a mission

Cancel a running Jarvis-Agent mission by id.

- **Endpoint:** `POST /api/missions/{mission_id}/cancel`
- **Arguments:** `mission_id` (string; required)
- **Requires confirmation:** yes
- **Desktop UI section:** `agents`
- **Voice example (EN):** "cancel the mission"

## `tasks-list` — List tasks

List scheduled and running tasks.

- **Endpoint:** `GET /api/tasks`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `tasks`
- **Voice example (EN):** "show me my tasks"

## `task-cancel` — Cancel a task

Cancel a running or scheduled task by id.

- **Endpoint:** `POST /api/tasks/{task_id}/cancel`
- **Arguments:** `task_id` (string; required)
- **Requires confirmation:** yes
- **Desktop UI section:** `tasks`
- **Voice example (EN):** "cancel the task"

