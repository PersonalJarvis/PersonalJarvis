# Agentic IDE: thread layout

The Agentic IDE has two layouts, switched with the rounded switch in the
middle of the window caption:

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

- **New thread:** the small plus on a project row. The empty thread asks what
  to build in the project; the project name is a picker.
- **Conversation:** the person's messages on the right, the agent's answer as
  reading text, the work in between as folded rows ("Ran 3 commands, read 2
  files") that open to each call, its diff or its output. A running turn ends
  in a live "Working for 12s" line; a finished one says how long it worked and
  which files it changed.
- **Composer:** one toolbar — coding agent and model, reasoning effort, access
  mode — beside the paperclip and the send button. A new thread starts on the
  agent, model, effort and access the person picked last (else the newest
  thread's). Files and images go in by drop, paste or paperclip; `/` `@` `$`
  open the typeahead where the CLI offers it. An approval or a question the
  agent waits on opens at the top of the composer. A message sent while the
  agent works is queued; after Stop the queue waits.
- **Under the composer:** a strip that says where the agent works — the
  current checkout or a fresh git worktree for a new thread — and on which
  branch (the base of the worktree, or the branch to switch the checkout to).
- Changes, files, git and terminals live in the IDE's side panel, as in the
  grid layout.

## Platforms

Nothing in the thread layout is OS-specific: paths are compared
case-insensitively only for Windows drive paths and the git calls run the same
`git` everywhere. The layout choice and the last agent pick are kept in the
browser's storage and fall back to defaults when storage is blocked.
