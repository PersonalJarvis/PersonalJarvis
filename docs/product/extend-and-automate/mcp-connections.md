---
title: "MCP Connections"
slug: mcp-connections
summary: "Add and inspect Model Context Protocol servers, then understand how their tools become available safely."
section: "Extend and automate"
section_order: 5
order: 4
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [mcp, connections, tools, integrations, safety]
related: [plugins, skills, credentials-and-secrets, safety-and-approvals]
---

Model Context Protocol (MCP) is a standard that lets an external tool server
describe the actions it offers. Connect a trusted server and Jarvis can make
those actions available for relevant chat, voice, and delegated work.

An MCP connection is powerful: a local server runs software on your computer,
while a remote server can receive the information sent with a tool request.
Connect only servers you trust and give each one the smallest useful access.

## Before You Start

- Get the server configuration from its publisher's current documentation.
  Jarvis does not ship a built-in MCP catalog or automatically verify a server
  package for you.
- Check whether the server uses **stdio**, which starts a local command, or
  **streamable HTTP**, which connects to a remote URL. Install the required
  launcher for a local server before connecting it.
- Decide which folders, accounts, and write actions the server should reach.
  A local server runs with your operating-system account and inherits the
  environment available to Jarvis.
- Prefer a [Plugin](plugins) when the same service appears in **Plugins**. A
  plugin provides a guided sign-in flow and protected credential storage.

> [!warning]
> Never put a password, token, API key, or authorization value directly in
> `mcp.json`, chat, voice input, or a screenshot. The MCP view has no general
> credential form for arbitrary custom servers today.

## Add a Connection

### Add a Server Manually

1. Open **MCPs**, then select **Open mcp.json**. Jarvis opens its built-in JSON
   editor.
2. Add the server under `mcpServers` using the exact command or URL supplied by
   the publisher. Keep `enabled` set to `false` while you review it.

   This minimal shape starts a local server; replace the sample package name
   with the publisher's real package name, not a credential:

   ```json
   {
     "mcpServers": {
       "trusted-local-server": {
         "command": "npx",
         "args": ["-y", "publisher-package-name"],
         "enabled": false
       }
     }
   }
   ```

3. Select **Save**. The server appears in the MCP list as disconnected; saving
   the file does not grant access or start it.
4. Review the server name and configuration one more time, then turn on its
   switch. Jarvis starts the local command or opens the remote connection,
   completes the MCP handshake, and asks the server for its tool list.
5. Look for **connected**. Jarvis saves the enabled state only after the
   connection succeeds. If the check fails, the server stays disabled and the
   status changes to **error**.

For a remote server, use the publisher's streamable-HTTP configuration with a
`url` and `transport` set to `http`. Add authorization headers only when they
refer to a credential that is already stored safely. If the service needs a
new token and is not available as a plugin, the current MCP screen cannot
complete that credential setup safely; do not work around this by storing the
token in the JSON file.

### Import from Claude Desktop

On Windows, **Import Claude Desktop config** reads the MCP servers from the
standard Claude Desktop configuration on the same account.

1. Review the Claude Desktop configuration first. Import copies each new
   server, including its environment entries, so do not import a file that
   contains raw credentials.
2. Select **Import Claude Desktop config**. Existing Jarvis entries with the
   same name are left unchanged.
3. Review each imported entry in **Open mcp.json**. Imported servers start
   disabled.
4. Turn on one server at a time and confirm that it reaches **connected**.

The import button currently looks only in the Windows Claude Desktop location.
On macOS, Linux, or a computer without that file, add the server manually.

## Understand Access and Tool Exposure

| Boundary | What it controls | What you should know |
|---|---|---|
| Server configuration | Which program or URL Jarvis connects to | Enabling a local server allows its process to run with your user permissions |
| Service authorization | Which external account data the server can read or change | This is separate from the keys that power Jarvis's Brain |
| MCP tool list | The names, descriptions, and input fields advertised by the server | Jarvis adds tools only after a successful connection and keeps names separated by server |
| Jarvis safety | Whether a proposed call is logged, confirmed, allowed, or blocked | Blacklists and other safety checks still apply, but confirmation is not guaranteed for every MCP tool |
| Server-side permissions | What the service ultimately accepts | Jarvis cannot grant more access than the service account has, and it cannot narrow an overly broad server token by itself |

Connected MCP tools currently share Jarvis's configured default tool risk tier,
normally **monitor**. Jarvis records monitored calls and can require approval
when its safety or plausibility rules say so, but it does not infer a separate
risk tier from every server-provided tool description. Treat the server's own
permissions as the primary access boundary, especially for delete, send, and
publish actions.

Turning a server off stops its current connection, removes its tools from new
assistant requests, and keeps its configuration for later. A failed server does
not stop other connections. A tool call that does not respond is timed out;
repeated failures temporarily pause further calls to that server.

## How It Fits Together

A successful connection follows this flow:

1. You add or import a server in a disabled state.
2. Turning it on makes Jarvis connect and request the server's current tool
   definitions.
3. Jarvis adds those tools to the live assistant without requiring an app
   restart.
4. A chat, voice request, or [Skill](skills) can select a matching tool. The
   request then passes through Jarvis's normal safety flow before the tool runs.
5. For longer work, a relevant enabled server can also be made available to a
   [Jarvis-Agent](jarvis-agents). Relevance selection reduces noise, but it is
   not an access-control boundary; enable only servers you are willing to make
   available to delegated work.
6. The server returns a result to the conversation or mission. If it is down,
   the affected step fails honestly while unrelated tools remain available.

| Related feature | How it differs from MCP |
|---|---|
| [Plugins](plugins) | Packaged service connections can provide OAuth sign-in, tools, channels, and paired skills; use them when available |
| [Skills](skills) | A skill gives Jarvis repeatable instructions but does not create service access or store a credential |
| [CLI Connections](cli-connections) | A CLI connection discovers an installed command-line program; an MCP server advertises a protocol-based collection of tools |
| [Providers and API Keys](providers-and-api-keys) | Brain provider keys authorize an external model service when one is used; they do not authorize an MCP service |
| [Jarvis-Agents](jarvis-agents) | A Jarvis-Agent handles longer work and may receive relevant enabled connections for that mission |
| [Safety and Approvals](safety-and-approvals) | Safety evaluates the requested action after the server has defined what tools exist |

## Check That It Works

1. Connect one trusted server and confirm that its row says **connected**.
2. Ask Jarvis for one small, read-only action and name the server explicitly.
3. Confirm that the answer contains the server's result rather than setup
   instructions or a connection error. Review any approval request before
   continuing.
4. Turn the server off after the test if you do not want it available for
   future conversations or Jarvis-Agent missions.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **JSON-Syntax** when saving | The editor content is not valid JSON | Check commas, quotes, and braces, then confirm the root contains `mcpServers` |
| The switch returns to off or shows **error** | Jarvis could not start the command, reach the URL, complete authorization, or list tools | Verify the publisher's command, launcher installation, URL, authorization, and service status |
| Import reports no new servers | The Windows Claude Desktop file is missing, empty, unreadable, or every name already exists | Add the server manually, or rename and review the intended entry before importing again |
| A credential is reported as incomplete | The server declares an authorization name that Jarvis cannot find | Use the packaged plugin instead when available; do not paste the missing value into `mcp.json` |
| The server is connected but Jarvis chooses another tool | The request does not clearly match the server's advertised names and descriptions | Name the server and ask for one concrete action; add a skill only when you need repeatable guidance |
| Calls fail immediately after several timeouts | The server has been paused briefly after repeated failures | Wait about a minute, check the server itself, then try one read-only action again |
| A removed JSON entry still appears | The live registry may retain a previously loaded definition until restart | Disable the server first, save the removal, then restart Jarvis if the row remains |
| A URL-only SSE entry never appears | That legacy transport shape is not loaded reliably by the current editor path | Use the publisher's streamable-HTTP endpoint, a local stdio server, or a packaged plugin |

## Next Steps

- Read [Plugins](plugins) before adding a custom server for a supported service;
  the guided connection usually gives you safer sign-in and clearer health.
- Read [Skills](skills) to teach Jarvis a repeatable sequence that uses an
  already connected MCP tool.
- Review [Credentials and Secrets](credentials-and-secrets) to understand safe
  storage, removal, and the difference between a reference and a secret value.
- Review [Safety and Approvals](safety-and-approvals) before allowing a server
  to send, delete, publish, or change external data.
