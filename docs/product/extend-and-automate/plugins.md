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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [plugins, connections, marketplace, oauth, mcp, skills]
related: [skills, mcp-connections, providers-and-api-keys, credentials-and-secrets]
---

Plugins connect Jarvis to services you already use, such as project,
communication, and productivity tools. After you connect one, Jarvis can use
the capabilities that service makes available for a relevant request.

This page covers the service cards in the **Plugins** view. Jarvis also uses
packaged entry points for components such as speech, Brain providers, channels,
and tools. Those components are installed with Jarvis and are not managed from
this view.

The service catalog is packaged with Jarvis. Connecting a card saves access to
the listed service; it does not download an extension from the internet. This
makes **Installed** a list of saved connections, not a list of added programs.

## Browse the Current Catalog

The packaged catalog currently contains 14 service cards in three filter
categories:

| Category | Services |
|---|---|
| **Developer** | GitHub, Vercel, Supabase, Linear, Stripe, and Cloudflare |
| **Productivity** | Notion, Asana, Google Drive, and Google Calendar |
| **Communication** | Slack, Discord, Telegram, and Gmail |

Categories only organize the list. Each service has its own account,
permissions, connection method, and availability requirements.

## Understand the Plugin States

| What you see | What it means | What to expect |
|---|---|---|
| **Browse** | A service is available in the packaged catalog | You can search by name and filter by category |
| **Installed** | A saved connection exists or its status needs attention | The card remains visible so you can reconnect or remove it |
| **Connected** | A saved access grant is not marked for reauthorization | The credential is present, but an action tool may still be unavailable |
| **Live** | The plugin has a callable action tool in the current session | Jarvis can offer that tool when your request is relevant |
| **Reconnect needed** | The service rejected or revoked the saved grant | Run the connection flow again |
| **Error** | Jarvis could not read the saved connection normally | Reconnect first, then check the service if the error returns |

Telegram and Discord act as message channels rather than action tools. They
can work without a **Live** badge, so test them by sending a message from the
account you allowed during setup.

## Before You Start

- Have an account for the service you want to connect. The service may charge
  for its own plan or restrict connections through an administrator.
- Decide what Jarvis should be allowed to read or change. Choose the smallest
  useful permission set on the service's consent or token page.
- Keep service credentials separate from Brain provider credentials. A Brain
  provider answers your requests; a plugin gives Jarvis access to another
  service.
- Google Drive, Gmail, Google Calendar, Slack, and Asana need an OAuth client.
  If one is not already saved, create it in that service's developer console
  and enter it in the connection dialog.

> [!warning]
> Enter a service credential only in the plugin's protected connection dialog.
> Never paste it into chat, speak it, or include it in a screenshot.

## Connect a Plugin

1. Open **Plugins**, choose **Browse**, then search for the service by name or
   select a category. The card shows its connection method.
2. Select **Connect plugin** on the card. Jarvis opens the matching connection
   flow.
3. Complete the steps shown for that method. The current packaged catalog uses
   these methods:

   | Method | Current services | What you do |
   |---|---|---|
   | **Access Token** | GitHub, Vercel, Supabase, Stripe, Discord, and Telegram | Create a suitably restricted token on the service's token page and enter it in the protected field. Jarvis validates it before saving it. |
   | **One-Click** | Notion, Linear, and Cloudflare | Authorize Jarvis on the service's browser page, then return to the app while it finishes connecting. |
   | **Browser Login** | Slack, Asana, Google Drive, Gmail, and Google Calendar | Enter your OAuth client in the pre-connect dialog when needed, continue in your browser, and approve the requested access. OAuth is the standard browser-based authorization flow. |

   Jarvis also understands **Device Flow** and **Allowlist** catalog records,
   but no card in the current packaged catalog uses either method. Allowlist
   connections are unavailable until their required hosted proxy exists.
4. If a browser does not open, use **Copy** in the dialog and open the link in
   your browser. Keep the dialog open while Jarvis waits for the service.
5. Return to **Plugins** and look for **Connected**. Action plugins also show
   **Live** when their tools are ready. Jarvis normally adds the tools to the
   running session without an app restart.

For a channel plugin, use the recommended owner lock when the dialog offers
it. Enter your numeric service user ID so the bot accepts commands only from
that account. Leaving it blank can allow the first person who messages the bot
to claim access.

### Review Access Before Approving

The plugin card gives a description and connection type, but it does not list
every requested scope or operation. The service's consent page or token
settings are the authoritative permission review.

Cancel the flow if the requested access is broader than you expect. When a
service offers read-only or repository-, workspace-, or folder-specific
access, prefer that narrower option unless your task needs writes.

## Manage a Connection

### Reconnect or change settings

Use **Reconnect plugin** when a card says **Reconnect needed** or **Error**.
Jarvis runs the same connection method again. Refreshable browser connections
are renewed in the background when the service allows it; a revoked grant still
requires you to reconnect.

There is no separate **Configure** action. Reconnect the card to replace its
token, OAuth client, account, or channel owner lock. Google Drive, Gmail, and
Google Calendar share one saved Google OAuth client, so changing it can affect
all three connections.

### Refresh, update, and restart

**Refresh catalog** asks the backend for the catalog and connection states
again. It does not repair a connection, reload a tool, or install an update.

Plugin definitions and built-in connection support arrive with Jarvis updates.
There is no per-plugin **Install**, **Enable**, **Disable**, **Update**,
**Configure**, or **Test** action. Connecting grants access and makes a tool or
channel available. Removing the connection stops that access. Update Jarvis to
receive a newer packaged connector.

Saved connections survive an app restart. Action tools are refreshed in the
running session after a connect or removal. Telegram and Discord also try to
reload immediately, but a saved channel change can require the next app start
if the live channel manager or an optional channel dependency is unavailable.

### Remove

Select the check-marked connection button, review **Remove _service_?**, then
choose **Remove**. Jarvis deletes that plugin's local access grant and removes
its tools from new requests. A shared OAuth client that you entered separately
can remain available for other plugins in the same service family.

Removing a plugin from Jarvis does not necessarily revoke the authorization
record held by the service. For complete revocation, also open the service's
account security or connected-app settings and revoke Jarvis or the token
there.

### Use the CLI

Marketplace routes also have `jarvis marketplace` commands. The command-line
interface can list connections, start and poll browser authorization, and
remove a connection. Run `jarvis marketplace --help` to see the current
commands. Use the protected **Plugins** dialog for service credentials.

## Platform and Provider Support

The packaged service catalog and its HTTP-based action tools run on Windows,
macOS, and Linux. The current GitHub and Supabase cards use hosted HTTP servers,
so they do not require Docker or Node.js. Desktop browser authorization uses a
local callback and works when the browser and Jarvis run on the same computer.
Google Calendar is the exception: its action tool needs Node.js 18 or newer.
Discord needs the channel dependency included in the standard full install.

The catalog and marketplace routes also load on a headless Linux server.
Browser authorization on a remote server needs an operator-configured public
HTTPS callback. The **Plugins** view does not currently provide a setting for
that callback. Without it, use a desktop instance for the browser connection
or an access-token connection where the service offers one.

A plugin connection is separate from the selected Brain provider. Calling an
action still needs a Brain path that supports tools, a reachable service, and
the permissions granted by that service. Jarvis does not treat one service as
a fallback for another.

## How It Fits Together

| Related feature | Relationship to a plugin |
|---|---|
| **Skills** | Most action plugins have a paired skill that teaches Jarvis when and how to use the service. It does not contain the credential or create the connection. A message channel such as Telegram can work without an action-tool skill. |
| **MCP connections** | Many plugins use Model Context Protocol (MCP) to expose service tools. Manually added MCP servers are managed separately, and some plugins use a native tool or a message channel instead. |
| **API Keys** | Brain provider keys choose the service that reasons about your request. Plugin sign-in authorizes the external service on which Jarvis may act. |
| **Permissions and safety** | The service's scopes set the outer access boundary. Marketplace MCP tools currently use the **monitor** tier, which records the action without asking first. Native plugin tools can use a stricter tier, and blacklist rules can block an action. A connection badge does not mean every call will ask for approval. |
| **Jarvis-Agents** | For longer work, a Jarvis-Agent can receive only the connected tools relevant to that mission through a short-lived grant. The worker does not receive the stored credential itself. |

A typical action request follows this path:

1. You name a service or ask for work that clearly matches it.
2. Its paired skill and live tool definitions help Jarvis recognize the right
   capability.
3. Jarvis offers relevant live tools and applies the safety rules for the
   proposed action.
4. The plugin sends the allowed request to the service and returns the result
   to the conversation or Jarvis-Agent mission.

If one plugin is unavailable, Jarvis keeps other connected capabilities
running. The step that needs the unavailable service can still fail or require
you to reconnect; Jarvis does not silently substitute an unrelated service.

## Check That It Works

There is no generic **Test** button, so use one small observable request:

1. Connect an action plugin and confirm that its card shows **Connected** and
   **Live**.
2. Ask for a small read-only result and name the service, such as asking Jarvis
   to list a few recent items from it.
3. Confirm that Jarvis returns service data rather than a setup prompt. If an
   approval appears, review the proposed action before allowing it.

For Telegram or Discord, send a simple test message from the allowed account.
A reply confirms the channel even though its card has no **Live** badge.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Backend unreachable** or no catalog cards | The interface cannot reach the local Jarvis service | Wait for Jarvis to finish starting, then choose **Refresh catalog**. Restart the app only if other views are also unavailable. |
| No browser page opens | The desktop shell or browser blocked the automatic open | Use **Copy** in the connection dialog, open the link in your browser, and leave the dialog open. |
| An OAuth client is not configured | That service needs a client registered to your own account | Reopen the plugin, expand **Use your own OAuth client**, and enter the values created in the service's developer console. Do not put them in chat. |
| **Connected** appears without **Live** on an action plugin | The grant was saved, but no callable tool loaded | Wait for startup to finish, then choose **Refresh catalog** to reread the status. If it remains unchanged, reconnect and check the service's status and permissions. |
| **Reconnect needed** keeps returning | The service revoked the grant, the consent app is still temporary, or an administrator blocked it | Reauthorize with the intended account and review the provider's connected-app or administrator settings. |
| A channel is connected but does not reply | Its live reload failed, its runtime dependency is missing, or the sender is not allowed | Confirm the standard full install is present, send from the owner account, and restart Jarvis once. Reconnect if it still does not reply. |
| Browser authorization on a remote server never completes | The provider cannot reach a callback on the Jarvis server | Use a desktop instance for the connection, or ask the server operator to configure a public HTTPS callback. |

## Next Steps

- Read [Skills](skills) to understand the instructions that help Jarvis choose
  a connected capability at the right time.
- Use [MCP Connections](mcp-connections) when you need to add and inspect a
  server that is not packaged as a plugin card.
- Compare [Providers and API Keys](providers-and-api-keys) to keep reasoning
  providers separate from service connections.
- Review [Credentials and Secrets](credentials-and-secrets) to learn where
  Jarvis stores sensitive values and how to remove them safely.
