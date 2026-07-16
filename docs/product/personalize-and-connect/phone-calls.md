---
title: "Phone Calls"
slug: phone-calls
summary: Connect the optional telephony feature, understand inbound and outbound calls, and keep call credentials private.
section: "Personalize and connect"
section_order: 3
order: 6
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [telephony, phone-calls, voice, contacts, security]
related: [providers-and-api-keys, credentials-and-secrets, safety-and-approvals]
---

Phone Calls lets people reach Jarvis through a real telephone number. You can
also ask Jarvis to call a saved contact after you approve the action. A call
uses the configured speech recognition, Brain, and voice output, so it sounds
and behaves like the Pipeline voice path without needing audio hardware on the
computer that hosts Jarvis.

This is an optional, experimental integration. The current implementation
targets Twilio and requires a separate account plus a public internet address.
It does not yet have a recorded end-to-end test sign-off, so treat it as a
preview, use only a permitted test number, and keep Chats or desktop voice
available as your fallback.

## Before You Start

You need:

- an installation that includes the optional telephony feature;
- a Twilio account and a voice-capable number;
- a public HTTPS address that Twilio can reach;
- working speech recognition, Brain, and voice-output choices for the call;
- permission to call the intended people and any account verification required
  for your chosen number or destination.

Open **API Keys & Providers > Advanced > Telephony** first. If the page says
**Telephony extra not installed**, follow the installation hint shown there,
restart Jarvis, and return to the same section.

> [!warning] A telephone number is a public entry point. Jarvis validates the
> Twilio connection, but it does not prove who the human caller is. Do not let
> callers speak credentials, and review action permissions before accepting
> inbound calls from people you do not know.

## Plan for Costs and Public Access

Phone Calls can create charges outside Personal Jarvis.

| Cost or requirement | What to check |
|---|---|
| Telephone number | Monthly number rental, regional availability, and identity or address requirements |
| Inbound and outbound minutes | Per-minute rates, destination rates, trial restrictions, and account credit |
| Public HTTPS address | Hosting or tunnel limits, a valid certificate, and whether the address remains stable |
| Speech and Brain services | Usage charges for the providers selected for recognition, reasoning, and voice output |

Review current terms in the relevant provider dashboards before enabling real
calls. Trial accounts may limit who can be called, and local rules can affect
which numbers you may obtain or use.

The public address is a real security boundary. Twilio sends a signed request
to the displayed **Voice webhook**, then opens a protected media stream for one
call. Jarvis checks the provider signature against the exact public URL and
uses a new, one-call secret for the audio stream. Other app endpoints keep
their normal authentication and host checks.

These checks protect the network bridge; they do not authenticate the person
who dialed the number. Also remember that the telephony provider carries the
call audio and call metadata. A connected remote speech or Brain provider may
process its part of the conversation too.

## Set Up Phone Calls

### 1. Prepare the telephony account

Create the Twilio account, complete any required verification, and obtain a
voice-capable number. Keep the account dashboard open, but do not copy its
credential into chat, voice input, a task, or a screenshot.

### 2. Make Jarvis reachable over HTTPS

In the Telephony section, select **Setup script**. The setup view offers a
portable development tunnel, a Windows development helper, and a reverse-proxy
example for a stable server. Choose the path that matches where Jarvis runs.

A tunnel is useful for a short test, but its address may change when it
restarts. A stable server address avoids having to update the provider webhook
after every restart. Paste the public HTTPS address into **Public base URL**.
A local-only address cannot receive a telephone callback.

### 3. Connect the account in the app

Return to **API Keys & Providers > Advanced > Telephony** and fill in:

- **Account SID**;
- **Auth Token** in the protected password field;
- **Phone number (E.164)** using the international format required by the app;
- **Public base URL**;
- **Language code** and an optional **Greeting**.

Turn on **Enabled**, then select **Save**. Jarvis stores the Auth Token through
its credential system and clears the field after saving. The app later reports
only whether a token is stored; it does not return the token value.

### 4. Point the number at Jarvis

Copy the **Voice webhook** shown in the Telephony setup view. In the Twilio
number settings, set the incoming voice webhook to that exact address using
the method shown by the setup guide.

The public base URL and the provider's webhook address must agree exactly. A
different hostname, path, scheme, or stale tunnel address can make a genuine
callback fail its signature check.

### 5. Test both halves

Select **Test connection**. A reachable result confirms that the saved account
details can reach Twilio; it does not test the public callback or the audio
pipeline.

Next select **Self-test voice**. It runs a fixed sample through the Brain and
voice-output path without placing a real call. Review the displayed transcript,
response, and audio result. Fix this test before paying for telephone minutes.

Finally, dial the connected number. A successful inbound call plays the saved
greeting, accepts speech, and speaks a reply. You can start talking while
Jarvis is speaking to interrupt playback. The call ends on a clear hang-up
request, when the caller disconnects, or when the configured time limit is
reached.

## Inbound and Outbound Calls

| Direction | What starts it | What Jarvis does | Safety point |
|---|---|---|---|
| Inbound | Someone dials the connected number | Validates Twilio, opens a one-call audio stream, speaks the greeting, then runs speech recognition -> Brain -> voice output | The bridge is authenticated, but the human caller is not |
| Outbound | You ask Jarvis to call a saved contact | Resolves the contact's phone number, asks for approval, places the call, and speaks the requested or default opening | No call is placed until the confirmation succeeds |

The desktop app does not currently provide a general-purpose dial pad. For an
outbound call, first save a phone number under **Contacts**, then ask Jarvis to
call that contact. If the contact is missing, has no phone number, telephony is
disabled, or the integration is unavailable, Jarvis reports the problem and
does not dial.

## How It Fits Together

| Related feature | Relationship to Phone Calls |
|---|---|
| [Providers and API Keys](providers-and-api-keys) | Telephony has its own account connection, while each call also needs usable speech recognition, Brain, and voice-output capabilities. The connection test and voice self-test check different parts. |
| [Credentials and Secrets](credentials-and-secrets) | The Auth Token is saved through the protected credential path and is never returned to the Telephony page. Account identifiers, telephone numbers, and call details are still private data even when they are not passwords. |
| [Profile and Contacts](profile-and-contacts) | Outbound requests resolve a saved contact by name or alias and use that contact's primary phone number. Telephony does not create missing contacts automatically. |
| [Languages and Voices](languages-and-voices) | The phone path uses the configured Pipeline speech services, language code, greeting, and voice. Test the result because the telephony-specific default prompts currently distinguish English from German; another language code may receive German fallback phrasing. |
| [Safety and Approvals](safety-and-approvals) | Placing a real outbound call is an approval-required action. Tools requested during a call remain subject to their own safety rules; an inbound telephone number does not grant extra permission. |
| [Sessions and Run Inspector](sessions-and-run-inspector) | Each telephone call gets isolated conversational context, separate from desktop chats and voice sessions. **Recent calls** shows a temporary summary, not a durable transcript or a main Sessions archive. |

If the telephony package is absent, the Telephony page still opens and explains
what is unavailable. If the account is disabled, inbound callbacks are ended
cleanly and outbound requests do not dial. If the speech stack cannot start,
the call cannot continue; use Chats or desktop voice while you repair the
reported provider or language setup.

## Check That It Works

The steps below are a test procedure for your installation, not evidence that
the integration has already been verified on every host or account.

1. Select **Test connection** and confirm that the account is reachable.
2. Select **Self-test voice** and confirm that it shows a usable response and
   audio result without an error.
3. Place one permitted test call to the connected number, say a short request,
   and listen for a relevant spoken reply.
4. Return to **Recent calls** and confirm that the call appears with a status,
   turn count, and duration.

If it succeeds, the final call verifies the public call address and live audio
connection on your installation. The two in-app tests alone cannot prove that
an outside caller can reach Jarvis.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Telephony extra not installed** | This installation does not include the optional phone integration | Follow the installation hint in the Telephony section, restart Jarvis, and reopen the page |
| **Setup needed** or **Not reachable** | A required account field is missing, the credential is invalid, the account is restricted, or the service cannot be reached | Recheck the saved fields, account status, credit, and network, then run **Test connection** again |
| **Self-test voice** fails | The Brain or voice-output path is unavailable even if the telephone account works | Test the related choices under **API Keys & Providers**, then review language and voice settings |
| An inbound call ends immediately | Telephony is disabled, the webhook is wrong, the public address changed, or signature validation failed | Enable telephony, copy the displayed webhook again, and make sure the public address is stable and reachable over HTTPS |
| Jarvis cannot place an outbound call | The contact has no usable phone number, approval was declined, telephony is incomplete, or the account cannot dial that destination | Check **Contacts**, approve only the intended call, and review destination permissions and account credit |
| The greeting or fallback phrase uses the wrong language | The phone language code and configured voice do not match, or the current telephony prompt fallback does not support that language | Choose English or German for the first controlled test, set a clear custom greeting, and run **Self-test voice** |
| **Recent calls** becomes empty after a restart | The list is held only for the running app process | Treat it as a live status list, not call-history storage; use the provider's account records when you need a durable billing log |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) to test the Brain,
  speech recognition, and voice output that a phone call uses.
- Read [Credentials and Secrets](credentials-and-secrets) before replacing or
  removing the telephony Auth Token.
- Read [Safety and Approvals](safety-and-approvals) to decide which actions an
  inbound caller may request and how outbound confirmation works.
