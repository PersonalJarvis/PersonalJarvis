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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [telephony, phone-calls, voice, contacts, security]
related: [providers-and-api-keys, credentials-and-secrets, safety-and-approvals]
---

Phone Calls connects a Twilio voice number to Jarvis. People can call that
number and talk to Jarvis, and you can ask Jarvis to call a saved contact after
you approve the action. Calls reuse the configured speech recognition, Brain,
and voice-output providers. The host does not need a microphone or speakers
because the audio travels over the network.

This integration is optional and experimental. Automated tests cover the
routes, audio conversion, and a simulated Media Stream, but the repository has
no recorded live telephone-network sign-off. Test with numbers and recipients
that your account and local rules permit, and keep Chats available as a
fallback.

## Before You Start

You need:

- an installation that includes the optional telephony feature;
- a Twilio Account SID, Auth Token, and voice-capable Twilio number;
- a stable public HTTPS address with a valid certificate and WebSocket support;
- working speech-recognition, Brain, and voice-output providers;
- permission to call the intended people, plus any identity, address, caller-ID,
  or destination approval required by Twilio and local rules.

Open **API Keys & Providers > Advanced > Telephony** first. If the page says
**Telephony extra not installed**, follow the installation hint shown there,
restart Jarvis, and return to the same section.

> [!warning] A telephone number is a public entry point. A valid Twilio
> signature proves that Twilio sent the webhook; it does not prove who the
> human caller is. Never ask callers to speak credentials. Review action
> permissions before you accept calls from people you do not trust.

## Plan for Costs and Public Access

Phone Calls can create charges outside Personal Jarvis.

| Cost or requirement | What to check |
|---|---|
| Telephone number | Number rental, regional inventory, voice capability, and identity or address requirements |
| Calls and Media Streams | Inbound and outbound call rates, Media Streams usage, destination rates, and account credit |
| Public HTTPS address | Hosting or tunnel limits, a valid certificate, and whether the address remains stable |
| Speech and Brain services | Usage charges for the providers selected for recognition, reasoning, and voice output |

Review current terms in each provider dashboard before enabling real calls.
Twilio trial limits and Voice Geographic Permissions can restrict incoming
callers and outbound destinations. Number availability and regulatory
requirements vary by country and number type.

Jarvis accepts telephone numbers only in E.164 form: `+`, the country code,
and the remaining digits without spaces or a local trunk prefix. This syntax
check does not prove that the number belongs to your account, supports Voice,
or can call a particular destination.

The public address is a security boundary. Twilio sends a signed `POST` request
to the displayed **Voice webhook**, then opens a secure WebSocket for the
bidirectional Media Stream. Jarvis checks the signature against the exact
public URL and uses a new secret for that call's media connection.

Twilio supports `wss://` for Media Streams, so the public base URL must use
HTTPS. The proxy or tunnel must pass both the webhook and WebSocket without
rewriting their public scheme, host, or path. Other Jarvis endpoints keep their
normal host and Control Key checks, but exposing the server still increases its
attack surface. Read [Control Key](control-key) before forwarding the Jarvis
port.

These checks protect the network bridge, not the caller's identity. Twilio
carries the call audio and metadata. Remote speech, Brain, or voice-output
providers also process the parts of the conversation sent to them.

## Set Up Phone Calls

### 1. Prepare the telephony account

Create the Twilio account, complete its current verification requirements, and
obtain a number whose capabilities include Voice. Check trial restrictions and
Voice Geographic Permissions for every destination you plan to test. Keep the
account dashboard open, but do not copy its credential into chat, voice input,
a task, or a screenshot.

### 2. Make Jarvis reachable over HTTPS

In the Telephony section, select **Setup script**. The setup view shows a
portable `cloudflared` command, a Windows PowerShell helper, and a Caddy
reverse-proxy example for a stable server. These are commands for you to run;
the button does not create a tunnel or server.

A tunnel is useful for a short test, but its address may change after a
restart. A stable server address avoids repeated webhook changes. Paste the
public HTTPS origin into **Public base URL**. A local-only address cannot
receive Twilio's webhook or WebSocket connection.

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

The phone-specific greeting and fallback phrases currently distinguish English
from German only. Use an English code such as `en-US` or a German code such as
`de-DE`. Other codes are treated as German for those phrases, even when the
selected providers support another language. The speech-recognition provider
keeps its own configured language, so align that setting with this field.

Restart Jarvis after you add or change **Public base URL**. The public hostname
is added to the server's trusted-host list when the server starts.

### 4. Point the number at Jarvis

Copy the **Voice webhook** shown in the Telephony setup view. In the Twilio
number settings, set the incoming voice webhook to that exact address and set
its method to `POST`.

The public base URL and the provider's webhook address must agree exactly. A
different hostname, path, scheme, or stale tunnel address can make a genuine
callback fail its signature check.

### 5. Test both halves

Select **Test connection**. A reachable result confirms that the Account SID
and Auth Token can fetch the Twilio account. Check the returned account status.
This test does not validate the number, credit, destination permissions,
public webhook, or audio path.

Next select **Self-test voice**. It sends fixed text to the Brain, synthesizes
the response, and tests conversion to Twilio's audio format without placing a
call. It does not run speech recognition, contact Twilio, or test the public
WebSocket. Review the displayed response, error, and audio-byte count.

Finally, dial the connected number. A successful inbound call plays the saved
or default greeting, accepts speech, and speaks a reply. The call ends after a
recognized hang-up request, when the caller disconnects, or when the configured
time limit is reached. Live interruption behavior has not been signed off on a
real call, so let the first test greeting finish before speaking.

## Inbound and Outbound Calls

| Direction | What starts it | What Jarvis does | Safety point |
|---|---|---|---|
| Inbound | Someone dials the connected number | Validates the Twilio webhook, opens a one-call media connection, speaks the greeting, then runs speech recognition, Brain, and voice output | Twilio is authenticated, but the human caller is not |
| Outbound by contact | You ask Jarvis to call a saved contact | Resolves the contact's primary phone number, requests approval, places the call, and speaks the requested or default opening | The `call-contact` action uses the `ask` safety tier |
| Outbound by CLI | You run `jarvis telephony outbound <number> --yes` | Validates an E.164 destination and asks Twilio to place the call | This skips contact lookup but still requires complete, enabled telephony setup |

The desktop app does not provide a general-purpose dial pad. For the normal app
flow, save a phone number under **Contacts**, then ask Jarvis to call that
contact. If the contact or number is missing, telephony is disabled, approval
is declined, or Twilio rejects the destination, Jarvis does not dial.

## How It Fits Together

| Related feature | Relationship to Phone Calls |
|---|---|
| [Providers and API Keys](providers-and-api-keys) | Telephony has its own account connection, while each call also needs usable speech recognition, Brain, and voice-output capabilities. The connection test and voice self-test check different parts. |
| [Credentials and Secrets](credentials-and-secrets) | The Auth Token is saved through the protected credential path and is never returned to the Telephony page. Account identifiers, telephone numbers, and call details are still private data even when they are not passwords. |
| [Profile and Contacts](profile-and-contacts) | Outbound requests resolve a saved contact by name or alias and use that contact's primary phone number. Telephony does not create missing contacts automatically. |
| [Languages and Voices](languages-and-voices) | The call uses the configured Pipeline speech and voice-output providers. The telephony language code controls synthesis and phone-specific phrases, while speech recognition keeps its own language setting. Phone-specific phrases currently support English and German only. |
| [Safety and Approvals](safety-and-approvals) | A contact-based outbound call requires approval. Actions requested during an inbound call keep their own safety tier and may wait for approval in the app. A telephone call grants no extra permission. |
| [Sessions and Run Inspector](sessions-and-run-inspector) | Each telephone call gets isolated conversational context, separate from desktop chats and voice sessions. **Recent calls** shows a temporary summary, not a durable transcript or a main Sessions archive. |

The phone transport is Twilio-only; there is no automatic fallback to another
telephony service. If the optional package is absent, the Telephony section
still opens and explains what is unavailable. If telephony is disabled,
inbound callbacks are ended and outbound requests do not dial. The Brain may
use its configured provider fallback chain, but a call cannot continue if the
speech-recognition or voice-output provider cannot start.

The network-audio path has no desktop audio-hardware dependency and is intended
to run on Windows, macOS, Linux, and a headless server. A minimal headless
install may need the telephony extra shown by the app. Live account, carrier,
and regional behavior still needs testing on your installation.

## Check That It Works

The steps below are a test procedure for your installation, not evidence that
the integration has already been verified on every host or account.

1. Save the setup and restart Jarvis if the public base URL changed.
2. Select **Test connection** and confirm that the account is reachable with
   the expected account status.
3. Select **Self-test voice** and confirm that it shows a response, a nonzero
   audio-byte count, and no error.
4. Place one permitted inbound test call, wait for the greeting, say a short
   request, and listen for a relevant spoken reply.
5. Return to **Recent calls** and confirm that the call appears with a status,
   turn count, and duration.
6. If you need outbound calling, ask Jarvis to call a permitted saved contact,
   review the confirmation, and approve only the intended call.

If it succeeds, the final call verifies the public call address and live audio
connection on your installation. The two in-app tests alone cannot prove that
an outside caller can reach Jarvis.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Telephony extra not installed** | This installation does not include the optional phone integration | Follow the installation hint in the Telephony section, restart Jarvis, and reopen the page |
| **Setup needed** or **Not reachable** | A required account field is missing, the credential is invalid, or Twilio cannot be reached | Recheck the saved SID and token, inspect the returned account status, then run **Test connection** again; remember that this test does not validate the number or webhook |
| **Self-test voice** fails or reports zero audio bytes | The Brain or voice-output path is unavailable even if Twilio credentials work | Test the related providers under **API Keys & Providers**, then align the voice and language settings |
| An inbound call ends immediately or never reaches **Recent calls** | Telephony is disabled, the app was not restarted after a hostname change, the webhook is not exact `POST`, the host is unreachable, or signature validation failed | Restart Jarvis, copy the displayed webhook again, and verify HTTPS plus WebSocket forwarding without URL rewriting |
| The call connects but has no usable conversation, or uses the wrong fallback language | The media WebSocket or speech provider failed, or the phone language does not match the speech settings | Check proxy WebSocket support and provider health; test with `en-US` or `de-DE` and a matching speech-recognition language |
| Jarvis cannot place an outbound call, or **Recent calls** is empty after restart | The contact or E.164 number is invalid, approval was declined, Twilio blocked the destination, or the in-memory call list was cleared | Check **Contacts**, account credit, trial and geographic permissions, and approve only the intended call; use Twilio's records for durable billing history |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) to test the Brain,
  speech recognition, and voice output that a phone call uses.
- Read [Credentials and Secrets](credentials-and-secrets) before replacing or
  removing the telephony Auth Token.
- Read [Profile and Contacts](profile-and-contacts) to add a telephone number
  for contact-based outbound calls.
- Read [Safety and Approvals](safety-and-approvals) to decide which actions an
  inbound caller may request and how outbound confirmation works.
