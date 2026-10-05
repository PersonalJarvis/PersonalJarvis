# Agentic IDE: thread layout

The Agentic IDE has two layouts, switched with the two buttons in the sidebar
header:

- **Terminal grid** — workspaces of terminal panes, each running a coding CLI.
- **Threads** — one conversation with a coding agent at a time.

Switching never stops anything: the grid stays mounted behind the thread view,
so its terminals keep running and come back exactly as they were.

## What a thread is

A thread is an agent-chat session on the IDE's own surface (`agent`). The
coding CLI — Claude Code, Codex, OpenCode, Grok Build, Antigravity, … — is
driven through its own binary (`jarvis/agent_chat/runner_cli.py`), so its
tools, skills, MCP servers, permissions and subscription login are the CLI's,
and the conversation is the CLI's own session (resumed with its own id). No
second harness sits in between.

A thread belongs to a project: the one it was started in, else the deepest
connected project folder that holds the thread's folder.

## Sidebar

Projects with their threads, newest first. A thread is named by the title its
CLI gave the conversation (Claude Code's session title, Codex's thread name —
`cli_title` on `GET /api/agent-chat/sessions?surface=agent`), until the person
renames it; before the CLI names it, the first message is the name. The row's
end shows the CLI's logo, a spinner while it works, an amber dot while it waits
for an approval and a blue dot for news since it was last opened.

## Thread view

- **Header:** project and thread name (double-click renames), project actions
  (named shell commands run in the terminal drawer), *Open* in an installed
  editor or the file manager, *Commit & push* (commit, push, pull request via
  `gh`), and the terminal drawer and diff panel switches.
- **New thread:** asks what to build in the project; the project name is a
  picker.
- **Conversation:** the person's messages on the right, the agent's answer as
  reading text, the work in between as folded rows ("Ran 3 commands, read 2
  files") that open to each call, its diff or its output. A running turn ends
  in a live "Working for 12s" line; a finished one says how long it worked and
  which files it changed.
- **Composer:** coding agent and model, reasoning effort, access mode, files and
  images (drop, paste, paperclip), `/` `@` `$` typeahead where the CLI offers
  it. An approval or a question the agent waits on opens at the top of the
  composer. A message sent while the agent works is queued and goes out when it
  is free.
- **Under the composer:** a new thread runs in the current checkout or a fresh
  git worktree, on the branch picked there (the base of the worktree, or the
  branch to switch the checkout to).
- **Diff panel:** every changed file under the thread's folder and each file's
  change against the last commit (`GET /api/agentic-ide/git/changes` and
  `/diff?folder=&path=`).
- **Terminal drawer:** shells in the thread's folder
  (`/api/workspace/pty/{key}?agent=shell&folder=`).

## Platforms

Nothing in the thread layout is OS-specific: paths are compared
case-insensitively only for Windows drive paths, the drawer opens the
platform's default shell, and the git routes run the same `git` everywhere.
Project actions and the layout choice are kept in the browser's storage and
fall back to defaults when storage is blocked.
