---
title: "Speech Dictionary"
slug: speech-dictionary
summary: "Teach speech recognition names and specialist words it often mishears, then test the improvement."
section: "Everyday use"
section_order: 2
order: 6
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [speech-recognition, dictionary, voice, pipeline]
related: [voice-conversations, audio-and-wake-word, languages-and-voices]
---

Use the **Dictionary** for names, brands, abbreviations, and specialist terms
that speech recognition repeatedly spells the wrong way. It is a **Research
Preview**, but its local corrections now reach Pipeline voice, Realtime
transcripts, and Dictation.

The Dictionary changes recognized text. It does not train a provider, improve
microphone audio, detect the wake phrase, or change reply pronunciation.

## Before You Start

1. Open **Voice > Dictionary**.
2. Say the term in the failing path and copy the visible wrong transcript.
3. Use a harmless test phrase. Never save credentials, recovery codes, or
   private keys as vocabulary.

Rules are shared across all input languages. A broad correction that is safe in
one language can alter an ordinary word in another.

## Choose the Right Entry

| What recognition produces | Best entry | Result |
|---|---|---|
| Correct letters, wrong capitalization | Add the correct term | Exact matches use your saved capitalization |
| One close spelling error | Add the correct single word | A conservative near match may be repaired |
| The same wrong word or phrase | Turn on **Fix a misrecognition** | The saved wrong form is replaced predictably |
| Missing, random, or changing text | Fix audio, provider, or language first | A text rule cannot recover words that never appear |

Near-match repair needs a single word of at least four characters, the same
first letter, and only a small spelling difference. Ambiguous and multi-word
cases need an explicit variant.

## Add a Name or Term

1. Select **Add word**.
2. Leave **Fix a misrecognition** off.
3. Enter the intended spelling, save, and wait about one second.
4. Start a new utterance in the same path and inspect its transcript.

Compatible providers may also receive the term as a recognition hint. Local
capitalization and near-match repair are the cross-provider behavior.

## Fix a Repeated Misrecognition

1. Select **Add word** and enable **Fix a misrecognition**.
2. Enter the wrong form on the left and intended form on the right.
3. Separate known alternatives with commas, save, wait one second, and retest.

Rules ignore capitalization, tolerate different spaces, and replace only whole
words or phrases. Keep variants specific; commas separate them.

## Understand Where Corrections Apply

| Path | What the Dictionary changes | Important limit |
|---|---|---|
| **Pipeline** | Preview and final transcripts, including fallback | Provider hints load when the provider is built; later edits still correct locally |
| **Realtime** | Transcript, local routing, tools, and hang-up matching | The provider may have begun its own response before local correction |
| **Dictation** | Preview, final text, and insertion into another app | Optional polish or translation happens later; saved terms are protected |

Edits usually affect the next new transcript within one second. Existing
transcripts and Dictation history are unchanged.

Some providers receive a capped list of correct terms when built; variants stay
local. Provider fallback keeps local correction. If the list cannot load,
speech continues with raw text.

## Find, Edit, or Delete Rules

- **Search dictionary...** matches terms and variants.
- **Edit** changes them; turning correction mode off keeps plain vocabulary.
- **Delete** removes an entry immediately, without confirmation or undo.

Correct terms are unique regardless of capitalization. Edit the existing entry
after a duplicate warning. Within an entry, repeated variants merge, spaces are
normalized, and a variant identical to the term is discarded.

One variant can still appear in different entries; avoid this because the
earlier rule can win. Limits are 100 characters per term or variant, 20
variants per entry, and 2,000 entries.

Deletion stops local correction after reload, but does not change history or
retract a hint held by a running provider. Rebuild that provider if needed.

## Privacy and Storage

Entries stay in local app data, survive restarts, and do not sync.

Cloud speech can receive audio and some terms as hints. Dictation polish or
translation can receive terms as protected spellings. Variants stay in the
local corrector. Deletion cannot retract data already sent.

## What Does Not Change

The Dictionary does not fix typed text, history, bad audio, the wrong
microphone, wake detection, reply language, voice, or pronunciation. It cannot
force provider hints or infer a homophone without a repeatable pattern.

Use [Audio and Wake Word](audio-and-wake-word) for capture or activation
problems and [Voice Conversations](voice-conversations) for Pipeline and
Realtime behavior.

## How It Fits Together

1. The microphone, language, and active path produce raw text; wake detection
   happens earlier.
2. Explicit replacements run first, then capitalization and cautious
   single-word repair.
3. Pipeline sends corrected text to the assistant, Realtime uses it locally,
   and Dictation may polish it before insertion.
4. A fallback can change the provider while local rules remain.

## Check That It Works

1. Say one harmless phrase and note a repeatable wrong term.
2. Add that exact form under **Fix a misrecognition** and save the intended form.
3. Wait one second, then repeat the phrase in the same path.
4. Confirm the Pipeline/Realtime transcript or Dictation draft shows the saved
   form. No app restart should be needed for local correction.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| The next transcript is unchanged | The provider produced a different form, or reload has not happened | Wait one second and add the exact new variant |
| A plain word does not repair the error | The difference is too large, multi-word, or ambiguous | Use **Fix a misrecognition** with the exact source |
| Realtime transcript is fixed but its reply is not | The provider interpreted audio before local correction | Retry in Pipeline when corrected text must drive the answer |
| An unrelated phrase changes | A variant is too broad or shared across entries | Make it more specific, remove the duplicate mapping, or delete it |
| Recognition varies every time | Audio, language, or provider behavior is unstable | Check microphone and input language before adding more rules |

## Next Steps

- Read [Dictate Into Any App](dictation) for Dictation delivery, polish, and
  recovery.
- Read [Voice Conversations](voice-conversations) for Pipeline and Realtime.
- Use [Audio and Wake Word](audio-and-wake-word) for microphone and wake issues.
