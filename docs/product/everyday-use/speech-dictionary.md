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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [speech-recognition, dictionary, voice, pipeline]
related: [voice-conversations, audio-and-wake-word, languages-and-voices]
---

The Speech Dictionary helps Jarvis turn recurring recognition mistakes into the
words you intended. It is useful for names, product terms, abbreviations, and
specialist vocabulary that appear incorrectly in a Pipeline voice transcript.

The Dictionary is a research-preview feature. It adjusts recognized text; it
does not train the speech service, repair poor microphone audio, or change how
a word is pronounced in Jarvis's reply.

## Before You Start

- Use **Pipeline** for the voice turn you want to correct. Dictionary entries do
  not currently rewrite Realtime transcripts.
- Test the word once and note the exact text shown in **Transcription**. A known,
  repeatable mistake is easier to correct than a different guess each time.
- Use a short, non-sensitive test phrase. Never add passwords, credentials,
  recovery codes, or other secrets to the Dictionary.

Dictionary entries are saved in your local Jarvis data. If you use a compatible
cloud speech service, Jarvis may also send the correct terms to that service as
recognition hints. The service still receives audio according to your selected
speech setup.

## Choose the Right Entry

Start with the smallest rule that describes what you see.

| What appears in Transcription | Entry to use | What Jarvis changes |
|---|---|---|
| The right word with the wrong capitalization | Add the correct word | Exact matches use your saved capitalization |
| A close, single-word spelling mistake | Add the correct word | A conservative near match may be repaired |
| The same wrong word or phrase appears repeatedly | Turn on **Fix a misrecognition** | That whole word or phrase is replaced with the correct form |
| The result is missing, random, or changes every time | Improve the audio or language setup first | The Dictionary does not guess from unclear audio |

A plain word entry only repairs close, unambiguous single-word mistakes. Jarvis
leaves distant or ambiguous matches unchanged to avoid rewriting ordinary
speech. An explicit correction is better when you already know the exact wrong
word or phrase.

## Add a Word

1. Open **Dictionary** from the app navigation.
2. Select **Add word**.
3. Leave **Fix a misrecognition** off.
4. Enter the spelling and capitalization you want Jarvis to use.
5. Select **Add word** in the dialog. The new entry appears in the list.
6. Start a new Pipeline voice turn. The change applies without restarting the
   app.

Saved correct terms can also help a compatible cloud speech service when
Jarvis supplies them as hints as that service starts. Services that do not
support hints still receive the same local text correction after recognition.

## Correct a Repeated Mistake

1. Open **Dictionary** and select **Add word**.
2. Turn on **Fix a misrecognition**.
3. In **Misrecognized as...**, enter the exact wrong word or phrase from the
   transcript.
4. In **Correct spelling**, enter the form you want to see.
5. To cover several known variants, separate them with commas. Keep each
   variant specific enough that it will not replace ordinary speech.
6. Select **Add word**, then repeat the same non-sensitive phrase in a new
   Pipeline turn.

Corrections ignore capitalization and match complete words or phrases. They do
not replace part of a longer word. If the speech service produces a new variant,
edit the entry and add that variant rather than creating a duplicate correct
word.

## Edit, Find, or Remove an Entry

- Use **Search dictionary...** to find either the correct form or one of its
  misheard variants.
- Select **Edit** on an entry to change the correct form or its variants.
- Select **Delete** to remove a rule that is no longer useful or changes the
  wrong text.

The app accepts one entry for each correct form, regardless of capitalization.
An entry can contain up to 20 misheard variants; a word or variant can contain
up to 100 characters. The Dictionary holds up to 2,000 entries.

## How It Fits Together

1. **Audio and Wake Word starts listening.** Your microphone and activation
   settings determine whether Jarvis receives clear audio. Dictionary rules do
   not run during wake detection, so adding a wake phrase here cannot make it
   easier to activate Jarvis.
2. **Languages and Voices shapes recognition and the reply.** The selected
   input language helps the speech service interpret your audio. Dictionary
   entries then apply across languages; they are not stored in separate
   language lists. The selected output language and voice affect what Jarvis
   says back, not the correction itself.
3. **Pipeline creates a transcript.** A compatible cloud service can receive
   saved correct terms as hints when it starts. Jarvis then applies exact
   corrections, saved capitalization, and conservative near-match repair to
   the recognized text.
4. **The corrected request continues through Jarvis.** The corrected text is
   shown in the voice transcript and passed to the assistant or action that
   handles your request.
5. **Fallback stays provider-independent.** If Pipeline uses another available
   speech service, the text-correction step still applies. Recognition hints
   are used only where the active service supports them.

Realtime follows a separate live-audio path and does not currently pass its
transcript through the Dictionary. Read [Voice Conversations](voice-conversations)
for the full Pipeline and Realtime comparison.

## What the Dictionary Cannot Improve

The Dictionary cannot:

- repair silence, clipping, background noise, or the wrong microphone;
- make a wake phrase easier to detect;
- rewrite current Realtime transcripts;
- choose the correct meaning when two words sound alike without a consistent
  transcript pattern;
- change Jarvis's reply language, speaking voice, or pronunciation;
- correct text you already typed.

Use [Audio and Wake Word](audio-and-wake-word) for capture or activation
problems, and [Languages and Voices](languages-and-voices) when recognition is
using the wrong language or the spoken reply sounds wrong.

## Check That It Works

1. In a Pipeline voice turn, say a short non-sensitive phrase containing a term
   that is misheard consistently.
2. Open **Transcription** and copy only the incorrect word or phrase into a new
   **Fix a misrecognition** entry. Add the intended form on the other side.
3. Wait a moment, then repeat the same phrase in a new Pipeline turn.
4. Confirm that **Transcription** now shows the intended form and that the
   request reaches Jarvis with that wording.

Success means the repeated transcript changes. It does not require restarting
Jarvis or reconnecting the voice service.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The entry saves, but the next transcript is unchanged | The turn used Realtime, or the speech service produced a different variant | Choose Pipeline and add the exact new variant shown in Transcription |
| **Add word** stays unavailable | The correct form is empty, or correction mode has no misheard value | Complete the required field, or turn correction mode off for a plain word |
| The app says the word already exists | Correct forms are compared without capitalization | Search for the existing entry and edit it |
| An unrelated phrase changes | A correction variant is too broad | Edit it to a more specific phrase, or delete the entry |
| The wake phrase still fails | Wake detection happens before Dictionary correction | Test the microphone and wake phrase under **Audio and Wake Word** |
| Recognition changes on every attempt | Audio quality or the input language is unstable | Check the selected microphone and language before adding more rules |

## Next Steps

- Read [Voice Conversations](voice-conversations) to understand where
  Dictionary correction sits in Pipeline and why Realtime behaves differently.
- Read [Audio and Wake Word](audio-and-wake-word) to fix microphone quality or
  activation problems that text correction cannot solve.
- Read [Languages and Voices](languages-and-voices) to choose the input language
  and the voice used for Jarvis's reply.
