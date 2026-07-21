---
title: "Credentials and Secrets"
slug: credentials-and-secrets
summary: Understand how credentials are entered, stored, redacted, replaced, and kept out of conversations and documentation.
section: "Privacy, safety, and support"
section_order: 6
order: 2
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [credentials, secrets, api-keys, tokens, privacy, security]
related: [providers-and-api-keys, privacy-and-local-data, control-key, troubleshooting]
---

Credentials are private values that let Jarvis use an account or service on
your behalf. They include API keys, access tokens, OAuth connections, bot
tokens, client secrets, and the Jarvis Control API key.

Enter each credential only in the protected field built for that connection.
After you save it, Jarvis normally returns a configured state or a masked
preview, not the full value. A configured state confirms that Jarvis can read a
credential. It does not prove that the service accepts it.

The Jarvis Control API key is the deliberate display exception. Its dedicated
panel can reveal and copy the full key because you need it to unlock the
browser or connect a local client.

## Before You Start

- Get the credential from the service's official account page or complete the
  sign-in window opened by Jarvis.
- Review the access scopes and any usage charges before you connect.
- Use a trusted Jarvis session. Close screen sharing and keep credentials out
  of screenshots, recordings, and support messages.

## Use the Right Place

| Connection | Safe place in Jarvis | Never put the value here |
|---|---|---|
| Brain, speech, Realtime, or Jarvis-Agent provider | **API Keys & Providers**, on the matching provider card | Chat, voice input, a task, or `jarvis.toml` |
| Plugin or messaging channel | **Plugins**, then **Connect**, including the browser sign-in or token field | Plugin descriptions, feedback, screenshots, or source files |
| Command-line tool | **CLIs**, open the tool, then use its login action or **Set API Key** | A shell command, command history, or a custom command definition |
| Phone service | **API Keys & Providers > Advanced > Telephony** | Greeting text, call scripts, logs, or phone-call transcripts |
| Jarvis Control API | **API Keys & Providers**, on the key tab named for your assistant (see [Your Assistant's Key](control-key)) | Chat, docs, issue reports, or an untrusted client |
| Manual MCP server | Use a protected plugin connection when one is available | A literal value in `mcp.json`, including an environment or header value |

> [!warning] A chat box and a password box may look similar, but they have
> different jobs. Never ask Jarvis to remember, repeat, send, or configure a
> credential through chat or voice.

The current **MCPs** screen can edit `mcp.json`, but it does not have a
protected credential field. A Model Context Protocol (MCP) definition can use
a reference to a credential that already exists in Jarvis's credential
store. It must not contain the literal value. Prefer the matching **Plugins**
connection when one is available. Otherwise, treat the server as an
operator-managed connection until there is a protected in-app setup path.

## Add, Replace, or Remove Access

1. **Open the feature that owns the connection.** Use the table above instead
   of putting every credential into the provider screen.
2. **Enter the value in its protected field.** Password-style fields hide what
   you type by default. Some connections open the service's browser sign-in
   instead.
3. **Save or connect.** Jarvis stores the resulting credential, then returns a
   status rather than the stored value. A provider field can warn about a
   likely key-format mismatch, but the warning does not block the save.
4. **Verify the connection.** Use **Test** on a provider card, **Test
   connection** for Telephony, **Save and validate** for an API-key CLI, or the
   connection state shown by a plugin or MCP server.
5. **Replace a credential deliberately.** Create or rotate it at the issuing
   service, select **Replace** on a provider card, save the new value, and test
   it. Reconnect a plugin or CLI through its own connection flow.
6. **Remove access from both sides when needed.** Use the provider card's
   delete control, **Remove** for a plugin, or **Disconnect** for a CLI. Then
   revoke the credential in the issuing service's account. Removing Jarvis's
   local copy does not revoke a credential at the service.

Provider-card deletion is fail-closed: if Jarvis cannot verify that the
operating system's credential store removed the value, the app reports a
failure. Unlock the credential store and retry instead of assuming the value
is gone.

Three current exceptions matter:

- **Telephony** can replace its authentication token, but its card has no
  **Remove** control. Turning Telephony off stops the feature; it does not erase
  the stored token. Revoke the token at the phone-service account if you need
  immediate invalidation.
- **The Control API key** cannot be removed. Jarvis creates one on first boot.
  You can replace it with a valid value or regenerate it. Either action
  immediately invalidates the previous key for future authentication.
- A credential supplied outside the app by an operator can still appear as
  configured after you remove an app-stored copy. Clear it through the same
  deployment mechanism and restart Jarvis.

## Shared Provider Credentials

One provider-family credential can cover more than one card. For example, a
single saved credential may be usable by Brain, speech, Realtime, Computer Use,
or Jarvis-Agents from the same provider family. Jarvis does not copy the value
between cards.

When a card is using another compatible slot, it says that it is covered by a
shared key and offers an optional dedicated key. Before deleting a slot that
other provider surfaces use, the app lists those surfaces and asks you to
confirm. After deletion, another compatible credential can still keep the card
configured.

## How Storage Works

The normal path is:

1. **Protected field or browser sign-in** collects the credential.
2. **The operating system's credential store** is used when it works:
   Credential Manager on Windows, Keychain on macOS, or Secret Service on
   Linux.
3. **The feature retrieves the value only when needed.** A provider sends it to
   its service, a CLI receives it for that process, and a plugin or MCP
   connection uses it for its own authenticated request.
4. **The UI receives status, not the stored value.** Provider cards show a
   masked value, a shared-key state, or an empty field. Plugin and MCP lists
   report connection state. The Control API key panel is the reveal exception.

For normal secret lookup, the source order is the operating-system credential
store, an environment variable, the project `.env` file, and then Jarvis's
local file fallback. Environment variables and `.env` are compatibility inputs;
an in-app save never writes to either one.

If no usable operating-system store exists, or it is locked when you save,
Jarvis uses `credentials.json` in its data directory. The file is restricted to
the account that runs Jarvis where the operating system supports those
permissions, but Jarvis does not encrypt it. This fallback keeps in-app setup
working on a headless Linux server with no Secret Service. A missing or locked
credential store does not prevent Jarvis from starting.

If a failed platform write created a newer file copy, that copy can take
priority over a stale platform copy for the same slot. Environment and `.env`
values still take priority over the file. On the next explicit in-app save,
Jarvis retries the platform store and reconciles the fallback copy when it can.

The Control API key also recognizes a dedicated owner-only `.control_api_key`
fallback. Jarvis writes that file when the general credential backend cannot
store the key, and keeps an existing copy aligned when the key changes. An
operator can also seed the key through its supported environment variable. The
app cannot delete or rotate an environment-provided value at its source.

## Backups and Other Devices

Credentials stay on the computer where you saved or connected them. Source
code sync, Wiki sync, and provider fallback do not copy credentials to another
Jarvis installation. Save the key or reconnect the account separately on each
device.

A backup of the Jarvis data directory can contain the unencrypted local
fallback and the Control API key file. Protect such a backup as carefully as
the original credentials. A backup that contains only project files or an
operating-system credential store may contain only part of the credential
state, so do not rely on it as a portable login backup. Use the issuing
service's recovery or rotation flow when moving to a new device.

## Redaction Is a Safety Net, Not a Vault

Jarvis masks common credential shapes in capped Run Inspector and decision-log
previews. It blocks recognized secret patterns from new Wiki content. Provider,
plugin, CLI, and MCP screens receive status or masked previews rather than full
stored tokens.

Provider credential-change events name the storage slot and whether it was set
or deleted; they do not contain the value. Revealing or replacing the Control
API key records the action and the requesting client address without logging
the key itself.

These checks are pattern-based. They cannot recognize every private value, and
they do not turn chat, voice, tasks, logs, or documentation into safe
credential-entry surfaces. If a credential appears in any of those places,
assume it may have been exposed: revoke it at the issuing service, create a new
one, and replace it through the correct protected field.

Review diagnostic output before sharing it. A status or error can safely name
a provider, credential slot, or failure category, but an unfamiliar secret
shape may not match the redaction patterns.

## How It Fits Together

| Feature | Relationship to credentials |
|---|---|
| [Providers and API Keys](providers-and-api-keys) | A card stores a dedicated key or uses a compatible shared key. Runtime fallback can choose another ready provider family, but it does not copy credentials between accounts. |
| [Plugins](plugins) | **Connect** stores a pasted token or the result of OAuth sign-in. **Reconnect** repairs expired or revoked access. **Remove** deletes Jarvis's local plugin tokens. |
| [MCP Connections](mcp-connections) | MCP servers can resolve placeholders from the credential store at connection time. The current MCP editor is for server definitions, not literal secrets. |
| [CLI Connections](cli-connections) | Jarvis can store an API key and provide it to the launched tool as an environment value for that process. A CLI with its own browser login or config file also owns its logout behavior. |
| [Permissions](permissions) | A credential opens an account connection; permissions decide which tools and operating-system capabilities Jarvis may use. One does not replace the other. |
| [Privacy and Local Data](privacy-and-local-data) | Storage location and service choice determine what stays local and what must travel to an external provider. |
| [Safety and Approvals](safety-and-approvals) | Safety rules can require approval before an action. Possessing a credential does not bypass those checks or grant unlimited authority. |

In practice, you save or connect through the owning feature. Jarvis reports the
connection state, then retrieves the credential when that feature makes an
authenticated request. Permissions and safety checks still apply. When a
preferred provider is unavailable, Jarvis can use a compatible configured
fallback where that feature supports one. Otherwise, it reports the missing or
rejected connection.

## Check That It Works

1. Open **API Keys & Providers > Brain**.
2. Choose an API-key provider that you configured. Confirm that its card shows
   a masked saved value or says that a shared key covers it.
3. Select **Test** on that card.
4. Confirm that the result says **Works**.

This verifies that Jarvis can retrieve the credential, reach the selected
service, use the chosen model, and receive a response. A test can make a small
billable provider request.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Save failed** or **Keyring write failed** | Jarvis could not complete a verified write to the platform store or local fallback | Check that Jarvis can write to its data directory, unlock the operating-system credential store, and save again in the same protected field. |
| **Deletion could not be verified** | The credential store is locked, unavailable, or retained a copy | Unlock it and retry. Revoke the credential at the issuing service if access must stop immediately. |
| A removed credential still shows as configured | A shared provider key, environment or `.env` value, or external CLI login still supplies access | Check the card's shared-key state. Clear or disconnect the original source, restart Jarvis when the source is external, and check again. |
| A saved provider says **Key invalid**, **Out of credits**, or **Rate limited** | Storage worked, but the provider rejected the account or request | Check the official provider account, replace or fund the credential, or activate a ready provider from another compatible family. |
| A plugin says **Reconnect**, an MCP server reports incomplete credentials, or a CLI fails validation | Access expired, required authentication is missing, or the external tool rejected it | Reconnect through that feature's protected flow. The MCP screen has no protected credential form, so do not copy a literal value into `mcp.json`. |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) to connect, activate,
  and test the services that power chat, speech, and Jarvis-Agents.
- Review [Privacy and Local Data](privacy-and-local-data) to understand which
  requests stay on your device and which reach a connected service.
- Read [Your Assistant's Key](control-key) before revealing, replacing, or
  regenerating the Control API key.
- Open [Troubleshooting](troubleshooting) when credential, account, network, or
  app health checks fail together.
