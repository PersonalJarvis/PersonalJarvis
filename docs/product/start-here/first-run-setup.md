---
title: "Complete First-Run Setup"
slug: first-run-setup
summary: "Choose languages, a local or connected Brain, permissions, and voice activation without exposing credentials."
section: "Start here"
section_order: 1
order: 3
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [setup, onboarding, language, permissions, microphone, wake-word, providers]
related: [providers-and-api-keys, audio-and-wake-word, permissions, start-your-first-chat]
---

First-run setup accepts the Terms, introduces model choices, checks system
access, and sets voice activation. You can finish without a cloud account or
microphone, but chat needs a ready local or connected Brain.

## Before You Start

- Open the installed desktop app. The first launch may take several seconds.
- For a keyless Brain, start Ollama with one installed model. Connect a
  microphone only if testing a wake word.
- On macOS, launch the signed app from its application bundle before granting
  access; permissions belong to that exact app identity.

> [!warning] Enter a provider credential only under **API Keys & Providers**
> after onboarding. Never paste one into chat, speak it, put it in a wake word,
> add it to configuration, or include it in a screenshot.

## Complete the Setup

### 1. Accept the risk notice and view the tour

1. On **Before you continue**, read the summary and use **View the full Terms of
   Use** for the complete text.
2. Accept the risk checkbox, then select **I understand — continue**. **Decline
   & quit** closes the app; the notice returns next time.
3. Play or skip **Watch the 2-minute tour**, then select **Get started** on the
   welcome screen.

**Skip setup for now** skips only the welcome step and continues to language.
It does not dismiss the remaining first-run guide.

### 2. Choose interface and reply languages

On **Choose your language**, select **Interface language** and **Reply
language**, then **Next**. English, German, and Spanish are supported.

Interface language changes menus. Reply language controls answers; **Auto**
follows the conversation without switching for short interjections. Wake-word
pronunciation is separate and can be pinned under **Settings > Wake Word**.

### 3. Choose a Brain path and learn the voice modes

**Set up API keys after onboarding** previews **API Keys & Providers**. It does
not accept a key, choose a cloud provider, or test an account.

| Voice mode | What the onboarding screen offers |
|---|---|
| **Realtime (recommended, research preview)** | One compatible OpenAI or Gemini credential handles listening, reasoning, and speech in one live connection |
| **Pipeline (not recommended)** | Separate choices handle **Brain**, **Voice Input**, and **Voice Output** |

The same step checks this machine for Ollama:

- **Use local model** appears when Ollama is reachable with a model; it makes
  Ollama the active Brain without a key.
- If Ollama is empty, install a model there first. The wizard does not download
  one.
- **Get Ollama** appears when no server is detected; you may choose another
  Brain later.

This button changes only the Brain, not voice, vision, or other model features.
Select **Continue onboarding** when ready.

### 4. Review operating-system permissions

On macOS, **Allow access on this Mac** shows **Microphone**, **Screen
Recording**, **Accessibility**, **Input Monitoring**, **Input control**, and
**Keychain (API keys)**. Grant only what you need:

- Microphone supports voice input; Screen Recording, Accessibility, and Input
  control support Computer Use; Input Monitoring supports global shortcuts;
  Keychain stores credentials encrypted.

Use **Allow** or **Open Settings**, return, and wait for the status. **Allowed**,
**Not required**, and **Restart pending** are ready. Pending access applies
during the final restart. To defer access, select **Continue with text only**.

Windows and Linux show **No extra desktop privacy permissions are required on
this operating system**. This does not prove that a microphone, display,
shortcut, or platform backend is available.

### 5. Choose how voice starts and name the assistant

On the activation screen, choose one path:

- **Keyboard shortcut** disables always-listening activation. Use the Call
  shortcut later. With no wake phrase, the neutral name is **Assistant**.
- **Wake word** keeps a local listener ready for your phrase. **Hey** is fixed;
  enter at least two characters for the rest. The phrase also defines the
  assistant's display name. For example, entering **Nova** shows **Your
  assistant will be called: Nova**.

Accept responsibility for the name, then optionally select **Test your
microphone** or **Say your wake word once**. Both check the input level and
report good, quiet, missing, blocked, or temporarily unavailable input. The
check does not block saving.

Select **Save wake word**. If no local engine supports it, choose **Enable any
wake word** to install the optional local wake speech pack, then save again.
This repairs wake detection, not the full Pipeline voice stack. **Continue
anyway** leaves the Call shortcut as the reliable path until wake is healthy.

### 6. Finish and restart once

On **You're all set!**, review skipped entries. Enable **Start Jarvis
automatically at login** only if the supported switch appears.

Select **Get started**. Setup is saved before one fresh restart initializes the
new choices. If it does not restart, reopen the app manually.

## Connect and Test Providers After Setup

Open **API Keys**. Under **Brain**, use Ollama or connect one provider you have.
For voice, choose **Realtime** or **Pipeline** and connect only its required
categories. Each card shows its supported sign-in method.

Save credentials only in the masked card, then select **Test**. **Works** proves
the account, model, quota, and service answered. The app prefers the OS
credential store; its user-local fallback is not OS-encrypted.

## Recover a Skipped or Deferred Choice

- Change interface and reply languages under **Settings > Languages**.
- Repair macOS access under **Settings > Privacy permissions**.
- Change the phrase, spoken wake language, activation switch, or local wake
  pack under **Settings > Wake Word**.
- Record or change the Call shortcut under **Settings > Voice Keybinds**.
- Connect and test models under **API Keys & Providers**; change login startup
  under **Settings > App settings** where supported.

## How It Fits Together

1. Interface language controls menus; reply language controls answers.
2. The wake phrase supplies both local activation and the assistant's name.
   The Call shortcut starts voice without an always-listening wake engine.
3. Permissions allow an operating-system capability; they do not approve a
   later Computer Use action or bypass its safety check.
4. The Brain handles chat reasoning. Pipeline adds separate voice input and
   output; Realtime combines the live voice path.
5. Ready compatible providers can act as fallbacks; otherwise the affected
   feature reports that setup is needed.

## Check That It Works

1. After **Get started**, confirm the app returns to the main sidebar and the
   first-run guide stays closed.
2. Open **API Keys**, select **Test** on the active Brain card, and look for
   **Works**.
3. Open **Chats** and send a harmless message. Confirm a reply arrives in the
   selected reply language.
4. For voice, verify **Settings > Audio devices** and try the Call shortcut. If
   wake is enabled, run **Test wake word** and try the phrase after restart.

On a headless system, verify text chat or the Control API; desktop-only features
should report their limits rather than prevent startup.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Use local model** does not appear | Ollama was unreachable or empty | Continue, prepare Ollama, then use its Brain card under **API Keys** |
| **Continue** is disabled on macOS | A permission or stable app-identity check is unresolved | Use **Allow** or **Open Settings**, return and wait for refresh, or choose **Continue with text only** |
| Microphone check reports quiet, missing, or blocked | The input has no usable signal | Check OS access and **Settings > Audio devices** |
| Saved wake word does not respond | Its local model, spoken-language pin, microphone, or activation switch is not ready | Use the Call shortcut; under **Settings > Wake Word**, choose the language you speak, install the offered model, and run **Test wake word** |
| Provider card is active but chat cannot answer | Active does not guarantee a successful live request | Select **Test**, follow the visible error, or configure another compatible provider family |
| App does not reopen | The restart could not start a fresh process | Open the app; setup was already saved |
| First-run setup returns every launch | The completion state is not being read from the same writable data location | Stop repeating setup and follow [Troubleshooting](troubleshooting) for data-directory and version checks |

## Next Steps

- Follow [Start Your First Chat](start-your-first-chat) for a safe first test.
- Read [Providers and API Keys](providers-and-api-keys) before connecting a
  cloud account or changing fallbacks.
- Read [Local AI Providers](local-ai-providers) for Ollama setup and limits.
- Use [Audio and Wake Word](audio-and-wake-word) to finish microphone, wake
  language, activation, or shortcut setup.
- Review [Permissions](permissions) before enabling Computer Use or global
  shortcuts on a new operating system.
