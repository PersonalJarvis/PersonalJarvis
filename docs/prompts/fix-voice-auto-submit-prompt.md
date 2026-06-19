# Prompt: Fix Jarvis Voice Auto-Submit on Brief Pauses

You are working in the Personal Jarvis repository at:

`C:\Users\Administrator\Desktop\Personal Jarvis`

Your task is to fix an intermittent voice bug: while the user is speaking a prompt to Jarvis, a very brief thinking or breathing pause, roughly 0.5 seconds, sometimes causes Jarvis to finalize and submit the prompt even though the user clearly has not finished the sentence. This is annoying because the brain receives a half-prompt and the conversation flow breaks.

## Goal

Make the normal Jarvis voice path robust against premature submission during short mid-sentence pauses.

Done means:

- A quiet 0.5 second mid-sentence pause after real speech does not end the voice turn, does not call the brain, and does not create a submitted user prompt if the user continues speaking.
- Intentional end-of-turn still works promptly after the configured endpoint policy, currently `vad_silence_ms=1500`.
- Loud speaker bleed protection still works: if music/TV/background audio keeps VAD active after the user stops, the STT stability probe may still force an endpoint.
- Push-to-talk and chat mic dictation semantics are unchanged.
- The fix is proven by targeted tests and the relevant existing speech/audio tests remain green.

## Required Context

Read these files first, in this order:

1. `CLAUDE.md`
   - Follow the repository rules: English artifacts, cloud-first defaults, no unrelated refactors, do not overwrite user changes.
   - This repo may have a dirty worktree. Treat unrelated changes as user work.

2. `docs/BUGS.md`
   - Read the entry `Bug Voice-Turn-2026-05-31: "keeps listening, never answers" - grace-on-COMPLETE recurrence`.
   - Important rule from that entry: a completed prompt is dispatched, never parked. Do not reintroduce grace-hold on every COMPLETE utterance.

3. `docs/plans/voice-endpoint-patience/README.md`
   - This explains the original root cause: the STT stability probe could bypass `silence_ms` via `request_endpoint()`.
   - It also explains the intended design: quiet empty/stable tails are thinking pauses; loud empty/stable tails are speaker bleed.

4. `docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-design.md`
   - Boundary: semantic incompleteness is a higher layer after acoustic endpointing. Do not confuse this with fixing premature acoustic endpointing.

5. Relevant code:
   - `jarvis/speech/pipeline.py`
   - `jarvis/audio/vad.py`
   - `jarvis/speech/completion.py`
   - `jarvis/speech/pending_buffer.py`
   - `jarvis/ui/desktop_app.py`
   - `jarvis/ui/web/frontend/src/components/ChatInput.tsx`
   - `jarvis/ui/web/frontend/src/hooks/useWebSocket.ts`

6. Relevant tests:
   - `tests/unit/audio/test_vad_turn_taking.py`
   - `tests/unit/speech/test_thinking_pause_patience.py`
   - `tests/unit/speech/test_turn_taking.py`
   - `tests/unit/speech/test_pipeline_completion.py`
   - `tests/unit/speech/test_long_dictation_accumulation.py`
   - `tests/unit/speech/test_pipeline_push_to_talk.py`

## Current Code Map

The likely surface is the normal voice path, not typed chat.

Known current behavior:

- `SpeechPipeline.__init__` has `vad_silence_ms: int = 1500`. This is the current "1.5 second pause rule".
- `jarvis/ui/desktop_app.py` constructs `SpeechPipeline(...)` without overriding `vad_silence_ms`, so the constructor default is the live desktop default unless another caller differs.
- `SileroEndpointer.utterances()` in `jarvis/audio/vad.py` ends an utterance through three broad mechanisms:
  - natural silence: `silent_run >= _silence_frames`
  - forced max length: `reached_max`
  - external endpoint request: `_endpoint_requested`, usually from the STT probe, reported as `stt_stable`
- The VAD probe callback now passes `tail_loud` based on tail RMS vs the same relative-silence floor used by the VAD state machine.
- `SpeechPipeline._on_vad_probe()` and `_stt_probe_async()` are supposed to force endpoints only for loud empty/stable tails. Quiet empty/stable tails should defer to natural silence.
- `_on_vad_probe(..., tail_loud=True)` defaults to `True` for legacy/direct callers. Verify no live path accidentally uses the default for quiet tails.
- `VoiceConfig.complete_grace_ms = 1500` still exists in config, but current `_complete_or_buffer_context()` returns fresh COMPLETE utterances immediately. This is intentional because the old complete-grace behavior caused a fixed regression where Jarvis kept listening and never answered.
- Chat mic dictation is a separate endpoint-free path: `ChatInput.tsx` sends `stt_dictate`; `SpeechPipeline._dictation_session()` captures until explicit stop/max duration and publishes `DictationTranscript`. It should not auto-submit to the brain.
- Push-to-talk is endpoint-free by design: holding records, releasing submits. Do not turn normal voice mode into PTT unless the user explicitly asks.

## Non-Negotiable Constraints

- Do not blindly increase every timeout. First prove which endpoint path fires too early.
- Do not reintroduce grace-hold on every COMPLETE transcript. `tests/unit/speech/test_pipeline_completion.py::test_complete_text_returns_unchanged` must stay green.
- Do not disable the STT probe entirely. It is needed for speaker bleed.
- Do not broaden semantic incomplete heuristics as the first fix. The reported symptom is acoustic premature endpointing unless logs prove otherwise.
- Do not route chat dictation through `_handle_utterance`.
- Do not add an LLM call to the voice hot path.
- Keep changes narrowly scoped to speech/audio endpointing and only touch frontend if reproduction proves the chat mic is the actual surface.

## Investigation Plan

Start by finding the exact premature endpoint path.

1. Run preflight:

```powershell
pwsh scripts/preflight.ps1
python -c "import jarvis; print(jarvis.__file__)"
```

2. Inspect recent logs around a reproduction, especially:

```powershell
rg -n "voice activity stop: reason=|VAD endpoint: reason=|STT probe:|quiet empty tail|stable but quiet|force endpoint|completion" data logs -S
```

Classify the premature submission:

- If the endpoint reason is `stt_stable` before the configured 1500 ms quiet window, the STT probe or `tail_loud` handling is bypassing patience.
- If the endpoint reason is `silence`, the VAD state machine is treating the pause as a true end-of-turn or failing to cancel silence when the user resumes.
- If the final transcript is COMPLETE and goes to the brain immediately after a real 1500 ms silence, that is expected under the current design. Do not "fix" it with complete-grace buffering.
- If the surface is chat mic dictation, prove it by following `DictationTranscript` in `useWebSocket.ts` and `ChatInput.tsx`; dictation should only fill the textarea and should not call `send()` automatically.

3. Check for stale-build/import traps:

- Confirm the live import path points at this checkout.
- If frontend is touched, rebuild the frontend and restart the desktop app before judging behavior.

## Test-First Requirements

Write or extend failing tests before changing production code.

Minimum useful regression coverage:

1. VAD-level test in `tests/unit/audio/test_vad_turn_taking.py`
   - Script frames for: real speech -> roughly 0.5 s quiet pause -> real speech resumes -> real end silence.
   - Assert exactly one utterance is yielded and the midpoint pause does not split the turn.
   - Include a probe callback that would request endpoint only if it receives a loud/stable signal, so the quiet-pause path is covered.

2. Probe-level test in `tests/unit/speech/test_thinking_pause_patience.py`
   - Quiet empty tail does not call `request_endpoint()`.
   - Quiet stable tail does not call `request_endpoint()`.
   - Loud empty/stable tails still do call `request_endpoint()`.
   - If these tests already exist and pass, add the missing end-to-end case that reproduces the actual failure.

3. Pipeline-level guard if the failure is not caught at VAD level
   - Build a minimal `SpeechPipeline.__new__` stub like existing speech tests.
   - Prove no brain dispatch happens until the resumed speech is included.

4. If frontend dictation is actually the failing surface
   - Add a Vitest test proving final `DictationTranscript` commits text to the textarea but does not call `send()` or WebSocket `type:"message"` automatically.

## Implementation Guidance

Prefer the smallest fix that matches the observed endpoint reason.

Likely fix areas:

- `jarvis/audio/vad.py`
  - Verify `tail_loud` is false during quiet breath/thinking pauses.
  - Verify `cancel_hysteresis_ms` and relative-silence logic do not prevent a real resumed phrase from cancelling the silence timer.
  - Preserve the speaker-bleed fix: isolated loud spikes should not reset silence, but sustained user speech should.

- `jarvis/speech/pipeline.py`
  - Verify every live call to `_on_vad_probe()` gets the `tail_loud` computed by VAD.
  - Verify stale cloud STT probes cannot force endpoints on a later turn. The generation guard must remain intact.
  - If the probe default `tail_loud=True` is causing a live bypass, remove that live bypass without breaking tests that intentionally call the method directly.

- `jarvis/core/config.py`
  - Only add or move endpoint settings if the fix truly requires a configurable source of truth.
  - If you add config, keep `ConfigDict(extra="allow")` and add tests.

Avoid:

- Re-arming `_schedule_completion_timeout(..., is_complete=True)` for every complete transcript.
- Making `completion_wait_ms` responsible for acoustic pauses.
- Treating low STT confidence alone as "done"; prior BUG-018 says that cuts off real speech tails.

## Acceptance Commands

Run at least:

```powershell
pytest tests/unit/audio/test_vad_turn_taking.py -v
pytest tests/unit/speech/test_thinking_pause_patience.py -v
pytest tests/unit/speech/test_turn_taking.py -v
pytest tests/unit/speech/test_pipeline_completion.py -v
pytest tests/unit/speech/test_long_dictation_accumulation.py -v
pytest tests/unit/speech/test_pipeline_push_to_talk.py -v
python -m py_compile jarvis/speech/pipeline.py jarvis/audio/vad.py
ruff check jarvis/speech jarvis/audio tests/unit/speech tests/unit/audio
```

If frontend dictation is touched:

```powershell
cd jarvis/ui/web/frontend
npm run test
npm run build
```

## Live Verification

After tests pass, verify manually or with an existing probe script:

- Say a prompt with a short mid-sentence pause, around 0.5 s, then continue. The submitted transcript must contain both sides of the pause.
- Say a prompt and then stop. Jarvis should still submit after the configured natural endpoint.
- Play loud background audio or speaker bleed while staying silent after a prompt. The turn should still end promptly; do not regress the speaker-bleed cure.

Useful scripts to inspect:

- `scripts/voice_e2e_probe.py`
- `scripts/voice_compare.py`

## Final Report Format

When finished, report:

- Root cause: exact endpoint reason and code path.
- Files changed.
- Tests added or updated.
- Acceptance command results.
- Live verification result, or why it could not be run.
- Any remaining risk, especially if the bug is intermittent or hardware-dependent.
