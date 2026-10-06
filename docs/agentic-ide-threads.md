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

## Questions, approvals and plans

Every coding agent gets the same cards at the top of the composer
(`jarvis/agent_chat/turn_prompts.py`):

| Moment | Claude Code, GLM | Every other CLI (Codex, Antigravity, Grok Build, OpenCode, Kimi, Cursor, DeepSeek Harness) |
|---|---|---|
| The agent has a question | Its own `AskUserQuestion` opens the question card mid-turn; the answers go back on the control protocol and it keeps working | It ends its reply with a `jarvis-ask` block (the protocol rides in front of every prompt); the card opens when the turn ends and the answers are the next message |
| The plan is ready | `ExitPlanMode` opens a plan card with the plan itself; **Build it** switches the thread to the runner's build mode and the same turn starts building | A turn that finished in plan mode opens a plan card; **Build it** switches the thread to the runner's build mode and sends the go-ahead |
| A tool needs permission | The approval card (Approve, Always allow, Decline) | The CLI cannot ask from a headless run: the access mode decides, as each mode's description says |

The question card offers the agent's options (its recommendation first), a
typed answer, and Skip, which leaves the open questions to the agent's
recommendations. **Keep planning** closes a plan card so the person can type
what to change. A card nobody answered closes when the person sends another
message instead. Cards are rebuilt from the session's event log, so they
survive reopening the thread and restarting the app.

## Threads a Jarvis agent starts

A Jarvis agent can start a thread too ("let Opus 5.5 build this in the
website project"). It picks the coding agent, model and folder the person
named — looking into the folder first when it needs context — and writes the
brief itself. The thread then appears under its project like any other; the
agent's messages carry its name above the bubble, so the person sees exactly
what it asked and what came back, and can step in at any time.

The agent's own chat shows a line that opens the thread. When a turn the
agent started finishes, asks a question, presents a plan or waits for an
approval, the agent is woken with the result: it checks the work, answers the
question or plan card, sends a follow-up, and tells the person the outcome.
Approvals of the coding agent's own commands stay with the person. A turn the
person types into the thread is theirs and wakes no agent. After 25 automatic
updates for one thread the agent stops following it until the person writes
in the agent's chat again.

## Restarts do not stop a thread

A thread's coding CLI does not run as a child of the app. It runs in the
turn host (`jarvis/agent_chat/turn_host.py`), a small background process
that the app starts on first use and talks to over a loopback socket. The
terminal grid keeps its agents in the PTY host the same way. The host keeps
every line the CLI prints and streams them to the app.

- **Restart, update, crash:** the CLI keeps working. The next app start
  attaches again. It feeds the lines the old app already handled back into
  the translator without showing them twice, then carries the turn on live:
  new output, approval cards, the real ending.
- **Quit:** the turn stays open while the CLI works. A turn that ends while
  no app is attached is written to a spool folder, and the next start shows
  its answer.
- **Stop** ends the CLI as before.
- **Another app process takes over** (a second start of the same instance):
  the old process hands its turns over without ending them.

Right after the app starts, it attaches to the host on its own, before any
window opens a chat, so a waiting approval shows up without delay.

Only thread turns run there. A turn running as Jarvis, a goal turn and a
helper turn without tools need state inside the app and stay its children.
An installed (frozen) build starts the host by running its own executable
with `--turn-host`, since it has no `python -m`. Processes that are not the
desktop app or the web launcher, such as tests and scripts, never start or
attach to the host. Logs: `logs/turn_host.log` in the Jarvis data folder.

## Platforms

Nothing in the thread layout is OS-specific: paths are compared
case-insensitively only for Windows drive paths and the git calls run the same
`git` everywhere. The layout choice and the last agent pick are kept in the
browser's storage and fall back to defaults when storage is blocked.
