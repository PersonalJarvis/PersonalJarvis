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
- **Arguments:** `provider` (one of: claude-api, gemini, grok, local-openai, nvidia, ollama, openai, openrouter; required); `persist` (boolean; optional)
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
- **Arguments:** `provider` (one of: antigravity, claude-api, codex, gemini, grok, local-openai, nvidia, ollama, openai, openrouter; required); `persist` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `apikeys`
- **Voice example (EN):** "switch the computer use provider to gemini"

## `jarvis-agent-switch` — Switch mission-worker provider

Switch the provider used for new missions (e.g. codex to openai). The next mission uses the new provider.

- **Endpoint:** `POST /api/jarvis-agent/switch`
- **Arguments:** `provider` (one of: antigravity, claude-api, codex, gemini, grok, local-openai, nvidia, ollama, openai, openrouter; required); `persist` (boolean; optional)
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
- **Arguments:** `provider_id` (one of: antigravity, cartesia, claude-api, codex, elevenlabs, gemini, gemini-flash-tts, gemini-live, grok, grok-voice, groq-api, inworld, local-openai, nvidia, ollama, openai, openai-api, openai-realtime, openrouter, openrouter-stt, openrouter-tts; required)
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

## `agentic-ide-status` — Agentic IDE status

Report the open Agentic-IDE workspace: which folder, which coding agents run in which named terminals, and whether the focused coding mode is on.

- **Endpoint:** `GET /api/agentic-ide/state`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "what is running in the agentic ide"

## `agentic-ide-terminal-report` — Report on one Agentic-IDE terminal

Read what the coding agent in a named terminal is doing — its status and its recent terminal output. Use this whenever the user asks about a terminal by name (e.g. 'what is Mika doing?').

- **Endpoint:** `GET /api/agentic-ide/terminals/{name}/report`
- **Arguments:** `name` (string; required); `lines` (integer; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "what is mika doing"

## `agentic-ide-prompt` — Prompt an Agentic-IDE terminal

Send an instruction to the coding agent in a named terminal. Use this whenever the user tells a terminal to do something ('tell Kai to ...', 'Mika soll ...', 'let Nova refactor ...') — that work belongs to that agent, never to a background worker. Pass the user's instruction as the prompt; with compose=true it is rewritten into a briefed task with the relevant files of this workspace attached. CHECK THE REPLY: it carries a 'submitted' flag. True means the agent accepted the prompt and started. False means the text is only sitting in that terminal's input box — say so plainly and name the terminal, never report it as done.

- **Endpoint:** `POST /api/agentic-ide/terminals/{name}/prompt`
- **Arguments:** `name` (string; required); `prompt` (string; required); `compose` (boolean; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "tell mika to run the tests"

## `agentic-ide-spawn-terminals` — Open more Agentic-IDE terminals

Open one or more additional coding terminals in the open workspace. Use this when the user asks for MORE TERMINALS / panes ('spawn five new Claude Code terminals', 'open two more Codex terminals') — that is a request for workspace panes, never for a background worker. Pass count, and agent only when the user named one ('claude' or 'codex'); omitted, the new panes run whatever the last pane runs. CHECK THE REPLY: 'capped' true means the workspace maximum cut the request short — say how many actually opened and name them, never report the full number as done.

- **Endpoint:** `POST /api/agentic-ide/terminals/batch`
- **Arguments:** `count` (integer; required); `agent` (one of: claude, codex; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "spawn five new claude code terminals"

## `agentic-ide-move-terminal` — Move an Agentic-IDE terminal in the grid

Rearrange the open workspace: put one terminal at another one's place. Nothing is started or stopped — the panes keep their agents and their conversations, only where they are drawn changes. Use it for 'swap Mika and Nova', 'put Mika next to Nova', 'move Mika under Nova'. 'swap' exchanges the two panes and leaves the rest of the grid alone; 'left'/'right' give the moved pane its own column beside the target; 'above'/'below' stack it in the target's column. Both names must be terminals that are already open.

- **Endpoint:** `POST /api/agentic-ide/terminals/{name}/move`
- **Arguments:** `name` (string; required); `target` (string; required); `position` (one of: swap, left, right, above, below; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "swap mika and nova"

## `agentic-ide-close-agent-terminals` — Close Agentic-IDE terminals by coding agent

Stop and remove every terminal of one coding CLI in the front workspace. Use only when the user explicitly asks to close all Claude Code or all Codex terminals; this is destructive and requires confirmation.

- **Endpoint:** `DELETE /api/agentic-ide/terminals/agent/{agent}`
- **Arguments:** `agent` (one of: claude, codex; required)
- **Requires confirmation:** yes
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "close all codex terminals"

## `agentic-ide-focus` — Toggle Agentic-IDE focus mode

Turn the focused coding mode on or off. While on, answers are given inside the open coding workspace; turning it off returns to normal behaviour without stopping any agent.

- **Endpoint:** `PUT /api/agentic-ide/mode`
- **Arguments:** `enabled` (boolean; required)
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "switch into coding mode"

## `agentic-ide-resume` — Resume the last Agentic-IDE workspace

Reopen the coding workspace that was last open: the same folder, the same named terminals in the same grid positions, running the same coding CLIs — and continuing the same conversations wherever that CLI supports it. Use this when the user asks for their terminals or their coding session back after closing the window, restarting the app, or rebooting. CHECK THE REPLY: 'resumable_count' is how many panes actually continued their conversation and 'started_fresh' how many reopened empty. Name the empty ones — an agent that lost its history looks exactly like one that did not until it is asked a follow-up question.

- **Endpoint:** `POST /api/agentic-ide/resume`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "resume all my coding sessions"

## `agentic-ide-interrupted` — List interrupted Agentic-IDE sessions

Which coding terminals came back holding their conversation and have been told nothing since. That is what a restart leaves behind: reopening a workspace reconnects each pane to the conversation it was having, but the coding CLI reads that transcript and then WAITS at its prompt — so an agent stopped mid-task looks exactly like one that finished. Use this to answer 'what was interrupted?' before continuing anything. 'continuable' is per pane: a pane whose agent is not running cannot be typed into, and 'blocked_reason' says why.

- **Endpoint:** `GET /api/agentic-ide/interrupted`
- **Arguments:** none
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "which coding sessions were interrupted"

## `agentic-ide-continue-interrupted` — Continue interrupted Agentic-IDE sessions

Tell the coding terminals a restart left standing still to carry on: 'continue' is typed into each one and submitted. With no names, every interrupted pane in every open workspace — which is the shape of the problem, since a restart stops them all at once. CHECK THE REPLY: 'continued' really started, 'queued' had not finished starting yet and will carry on by itself within seconds (say 'shortly', not 'done'), 'unconfirmed' had the text typed in without a confirmed submit (it may be sitting in the input box — tell the user to look at that pane), and 'failed' names what refused and why. Reporting an unconfirmed or queued pane as running is the one wrong thing to do with this answer. Pressing twice is safe: each pane is claimed before anything is typed, so a repeat call cannot send a second 'continue' into the same agent.

- **Endpoint:** `POST /api/agentic-ide/interrupted/continue`
- **Arguments:** `names` (array; optional); `prompt` (string; optional)
- **Requires confirmation:** no
- **Desktop UI section:** `agentic-ide`
- **Voice example (EN):** "continue the interrupted coding sessions"

