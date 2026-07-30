---
title: "ADR-0032: Final Multilingual Dictation Transcription"
slug: adr-0032-final-multilingual-dictation
diataxis: adr
status: active
owner: project-maintainers
last_reviewed: 2026-07-30
phase: speech
audience: developer
---

# ADR-0032 — Final Multilingual Dictation Transcription

**Status:** Accepted (2026-07-30)
**Reference:** AP-21, AP-22, AP-23, AP-24, AP-26, AP-30, AP-31

## Context

The dictation lane previously delivered text assembled from short STT segments.
Each segment carried one recognition language, so a language change inside a
sentence could be misrecognized or translated. A selected cloud model was not
reliably forwarded, OpenRouter transcription did not receive deterministic
temperature, and provider adapters assumed request/response fields that some
models reject. The desktop capture path also exposed an echo-cancellation
setting although raw PortAudio has no portable AEC, noise-suppression, or AGC
control.

## Decision

1. `[stt].models.<provider-id>` is the only cloud model pin. `[stt].model`
   remains the local faster-whisper checkpoint. `[stt].temperature` defaults to
   `0.0` and is forwarded only where the request shape accepts it.
2. Transcription request shape is model-capability driven. Unknown non-Whisper
   models start with the universal JSON response; a field-specific HTTP 400
   narrows the process-local capability and retries without that field.
3. Short segments serve the live preview and are never the preferred delivered
   transcript. After capture closes, the whole recording is transcribed in
   25-second windows with 1.5-second overlap by default. Text overlap is merged
   without assuming whitespace, so CJK and other space-free scripts are not
   joined incorrectly. Preview text remains a fail-soft recovery path if the
   final pass cannot produce text.
4. With `[dictation].code_switching = true` (default), every final window asks
   for automatic language detection. A preferred language is not sent as a
   recognition lock. Provider output is returned in its spoken language; no
   translation request is made. Disabling the switch sends the explicit
   dictation-language pin for deliberately monolingual use.
5. `DictationCompleted` and the safe history API carry provider, effective
   model, reported languages, logical STT calls, aggregate latency, stable
   errors, sample rate, RMS, clipping ratio, capture dropouts, and path audit
   facts. Arbitrary history metadata remains private.
6. Raw PortAudio PCM stays byte-identical. The audit reports NS/AGC/AEC as
   unavailable (or AEC as off) until a platform backend advertises and passes a
   quality regression corpus. An unverified DSP filter is not a fallback.
7. `python -m jarvis.speech.stt_eval` compares exact provider/model pairs on a
   user-supplied, consented 16 kHz mono WAV corpus. It measures WER, annotated
   switch-anchor loss, median latency, billed gateway cost (when returned),
   explicit price-based estimates, and repeat variance. Reports omit reference
   text and hypotheses.

## Model evaluation status

GPT-4o Transcribe is a valid candidate, not a hardcoded dependency. OpenAI
reports improved word-error rate and language recognition relative to original
Whisper. GPT-4o Transcribe is currently token-priced; Whisper Large v3 Turbo is
currently minute-priced. These are vendor claims and inputs, not a Jarvis
quality verdict:

- [OpenAI GPT-4o Transcribe model](https://developers.openai.com/api/docs/models/gpt-4o-transcribe)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [Groq speech-to-text documentation](https://console.groq.com/docs/speech-to-text)

The 2026-07-30 smoke comparison used four newly synthesized utterances, three
runs each, temperature 0, and the same OpenRouter endpoint. It covered Latin,
Arabic, Cyrillic, CJK, Devanagari, and Hangul scripts, multilingual voices,
proper names, and technical terms. Lower is better except cost:

| Model | WER | Switch error | Repeat error | Median latency | Billed cost |
|---|---:|---:|---:|---:|---:|
| Whisper Large v3 Turbo | 0.296 | 0.458 | 0.000 | 478 ms | $0.0013 |
| GPT-4o Transcribe | 0.470 | 0.583 | 0.092 | 992 ms | $0.0032 |

This small synthetic proxy does **not** justify changing the default: Whisper
won every aggregate metric here. A representative, consented human-speech
corpus with real accents and background conditions remains the release gate.
Run it with the same harness (quote each contender because `|` is a shell
operator):

```powershell
python -m jarvis.speech.stt_eval `
  --corpus data/stt-eval/corpus.jsonl `
  --contender "current|openrouter-stt|openai/whisper-large-v3-turbo|0" `
  --contender "candidate|openrouter-stt|openai/gpt-4o-transcribe|0" `
  --repeats 3
```

Each JSONL row contains `id`, relative `audio`, human `reference`, optional
`switch_anchors` spanning language boundaries, and optional `tags`. Credentials
are entered in-app and resolved through the normal secret store; they never
enter the corpus or command.

## Consequences

- Final delivery adds one post-recording STT pass and therefore latency and
  possible cloud cost. Long windows reduce language ambiguity; overlap avoids
  word loss at boundaries.
- A cloud-only/headless install may still spend short-preview requests before
  the final pass because no local preview engine is available. Runtime
  cross-family fallback and failed-audio Restore remain intact.
- Providers that return only plain JSON may not report language tags. The
  transcript remains usable and telemetry honestly leaves languages empty.
- NS/AGC/AEC remain an explicit open capability, not a silently enabled claim.
  A future processor must beat the raw-PCM control on a representative corpus;
  any WER or switch-error regression keeps the raw arm enabled.
