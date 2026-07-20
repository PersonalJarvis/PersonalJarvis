---
title: "Audio Devices and Wake Word"
slug: audio-and-wake-word
summary: "Choose a microphone and speaker, set a wake phrase, and tune reliable hands-free listening."
section: "Personalize and connect"
section_order: 3
order: 2
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [audio, microphone, speaker, wake-word, voice, pipeline, realtime]
related: [voice-conversations, speech-dictionary, permissions, troubleshooting]
---

Choose which microphone Jarvis hears and where spoken replies play, then set a
wake phrase for hands-free activation. The recommended setup uses automatic
device selection and lets Jarvis choose the best available local wake engine.

The recommended Auto wake paths run locally. They only decide when to start
listening; the request you speak afterward follows your selected voice mode and
speech services. An advanced user-supplied ONNX model can use the selected
speech-recognition service to verify a possible match, so review that service's
privacy terms if you choose this path.

## Before You Start

- Connect the microphone and speaker or headset you want to use.
- Wait for the desktop app to show **Ready** instead of **Voice starting...**.
- Allow microphone access when your operating system asks. On macOS, review
  **Settings > Privacy permissions** in Jarvis as well.
- Choose a wake phrase that you are comfortable saying near your computer. Do
  not use a password, recovery code, or other secret.

Voice is optional. A computer without usable audio hardware can continue to use
Chats, tasks, and the other text-based features.

## Know What Each Setting Controls

The voice path has several distinct parts. Changing one does not automatically
repair another.

| Part | Its job | Where to change it |
|---|---|---|
| **Microphone** | Captures the wake phrase and the request that follows | **Settings > Audio devices > Microphone** |
| **Wake phrase** | The words you say to start a desktop voice session | **Settings > Wake Word > Wake phrase** |
| **Wake engine** | Listens for that phrase; the recommended Auto paths use local detection | **Settings > Wake Word > Detection engine** |
| **Speech recognition** | Turns the request after the wake into usable text in Pipeline | **Languages** and **API Keys & Providers** |
| **Voice output** | Plays Jarvis's spoken reply | **Settings > Audio devices > Voice output** |
| **Reply voice** | Controls the language and sound of the reply | **Languages** and the voice provider settings |

The [Speech Dictionary](speech-dictionary) can correct recurring words in a
Pipeline transcript after the wake. It does not make the wake phrase easier to
detect, and it does not currently rewrite Realtime transcripts.

## Choose Your Audio Devices

1. Open **Settings** and find **Audio devices**.
2. Leave **Voice output** on **Automatic (recommended)** for the simplest
   setup. Jarvis follows a suitable available output and adapts when devices
   come and go.
3. Leave **Microphone** on **Automatic (recommended)**, or choose a specific
   microphone when the automatic choice is not the one you want.
4. Plug in or reconnect a device, then select **Rescan devices** if it does not
   appear in the list.
5. Watch for the saved confirmation. With a running desktop voice pipeline,
   the output changes for the next reply and the wake listener reopens on the
   new microphone within a moment.

If a selected device is unplugged, its name can remain visible so the app does
not hide your saved choice. Jarvis uses automatic selection while that device
is absent and can use the saved device again when it returns.

If the app says the change applies on the next start, no live desktop voice
pipeline was available. The choice is still saved; reopen voice or restart the
app when convenient.

## Set Up a Wake Phrase

1. In **Settings**, find **Wake Word** and turn on **Activate wake word**.
2. Enter the complete phrase in **Wake phrase**. A short phrase with a prefix,
   such as “Hey Nova,” is less likely to activate when the core word appears in
   ordinary conversation. First-run setup adds the “Hey” prefix for you; the
   Settings field accepts the complete phrase.
3. Under **Which language do you speak?**, choose the language in which you
   actually pronounce the phrase. Choose based on your speech, not the origin
   of the name or word.
4. Leave **Detection engine** on **Auto (recommended)**. Auto looks for a local
   option that can serve your exact phrase and current language.
5. Select **Save wake word**. A running desktop voice pipeline applies the new
   phrase immediately. Follow a restart notice only when the app shows one.
6. Select **Test wake word** and speak normally during the check. The result
   reports the resolved engine, spoken language, microphone signal, and any
   clear readiness problem.

There is no wake-sensitivity slider. Jarvis uses calibrated settings for each
engine. Improve reliability by choosing the correct microphone and spoken
language, using a prefixed phrase, and acting on the self-test result.

> [!note] The spoken-language choice is shared with Pipeline speech
> recognition. The reply language and the voice you hear are separate choices;
> see [Languages and Voices](languages-and-voices).

## Understand the Wake Engines

Most people should keep **Auto**. The other choices are useful when you are
diagnosing a warning or already have an advanced local model.

| Choice | What it does | When to use it |
|---|---|---|
| **Auto** | Chooses a matching custom model, local keyword spotting, or local speech matching; otherwise leaves wake activation unavailable | Recommended for normal use |
| **Keyword spotting** | Uses an offline, CPU-friendly model for the selected spoken language | When the matching local wake model is installed |
| **STT match** | Transcribes short local audio windows and matches the phrase | A fallback for arbitrary phrases; unusual names can be less reliable |
| **Custom ONNX model** | Loads a user-supplied model trained for the phrase and verifies candidates with speech recognition | Advanced setups with a compatible model file |

The current selector can also show **openWakeWord (built-in phrases)**. Personal
Jarvis does not ship a named built-in wake phrase model. Leave **Auto** selected
unless you supplied a compatible custom model; otherwise Jarvis resolves
through the generic local fallback chain.

When the app says a phrase is using a degraded fallback, choose **Download wake
model** to fetch the local model for the selected spoken language. If it offers
**Enable any wake word**, that installs the optional local speech pack in the
app. Both downloads require a connection once; detection remains local after
the required files are present.

If no local engine can serve your phrase, Jarvis does not pretend to listen and
does not substitute a hidden wake phrase. Wake activation stays unavailable,
and you can use the configured Call shortcut instead.

## How It Fits Together

1. **The microphone supplies local audio.** The native desktop listener uses
   the microphone selected under **Audio devices**, subject to operating-system
   permission.
2. **The wake engine checks only for your phrase.** Auto uses the selected
   spoken language and a local capability that is actually available on this
   computer. An advanced custom ONNX setup can add a speech-recognition check.
3. **A confirmed wake opens a voice session.** The Jarvis Bar becomes active,
   and the app starts the voice mode selected in **API Keys & Providers**.
4. **Pipeline or Realtime handles the request.** Pipeline recognizes speech,
   sends the text to the assistant, and produces spoken output as separate
   stages. Desktop Realtime keeps the microphone and reply in one live audio
   session. If Realtime cannot open before accepting the turn, Jarvis can use
   Pipeline instead.
5. **The reply uses the output side.** The reply language and voice determine
   what Jarvis says; **Voice output** determines where you hear it.

The native **Audio devices** pickers do not select hardware for a Realtime
session started in a remote browser. In that case, the browser's site
permission and the device choices of that computer control its microphone and
speaker.

Jarvis does not require one operating system or graphics processor for wake
detection. Auto uses a suitable installed local path. If an optional accelerated
path is not verified on the machine, Jarvis stays on a supported local fallback
instead. On a headless system with no audio devices, the app remains available
for text use and stores settings for a later voice-capable start.

## Check That It Works

1. Set both audio pickers to **Automatic (recommended)** or your intended
   devices.
2. Save a prefixed phrase with **Detection engine** set to **Auto** and the
   correct spoken language.
3. Select **Test wake word**. Resolve any warning until the result says the
   phrase is ready and the microphone signal is present.
4. Return to the app's ready state and say the phrase once at a normal volume.
5. Confirm that the Jarvis Bar changes to **Listening**. Ask a short question
   and confirm that the reply plays through the selected output.

The self-test checks configuration and microphone readiness. The end-to-end
wake confirms how the phrase behaves in your actual room.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **No audio devices found on this machine** | The host has no usable local input or output, or enumeration is not ready | Connect or enable a device, select **Rescan devices**, and check operating-system audio settings |
| The saved device is listed but unplugged | Jarvis preserved the choice and is temporarily using automatic selection | Reconnect it or choose **Automatic (recommended)** |
| **Test wake word** reports no microphone or a quiet signal | Permission, input choice, or input level is blocking useful audio | Review [Permissions](permissions), choose the correct microphone, and test again while speaking normally |
| The test reports the wrong language or an unsupported word | The local language model does not match how you speak the phrase | Choose the language you speak, save again, or use a different prefixed phrase |
| Saving shows a degraded-engine warning | The preferred local wake model is missing or the phrase is on the weaker speech-match path | Select **Download wake model** or **Enable any wake word**, then save and test again |
| The phrase activates during ordinary conversation | The phrase is too short or lacks a prefix | Use a distinct two- or three-word phrase with a prefix; there is no sensitivity control to tune |
| The wake test passes, but a browser Realtime control hears the wrong device | The browser owns that session's audio capture | Allow microphone access for the site and choose the device in the browser or operating system |
| A save says a restart is required | The native voice pipeline was not running, so live apply was unavailable | Reopen voice or restart the app, then repeat the end-to-end check |

For persistent failures, record the exact visible status and continue with
[Troubleshooting](troubleshooting). Do not copy credentials, private
conversation text, or personal device details into a public report.

## Next Steps

- Read [Voice Conversations](voice-conversations) to understand how activation,
  Pipeline, Realtime, speech recognition, and spoken output form one turn.
- Read [Speech Dictionary](speech-dictionary) to correct recurring recognition
  mistakes after a successful wake without changing the wake engine.
- Read [Permissions](permissions) when the operating system or browser blocks
  microphone access.
- Read [Troubleshooting](troubleshooting) for a wider voice and app recovery
  checklist when the focused checks on this page do not resolve the issue.
