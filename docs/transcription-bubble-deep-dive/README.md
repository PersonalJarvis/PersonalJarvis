# Deep Dive: wrong text in the listening bubble

Date: 2026-05-24

> **UPDATE 2026-05-27 (pendulum episode 3, fixed)**
>
> The diagnosis below is correct. The fix is now landed: ``ui/orb/bus_bridge.py``
> no longer runs ``_merge_listening_transcript`` on incoming ``TranscriptionUpdate``
> events. The pipeline already accumulates probe tails into a complete
> snapshot (``jarvis/speech/pipeline.py::_merge_partial_transcript`` over
> ``_probe_live_text``); the bridge now mirrors that snapshot 1:1, matching
> the Desktop App's ``TranscriptionView`` (``setTranscription`` in
> ``useWebSocket.ts:138-140``) byte-for-byte. The four helper functions
> (``_merge_listening_transcript``, ``_normalized_transcript_words``,
> ``_is_likely_transcript_correction``, ``_is_likely_repeated_transcript_tail``)
> were deleted as dead code. Regression tests:
> ``tests/unit/ui/test_orb_bus_bridge.py::test_listening_bubble_mirrors_pipeline_snapshot_one_to_one``
> and
> ``tests/unit/ui/test_orb_bus_bridge.py::test_listening_bubble_keeps_clean_snapshot_after_pipeline_downward_fix``.
>
> The rest of this document is preserved as historical context for how the
> bug manifested.

## Short verdict

The screenshot most likely does not show a single final STT error, but rather a
wrong rendering assembled from several unstable live-STT intermediate states.

The user said:

```text
Was ist morgen fuer ein Tag?
``` <!-- i18n-allow: STT test fixture -->

The bubble showed:

```text
Was? Was ist morgens? Was ist morgen fuer ein Tag? Morgen fuer einen Tag.
``` <!-- i18n-allow: STT test fixture -->

The pattern is typical for the current live-bubble architecture:

1. The speech pipeline repeatedly transcribes only short audio tails.
2. These tail transcripts are intermediate hypotheses, not stable final sentences.
3. The merge function appends new hypotheses when it finds no clear word overlap.
4. As a result, corrected STT hypotheses do not replace old wrong variants.
5. The Orb bridge shows the merged result as if it were the actual dictated sentence.

This makes the stronger diagnosis:

```text
Primary error: bubble / merge logic for live partials.
Secondary trigger: STT delivers normal, unstable intermediate hypotheses such as "morgens".
```

## Answer to the two options

### Option 1: wrongly displayed in the speech bubble

Yes, this is the main error.

The speech bubble treats live intermediate hypotheses like an append-only text stream.
That is wrong for STT partials. STT partials are not guaranteed to be monotonic.
A later partial can correct an earlier partial.

Example:

```text
Partial 1: Was?
Partial 2: Was ist morgens?
Partial 3: Was ist morgen fuer ein Tag?
Partial 4: Morgen fuer einen Tag.
``` <!-- i18n-allow: STT test fixture -->

The correct display would not be:

```text
Was? Was ist morgens? Was ist morgen fuer ein Tag? Morgen fuer einen Tag.
``` <!-- i18n-allow: STT test fixture -->

The better display would be either:

```text
Was ist morgen fuer ein Tag?
``` <!-- i18n-allow: STT test fixture -->

or, while speaking live, always only the currently best hypothesis.

### Option 2: speech-to-text tool does not work

Not clearly proven as the main cause.

On short tail windows, the STT tool may well recognize "morgen" as "morgens" or
"fuer ein" as "fuer einen". That is normal for short, context-poor live tails.
The decisive point is: such intermediate hypotheses must not be permanently
preserved in the UI.

Without a current runtime log containing `transcript final: ...` it cannot be
conclusively proven whether the final brain prompt was wrong too. The screenshot
alone proves only what the bubble displayed. The code shows, however, that the
bubble receives not only final transcripts but also live partials.

## Data flow

```text
Microphone
  -> VAD active turn
  -> STT probe on short audio tail
  -> TranscriptionUpdate(is_final=false)
  -> OrbBusBridge._on_transcription_update()
  -> _merge_listening_transcript()
  -> OrbOverlay.show_listening_transcript()
  -> visible bubble
```

The final brain path is a different one:

```text
Microphone
  -> VAD endpoint
  -> final STT transcription on complete PCM
  -> TranscriptFinal
  -> hallucination filter
  -> Brain
  -> optional final TranscriptionUpdate(is_final=true)
```

The bubble is therefore a live-preview channel. It is not automatically identical
to the final prompt that Jarvis processes.

## Reproduction in the current code

The merge logic can produce the screenshot behavior directly.

Reproduction with the speech merge function:

```python
from jarvis.speech.pipeline import _merge_partial_transcript

parts = [
    "Was?",
    "Was ist morgens?",
    "Was ist morgen fuer ein Tag?",
    "Morgen fuer einen Tag.",
]

cur = ""
for part in parts:
    cur = _merge_partial_transcript(cur, part)
    print(cur)
```

Result:

```text
Was?
Was? Was ist morgens?
Was? Was ist morgens? Was ist morgen fuer ein Tag?
Was? Was ist morgens? Was ist morgen fuer ein Tag? Morgen fuer einen Tag.
```

The Orb bridge has a very similar merge function:

```text
ui/orb/bus_bridge.py::_merge_listening_transcript
```

It too produces the same append-only error with the same sequence.

## Why does exactly "morgens" happen?

The live probe works on a short tail window:

```text
probe_tail_ms = 1800
probe_interval_ms = 650
```

That means: the probe does not hear the entire sentence each time, but only the
last roughly 1.8 seconds. For a sentence like:

```text
Was ist morgen fuer ein Tag?
```

a tail can look roughly like this:

```text
... ist morgen
... morgen fuer ein
... fuer ein Tag
```

On such short excerpts STT can produce intermediate hypotheses:

```text
morgen  -> morgens
fuer ein -> fuer einen
```

That is not unusual for live STT. The error is that these intermediate
hypotheses are not replaced in the bubble when a better hypothesis arrives later.

## Why did the last bubble fix make it more likely?

The last fix was supposed to prevent long texts from being "forgotten" in the
bubble. For that, a dedicated accumulator was introduced in the Orb bridge:

```text
self._listening_transcript_text
```

This solves the forgetting on cleanly overlapping partials:

```text
Hallo ich moechte einen langen Prompt
langen Prompt der weiter geht
-> Hallo ich moechte einen langen Prompt der weiter geht
```

But it worsens the case where STT corrects its opinion:

```text
Was ist morgens?
Was ist morgen fuer ein Tag?
```

Since "morgens" and "morgen" are not exactly equal, the merge recognizes no
clean overlap and appends both variants.

## Why the current merge rule is too weak

The current merge rule works word-based and requires exact overlap:

```text
current_words[-overlap:] == incoming_words[:overlap]
```

Problem cases:

```text
Was?      != Was
morgens?  != morgen
Tag?      != Tag
ein       != einen
```

As soon as punctuation or small STT corrections come into play, the overlap is
lost. Then the code falls back to append:

```text
return f"{current} {incoming}"
```

That is the concrete mechanism behind the screenshot.

## What the screenshot proves and what it does not

The screenshot proves:

- The bubble displayed wrong intermediate hypotheses.
- At least one STT partial presumably contained "morgens".
- The UI merged old and new hypotheses instead of correcting.

The screenshot does not prove:

- That the final brain prompt was exactly the same wrong text.
- That the final STT model misunderstood the sentence.
- That Jarvis internally kept working with "morgens".

For that you need a current log entry:

```text
transcript final: text='...'
```

In the examined logs no current entry for this specific screenshot was found.

## Affected code locations

### Speech live probe

```text
jarvis/speech/pipeline.py::_stt_probe_async
```

Publishes live partials:

```text
TranscriptionUpdate(source_layer="speech.stt.partial", is_final=False)
```

### Speech merge

```text
jarvis/speech/pipeline.py::_merge_partial_transcript
```

Merges tail hypotheses append-only when no exact overlap is recognized.

### Orb bridge

```text
ui/orb/bus_bridge.py::_on_transcription_update
ui/orb/bus_bridge.py::_merge_listening_transcript
```

Takes every `TranscriptionUpdate` in the LISTENING state and runs another
dedicated merge for the bubble.

### Orb overlay

```text
ui/orb/overlay.py::show_listening_transcript
```

Draws only the passed text. The overlay drawing is not the cause of this error.
It merely renders what the bridge delivers.

## Assessment

| Question | Assessment |
| --- | --- |
| Is the speech bubble wrong? | Yes, very likely. |
| Is STT completely broken? | No, not proven. |
| Does STT deliver unstable live hypotheses? | Yes, that is expected. |
| Is the merge robust against corrections? | No. |
| Can the final prompt still be correct? | Yes. |
| Can the final prompt also be wrong? | Yes, but this screenshot does not prove it. |

## Recommended fix direction

The bubble should not use two append-only mergers in sequence.
Instead it should use one of three clear strategies.

### Recommendation A: live bubble shows the currently best hypothesis

For every `speech.stt.partial` the bubble is set to the newest text.
Not append-only.

Advantage:

- Corrections like "morgens" -> "morgen" disappear automatically.
- No duplicate chains.

Disadvantage:

- If the STT provider delivers only tail fragments, the beginning can visually
  be missing.

### Recommendation B: pipeline delivers a stable preview text, UI does not merge

The speech pipeline remains solely responsible for live-text accumulation.
The Orb bridge only displays what it receives.

Advantage:

- Only one place decides about the merge.
- Fewer duplicate errors.

Disadvantage:

- The pipeline merge function must become more correctable.

### Recommendation C: final-or-replace model

While speaking, the bubble shows only the best current hypothesis.
Only at `is_final=True` is a stable complete text adopted.

Advantage:

- Cleanest separation between preview and final text.
- No wrong intermediate hypotheses stay stuck.

Disadvantage:

- The live bubble can jump slightly while speaking, because STT corrects
  itself.

## My recommendation

Recommended is B plus a part of C:

1. The Orb bridge should not accumulate again when the event already comes
   from `speech.stt.partial`.
2. The pipeline merge function should be able to recognize corrections:
   - normalize punctuation.
   - not bluntly append small word variants.
   - on high similarity, prefer to let the newer hypothesis replace.
3. `is_final=True` should replace the bubble text, not append it.
4. For diagnosis, a field that `TranscriptionUpdate` already uses/transports,
   such as `source_layer`, should be visibly logged, including `is_final`.

## Next debug steps

To finally separate "bubble only" from "final STT also wrong":

1. On the next repro, search for the log entry:

   ```text
   transcript final: text='...'
   ```

2. In parallel, log the bubble events:

   ```text
   source_layer, is_final, text
   ```

3. If `transcript final` is correct, it is a pure bubble/preview error.
4. If `transcript final` is also wrong, the final STT provider or its audio
   quality must be examined in addition.

## Final diagnosis

The visible error is caused by the live bubble, which permanently glues together
unstable STT intermediate hypotheses.

The STT tool is involved because it must have delivered the intermediate
hypothesis "morgens". But that is normal for live tails and not yet proof that
the final STT prompt was wrong.

The actual architecture rule should be:

```text
Live STT partials must be correctable in the UI.
They must not be stored append-only as if they were final dictated text.
```

## Fix status

Implemented on `main` on 2026-05-24.

Two merge points were changed:

1. `jarvis/speech/pipeline.py::_merge_partial_transcript`
   - normalizes punctuation and small STT variants like `morgens`/`morgen`
     or `ein`/`einen`;
   - recognizes corrected live hypotheses and replaces the old preview text;
   - recognizes repeated tail fragments and does not append them again.

2. `ui/orb/bus_bridge.py::_merge_listening_transcript`
   - uses the same correction logic for the visible bubble;
   - `is_final=True` always replaces the preview completely.

The screenshot case:

```text
Was?
Was ist morgens?
Was ist morgen fuer ein Tag?
Morgen fuer einen Tag.
```

now becomes:

```text
Was ist morgen fuer ein Tag?
```

The previous long-prompt fix is preserved:

```text
Hallo ich moechte einen langen Prompt
langen Prompt der weiter geht
```

still becomes:

```text
Hallo ich moechte einen langen Prompt der weiter geht
```
