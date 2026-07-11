# Codex Handoff — Fix: duplicate `ResponseGenerated` per delegated realtime turn

Repo: `C:\Users\Administrator\Desktop\Personal Jarvis` (Personal Jarvis). Read `CLAUDE.md`/`AGENTS.md` first — its rules are binding (English-only artifacts, shared working tree, no push).

<task>
In realtime voice mode with the default delegate tool mode (`voice.realtime_tool_mode = "delegate"`), ONE user turn publishes the `ResponseGenerated` bus event TWICE:

1. The realtime session's `jarvis_action` tool call spawns a background delegate (`jarvis/realtime/session.py:825-910`) that runs a full router-brain turn via `BrainManager.generate(...)`. `generate` publishes `ResponseGenerated` itself when the turn completes (`jarvis/brain/manager.py:5834` and `:7620` — two generate paths; there is also a canned-phrase publish at `:2669`).
2. When the provider signals `turn_complete`, the realtime session publishes `ResponseGenerated` AGAIN in `_publish_turn_completed` (`jarvis/realtime/session.py:719-726`) — this time with the realtime model's spoken paraphrase, which may DIFFER textually from the brain's reply.

Downstream consumers therefore see two assistant replies for one turn: `jarvis/memory/message_recorder.py` (recall log — duplicate rows), `jarvis/awareness/story.py::StoryTracker._on_response_generated` (~`:287`, duplicate L2 episode markers), `jarvis/sessions/recorder.py:246` (benign — `VoiceTurnCompleted` values win), `jarvis/memory/wiki/voice_bridge.py:233` (currently no-ops in realtime because no user-text event pairs, but a realtime→wiki feed is being built in a parallel session — double-fire must not survive into that world).

Goal: **exactly ONE `ResponseGenerated` per user turn**, in every mode:
- Realtime + delegate, turn WITH a completed `jarvis_action` call → one event (decide which of the two is canonical — see design notes).
- Realtime + delegate, pure conversational turn (no tool call) → the session's publish stays (it is the only one).
- Realtime + direct tool mode → unchanged (session publish is the only one).
- Classic pipeline mode → completely unchanged (`generate`'s publish drives existing hooks: `MessageRecorder`, `VoiceFactBridge` pairing, `StoryTracker`).

Design notes (recommendation, verify before adopting): suppressing the SESSION's publish for turns whose answer came from a completed delegate is the least invasive option — the brain's publish must survive because pipeline-mode hooks key on it, and threading a `publish_response=False` flag through `generate` risks touching the shared brain path. But you own the final call; whichever side you silence, `VoiceTurnCompleted` (`session.py:727-741`) must still carry the ACTUALLY SPOKEN text in `jarvis_text`.

Edge cases that must be handled and tested:
- Delegate TIMEOUT (`_DELEGATE_TIMEOUT_S`, `session.py:849-874`): if the awaited `generate` task keeps running past the timeout and publishes late, do not end up with zero OR two events for the turn — check whether the timed-out task is cancelled and define the behavior.
- Multiple `jarvis_action` calls inside one turn.
- Delegate failure path (`:877-887`) — the model speaks an error; the session publish must then still fire (the brain never published).
- Turn where the delegate completed but the realtime model spoke an EMPTY answer (`answer` falsy at `session.py:719`) — today that publishes nothing from the session; make sure the invariant "exactly one" still holds.
</task>

<key_files>
- `jarvis/realtime/session.py` — `_start_delegate`, `_run_delegate`, `_dispatch_brain_turn` (`:825-910`), `_publish_turn_completed` (`:709-747`), turn state fields
- `jarvis/brain/manager.py` — `ResponseGenerated` publishes (`:2669`, `:5834`, `:7620`) and `_record_response_side_effects`
- Consumers: `jarvis/memory/message_recorder.py`, `jarvis/awareness/story.py`, `jarvis/sessions/recorder.py`, `jarvis/memory/wiki/voice_bridge.py`
- Existing tests: `tests/unit/realtime/` (delegate-mode tests), `tests/unit/memory/` for the recorder
</key_files>

<constraints>
- CLAUDE.md is binding: everything you commit is English; Conventional Commit messages; NEVER push; the working tree is SHARED with other live agent sessions — stage only your own files by explicit path (never `git add -A`/`git add .`), commit after each completed logical step.
- Do NOT change the `VoiceTurnCompleted` event shape or the meaning of its `user_text`/`jarvis_text` fields — a parallel session is building the realtime→wiki memory feed on exactly that event. A separate Codex session hardens transcript persistence (`SessionRecorder`); stay out of its scope.
- Classic pipeline behavior must be bit-for-bit unchanged; guard tests in `tests/unit/brain/` must stay green.
- EventBus rules: frozen event dataclasses, subscriber errors never propagate (AP-18); nothing heavy on the voice hot path (AP-9).
- Cross-platform: tests must run on headless Linux; use fakes from `tests/fakes/`, not `unittest.mock`.
</constraints>

<default_follow_through_policy>
Default to the most reasonable low-risk interpretation and keep going. Only stop when a missing detail changes correctness or an irreversible action.
</default_follow_through_policy>

<completeness_contract>
Resolve the task fully before stopping: cover all listed modes and edge cases with tests asserting the exactly-one invariant. Do not stop at the first plausible fix.
</completeness_contract>

<verification_loop>
Before finalizing: run `tests/unit/realtime/`, `tests/unit/brain/` and the touched consumer test modules; run `ruff check` on changed files. If a check fails, fix it instead of reporting the first draft.
</verification_loop>

<grounding_rules>
Ground every claim in file:line evidence or test output. If a behavior (e.g. the timeout/late-publish path) turns out different from described above, say so and adapt — do not force the described fix onto contrary evidence.
</grounding_rules>

<action_safety>
Keep changes tightly scoped to the duplicate-event invariant. No unrelated refactors.
</action_safety>

<structured_output_contract>
Return, in order:
1. Chosen design (which publish is canonical per mode) + why, with evidence.
2. Fixes applied (files + one-line rationale each).
3. Tests added (names + which invariant/edge case each locks).
4. Test/lint run output summary.
5. Commits made.
</structured_output_contract>
