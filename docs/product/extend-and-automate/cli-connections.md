---
title: "CLI Connections"
slug: cli-connections
summary: "Let Jarvis discover command-line tools, check their readiness, and use only the capabilities available on the current computer."
section: "Extend and automate"
section_order: 5
order: 5
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [cli, connections, terminal, tools, automation, safety]
related: [skills, workflows-and-commands, cli-reference, safety-and-approvals]
---

A command-line interface (CLI) connection lets Jarvis use a trusted terminal
program that is installed on the same computer. Jarvis finds the program,
checks whether its account is ready, and exposes it as a tool for matching chat,
voice, and test requests.

This is different from using the **Jarvis CLI** yourself. A CLI connection lets
the assistant invoke a cataloged command-line program as a tool. The Jarvis CLI
controls Personal Jarvis itself; its commands are listed in the
[CLI Reference](cli-reference).

The packaged catalog currently contains these tools:

| Category | Command-line tools |
|---|---|
| Cloud | Google Cloud CLI, Azure CLI, AWS CLI v2, Cloudflare Wrangler |
| App hosting | Vercel CLI, Netlify CLI, Heroku CLI, Railway CLI, Fly.io CLI, Render CLI |
| Backend services | Supabase CLI, Firebase CLI, PlanetScale CLI, Neon CLI |
| Source control | GitHub CLI, GitLab CLI |
| Payments | Stripe CLI, Twilio CLI |
| Containers | Docker CLI, Kubernetes CLI (`kubectl`) |
| Workspace | Google Workspace CLI (GAM) |
| Personal Jarvis | Jarvis Control CLI |

Codex, Claude, Antigravity, and the Gemini CLI are not entries in this
catalog. Their supported subscription sign-ins power background Jarvis-Agent
work instead of becoming general command tools. Open **Settings > API Keys**,
then open the Agents tab named after your wake word to connect or test them.
Codex uses a ChatGPT sign-in, Claude uses a Claude subscription sign-in, and
Antigravity can use either the `agy` or Gemini CLI for Google sign-in. These
subscription paths do not become the main Brain. Read
[Providers and API Keys](providers-and-api-keys) for the API-key alternatives
and current billing choices.

## Before You Start

- Use a program and account that you trust. The connected program runs with
  your operating-system account and can reach whatever its own sign-in permits.
- Install any package manager required by the selected installation method.
  Jarvis shows the command, but it does not verify that the package manager is
  present before opening the terminal.
- Configure a Brain provider that supports tool use before testing a natural-
  language instruction. You can still browse and recheck the catalog without
  one.
- Keep the third-party service's permission set as narrow as your work allows.

> [!warning]
> Enter a credential only in the protected connection dialog. Never put it in
> chat, voice input, the CLI Test Hub instruction, a custom command, or a
> screenshot. Full commands appear in local usage history.

The current **Install in terminal** and **Browser Login** actions open Windows
Terminal or PowerShell on the computer running Jarvis. On macOS, Linux, or a
headless server, install and sign in with the program's official terminal
instructions, then let Jarvis recheck it. Discovery and non-interactive use can
work on those systems when the executable is on the process path.

## Connect a Command-Line Tool

The shortest path is:

| Stage | What you do | Visible result |
|---|---|---|
| Discover | Open **CLIs** | The catalog shows what this computer has installed |
| Install | Choose a supported method when needed | The program finishes in an external terminal |
| Sign in | Complete browser login or use the protected key form | The program has access to the selected account |
| Recheck | Use **Recheck status** | The row changes to **Connected** when both checks pass |
| Use | Run a read-only request | Jarvis shows the chosen command and its result |

### Find and Inspect a Tool

1. Open **CLIs** from the sidebar, then choose the **CLIs** tab. Jarvis lists
   the packaged catalog plus any custom entries saved on this computer.
2. Use **All**, **Connected**, **Installed**, **Custom**, or a category filter
   to narrow the list. The counts describe the current catalog, not programs
   available on another computer.
3. Select a row to open its detail panel. Review the executable path, version,
   sign-in method, status check, default safety tier, and official
   documentation link before connecting it.

The status labels describe two separate checks:

| Status | Meaning |
|---|---|
| **Checking** | Jarvis has not finished its local check |
| **Not installed** | The expected executable was not found on the process path |
| **Disconnected** | The executable was found, but its sign-in check did not pass |
| **Connected** | The program is installed and considered ready for Jarvis to use |
| **Error** | The check could not complete normally; open the row for the reported cause |

The header's reload button fetches the latest cached list. To inspect the
computer again after an install or sign-in change, select the tool and use
**Recheck status**.

### Install the Program

1. Select a row marked **Not installed**, then choose **Install**. If no
   install button appears, use the linked official documentation.
2. Review the selected package manager and the exact command. The current
   dialog preselects the first available method, so choose another one when
   that better matches your computer.
3. Choose **Install in terminal**. For a manual method, Jarvis opens the
   publisher's installation page instead.
4. Finish the installation in the external terminal, then return to **CLIs**
   and choose **Recheck status**. A successful check shows a version when the
   program reports one.

Installing through this screen changes software on the host computer. It does
not yet authorize an external account, and removing a custom catalog entry
later does not uninstall the program.

### Complete the Matching Sign-In

The detail panel offers only the sign-in action declared for that program.

| Sign-in type | What happens |
|---|---|
| **Browser Login** | Jarvis opens the program's login command in an external Windows terminal. Complete the browser or device flow and leave Jarvis running while it checks in the background for up to about five minutes. |
| **API key** | **Set API Key** opens password fields. **Save and validate** tests the values, then stores them through Jarvis's protected credential storage when the check succeeds. |
| **Existing configuration** | The program reads its own configuration file. No Jarvis sign-in button appears, and installation alone may show **Connected**, so verify access with a read-only request. |
| **No sign-in** | Finding the installed executable is enough for the row to become **Connected**. |

OAuth-style browser login and existing configuration remain owned by the
third-party program. Jarvis stores an API key entered through the protected
form and passes it to that program as an environment value when it runs.
**Disconnect** removes the local Jarvis credential or runs the program's logout
command when one is defined; revoke access in the service's account settings as
well when you need complete removal.

### Test and Use the Connection

1. Choose **Recheck status** and confirm that the row says **Connected**.
   Jarvis adds a ready tool to the live assistant without an app restart.
2. Open **CLI Test Hub**. Type one small, read-only instruction. You can let
   Jarvis choose a connected tool or select a CLI hint to narrow the choice.
3. Choose **Run**, or press Control+Enter on Windows and Linux or
   Command+Enter on macOS. Review the selected tool, exact command, safety
   tier, exit code, program output, error output, duration, and Jarvis summary.
4. Ask for the same kind of result in chat or by voice. Naming the service and
   the read-only outcome you want helps Jarvis choose the intended tool.
5. Select **History** on the CLI row to review calls, success rate, duration,
   caller, and short error previews. **Clear** deletes that tool's local usage
   history after confirmation.

An exit code of `0` means the program reported success. Other values mean the
program rejected or could not complete the command; read its error output
before retrying. Jarvis does not keep full command output in usage history,
but it does retain the full command, output lengths, and an error preview with
recognized secret patterns redacted. Test Hub output is also bounded: normal
commands return up to 4,000 characters of standard output, help commands return
up to 16,000, and error output is limited to 2,000 characters. Do not pass
credentials as command arguments.

CLI execution is non-interactive. A command that waits for a password,
confirmation prompt, or other terminal input fails or times out. Complete
interactive setup in a real terminal first, then use non-interactive commands
through Jarvis.

### Add a Trusted Custom CLI

Use **Add Custom** only when the program is not in the packaged catalog and
you understand its commands and authorization model.

1. In step 1, enter a stable lowercase ID, display name, executable name,
   description, category, and official homepage.
2. In step 2, enter a harmless check such as the program's version command and
   a version-matching pattern. The executable name must match the first word of
   every command Jarvis will run.
3. In step 3, choose the real sign-in type and provide only command names and
   credential-slot names. Enter credential values later in the protected
   connection dialog.
4. In step 4, choose the default safety tier. Add block patterns for commands
   Jarvis must never run. Add allow patterns only for narrow, well-understood
   read-only commands because a matching allow pattern can make a call safe.
5. Save the entry, select it in the **Custom** filter, and use **Recheck
   status**. If you later remove a connected custom entry, use **Disconnect**
   first when that action is available, remove the entry with its **Remove
   custom CLI** button, and restart Jarvis so the running assistant drops any
   tool it already loaded.

A block pattern takes priority over an allow pattern; an allow pattern takes
priority over the default tier. A custom definition is local metadata, not a
review or endorsement of the program. Removing it hides the catalog definition,
but does not uninstall the binary, revoke the external account, erase the
program's own files, or clear its usage history.

## How It Fits Together

A normal request follows this path:

1. Jarvis probes the current computer and exposes only CLIs considered usable.
2. Your chat, voice, or Test Hub instruction is matched to a connected tool.
3. Jarvis chooses a command, then evaluates its catalog rules and the global
   safety policy.
4. The approved command runs as a local, non-interactive subprocess. Its
   credential stays in Jarvis or the third-party program rather than entering
   the conversation.
5. Jarvis summarizes the real result and records a local usage entry.

| Related feature | Relationship to a CLI connection |
|---|---|
| [Skills](skills) | A skill can teach Jarvis a repeatable way to use a connected CLI; it does not install or authorize the program. |
| [Workflows and Commands](workflows-and-commands) | A Jarvis command is one stable app operation. A CLI connection exposes an external program whose subcommand is chosen for the request. |
| [Plugins](plugins) | A plugin is a packaged service connection that may not need a local executable. When both cover the same live-data domain, Jarvis generally prefers the connected local CLI. If that CLI command fails, Jarvis reports the failure instead of silently changing services. |
| [MCP Connections](mcp-connections) | A Model Context Protocol server advertises a collection of tools over a standard connection. A CLI connection discovers one installed program and runs its commands locally. |
| [Jarvis-Agents](jarvis-agents) | Connected catalog tools work in the live assistant. Background missions receive a separate, restricted tool grant and do not receive general `cli_*` tools. Codex, Claude, and Google subscription CLIs are supported worker providers only when connected from the Agents tab. |
| [Credentials and Secrets](credentials-and-secrets) | Jarvis stores protected API-key values; browser-login and configuration files remain under the external program's own storage rules. |
| [Safety and Approvals](safety-and-approvals) | Service permissions decide what the account can do. Jarvis separately decides whether a proposed command runs, is logged, asks for approval, or stays blocked. |

Disconnecting one CLI removes that tool from new assistant requests without
stopping other connected tools. If no connected CLI covers a request, a
relevant plugin or MCP tool may still be available. Jarvis does not invent
external data when every suitable connection is unavailable.

## Check That It Works

1. Select one row that says **Connected** and open **CLI Test Hub**.
2. Choose that CLI as the hint and ask it to report its installed version
   without changing anything.
3. Confirm that the result names the selected tool, shows the exact version
   command, and reports exit code `0` with a version in the program output.
4. Return to the CLI row and open **History**. A new successful entry for that
   command confirms discovery, tool exposure, execution, and usage logging.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Not installed** remains after installation | The cached status was reloaded, or the executable is not on the path visible to Jarvis | Finish the installer, restart Jarvis only if the installer changed the process path, then use **Recheck status** |
| **Browser Login** does not open a terminal | The host is not Windows, is headless, or has no supported Windows terminal | Use the program's official install and login commands in a terminal on that host, then recheck it in Jarvis |
| The browser login finishes but the row stays **Disconnected** | The program's account check did not pass, or its five-minute background wait ended | Confirm the login in the external terminal, keep the intended account active, and choose **Recheck status** |
| A row says **Connected**, but the command fails | Existing-configuration tools can look ready before real access is proved, the account lacks permission, or the command expects input | Run one official read-only status command in a real terminal, fix the program's own sign-in or permissions, then retry a non-interactive request |
| Test Hub reports no CLI tool, a Brain error, or a blocked command | No connected tool matched, the active Brain cannot call tools, or safety refused the command | Recheck the CLI, select it as the hint, verify the Brain provider, and review the command's risk rules before changing them |

For repeated backend, credential, or process failures, continue with the main
[Troubleshooting](troubleshooting) guide.

## Next Steps

- Read [Skills](skills) to give Jarvis a repeatable method for an already
  connected command-line tool.
- Read [Workflows and Commands](workflows-and-commands) when you need a stable
  Personal Jarvis operation rather than an external program's command set.
- Use the [CLI Reference](cli-reference) to manage connections from a terminal
  or a headless host and to understand common Jarvis CLI flags.
- Review [Safety and Approvals](safety-and-approvals) before allowing commands
  that create, publish, update, or delete external resources.
