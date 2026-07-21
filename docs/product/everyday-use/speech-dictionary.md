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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [speech-recognition, dictionary, voice, pipeline]
related: [voice-conversations, audio-and-wake-word, languages-and-voices]
---

The Speech Dictionary corrects repeatable mistakes in Pipeline voice
transcripts. Pipeline is the voice mode that turns your speech into text before
Jarvis handles the request. Use the Dictionary for names, abbreviations,
product terms, and specialist words that speech recognition often gets wrong.

The app marks the Dictionary as **Research Preview**. It changes recognized
text after transcription. It does not train the speech provider, repair poor
microphone audio, or change how Jarvis pronounces a word in a reply.

## Before You Start

- Use **Pipeline** for the voice turn you want to correct. Dictionary rules do
  not change Realtime transcripts.
- Test the word once and note the exact text shown in **Transcription**. A
  repeatable mistake is easier to correct than a different result each time.
- Use a short, non-sensitive test phrase. Never add passwords, credentials,
  recovery codes, or other secrets to the Dictionary.

The saved list stays in the local data for this Jarvis installation and
survives restarts. It does not sync to another installation. A compatible
cloud speech provider may receive some correct terms as recognition hints when
Jarvis creates that provider. The provider still receives audio according to
your selected voice setup.

## Choose the Right Entry

Start with the smallest rule that describes what you see.

| What appears in Transcription | Entry to use | What Jarvis changes |
|---|---|---|
| The right word with the wrong capitalization | Add the correct word or phrase | Complete matches use your saved capitalization |
| A close, single-word spelling mistake | Add the correct word | A conservative near match may be repaired |
| The same wrong word or phrase appears repeatedly | Turn on **Fix a misrecognition** | That whole word or phrase is replaced with the correct form |
| The result is missing, random, or changes every time | Improve the audio or language setup first | The Dictionary does not guess from unclear audio |

Every entry preserves the saved capitalization of an exact word or phrase. A
single-word term with at least four characters may also repair a near match
that starts with the same letter and differs by only one or two edits,
depending on its length. Jarvis leaves distant or ambiguous matches unchanged.
Use an explicit correction when you know the exact wrong word or phrase.

## Add a Word

1. Open **Dictionary** from the app navigation.
2. Select **Add word**.
3. Leave **Fix a misrecognition** off.
4. Enter the spelling and capitalization you want in **Add a new word**.
5. Select **Add word** in the dialog. The new entry appears in the list.
6. Wait up to one second, then start a new Pipeline voice turn. You do not need
   to restart the app.

The next transcript can use the saved capitalization or a conservative
single-word repair. If the mistake is not close enough, add its exact form as a
misrecognition instead.

## Correct a Repeated Mistake

1. Open **Dictionary** and select **Add word**.
2. Turn on **Fix a misrecognition**.
3. In **Misrecognized as…**, enter the exact wrong word or phrase from the
   transcript.
4. In **Correct spelling**, enter the form you want to see.
5. To cover several known variants, separate them with commas. Keep each
   variant specific enough that it will not replace ordinary speech. A comma
   cannot be part of a variant because it separates entries.
6. Select **Add word**, wait up to one second, then repeat the same
   non-sensitive phrase in a new Pipeline turn.

Corrections ignore capitalization, allow different spacing between words, and
match complete words or phrases. They do not replace part of a longer word. If
speech recognition produces a new variant, edit the existing entry and add it
there.

The same list applies to English, German, Spanish, and any other language used
by a Pipeline speech provider. Rules are not separated by language, so avoid a
broad variant that could be an ordinary word in another language.

## Edit, Find, or Remove an Entry

- Use **Search dictionary…** to find either the correct form or one of its
  misheard variants.
- Use the button labeled **Edit** to change the correct form or its variants.
- Use **Delete** to remove an entry. Deletion happens immediately without a
  confirmation dialog. You can add the entry again if you remove it by mistake.

With a keyboard, use Tab to reach an entry's **Edit** and **Delete** buttons.
Screen readers announce the same labels.

Jarvis removes extra whitespace and duplicate variants when it saves an entry.
It accepts one entry for each correct form, regardless of capitalization. An
entry can contain up to 20 misheard variants. A correct form or variant can
contain up to 100 characters, and the Dictionary can hold up to 2,000 entries.

## How It Fits Together

1. **Jarvis captures your speech.** Your microphone, wake setup, and input
   language determine what audio reaches Pipeline. Dictionary rules do not run
   during wake detection.
2. **A speech provider creates raw text.** Providers that accept recognition
   hints may receive a capped list of correct terms when Jarvis creates the
   provider. Those hints are not refreshed by a later Dictionary edit.
3. **Jarvis applies the local rules.** Explicit replacements run first,
   followed by saved capitalization and conservative single-word repair. This
   step corrects Pipeline's live preview and final transcript within about one
   second of an edit.
4. **The corrected request continues.** The final text appears in
   **Transcription** and goes to the assistant or action that handles the
   request.
5. **Provider fallback keeps the correction.** The full local rule set wraps
   any Pipeline speech provider. A provider that does not accept hints still
   gets the same post-transcription corrections.

Realtime follows a separate live-audio path and does not pass its transcript
through the Dictionary. Read [Voice Conversations](voice-conversations) for
the full Pipeline and Realtime comparison. Read [Audio and Wake Word](audio-and-wake-word)
and [Languages and Voices](languages-and-voices) for the settings that affect
the audio and raw transcript before correction.

## What the Dictionary Cannot Improve

The Dictionary cannot:

- repair silence, clipping, background noise, or the wrong microphone;
- make a wake phrase easier to detect;
- rewrite Realtime transcripts;
- choose the correct meaning when two words sound alike without a consistent
  transcript pattern;
- change Jarvis's reply language, speaking voice, or pronunciation;
- correct text you already typed;
- keep separate rules for different input languages;
- guarantee that a speech provider will use recognition hints.

Use [Audio and Wake Word](audio-and-wake-word) for capture or activation
problems, and [Languages and Voices](languages-and-voices) when recognition is
using the wrong language or the spoken reply sounds wrong.

## Check That It Works

1. In a Pipeline voice turn, say a short non-sensitive phrase containing a term
   that is misheard consistently.
2. Open **Transcription** and put only the incorrect word or phrase in a new
   **Fix a misrecognition** entry. Add the intended form in **Correct spelling**.
3. Wait one second, then repeat the same phrase in a new Pipeline turn.
4. Confirm that **Transcription** shows the intended form and that the
   request reaches Jarvis with that wording.

Success means the repeated transcript changes. It does not require restarting
Jarvis or reconnecting the voice service.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The entry saves, but the next transcript is unchanged | The turn used Realtime, the edit has not reloaded yet, or speech recognition produced a different variant | Choose Pipeline, wait one second, and add the exact new variant shown in Transcription |
| **Add word** stays unavailable | The correct form is empty, or correction mode has no misheard value | Complete the required field, or turn correction mode off for a plain word |
| The app says the word already exists | Correct forms are compared without capitalization | Search for the existing entry and edit it |
| An unrelated phrase changes | A correction variant is too broad | Edit it to a more specific phrase, or delete the entry |
| Recognition changes on every attempt | Audio quality or the input language is unstable | Check the selected microphone and language before adding more rules |

## Next Steps

- Read [Voice Conversations](voice-conversations) to understand where
  Dictionary correction sits in Pipeline and why Realtime behaves differently.
- Read [Audio and Wake Word](audio-and-wake-word) to fix microphone quality or
  activation problems that text correction cannot solve.
- Read [Languages and Voices](languages-and-voices) to choose the input language
  and the voice used for Jarvis's reply.
