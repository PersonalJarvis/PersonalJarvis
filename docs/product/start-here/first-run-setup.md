---
title: "Complete First-Run Setup"
slug: first-run-setup
summary: "Walk through language, permissions, microphone, wake word, and provider setup without exposing credentials."
section: "Start here"
section_order: 1
order: 3
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [setup, onboarding, language, permissions, microphone, wake-word, providers]
related: [providers-and-api-keys, audio-and-wake-word, permissions, start-your-first-chat]
---

First-run setup records your acceptance of the Terms, then guides you through
language, provider guidance, system permissions, and voice activation. You do
not enter a provider credential during onboarding. The app shows you where to
connect one after setup.

The interface supports English, German, and Spanish. A fresh installation
starts in English, and missing interface text falls back to English. Replies
start in **Auto**, which follows the language of the conversation. If Jarvis
cannot determine a reply language, it falls back to English.

You can finish setup without a microphone, a wake word, or an online provider.
The main app will still open, but chat replies and provider-backed voice
features need a working provider or local integration for the relevant stage.

## Before You Start

- Install and open the desktop app.
- Have a microphone available only if you want to use voice.
- If you plan to use an online provider, get its credential from the
  provider's official account page. A provider is the service or local
  integration that handles a request.

> [!warning] Enter a provider credential only in **API Keys & Providers**.
> Never paste it into chat, speak it to Jarvis, add it to `jarvis.toml`, or
> include it in a screenshot.

Jarvis saves credentials in the operating system's credential store when one
is available: Windows Credential Manager, macOS Keychain, or Linux Secret
Service. If that store is missing or unusable, Jarvis uses a local file that is
written with best-effort user-only permissions. That fallback is not
OS-encrypted storage.

## Complete the Setup

### 1. Accept the Terms and open the guide

1. On **Before you continue**, review the risk notice. Use **View the full
   Terms of Use** if you want to read the complete document.
2. Select the acknowledgment checkbox, then select **I understand —
   continue**. If you select **Decline & quit**, the app closes without
   completing setup. The same screen returns the next time you open the app.
3. On **Watch the 2-minute tour**, play the video or select **Skip the video**.
   Select **Continue** when you are ready.
4. On **Welcome to Personal Jarvis**, select **Get started**.

The **Skip setup for now** link on the welcome screen records that screen as
skipped and continues to language setup. It does not close the first-run guide.

### 2. Choose your languages

1. On **Choose your language**, choose **English**, **Deutsch**, or
   **Español** under **Interface language**. English is selected on a fresh
   installation.
2. Under **Reply language**, choose **Auto**, **English**, **Deutsch**, or
   **Español**. Auto is the initial setting and follows the current
   conversation.
3. Select **Next**. The interface changes to the selected language.

Interface and reply language are separate settings. Changing menu text does
not force every answer into the same language.

### 3. Review the provider setup path

The **Set up API keys after onboarding** screen is a guide only. It does not
contain a credential field and does not choose a provider for you.

1. Review where **API Keys** appears in the main sidebar.
2. Review the two voice modes:

   | Voice mode | What it uses | Product status |
   |---|---|---|
   | Realtime | One compatible provider listens and replies in a live audio stream | Recommended, research preview |
   | Pipeline | Separate Brain, Voice Input, and Voice Output providers | Available, not recommended |

3. Select **Continue onboarding**. You will choose providers and enter any
   required credentials after the main app opens.

### 4. Review system permissions

On macOS, **Allow access on this Mac** lists six capabilities:
**Microphone**, **Screen Recording**, **Accessibility**, **Input Monitoring**,
**Input control**, and **Keychain (API keys)**. Use **Allow** or **Open
Settings** for the features you want, then return to Jarvis. The status updates
automatically.

Select **Continue** when it becomes available. **Allowed** and **Not required**
are ready states; Screen Recording can show **Restart pending** and still allow
you to finish. Pending grants take effect with the single automatic restart at
the end of onboarding. The setup screen does not require a separate mid-flow
restart.

If you do not want to grant all access now, select **Continue with text only**.
Voice input, global shortcuts, Computer Use, or OS-backed credential storage
may remain unavailable until you return to Permissions in Settings.

On Windows and Linux, the page reports that no extra desktop privacy
permissions are required and allows you to continue. Features can still depend
on a microphone, display, or other capability being present on the machine.

### 5. Choose how to activate voice

The activation screen offers two choices:

- **Wake word** keeps a local listener ready for a phrase you choose.
- **Keyboard shortcut** turns the wake listener off and uses the Call shortcut
  to start a voice session. You can change the shortcut later under **Settings
  > Voice Keybinds**.

If you choose **Keyboard shortcut**, select **Continue**.

If you choose **Wake word**:

1. Enter at least two characters after the fixed word **Hey**.
2. Select the checkbox confirming that you are responsible for the word you
   choose.
3. Optionally select **Test your microphone** or **Say your wake word once**.
   Both listen for a short period and report **Sounds good**, a quiet input, a
   missing device, a permission problem, or a temporary check error. The check
   does not block saving.
4. Select **Save wake word**.

If the chosen phrase needs a local engine that is not ready, Jarvis offers
**Enable any wake word** to install the local speech pack. You can select
**Continue anyway**, but use the Call shortcut until the app reports that the
wake word is available.

### 6. Finish onboarding

1. On **You're all set!**, enable **Start Jarvis automatically at login** if
   the option appears and you want it. The option is hidden on unsupported or
   headless systems.
2. Select **Get started**.

The desktop app records setup as complete, then normally closes and reopens
once so language, permissions, and wake-word choices can start from fresh
state. If the automatic restart cannot be scheduled, setup still remains
complete and the current process stays open.

### 7. Connect only the providers you need

After the main app opens:

1. In the sidebar, open **API Keys**. The page title is **API Keys &
   Providers**.
2. Choose **Realtime** or **Pipeline** for voice. For text chat, open
   **Brain**. Realtime needs a compatible provider credential before it can
   become the active voice mode.
3. Open a provider card and follow the authentication method it shows. Local
   providers may need no credential. Subscription providers can show a sign-in
   action. API-key providers show a masked credential field and may include
   **Get your key here**.
4. For an API key, enter it in the provider card and select **Save**. If that
   category already has an active provider, select **Set active** for the new
   one.
5. Select **Test**. **Works** means the provider answered the live test.

You do not need to fill every credential field. Connect only the categories
you intend to use. A saved key can make a card ready, but only a successful
test proves that the account, model, quota, and service can answer now.

## How It Fits Together

1. The first-run guide records your Terms decision and setup choices.
2. Interface language controls app text. Reply language controls answers;
   **Auto** follows the conversation and keeps a short interjection in the
   conversation's established language.
3. A wake word or the Call shortcut starts voice input. The microphone and the
   relevant operating-system permissions must be ready for that input method.
4. A Brain provider handles chat answers. Pipeline voice uses separate Voice
   Input, Brain, and Voice Output stages, while Realtime uses one live audio
   connection.
5. Computer Use and other actions can require more system access and a safety
   confirmation. Granting a permission during setup does not approve future
   actions.
6. When a preferred provider is unavailable, Jarvis can use a compatible
   fallback that you have configured. If no compatible provider is ready, the
   affected feature reports that setup or attention is required.

## Check That It Works

After you select **Get started**, wait for the desktop app to reopen. Setup is
complete when the main sidebar appears and the first-run guide does not return.
If the app does not reopen automatically, open it once yourself; the completion
record was saved before the restart was attempted.

If you connected a Brain provider, select **Test** on its card before opening
**Chats**. A **Works** result confirms that the provider is ready for a first
message.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Continue** is unavailable on the macOS permissions page | A permission, app identity check, or status refresh is still pending | Use **Allow** or **Open Settings**, return to Jarvis, and wait for the row to update; or select **Continue with text only** |
| The microphone check reports quiet input, no device, or required permission | Jarvis cannot capture a usable signal from the selected input | Check the operating-system input device and level, grant **Microphone** access on macOS, then try again |
| The saved wake phrase does not activate Jarvis | The local engine is missing, the listener is off, the spoken language setting is wrong, or the microphone is unavailable | Use the Call shortcut, then review **Settings > Wake Word**, including the language you speak and the offered local-engine install |
| A provider card is ready but its test does not say **Works** | A credential is stored, but the account, model, quota, or service is not working | Follow the **Test** result and try another compatible provider family if one is available |
| Menus and answers use different languages | Interface language and reply language are independent | Review both choices in **Languages** and use **Auto** only when replies should follow the conversation |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) to understand provider
  categories, tests, fallback choices, and credential storage.
- Read [Audio and Wake Word](audio-and-wake-word) to adjust your microphone,
  spoken language, activation phrase, local engine, and Call shortcut.
- Read [Permissions](permissions) before enabling voice, global shortcuts, or
  Computer Use on a new operating system.
- Follow [Start Your First Chat](start-your-first-chat) for a simple first
  conversation and the signs that your Brain provider is ready.
