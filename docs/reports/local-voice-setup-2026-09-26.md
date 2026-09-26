# Local voice setup and response recovery

Scope: T2, the existing managed local realtime surface. Windows has a real
synthetic audio/tool observation below. macOS and Linux retain their existing
engine implementations; portable lifecycle decisions have regression coverage,
not new physical-device qualification.

## Changes

- The two-model Apply action tests speech before selecting the local provider
  and realtime mode. A failed test leaves the active provider alone.
- Provider copy describes local voice without implying an OpenAI subscription.
  Internal Ollama context/tuning copies are hidden unless explicitly selected.
- Loading status refreshes more frequently, with jitter and no overlapping idle
  polls. An exceeded boot estimate no longer promises another five seconds.
- Local models preload for the selected primary voice, not merely because they
  are configured as a fallback. Voice startup does not load an unrelated chat
  model when the chat provider is hosted.
- A local transcript can precede the first TTS audio. That preparation uses the
  declared response-start budget; after audio starts the normal short stall
  watchdog still applies. Every response resets its audio state, including tool
  continuation. Hosted providers without a declared startup budget are unchanged.

## Live observation

A synthetic English WAV asking "What is 2 plus 3?" was streamed through the
managed server's recognition path. The model requested `add_numbers` once with
2 and 3. The real `ToolExecutor` executed a harmless test tool and returned 5.
The same realtime conversation returned "2 plus 3 is 5.", 64,512 bytes of PCM
and a clean turn-complete event. Total observation time was 13.64 seconds,
including audio feeding. No desktop action or external service was invoked.

Before the watchdog fix, the same audio was recognized and its tool result was
correct, but the connection rebuilt while local TTS was still preparing speech.
The recorded TTS first-audio time was 19.76 seconds under memory pressure;
the client incorrectly used its eight-second stream-stall guard at that point.
This demonstrates a premature interruption, not a missing tool result.

An isolated startup-budget run passed: window 1,229 ms, app interactive 14,618 ms
and existing voice readiness 14,574 ms. A separate managed-server cold start on
the busy host exceeded four minutes while available system RAM was about 300 MB.
The successful tool observation is neither an instant-response claim nor a
latency percentile. Installation time, model loading and warm inference remain
separate measurements.

## Remaining physical acceptance

The microphone wake-word path still needs a spoken-user test on the intended
running build. Earlier diagnostics resolved the configured English phrase to
Vosk and found it in the vocabulary; a quiet ambient microphone measurement
does not prove or disprove recognition of a spoken wake word. During verification
the running desktop instance changed to a different installation/checkout.
Source changes and synthetic adapter tests do not certify that other executable.
