# Codex Handoff — Guarantee: Realtime voice mode ALWAYS keeps a transcript

Repo: `C:\Users\Administrator\Desktop\Personal Jarvis` (Personal Jarvis). Read `CLAUDE.md`/`AGENTS.md` first — its rules are binding (English-only artifacts, shared working tree, no push).

<task>
Personal Jarvis has two voice engines: the classic pipeline (STT→Brain→TTS) and the realtime engine (duplex sessions, `jarvis/realtime/`). The maintainer is making realtime the primary engine and needs a hard guarantee: **every realtime conversation turn — on BOTH surfaces (desktop and browser), with EVERY realtime provider (gemini-live, openai-realtime), in BOTH tool modes (`voice.realtime_tool_mode` = "delegate" default, "direct") — lands as a complete row (user_text + jarvis_text) in the sessions.db transcript store** (`jarvis/sessions/store.py`, consumed by the Transcription/Run-Inspector UI).

Current wiring (verified 2026-07-11, treat as starting evidence, re-verify yourself):
- The transcript store is fed by `SessionRecorder` (`jarvis/sessions/recorder.py`), a wildcard bus subscriber. Hard gate: `recorder.py:220` drops ALL events while `self._state is None`; state is only created by a `VoiceSessionStarted` event (`recorder.py:204`).
- The realtime session (`jarvis/realtime/session.py`) publishes `VoiceSessionStarted`/`VoiceSessionEnded` ONLY on the browser surface (guard at `session.py:302-303`, end guard ~`:1042`). It publishes `VoiceTurnStarted` (`:692`) and `VoiceTurnCompleted` with `user_text`/`jarvis_text`/`tier="realtime"` (`:709-747`) unconditionally.
- On desktop, the pipeline session loop wraps the realtime session: it publishes `VoiceSessionStarted` itself (`jarvis/speech/pipeline.py:4919-4926`), then enters `_active_realtime_session()` (`pipeline.py:5098`, impl `:5248`) and passes the SAME `session_id` into the realtime session (`pipeline.py:5316`). So the happy path appears covered by design.

Your job: prove or break that guarantee end-to-end, fix every REAL gap you find, and lock the guarantee with regression tests so it cannot silently regress. Candidate gaps to probe (non-exhaustive — probe beyond this list):
1. Turns where the provider never delivers a final user transcript (`_last_user_text` empty) or never delivers `turn_complete` — is a row still written / finalized? What happens on hangup mid-turn, provider error, provider-family failover mid-session (`session._open` retry chain)?
2. `recorder._on_turn_completed` (`recorder.py:364`) ignores the event when `current_turn.turn_id != event.turn_id` — can realtime turn-id sequencing (turn started on first FINAL input transcript, `session.py:441-444`; reset at `:744`) ever desync from the recorder's `_ensure_turn_open`/auto-close logic?
3. Browser surface: `jarvis/browser_voice/route.py` — same guarantee holds there (session start/end published, turns finalized on socket drop)?
4. Session end: desktop publishes `VoiceSessionEnded` in the pipeline's `finally` (`pipeline.py:4990`) — verify open turns are finalized, not orphaned.
5. Raw event visibility: check `_RAW_EVENT_KINDS` (`recorder.py:70-108`) records enough realtime events (`RealtimeSessionReady`, `TranscriptionUpdate`?) for the Run-Inspector to be useful.
If a probed gap turns out NOT to exist, say so with evidence instead of inventing a fix.
</task>

<key_files>
- `jarvis/realtime/session.py` — session lifecycle, `_pump`, `_publish_turn_started/_publish_turn_completed`, browser-gated session events
- `jarvis/speech/pipeline.py:4895-5010` (session loop + VoiceSessionStarted/Ended), `:5248-5400` (`_active_realtime_session`)
- `jarvis/sessions/recorder.py` + `jarvis/sessions/store.py` + `jarvis/sessions/init.py`
- `jarvis/browser_voice/route.py` (browser surface)
- `jarvis/core/events.py` (`VoiceSessionStarted`, `VoiceTurnStarted`, `VoiceTurnCompleted`, `TranscriptionUpdate`)
- Existing tests: `tests/unit/sessions/`, anything matching `test_*recorder*`, `tests/unit/realtime/`
</key_files>

<constraints>
- CLAUDE.md is binding: everything you commit is English; Conventional Commit messages; NEVER push; the working tree is SHARED with other live agent sessions — stage only your own files by explicit path (never `git add -A`/`git add .`), commit after each completed logical step.
- Do NOT change the `VoiceTurnCompleted` event shape or the meaning of its `user_text`/`jarvis_text`/`tier` fields — a parallel session is building the realtime→wiki memory feed on exactly that event.
- A separate Codex session is fixing the duplicate `ResponseGenerated` publish in delegate mode — do not touch that; your scope is transcript persistence only.
- EventBus rules: subscribers must never propagate exceptions (AP-18); events are frozen dataclasses carrying `trace_id`+`timestamp_ns`.
- Nothing heavy on the voice hot path (AP-9) and nothing on the boot critical path (AP-26).
- Cross-platform: fixes and tests must run on Windows, macOS, and headless Linux (`python:3.11-slim`, no audio) — tests use fakes from `tests/fakes/`, never real audio devices, never `unittest.mock` per repo convention.
</constraints>

<default_follow_through_policy>
Default to the most reasonable low-risk interpretation and keep going. Only stop when a missing detail changes correctness or an irreversible action.
</default_follow_through_policy>

<completeness_contract>
Resolve the task fully before stopping: verify all four surface/provider/tool-mode combinations conceptually, fix every confirmed gap, and add the regression tests. Do not stop at the first plausible answer.
</completeness_contract>

<verification_loop>
Before finalizing: run the touched test modules plus `tests/unit/sessions/` and `tests/unit/realtime/` (`pytest -m "not slow"` subset is acceptable), and `ruff check` on changed files. If a check fails, fix it instead of reporting the first draft.
</verification_loop>

<grounding_rules>
Ground every claim in file:line evidence or test output. If a gap is a hypothesis you could not reproduce, label it clearly instead of "fixing" it.
</grounding_rules>

<action_safety>
Keep changes tightly scoped to transcript persistence. No unrelated refactors or renames.
</action_safety>

<structured_output_contract>
Return, in order:
1. Verdict per candidate gap (real / not real) with file:line evidence.
2. Fixes applied (files + one-line rationale each).
3. Regression tests added (test names + what each locks).
4. Test/lint run output summary.
5. Commits made.
</structured_output_contract>
