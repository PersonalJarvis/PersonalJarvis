# Voice Turn-Taking Manual Tests

Run with `voice.bat` or `python -m jarvis.speech.watchdog`, then inspect
`data/jarvis_watchdog.log`.

1. Short pause inside one command:
   - Say: "Jarvis, oeffne ... Chrome" with a pause shorter than one second.
   - Expected: one VAD endpoint after the final word, one final transcript, then response.

2. Long silence after command:
   - Say a normal command, then stop speaking completely.
   - Expected: `VAD endpoint: reason=silence`, `transcript final`, `PROCESSING`,
     then `JARVIS_SPEAKING` promptly.

3. Missing or slow final transcript:
   - Temporarily slow the STT provider or disconnect its backend.
   - Expected: `STT final timeout ... reset turn to LISTENING`; Jarvis does not
     stay stuck in `WAITING_FOR_FINAL_TRANSCRIPT`.

4. Background noise / silent room:
   - Leave the mic open in a quiet room and near low fan noise.
   - Expected: no `voice activity start` from complete silence. Brief false starts
     should log `VAD false start discarded` and return to listening.

5. User interruption during Jarvis speech:
   - Ask for a longer answer, then speak clearly over TTS after the first second.
   - Expected: barge-in stops playback only for clear user speech, not for TTS echo.
