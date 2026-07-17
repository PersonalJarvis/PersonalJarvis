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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [credentials, secrets, api-keys, tokens, privacy, security]
related: [providers-and-api-keys, privacy-and-local-data, control-key, troubleshooting]
---

Credentials are private values that let Jarvis use an account or service on
your behalf. They include API keys, access tokens, OAuth connections, bot
tokens, client secrets, and the Jarvis Control API key.

Enter each credential only in the protected field built for that connection.
Jarvis normally shows a saved/not-saved state or a masked value afterward. A
connected account still has its own permissions, billing, and revocation rules
at the service that issued the credential.

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
| Plugin or messaging channel | **Plugins > Connect**, including the browser sign-in or protected token field | Plugin descriptions, feedback, screenshots, or source files |
| Command-line tool | **CLIs**, open the tool, then use **Connect** or **Set API Key** | A shell command, command history, or a custom command definition |
| Phone service | **API Keys & Providers > Advanced > Telephony** | Greeting text, call scripts, logs, or phone-call transcripts |
| Jarvis Control API | **API Keys & Providers**, on the dedicated key tab named for your assistant (see [Your Assistant's Key](control-key)) | Chat, docs, issue reports, or an untrusted client |
| Manual MCP server | Use a protected plugin connection when one is available | A literal value in `mcp.json`, including its environment and header fields |

> [!warning] A chat box and a password box may look similar, but they have
> different jobs. Never ask Jarvis to remember, repeat, send, or configure a
> credential through chat or voice.

The current **MCPs** screen can edit `mcp.json`, but it does not provide a
protected credential-entry form. If a manual Model Context Protocol (MCP)
server requires a secret, do not paste the value into that editor. Prefer the
corresponding **Plugins** connection when available; otherwise treat the server
as an operator-managed connection until a protected in-app path is available.

## Add, Replace, or Remove Access

1. **Open the feature that owns the connection.** Use the table above instead
   of putting every credential into the provider screen.
2. **Enter the value in its protected field.** Password-style fields hide what
   you type. Some connections open the service's own browser sign-in instead.
3. **Save or connect.** Jarvis stores the resulting credential, then returns a
   readiness state rather than the full value. A format hint can warn about the
   wrong kind of key, but the connection test is the real check.
4. **Verify the connection.** Use **Test** on a provider or Telephony card,
   **Connected** in Plugins or CLIs, or a successful MCP connection status.
5. **Replace a credential deliberately.** Create or rotate it at the issuing
   service, select **Replace** in Jarvis, save the new value, test it, and then
   revoke the old value at the service when that service supports overlap.
6. **Remove access from both sides when needed.** Use the provider card's
   delete control, **Disconnect** for a plugin or CLI, and revoke the
   credential in the issuing service's account. Removing a local copy does not
   revoke a credential that still exists at the service.

Provider-card deletion is fail-closed: if Jarvis cannot verify that the
operating system's credential store removed the value, the app reports a
failure. Unlock the credential store and retry instead of assuming the value
is gone.

Two current exceptions matter:

- **Telephony** can replace its authentication token, but its card has no
  **Remove** control. Turning Telephony off stops the feature; it does not erase
  the stored token. Revoke the token at the phone-service account if you need
  immediate invalidation.
- A credential supplied outside the app by an operator can still appear as
  configured after you remove the app-stored copy. Clear it through the same
  deployment mechanism, then restart Jarvis.

## How Storage Works

The normal path is short:

1. **Protected field or browser sign-in** collects the credential.
2. **Jarvis's credential service** chooses a portable storage option.
3. **The operating system's credential store** is used when it is available:
   Credential Manager on Windows, Keychain on macOS, or Secret Service on
   Linux.
4. **The feature retrieves the value only when needed.** A provider sends it to
   its service, a CLI receives it for that process, and a plugin or MCP
   connection uses it for its own authenticated request.
5. **The UI receives status, not the secret.** Provider cards show whether a
   credential is set. The Control API key is the deliberate exception: its
   panel can reveal or copy the full value, replace it with a key you choose,
   or regenerate it — replacement and regeneration immediately invalidate the
   previous Control API key.

If no usable operating-system store exists, or it is locked when you save,
Jarvis falls back to a restricted file in its data directory. This keeps
in-app setup working on a headless server, but the fallback is not encrypted by
Jarvis. Protect the data directory, its backups, and the account that runs the
app. When the operating-system store becomes usable again, saving a credential
prompts Jarvis to retry it and reconcile an older fallback copy.

Environment-provided credentials are a compatibility input, not an in-app
storage destination. Jarvis cannot delete or rotate them for you, and they can
take precedence over a file fallback. This is why a removed credential can
appear again until the deployment source is cleared and the app restarts.

## Redaction Is a Safety Net, Not a Vault

Jarvis masks common credential shapes in short Run Inspector previews and
blocks recognized secret patterns from new Wiki content. Plugin lists and
provider lists return connection status without returning stored tokens.

These checks are pattern-based. They cannot recognize every private value, and
they do not turn chat, voice, tasks, logs, or documentation into safe
credential-entry surfaces. If a credential appears in any of those places,
assume it may have been exposed: revoke it at the issuing service, create a new
one, and replace it through the correct protected field.

## How It Fits Together

| Feature | Relationship to credentials |
|---|---|
| [Providers and API Keys](providers-and-api-keys) | The card chooses a provider and model; its protected field stores the private value. Fallback can switch to another ready provider, but it never copies credentials between accounts. |
| [Plugins](plugins) | **Connect** stores a pasted token or the result of OAuth sign-in. **Reconnect** repairs expired or revoked access; **Disconnect** removes the plugin connection. |
| [MCP Connections](mcp-connections) | MCP servers can resolve protected placeholders at connection time. The current MCP editor is for server definitions, not literal secrets. |
| [CLI Connections](cli-connections) | Jarvis can store an API key and provide it only to the launched tool. A CLI that owns its own browser login or config file also owns that credential's logout behavior. |
| [Permissions](permissions) | A credential opens an account connection; permissions decide which tools and operating-system capabilities Jarvis may use. One does not replace the other. |
| [Privacy and Local Data](privacy-and-local-data) | Storage location and service choice determine what stays local and what must travel to an external provider. |
| [Safety and Approvals](safety-and-approvals) | Safety rules can require approval before an action. Possessing a credential does not bypass those checks or grant unlimited authority. |

The practical flow is: **you connect privately -> Jarvis confirms readiness ->
a feature requests an allowed capability -> permissions and safety checks apply
-> the selected service receives only what that request needs**. If the
credential is missing or rejected, Jarvis should keep the failure visible or
use a compatible connection that you already configured.

## Check That It Works

1. Open **API Keys & Providers > Brain**.
2. Confirm that the active provider card shows a saved, masked credential.
3. Select **Test** on that card.
4. Confirm that the result says **Works**.

This verifies that Jarvis can retrieve the credential, reach the selected
service, use the chosen model, and receive a response. A test can make a small
billable provider request.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Save failed** or **Keyring write failed** | Neither the operating-system store nor the local fallback accepted the value | Check that Jarvis can write to its data directory, unlock the operating-system credential store, and save again in the same protected field. |
| **Deletion could not be verified** | The credential store is locked, unavailable, or retained a copy | Unlock it and retry. Revoke the credential at the issuing service if access must stop immediately. |
| A removed credential still shows as configured | An operator-provided environment value or another external login still exists | Clear the original deployment or login source, restart Jarvis, and check again. |
| A saved provider says **Key invalid**, **Out of credits**, or **Rate limited** | Storage worked, but the provider rejected the account or request | Check the official provider account, replace or fund the credential, or activate a ready provider from another compatible family. |
| A plugin says **Reconnect**, an MCP server says credentials are incomplete, or a CLI fails validation | Access expired, required authentication is missing, or the external tool rejected it | Reconnect through that feature's protected flow. Do not copy the value into chat, logs, or `mcp.json`. |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) to connect, activate,
  and test the services that power chat, speech, and Jarvis-Agents.
- Review [Privacy and Local Data](privacy-and-local-data) to understand which
  requests stay on your device and which reach a connected service.
- Use [MCP Connections](mcp-connections) to add tool servers without placing
  literal credentials in their server definitions.
- Open [Troubleshooting](troubleshooting) when credential, account, network, or
  app health checks fail together.
