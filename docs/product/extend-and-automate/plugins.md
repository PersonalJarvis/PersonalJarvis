---
title: "Plugins"
slug: plugins
summary: "Connect packaged capabilities, understand sign-in and health states, and see how plugins can expose tools and skills."
section: "Extend and automate"
section_order: 5
order: 3
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [plugins, connections, marketplace, oauth, mcp, skills]
related: [skills, mcp-connections, providers-and-api-keys, credentials-and-secrets]
---

Plugins connect Jarvis to services you already use, such as project,
communication, and productivity tools. After you connect one, Jarvis can use
the capabilities that service makes available for a relevant request.

The catalog is packaged with Jarvis. Connecting a plugin authorizes a listed
service; it does not download an arbitrary extension from the internet. This
makes **Installed** a list of your connections, not a list of added programs.

## Understand the Plugin States

| What you see | What it means | What to expect |
|---|---|---|
| **Browse** | A service is available in the packaged catalog | You can search by name and filter by category |
| **Installed** | You connected the service before, or its connection needs attention | The plugin remains visible so you can reconnect or remove it |
| **Connected** | Jarvis has a saved access grant for the service | The connection is present, but every action tool may not be ready yet |
| **Live** | The plugin has a callable action tool in the current session | Jarvis can offer that tool when your request is relevant |
| **Reconnect needed** | The service rejected or revoked the saved grant | Run the sign-in flow again |
| **Error** | Jarvis could not read or use the connection normally | Reconnect first, then check the service if the error returns |

Communication plugins can act as message channels rather than action tools.
They may work without a **Live** badge, so test them by sending a message from
the account you allowed during setup.

## Before You Start

- Have an account for the service you want to connect. The service may charge
  for its own plan or restrict connections through an administrator.
- Decide what Jarvis should be allowed to read or change. Choose the smallest
  useful permission set on the service's consent or token page.
- Keep service credentials separate from Brain provider credentials. A Brain
  provider answers your requests; a plugin gives Jarvis access to another
  service.

> [!warning]
> Enter a service credential only in the plugin's protected connection dialog.
> Never paste it into chat, speak it, or include it in a screenshot.

## Connect a Plugin

1. Open **Plugins**, choose **Browse**, then search for the service by name or
   select a category. The card shows a short description and its connection
   method.
2. Select the **Connect plugin** button on the card. Jarvis opens the matching
   connection flow.
3. Complete the steps shown for that method:

   | Method | What you do |
   |---|---|
   | **Access Token** | Open the service's token page, create an appropriately scoped token, and paste it into the protected field. Jarvis checks it before saving it. |
   | **Browser Login** | Continue to your browser, review the requested access, and approve it. Some services require you to provide your own OAuth client in the pre-connect dialog. OAuth is the standard browser-based authorization flow. |
   | **One-Click** | Authorize Jarvis on the service's browser page, then return to the app while it finishes connecting. |
   | **Device Flow** | Copy the displayed code, open the service page, and enter the code before its timer expires. |
   | **Allowlist** | This connection method is not available yet. The app reports that its required cloud proxy is not deployed. |

4. If a browser does not open, use **Copy** in the dialog and open the link in
   your browser yourself. Keep the dialog open while Jarvis waits for the
   service.
5. Return to **Plugins** and look for **Connected**. Action plugins also show
   **Live** when their tools are ready in the current session.

For a channel plugin, use the recommended owner lock when the dialog offers
it. Enter your numeric service user ID so the bot accepts commands only from
that account. Leaving it blank can allow the first person who messages the bot
to claim access.

### Review Access Before Approving

The plugin card gives a description and connection type, but it does not
currently list every requested scope or operation. The service's consent page
or token settings are the authoritative permission review.

Cancel the flow if the requested access is broader than you expect. When a
service offers read-only or repository-, workspace-, or folder-specific
access, prefer that narrower option unless your task genuinely needs writes.

## Manage a Connection

### Reconnect

Use the amber **Reconnect plugin** button when a card says **Reconnect needed**
or **Error**. Jarvis tries the same sign-in method again and replaces the old
access grant only after the new one succeeds. Refreshable browser connections
are renewed in the background when the service allows it; a revoked grant
still requires you to reconnect.

### Refresh and update

The **Refresh catalog** button reloads the visible catalog and connection
states. It does not install an update.

Plugin definitions and built-in connection support arrive with Jarvis updates.
There is currently no per-plugin **Install**, **Update**, or **Disable** action.
To stop access, remove the connection; to receive a newer packaged connector,
update Jarvis.

### Remove

Select the check-marked connection button, review **Remove _service_?**, then
choose **Remove**. Jarvis deletes that plugin's local access grant and removes
its tools from new requests. A shared OAuth client that you entered separately
can remain available for other plugins in the same service family.

Removing a plugin from Jarvis does not necessarily revoke the authorization
record held by the service. For complete revocation, also open the service's
account security or connected-app settings and revoke Jarvis or the token
there.

## How It Fits Together

| Related feature | Relationship to a plugin |
|---|---|
| **Skills** | A paired skill teaches Jarvis when and how to use a plugin. It does not contain the credential or create the connection. |
| **MCP connections** | Many plugins use Model Context Protocol (MCP) behind the scenes to expose service tools. Manually added MCP servers are managed separately, and some plugins use a native tool or a message channel instead. |
| **API Keys** | Brain provider keys choose the service that reasons about your request. Plugin sign-in authorizes the external service on which Jarvis may act. |
| **Permissions and safety** | The service's scopes set the outer access boundary. Jarvis then applies its own safety tier, audit, and approval rules to each tool call. Some actions are monitored, while consequential actions may ask or be blocked. |
| **Jarvis-Agents** | For longer work, a Jarvis-Agent can receive only the connected tools relevant to that mission through a short-lived grant. The worker does not receive the stored credential itself. |

A typical request follows this path:

1. You name a service or ask for work that clearly matches it.
2. The paired skill helps Jarvis recognize the right capability.
3. Jarvis offers only relevant live tools and applies the safety rules for the
   proposed action.
4. The plugin sends the approved request to the service and returns the result
   to the conversation or Jarvis-Agent mission.

If one plugin is unavailable, Jarvis keeps other connected capabilities
running. The step that needs the unavailable service can still fail or require
you to reconnect; Jarvis does not silently substitute an unrelated service.

## Check That It Works

1. Connect an action plugin and confirm that its card shows **Connected** and
   **Live**.
2. Ask for a small read-only result and name the service, such as asking Jarvis
   to list a few recent items from it.
3. Confirm that Jarvis returns service data rather than a setup prompt. If an
   approval appears, review the proposed action before allowing it.

For a communication channel, send a simple test message from the allowed
account instead. A reply confirms the channel even when its card has no
**Live** badge.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Backend unreachable** or no catalog cards | The desktop interface cannot reach the local Jarvis service | Wait for Jarvis to finish starting, then choose **Refresh catalog**. Restart the app only if other views are also unavailable. |
| No browser page opens | The desktop shell or browser blocked the automatic open | Use **Copy** in the connection dialog, paste the link into your browser, and leave the dialog open. |
| An OAuth client is not configured | That service needs a client registered to your own account | Reopen the plugin, expand **Use your own OAuth client**, and enter the values created in the service's developer console. Do not put them in chat. |
| **Connected** appears without **Live** on an action plugin | The grant was saved, but no callable tool loaded | Choose **Refresh catalog**. If it remains unchanged, reconnect and check the service's status and permissions. A channel plugin is the exception; test it with a message. |
| **Reconnect needed** keeps returning | The service revoked the grant, the consent app is still temporary, or an administrator blocked it | Reauthorize with the intended account and review the provider's connected-app or administrator settings. |

## Next Steps

- Read [Skills](skills) to understand the instructions that help Jarvis choose
  a connected capability at the right time.
- Use [MCP Connections](mcp-connections) when you need to add and inspect a
  server that is not packaged as a plugin card.
- Compare [Providers and API Keys](providers-and-api-keys) to keep reasoning
  providers separate from service connections.
- Review [Credentials and Secrets](credentials-and-secrets) to learn where
  Jarvis stores sensitive values and how to remove them safely.
