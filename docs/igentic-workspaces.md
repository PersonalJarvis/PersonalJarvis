# iGentic projects and workspace orchestration

The IDE is organized as **Project > Workspace > Coding Agent session**. A
project is a connected folder in the existing project library. A workspace is
an independently named group of up to eight coding sessions in that project.
Multiple workspaces may use the same folder; independent sessions do not imply
isolated source checkouts. Users who need separate files connect a worktree as
another project.

## Interaction and identity

Connecting or creating a folder registers a project without starting a process.
The project's plus action opens a compact agent setup dialog. The project tree
shows open and saved workspaces, their session counts, and active selection.
Selecting an open workspace changes the visible group and active runtime ID;
it does not stop background agents. Restoring a closed workspace is explicit.

The grid uses at most four columns and two rows on a sufficiently wide display.
Narrow screens retain at most two rows and scroll horizontally when necessary,
rather than shrinking the user's terminal font. Dragging a card header or using
Alt+Arrow reorders stable session IDs. Reordering does not recreate the CLI
process. Each tile uses the existing
PTY adapter, output replay, reconnect budget, and terminal appearance tokens.

Project IDs come from the canonical folder identity. Workspace IDs survive
restore. A coding session is addressed as `pane:<history_id>`; its display name
and grid position are not execution identities. Additive snapshot migration
retains older project-less workspaces and keeps sibling workspaces in the same
folder distinct. Older snapshots above the eight-session limit are retained
but cannot be restored into the new grid unchanged.

## Live integration

The new `workspace-orchestrate` tool is available through the supervisor's voice
catalog to ChatGPT Live and the native Gemini/local Live path. It replaces the
legacy ambient IDE prompt tools on that surface. It is not installed in mission
worker tools and does not grant Society agents any new capability.

1. `inspect` reads the same project graph used by the sidebar, enriched with
   actual coding-session identities, status and observed activity.
2. `resolve` interprets model-extracted project/workspace/agent names or IDs.
   Explicit references take precedence. With no reference, the active workspace
   is the default. An unnamed agent is selected in stable order from idle or
   unstarted sessions. Duplicate names and multiple matching workspaces return
   structured clarification candidates rather than guessing.
3. `send` requires the resolved project, workspace and terminal IDs, a task, and
   the unique request ID issued by resolution. The application-owned ToolExecutor
   applies permission policy before the coding-session gateway delivers to that
   exact session.
4. `context` reads recorded results from the addressed session. A delivery
   receipt never asserts that the coding task has completed.

For example, a request to fix an installer in a named project can address a
background workspace while the user continues viewing another project. The
orchestrator never temporarily changes global selection to make a delivery.
Changing selection while an approval is pending cannot change its target.

Each send is durably claimed in SQLite before delivery. Retrying the same ID
returns its existing receipt. Reusing an ID with changed arguments fails.
Cancellation or interruption after a possible write leaves an uncertain claim;
neither voice reconnection nor a new tool-call ID replays that request. Receipts
include the immutable target and execution trace. Requests to a closed, moved,
archived or non-coding session fail without falling back to another terminal.

## Boundaries and verification

This is a T3 shared contract change. No new OS adapter, credential or model
default is introduced. PTY availability remains capability-probed; Windows uses
the existing ConPTY backend and Linux/macOS the existing POSIX backend.
Headless systems can inspect projects even without a usable terminal backend.

Contract coverage is in `tests/contract/test_workspace_orchestration.py`; the
workspace graph and migration tests are in
`tests/unit/agentic_ide/test_workspace_graph.py`. Frontend interaction tests
exercise project creation, switching, bounded grid layout and stable reordering.
Passing these tests is not evidence of native macOS execution, a microphone
test, or a completed real coding assignment. Live and desktop acceptance must
be reported separately from mocked transport checks.

A synthetic Gemini Realtime probe on Windows and a fresh headless Linux source
installation exercised model-selected resolution and one addressed delivery
through the real ToolExecutor, then received spoken delivery confirmation. The
Linux dependency image was preinstalled; the application was freshly installed
from source. The coding-session adapter in that probe was a fake; no repository
was changed. An OpenAI Live probe was attempted but session setup was rejected,
so live OpenAI acceptance remains open. Portable Linux contract tests pass;
native macOS execution and microphone/device acceptance remain unverified.
