---
title: "Your Assistant's Key (Control Key)"
slug: control-key
summary: Find, use, replace, and regenerate the one key that unlocks Jarvis in a browser and authenticates local agents.
section: "Privacy, safety, and support"
section_order: 6
order: 6
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-16
phase: "-"
audience: end-user
tags: [control-key, authentication, unlock, browser, security, api-keys]
related: [credentials-and-secrets, control-api-reference, providers-and-api-keys, troubleshooting]
---

Every Personal Jarvis installation protects itself with one private key, the
**Control Key**. It has exactly two jobs:

- **Unlock the interface.** When you open Jarvis in a normal browser — on the
  same computer or from another device — a lock screen asks for this key once,
  then keeps you signed in with a session cookie.
- **Authenticate local agents.** Terminal tools and coding agents (the Jarvis
  CLI, Codex, Claude Code) present the same key to drive Jarvis over the
  Control API instead of clicking through the screen.

The desktop app itself never asks for the key: it proves it belongs to the
installation through a private startup handshake.

## You Already Have a Key

Nobody sends you a Control Key, and there is no account behind it. The moment
Jarvis starts for the first time, it generates a long random key **for this
installation only** and stores it in your operating system's credential store
(Credential Manager on Windows, Keychain on macOS, Secret Service on Linux),
with a restricted file in the Jarvis data directory as the fallback on systems
without such a store. No two installations share a key, and the key never
leaves your machine by itself.

## Find the Key

The key lives in its own section, named after your assistant: if your wake
word is "Nico", the tab is called **Nico Key**.

1. Open Jarvis on the computer it is installed on.
2. Open **API Keys & Providers**.
3. Select the key tab named after your assistant (for example **Nico Key**).
4. Use **Show** to reveal the key, or **Copy** to put it on the clipboard.

The lock screen in the browser points to this same place, so anyone who hits
it can find their way here.

## Unlock Jarvis in a Browser

1. Open the Jarvis address in the browser. The lock screen appears.
2. Paste the Control Key and select **Unlock**.
3. The browser receives a private session cookie; the key itself is not stored
   in the browser.

The exchange requires a direct same-computer connection or an encrypted
(HTTPS) one, so the key never crosses a sniffable network hop in the clear.

## Choose Your Own Key

If you would rather remember the key than copy a generated one:

1. In the key section, select **Choose my own key**.
2. Enter the new key twice. It must be at least 12 characters long and may use
   letters, digits, and `. _ ~ -` (no spaces).
3. Select **Set this key**.

The new key replaces the old one immediately and everywhere: the browser lock
screen, other devices, and every agent using the old key need the new value.
Sessions that are already signed in stay signed in.

## Regenerate the Key

If the key may have leaked — it appeared in a screenshot, a chat, or a log —
generate a fresh random one:

1. In the key section, select **Generate random key**.
2. Confirm the dialog. The old key stops working immediately, everywhere.
3. Copy the new key and update every device and agent that used the old one.

Regeneration is deliberately behind a confirmation dialog because it is a
lockout-grade action for every client that still holds the old key.

## On a Headless Server

A server installation without a display has no settings screen, so two other
paths exist:

- Read the fallback file `.control_api_key` in the Jarvis data directory.
- Ask a running instance over the loopback interface:
  `GET /api/control/api-key` (see the
  [Control API Reference](control-api-reference)).

A public (non-loopback) listener refuses to start without a key, so a server
install can never be exposed unlocked by accident.

## Keep It Safe

Treat the Control Key like an administrator password. Whoever has it can
operate your Jarvis: read settings, switch providers, and use every protected
operation of the instance. Never put it in chat or voice input, source code,
URLs, logs, screenshots, documentation, or shell history. Store copies only in
a trusted password manager.

## How It Fits Together

| Feature | Relationship to the Control Key |
|---|---|
| [Credentials and Secrets](credentials-and-secrets) | The key is stored like every other credential — operating-system store first, restricted file fallback — but it is the one value the UI may reveal and copy. |
| [Control API Reference](control-api-reference) | Agents and scripts present the key as the Bearer credential for `/api/control/*` operations. |
| [Providers and API Keys](providers-and-api-keys) | Provider keys connect external services to Jarvis; the Control Key protects your own Jarvis. Different credentials for different doors. |

## Check That It Works

1. Open **API Keys & Providers** and select the key tab named after your
   assistant.
2. Select **Show**, then **Copy**.
3. Open the Jarvis address in a private browser window. The lock screen
   appears.
4. Paste the key and select **Unlock**. The interface loads, which proves the
   stored key and the unlock exchange both work.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The lock screen rejects your key | The key was regenerated or replaced since you copied it | Read the current key in the key section on the install computer and try again |
| You never received a key | Expected — keys are generated locally, not sent | Follow [Find the Key](#find-the-key) above |
| **Setting the key failed** | Neither the OS credential store nor the fallback file accepted the new value | Unlock the credential store, check that Jarvis can write to its data directory, and retry; the old key stays active until a new one is stored |
| An agent or CLI suddenly gets `401` errors | The key was regenerated or replaced after the agent stored it | Update the agent's stored key with the current value |

## Next Steps

- Read [Credentials and Secrets](credentials-and-secrets) for how Jarvis
  stores private values in general.
- Use the [Control API Reference](control-api-reference) to drive Jarvis from
  scripts and agents with this key.
