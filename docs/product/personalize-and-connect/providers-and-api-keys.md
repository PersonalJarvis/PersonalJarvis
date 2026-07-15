---
title: "Providers and API Keys"
slug: providers-and-api-keys
summary: Connect the services you choose, see which capabilities they provide, and learn where fallback is available.
section: "Personalize and connect"
section_order: 3
order: 1
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [providers, api-keys, models, capabilities, fallback, connections]
related: [credentials-and-secrets, languages-and-voices, troubleshooting, tasks-and-reminders]
---

Use **API Keys & Providers** to connect the services that power Jarvis. You can
choose one service for chat, another for speech, and another for longer
Jarvis-Agent work. You are not locked into one company or one model.

The safe setup has three separate checks: **connect a credential -> set the
provider active -> test that it answers**. A saved credential alone does not
prove that the account has access, credit, or a working model.

## Understand the Three Choices

| Choice | Plain-English meaning | Example decision |
|---|---|---|
| **Provider** | The service Jarvis connects to | Which service should answer chats? |
| **Model** | A particular engine offered by that provider | Do you want a faster or more capable option? |
| **Capability** | Something the chosen provider and model can actually do | Can it understand images, call tools, transcribe speech, or produce audio? |

A provider can offer many models, and models from the same provider can have
different capabilities. Jarvis checks the capability needed for a feature; a
provider name alone is not proof that every model can do the job.

## Choose What Each Provider Powers

Open **API Keys & Providers** and select the category that matches what you
want to change.

| Category | What it powers | What to know |
|---|---|---|
| **Brain** | Chat answers and general reasoning | Choose a model on the provider card. |
| **Tool Model** | Actions, tool routing, and Computer Use | Needs a model that can work with the required tools; Computer Use also needs image understanding. |
| **Voice Input** | Speech-to-text in Pipeline voice | Turns what you say into text. |
| **Voice Output** | Text-to-speech in Pipeline voice | Controls the spoken voice and speech service. |
| **Realtime** | Listening and replying in one live audio stream | This mode is a research preview; some tools and features are not available. |
| **Jarvis-Agents** | Longer background missions | Has its own provider and model choice, separate from the main Brain. |
| **Advanced** | Optional connections such as telephony and the Wiki provider | Each integration can require its own account or credential. |

**Pipeline** uses Voice Input, the Brain, and Voice Output as three separate
steps. **Realtime** uses one full-duplex model for the live audio conversation.
The voice-engine switch shows which mode is selected and which one is serving
the current session. If you change it while idle, the choice applies to the
next voice session.

## Connect, Activate, and Test a Provider

1. **Open API Keys & Providers.** Choose **Brain**, **Tool Model**, **Voice
   Input**, **Voice Output**, **Realtime**, or **Jarvis-Agents**. The cards in
   that category show which connections are open, ready, active, or not
   working.

2. **Choose a provider that has the capability you need.** Read the short note
   on its card. Also check how the provider bills usage; an API account and a
   consumer subscription can be separate products even when they use the same
   sign-in.

3. **Get the credential from the provider.** Use **Get your key here** to open
   the provider's official dashboard. Some Jarvis-Agent providers instead show
   a **Connect** button for an official subscription sign-in.

4. **Enter the credential in the app.** Paste it only into the protected field
   on that provider card and select **Save**. Jarvis masks the saved value and
   returns only whether a credential exists; it does not show the value again.

   > [!warning] Never paste an API key, token, password, or recovery code into
   > chat, voice input, a task, a documentation page, a screenshot, or a
   > configuration file. Use only the protected connection field in the app.

5. **Make the provider active.** If this is the first ready provider in the
   category, Jarvis can activate it after saving. Otherwise select **Set
   active** on the card you want. One provider can be active for the Brain
   while a different provider is active for the Tool Model, speech, or
   Jarvis-Agents.

6. **Choose a model or voice when the card offers one.** A Brain model can be
   selected independently for each provider. Voice Input and Voice Output
   show their selector only on the active, connected provider. Realtime lets
   you choose both a model and a voice.

7. **Select Test.** Wait for a result on the same card. **Works** means the
   provider answered a real minimal request. Other results distinguish a
   missing or invalid key, depleted credit, rate limiting, an unavailable
   model, an unreachable service, or an integration error.

8. **Repeat only for the features you use.** A chat-only setup needs a working
   Brain. Pipeline voice also needs Voice Input and Voice Output. Realtime
   needs a compatible Realtime connection. Heavy missions need a working
   Jarvis-Agent connection.

To rotate a credential, select **Replace**, enter the new value, save it, and
run **Test** again. To remove one, use the delete control on its card. If the
app cannot verify deletion from the operating system's credential store, it
reports the failure instead of claiming the credential is gone.

## How Provider Fallback Works

Jarvis keeps provider choices separate by capability. A fallback for a chat
answer does not silently turn a speech provider into a Brain or grant a model
features it does not have.

1. **A request needs a capability.** Chat needs a Brain; Pipeline voice needs
   transcription, reasoning, and speech; Computer Use needs a suitable Tool
   Model; a mission needs a Jarvis-Agent worker.
2. **Jarvis starts with the active provider and selected model.** For Brain
   work, it can try another suitable model in the same provider family when
   one is available.
3. **Supported paths can continue through compatible fallbacks.** They skip
   connections already known to be missing, unavailable, rate limited, or out
   of credit and can cross to another connected provider family that supports
   the required capability.
4. **The feature degrades honestly when nothing fits.** Jarvis shows or records
   a failure instead of claiming success. A Realtime voice session can fall
   back to Pipeline when Realtime cannot serve the call, provided the Pipeline
   path is available.

Fallback is feature-specific, not one promise shared by every background
action. A scheduled Brain task currently uses its selected Brain provider for
that run and records **failed** instead of crossing to another provider family.
Read [Tasks and Reminders](tasks-and-reminders) before relying on unattended
work.

Fallback does not copy credentials between accounts. Some cards intentionally
reuse a credential for related services, while other surfaces use a dedicated
credential. The labels in the app are the source of truth for each connection.

## How It Fits Together

| Connected feature | Relationship to providers | Where to continue |
|---|---|---|
| **Credentials and Secrets** | Stores, replaces, and removes the private value behind a provider card. The provider list receives only a ready/not-ready signal. | Read [Credentials and Secrets](credentials-and-secrets) before rotating or removing access. |
| **Languages and Voices** | Your reply-language choice decides which language Jarvis uses. The active Voice Output or Realtime provider supplies the actual voice and available voice models. | Read [Languages and Voices](languages-and-voices) to keep language and voice choices aligned. |
| **Phone Calls** | Calls use their own telephony account, then use Jarvis's configured speech-to-text, Brain, and text-to-speech Pipeline for the conversation. | Read [Phone Calls](phone-calls) before adding telephony credentials or placing a call. |
| **Jarvis-Agents** | Heavy missions have a separate worker selection. If that worker is unavailable, Jarvis can use another reachable worker family; the main Brain selection does not automatically become the worker selection. | Read [Jarvis-Agents](jarvis-agents) to choose a worker and follow mission results. |
| **Tasks and Reminders** | A scheduled Brain task currently stays with its selected Brain provider for that run and records a failure when it cannot continue. | Read [Tasks and Reminders](tasks-and-reminders) before depending on unattended fallback behavior. |
| **Troubleshooting** | Provider tests identify the failing layer before you restart or replace anything. | Use [Troubleshooting](troubleshooting) when several categories fail or the app itself is offline. |

The full path is: **your request -> required capability -> active provider and
model -> safety or tool checks -> answer, speech, action, or mission output**.
When the selected feature supports fallback and cannot continue, Jarvis tries
only compatible, connected alternatives and keeps the failure visible when
none are available. Features without that fallback record the failure at their
own surface.

## Check That It Works

1. Open **API Keys & Providers > Brain**.
2. On the active provider card, select **Test** and confirm that it shows
   **Works**.
3. Open **Chats** and send `Reply with: provider check complete.`
4. Confirm that a matching answer appears.

This proves that the credential, selected model, provider connection, and main
chat path work together. Test Voice Input, Voice Output, Realtime, and
Jarvis-Agents on their own cards before relying on those separate features.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Setup needed**, **No key set**, or an open card | The active category has no usable credential | Save the credential on the intended card, then select **Set active** if another provider is already active. |
| **Key invalid**, **Out of credits**, **Rate limited**, or **Model unavailable** | Jarvis reached the service, but the account, allowance, or selected model blocked the request | Check the provider's official account page, choose an available model, or activate a ready provider from another compatible family. |
| **Unreachable** or **Integration error** | The service, network path, provider adapter, or app connection did not complete the test | Retry once, check the provider's service status and your network, then use [Troubleshooting](troubleshooting) if other providers fail too. |
| The card is **ready** but another card is **active** | The credential exists, but that provider does not currently power this category | Select **Set active**, wait for the success message, then run **Test** on the newly active card. |
| A speech change is not used immediately, or Realtime shows Pipeline at runtime | Voice Input may require the next voice or app start; a live Voice Output switch can also require the next start; Realtime fell back because it could not serve this session | End and start the voice session again. Re-test the selected voice categories and confirm that the runtime status names the expected engine. |

## Next Steps

- Read [Credentials and Secrets](credentials-and-secrets) to understand where
  protected values are stored and how to rotate or remove them safely.
- Use [Languages and Voices](languages-and-voices) to choose the reply language,
  spoken voice, and voice behavior after the providers work.
- Follow [Phone Calls](phone-calls) when you want the same Brain and Pipeline
  voice path to answer through a telephone connection.
- Open [Troubleshooting](troubleshooting) for app-wide connection, startup, and
  provider recovery checks.
