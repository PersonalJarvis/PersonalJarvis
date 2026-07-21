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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [control-key, authentication, unlock, browser, security, api-keys]
related: [credentials-and-secrets, control-api-reference, providers-and-api-keys, troubleshooting]
---

The **Control Key** is the administrator credential for one Personal Jarvis
installation. You use it to unlock a protected browser and to authenticate the
Jarvis CLI, trusted agents, and other Control API clients.

When no existing key or operator-provided seed is available, Jarvis creates a
random Control Key on first start. The key does not expire on a timer, and
there is no Jarvis account that can email or reset it. Replacing or regenerating
it is the way to revoke the old value.

> [!warning] The Control Key is the deliberate exception to normal secret
> visibility in Jarvis. The app can reveal and copy it so that you can recover
> access and connect trusted clients. Other stored credentials stay masked.

The desktop app does not ask you to enter the key. It exchanges a private,
one-use startup token for an app session instead.

## Before You Start

- Use the installed desktop app, an already authenticated browser, or a
  browser running directly on the Jarvis computer to manage the key.
- Use HTTPS for a browser on another device. A direct loopback connection on
  the Jarvis computer is the only plain HTTP exception.
- Put the key only into the Jarvis lock screen, the CLI's hidden prompt, or a
  trusted secret manager. Do not put it in chat, voice input, a URL, source
  code, screenshots, logs, or an inline shell argument.

## Understand the Browser Lock

**Ask for the key in the browser** is off by default. While it is off, a direct
browser connection on the Jarvis computer can open the interface without a
credential. Both the browser connection and the requested host must be
loopback, and the request must not contain a standard forwarding indicator.

Another device or a non-loopback address still has to authenticate. An HTTP
proxy also stays locked when it preserves the remote host or adds standard
forwarding headers. After you enter the Control Key once, Jarvis gives that
browser an HttpOnly session cookie. The browser sends the cookie on later
requests, not the Control Key.

Turn on **Ask for the key in the browser** if other people can use the same
computer. The confirmation includes **Copy** and **Require the key**. Jarvis
creates a session for the browser that enabled the lock, so that browser stays
signed in. Turning the switch off restores only the direct local-browser
bypass.

You cannot disable the Control Key itself. The focused `/api/control/*`
operations still require it as a Bearer credential, including from the same
computer. Jarvis also refuses to start a non-loopback listener when no Control
Key exists.

> [!warning] A headerless TCP relay on the Jarvis computer, such as a local SSH
> port forward or raw TCP tunnel, can look exactly like a direct local browser.
> Turn on **Ask for the key in the browser** before forwarding the Jarvis port.

## Find and Copy the Key

The key has a dedicated tab named after your assistant. For example, a wake
word named Nico produces the **Nico Key** tab. The neutral first-run label is
**Assistant Key**.

1. Open the installed Personal Jarvis app or an authenticated browser.
2. Open **API Keys & Providers**.
3. Select the tab named **<assistant name> Key**.
4. Find the **Control Key** card. The key is masked by default.
5. Select **Show** to reveal it, **Hide** to mask it again, or **Copy** to put
   it on the clipboard.

Clear the clipboard after you have stored or entered the key. Clipboard
history and synchronization can copy it to other devices.

## Unlock Jarvis in a Browser

The lock screen appears for a remote browser and for a local browser when
**Ask for the key in the browser** is on.

1. Open the configured Jarvis address. The page shows **Unlock Personal
   Jarvis**.
2. Enter the key in **Control Key** and select **Unlock**.
3. Wait for the Jarvis interface to load. The key field is cleared and the
   browser receives an HttpOnly, `SameSite=Strict` session cookie.

The exchange works only over HTTPS or a direct loopback connection. The key is
sent in the request body for this exchange. It is not stored in the session
cookie and is not accepted from a URL query parameter.

A browser session has no fixed time limit. It ends when the browser discards
its session cookie or when Jarvis restarts and clears its in-memory session
list. Replacing or regenerating the Control Key does not revoke a browser
session that is already signed in.

## Choose Your Own Key

1. In the **Control Key** card, select **Choose my own key**.
2. Enter the same value in **New Control Key** and **Repeat new Control Key**.
   Use 12 to 128 characters. Only letters, digits, `.`, `_`, `~`, and `-` are
   accepted; spaces are not accepted.
3. Select **Set this key**.

The new value becomes active immediately. The previous value can no longer
create a browser session or authenticate a Bearer request. Update every CLI,
device, and agent that stored the previous value. Existing browser sessions
remain signed in.

If neither the operating-system credential store nor the restricted file
fallback accepts the new value, Jarvis reports an error and keeps the previous
key active.

## Generate a New Random Key

Use regeneration when the current value may have appeared in a screenshot,
chat, log, or other untrusted place.

1. In the **Control Key** card, select **Generate random key**.
2. Review the warning and select **Generate new key**.
3. Select **Copy**, store the new value safely, and update every trusted client
   that used the previous value.

The previous key stops authenticating new requests immediately. Existing
browser sessions remain valid until their cookie is discarded or Jarvis
restarts. Regeneration therefore revokes the key, but it is not a sign-out
button for browsers that are already authenticated.

## Use the CLI or a Headless Server

On the same computer, the Jarvis CLI discovers the running instance and reads
the local Control Key through Jarvis's credential resolver. Check the
connection without printing the key:

```bash
jarvis --json auth status
```

Success includes `"reachable": true`. For a remote instance, save its address
and key through the hidden prompt:

```bash
jarvis auth login --url https://jarvis.example
```

Do not add the key after `--key` on the command line. An inline value can remain
in shell history. The CLI also accepts the key from standard input for managed
automation, but the process supplying it must keep both input and logs private.

A headless installation uses the same key lifecycle as the desktop app. Jarvis
tries the operating-system credential store first. If it is unavailable, such
as on a server without Secret Service, it uses a restricted `credentials.json`
file in the Jarvis data directory. Older or emergency fallback paths may also
have a `.control_api_key` file, but that file is not guaranteed to exist.

`GET /api/control/api-key` is the intentional clear-value reveal endpoint. It
accepts an authenticated app session or the current Bearer key. A direct
loopback caller can also use it while the local browser bypass is enabled. Keep
its response out of terminal recordings and request logs.

For API requests, send the key in exactly one `Authorization: Bearer` header.
Do not send it as a query parameter or ordinary cookie. A malformed
Authorization header is rejected instead of falling back to a valid browser
cookie.

## Recover Access

1. Return to the computer that runs Jarvis. There is no remote account-recovery
   service.
2. On a desktop installation, open the installed app. Its startup token can
   create a session without the Control Key. Then follow [Find and Copy the
   Key](#find-and-copy-the-key).
3. On the same desktop or headless host, try `jarvis --json auth status`. The
   local CLI resolves the stored key without requiring you to paste it.
4. If you have neither host access nor an authenticated browser session, the
   remote interface cannot reveal or reset the key. Regain operating-system
   access to the Jarvis host first.

On macOS, an upgrade from an older Python launcher may ask for the login
Keychain password once when the installed app first reads the existing item.
Approve that read and start Jarvis through **Personal Jarvis.app** so later
starts use the app's stable identity.

## How It Fits Together

| Feature | Relationship to the Control Key |
|---|---|
| [Credentials and Secrets](credentials-and-secrets) | Jarvis stores the key through the same portable credential system, but this is the one credential the interface may reveal and copy. |
| Browser lock | A locked browser exchanges the key once for an HttpOnly session cookie. Turning off the lock bypasses authentication only for direct local-browser access. |
| [Control API Reference](control-api-reference) | CLI clients and agents send the key as a Bearer credential. Most focused Control API routes do not accept a browser session instead. |
| [Providers and API Keys](providers-and-api-keys) | Provider credentials connect Jarvis to outside services. They cannot unlock or administer your Jarvis installation. |

## Check That It Works

1. Open **API Keys & Providers**, select **<assistant name> Key**, and use
   **Copy** on the **Control Key** card.
2. Open Jarvis in a private browser window. A remote browser should show the
   lock screen. A direct local browser shows it only when **Ask for the key in
   the browser** is on.
3. Enter the key and select **Unlock**. The interface loads without putting the
   key in the address bar.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| No lock screen appears on the Jarvis computer | The local browser bypass is on by default | Turn on **Ask for the key in the browser** if this is a shared computer |
| The lock screen rejects the key | The key was replaced, regenerated, or copied incorrectly | Use an existing desktop or browser session to copy the current value, then try again |
| A CLI or agent starts returning `401` | Its stored key is no longer current, or it did not send one Bearer header | Use the hidden `jarvis auth login` prompt for that target and retry once |
| **Setting the key failed** appears | Neither available credential store accepted the replacement | Check host storage access and retry; the previous key remains active |
| `.control_api_key` does not exist on a headless host | The current credential resolver is using the OS store or `credentials.json` instead | Use the same-host CLI or authenticated reveal endpoint; do not assume the compatibility file exists |

## Next Steps

- Read [Credentials and Secrets](credentials-and-secrets) to understand storage,
  masking, and safe credential replacement.
- Use the [Control API Reference](control-api-reference) to authenticate trusted
  scripts and agents without putting the key in a URL.
- Open [Troubleshooting](troubleshooting) when the host, browser, or CLI cannot
  reach the intended Jarvis instance.
