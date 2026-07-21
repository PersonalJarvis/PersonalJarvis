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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [mcp, connections, tools, integrations, safety]
related: [plugins, skills, credentials-and-secrets, safety-and-approvals]
---

Model Context Protocol (MCP) is a standard that lets an external server
describe tools Jarvis can call. After you connect a trusted server, its tools
can become available for relevant chat, voice, and Jarvis-Agent work.

The **MCPs** view manages custom servers. It is not a server catalog or a
general OAuth sign-in screen. If the service is available in **Plugins**, use
that guided connection instead.

## Before You Start

- Get the current configuration from the server publisher. Jarvis does not
  verify a package, command, or remote service for you.
- Choose a supported transport:

  | Transport | Configuration | What runs |
  |---|---|---|
  | **stdio** | `command` and optional `args` | Jarvis starts a local process with your operating-system account and environment |
  | **Streamable HTTP** | `transport: "http"` and `url` | Jarvis opens a session to a remote endpoint |

- Install the launcher required by a stdio server, such as Node.js for an
  `npx` command. The command must be available on the same computer that runs
  Jarvis.
- Decide which folders, accounts, and write actions the server may reach. Give
  it the smallest useful access.

The live registry does not reliably load the normal URL-only shape for the
older Server-Sent Events (SSE) transport. Use stdio or streamable HTTP for a
new connection.

> [!warning]
> Never put a password, token, API key, or authorization value directly in
> `mcp.json`, chat, voice input, a command, or a screenshot. The **MCPs** view
> has no general credential form for an arbitrary custom server.

## Add a Connection

### Add a Server Manually

1. Open **MCPs**, then select **mcp.json**. Jarvis opens the JSON editor.
2. Add the server under `mcpServers` with the exact command supplied by its
   publisher. Keep it disabled while you review it.

   This example declares a local stdio server without starting it:

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

3. Select **Save**. The server appears as **disconnected**. Saving the file
   reloads its definition, but does not start it.
4. Review the server name, executable, arguments, and access before enabling
   it.

For streamable HTTP, use `transport: "http"` with the publisher's `url`.
Headers can contain a reference such as `Bearer $SAVED_TOKEN_NAME`; Jarvis
resolves the reference from its credential sources when it connects. Do not
replace the reference with the secret itself.

Disable a running server before changing its command, URL, environment, or
headers. Saving `mcp.json` does not restart a connection that is already open.

### Import from Claude Desktop

Import currently works only on Windows. It reads command-based servers from
the standard Claude Desktop configuration for the same Windows account.
Remote URL-only entries are not imported.

1. Review the Claude Desktop file first. Import copies each new server's
   command, arguments, and environment entries. Do not import raw credentials.
2. If the MCP list is empty, select **Import Claude Desktop config**. If a
   server already appears, use `jarvis mcps import-claude-desktop` instead.
3. Existing Jarvis entries with the same name stay unchanged. Every imported
   server starts disabled.
4. Open **mcp.json** and review each imported entry before enabling it.

On macOS, Linux, or a Windows account without that file, add the server
manually.

## Connect and Inspect a Server

Turn on the switch in the server's row. Jarvis starts the command or opens the
HTTP session, completes the MCP handshake, and requests the tool list. It saves
`enabled: true` only after that connection succeeds. A failure leaves the
server disabled and shows **error**.

The command-line interface reaches the same running Jarvis service and is also
available on a headless host:

```bash
jarvis mcps list
jarvis mcps check trusted-local-server
jarvis mcps enable trusted-local-server
jarvis mcps disable trusted-local-server
jarvis mcps delete trusted-local-server --yes
```

`check` starts and stops a disabled server for one handshake and tool-count
probe without enabling it. If the server is already connected, it checks the
current tool list. `list` includes the cached tool names and descriptions for
connected servers. Add `--dry-run` to a mutating command to preview its request
without sending it.

Enabled servers start in the background when Jarvis launches. Enabling one
starts it immediately, so an app restart is not normally required.

## Disable or Remove a Server

Turn off the server's switch, or run `jarvis mcps disable <name>`. Jarvis
closes its current session, stops its local stdio process when applicable,
removes its tools from new assistant requests, and keeps the configuration for
later.

To remove a server completely, disable it first and then run
`jarvis mcps delete <name> --yes`. The explicit `--yes` is required because
deletion is destructive. The **MCPs** view has no delete button.

Removing an entry directly in the JSON editor can leave its previously loaded
row or tools visible for the rest of the session. If that happens, restart
Jarvis after saving. Disabling before removal avoids leaving an active process.

## Credentials and OAuth

Custom MCP entries can refer to credentials in two places:

- An `env` value that starts with `$`, such as `$SAVED_TOKEN_NAME`.
- A streamable-HTTP header containing the same kind of reference, such as
  `Bearer $SAVED_TOKEN_NAME`.

Jarvis resolves those references through its protected credential lookup. A
`required_auth` list only lets Jarvis report whether named credentials are
present. It does not save a secret or start a sign-in flow.

The **MCPs** view cannot perform OAuth for an arbitrary server, refresh its
grant, or safely onboard a new custom token. When a service appears in
**Plugins**, connect it there. A plugin can provide browser authorization,
protected credential storage, connection health, and MCP-backed tools without
creating a separate entry in **MCPs**.

## Understand Tool Access and Safety

| Boundary | What it controls | What you should know |
|---|---|---|
| Server configuration | Which program or URL Jarvis connects to | A local command runs with your user permissions |
| Service authorization | Which external data the server can read or change | This is separate from credentials for the Jarvis Brain |
| MCP tool list | The names, descriptions, and input fields advertised by the server | Jarvis exposes tools only after a successful connection and prefixes each name with its server |
| Jarvis safety | Whether a proposed call is recorded, allowed, or blocked | Blacklist and whitelist rules still apply, but the server's description does not set a separate tier for each tool |
| Service permissions | What the remote account ultimately accepts | Jarvis cannot grant more access than the account has or narrow an overly broad token by itself |

Custom MCP tools use Jarvis's configured default tool risk tier, which is
**monitor** in the standard configuration. Monitor-tier calls are recorded and
run without asking first. A blacklist can block a call, a whitelist can mark it
safe, and a configuration that assigns the **ask** tier requires confirmation.
Jarvis does not infer that a delete, send, or publish tool is consequential
from the server's description alone.

Treat the server's own account permissions as the main access boundary. Use a
read-only or narrowly scoped account whenever the service offers one.

A tool call has a time limit. After three consecutive call failures, Jarvis
pauses calls to that server for about one minute. That pause does not stop
other MCP servers or unrelated tools.

## How It Fits Together

A successful custom connection follows this flow:

1. You add or import a disabled server.
2. Enabling it makes Jarvis connect and request its current tool definitions.
3. Jarvis adds the connected tools to the running assistant without an app
   restart.
4. A chat or voice request can select a matching tool. An enabled
   [Skill](skills) can provide instructions that help Jarvis choose and use it.
5. Relevant enabled servers can also be offered to a [Jarvis-Agent](jarvis-agents)
   for a mission. Relevance filtering reduces unrelated choices, but you should
   still enable only servers you trust for delegated work.
6. The tool result returns to the conversation or mission. If that server is
   unavailable, the affected step fails while other connections keep running.

| Related feature | How it differs from a custom MCP connection |
|---|---|
| [Plugins](plugins) | Packaged service connections can provide guided OAuth or token setup, health reporting, tools, channels, and paired skills |
| [Skills](skills) | A skill gives Jarvis repeatable instructions but does not create service access or store credentials |
| [CLI Connections](cli-connections) | A CLI connection discovers one installed command-line program; an MCP server advertises a protocol-based tool collection |
| [Providers and API Keys](providers-and-api-keys) | Brain provider credentials authorize a model service, not an MCP service |
| [Safety and Approvals](safety-and-approvals) | Safety evaluates a proposed call after the server has advertised its tools |

## Check That It Works

1. Enable one trusted server and confirm that its row says **connected**.
2. Run `jarvis mcps list` and confirm that the server reports at least one tool
   with the expected name.
3. Ask Jarvis for one small, read-only action and name the server explicitly.
4. Confirm that the response contains the server's result rather than setup
   instructions or a connection error.
5. Disable the server after the test if you do not want it available to later
   conversations or Jarvis-Agent missions.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **JSON-Syntax** while saving | The editor content is not valid JSON | Check commas, straight quotes, and braces, then confirm that the root contains an `mcpServers` object |
| The switch returns to off or shows **error** | Jarvis could not start the command, reach the URL, authorize, complete the handshake, or list tools | Check the publisher's configuration, launcher installation, URL, saved credential reference, and service status |
| The error says `npx` or `node` is missing | The local server needs Node.js on the Jarvis host | Install Node.js 18 or newer, restart Jarvis so it sees the updated path, then check the server again |
| Import reports no new servers | The Windows Claude Desktop file is missing, unreadable, has no command-based entries, or every name already exists | Review that file or add the intended server manually |
| A credential is incomplete | `required_auth` names a credential Jarvis cannot find | Use a packaged plugin when available; do not paste the missing value into `mcp.json` |
| The server connects but Jarvis chooses another tool | The request does not clearly match the advertised names and descriptions | Name the server and request one concrete action; add a skill only when you need repeatable guidance |
| Calls fail immediately after several failures | The server is in its one-minute failure cooldown | Wait about a minute, check the server itself, then try one read-only action |
| A removed entry still appears | The JSON editor reloaded the file but retained a previously loaded runtime definition | Disable before removal and restart Jarvis if the stale entry remains |
| A URL-only SSE entry never appears | The current live registry does not load that legacy shape reliably | Use the publisher's streamable-HTTP endpoint, a stdio server, or a packaged plugin |

## Next Steps

- Read [Plugins](plugins) before adding a custom server for a supported service.
  The packaged connection provides a safer sign-in path.
- Read [Skills](skills) to give Jarvis repeatable instructions for an already
  connected tool.
- Review [Credentials and Secrets](credentials-and-secrets) to understand
  protected storage and safe removal.
- Review [Safety and Approvals](safety-and-approvals) before a server can send,
  delete, publish, or change external data.
