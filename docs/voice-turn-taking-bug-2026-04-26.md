# Voice Turn-Taking Bug - 2026-04-26

## Symptom

After a successful Jarvis call, the desktop app stayed in `Hört zu` /
`LISTENING` and Jarvis did not answer a normal smalltalk prompt such as
`Wie geht es dir, Jarvis?`.

## Root Cause

Two independent turn-taking gaps could produce the same user-visible symptom:

1. The speech pipeline already had a fallback for wellbeing prompts, but the
   fallback was not wired into `_handle_utterance()`. If the brain returned a
   non-substantive filler such as `Ich bin einsatzbereit.`, the output filter
   suppressed it and the pipeline silently returned to `LISTENING`.
2. The VAD endpoint trusted Silero probability plus a fixed silence window. In
   noisy rooms or with fan/echo leakage, Silero can keep reporting speech after
   the user has stopped. That prevents a final transcript and keeps the UI in
   `Hört zu`.

## Fix

- `_handle_utterance()` now uses the existing wellbeing fallback before
  suppressing non-substantive responses.
- The speech pipeline now buffers only clearly incomplete context fragments
  such as dangling `wenn`, `kannst du`, `if`, or trailing conjunctions/articles.
  Complete prompts are sent to the brain immediately.
- `SileroEndpointer` now treats a strong RMS drop from the speech peak as
  silence even when VAD probability remains high. This lets Jarvis finalize a
  sentence in noisy environments without cutting off normal short pauses.

## Regression Tests

- `tests/unit/speech/test_turn_taking.py`
  - wellbeing prompt + filler brain response produces a spoken fallback.
  - incomplete context is buffered and does not call the brain.
- `tests/unit/audio/test_vad_turn_taking.py`
  - energy drop endpoints the utterance even when VAD probability stays high.
