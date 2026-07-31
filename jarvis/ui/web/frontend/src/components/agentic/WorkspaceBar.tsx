/**
 * The row of open workspaces above the Agentic IDE, and the button that adds
 * another one.
 *
 * A workspace is a folder with its own running coding agents. Several can be
 * open at once, and switching between them costs nothing: the agents in the one
 * you leave keep working, and the one you come back to reconnects to the
 * processes that were running the whole time.
 *
 * Two things the tabs have to say out loud, because the alternative is a user
 * guessing:
 *
 * * **how many terminal panes are open in there.** The number follows panes as
 *   they are added or closed; it is not a historical spawn total or a process
 *   health readout.
 * * **which one is on screen.** With the panes of only one workspace visible at
 *   a time, an unmarked bar would make the grid look like it belongs to
 *   whichever tab the eye landed on.
 *
 * Closing is deliberately a two-step: the X reveals a confirm, because it is the
 * one control in this bar that stops work — every other one is reversible.
 *
 * The tabs are also file targets. Dropping a screenshot on a workspace tab is
 * the natural way to say "this belongs to THAT project" while looking at
 * another one — it addresses the workspace by name instead of making the user
 * switch first, drop second, and remember which pane had focus.
 */
import { useEffect, useState } from "react";
import { Check, FolderGit2, Pencil, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WorkspaceCard } from "@/lib/agenticIdeApi";
import { dragCarriesFiles, extractPaneDrop, type PaneDropPayload } from "./paneDrop";

interface WorkspaceBarProps {
  workspaces: WorkspaceCard[];
  /** Id of the workspace on screen, or null while the wizard is showing. */
  activeId: string | null;
  /** True while the wizard is open for an ADDITIONAL workspace. */
  addingNew: boolean;
  maxWorkspaces: number;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onRename: (id: string, name: string) => Promise<boolean>;
  onClose: (id: string) => void;
  /**
   * A file was dropped on a workspace TAB. Left out, the tabs refuse drags and
   * the cursor says so rather than accepting a file nothing would do anything
   * with.
   */
  onDropFiles?: (workspaceId: string, payload: PaneDropPayload) => void;
  /** Disable every control while a switch or a close is in flight. */
  busy?: boolean;
  /**
   * The workspace's own controls, pinned to the right of this same row.
   *
   * They used to sit in a second bar directly underneath, which cost a whole
   * line of the window to hold half a dozen buttons — in a view whose entire
   * job is showing terminal output. The tabs rarely fill their half of the row,
   * so the controls ride along in the space that was already there.
   */
  actions?: React.ReactNode;
  /**
   * Rendered INSIDE another bar rather than as its own — drops the frame (its
   * border and outer padding) so the host row draws exactly one line.
   */
  embedded?: boolean;
}

export function WorkspaceBar({
  workspaces,
  activeId,
  addingNew,
  maxWorkspaces,
  onSelect,
  onAdd,
  onRename,
  onClose,
  onDropFiles,
  busy = false,
  actions,
  embedded = false,
}: WorkspaceBarProps) {
  // Which tab has its close button armed. One at a time, and cleared on every
  // other interaction, so an armed X can never be clicked by accident later.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  // Which tab a file drag is currently over. One at a time — a drag has one
  // position — so this is an id rather than a set.
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const full = workspaces.length >= maxWorkspaces;

  // A drag that ends anywhere else owes this bar no `dragleave`, and a tab left
  // highlighted over a workspace nobody dropped on is a lie about what will
  // happen next. Watched globally, and only while something is actually armed.
  useEffect(() => {
    if (dropTarget === null) return;
    const clear = () => setDropTarget(null);
    window.addEventListener("drop", clear);
    window.addEventListener("dragend", clear);
    return () => {
      window.removeEventListener("drop", clear);
      window.removeEventListener("dragend", clear);
    };
  }, [dropTarget]);

  /** Drag-drop handlers for one tab. Empty when the owner takes no drops. */
  const dropHandlersFor = (workspace: WorkspaceCard) => {
    if (!onDropFiles) return {};
    return {
      onDragEnter: (event: React.DragEvent) => {
        // Claimed even for payloads this tab will not take: the default action
        // for a dropped link is to NAVIGATE, which would replace the whole IDE
        // — every running agent in it with it.
        event.preventDefault();
        if (!dragCarriesFiles(event.dataTransfer)) return;
        setDropTarget(workspace.id);
      },
      onDragOver: (event: React.DragEvent) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = dragCarriesFiles(event.dataTransfer)
          ? "copy"
          : "none";
      },
      onDragLeave: (event: React.DragEvent) => {
        // A drag crossing the tab's own children fires leave for each of them;
        // only a leave that actually exits the tab counts.
        const next = event.relatedTarget as Node | null;
        if (next && event.currentTarget.contains(next)) return;
        setDropTarget((current) => (current === workspace.id ? null : current));
      },
      onDrop: (event: React.DragEvent) => {
        event.preventDefault();
        setDropTarget(null);
        if (!dragCarriesFiles(event.dataTransfer)) return;
        // Read SYNCHRONOUSLY — a DataTransfer empties the moment this returns.
        onDropFiles(workspace.id, extractPaneDrop(event.dataTransfer));
      },
    };
  };

  const beginRename = (workspace: WorkspaceCard) => {
    setConfirming(null);
    setEditing(workspace.id);
    setDraft(workspace.name);
  };

  const commitRename = async (workspace: WorkspaceCard) => {
    const name = draft.trim();
    if (!name) return;
    if (name === workspace.name || (await onRename(workspace.id, name))) {
      setEditing(null);
    }
  };

  /*
   * With nothing open there are no TABS — only the row that may carry actions.
   *
   * The distinction matters twice over. It is what the user sees: a tab strip
   * offering "New workspace" above a wizard that IS the new workspace is a
   * second button for the thing already on screen. And it is what the tests
   * see: the tabs appear only once the workspace list has actually arrived, so
   * nothing can be clicked or measured in the instant before the first fetch
   * resolves — a bar that rendered its controls immediately handed those tests
   * a "New workspace" button that was neither disabled nor still attached by
   * the time the state landed.
   */
  const hasTabs = workspaces.length > 0;

  // Nothing open, nothing to add to and no controls to carry: the wizard IS the
  // screen, and an empty bar above it would be furniture.
  if (!hasTabs && !actions) return null;

  return (
    <div
      className={cn(
        "flex items-center gap-2",
        embedded ? "min-w-0 flex-1" : "border-b border-border px-2 py-1",
      )}
    >
      {!hasTabs && <div className="min-w-0 flex-1" />}
      {hasTabs && (
      <div
        data-testid="workspace-bar"
        className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto scrollbar-jarvis"
        role="tablist"
        aria-label="Open workspaces"
      >
      {workspaces.map((workspace) => {
        const selected = !addingNew && workspace.id === activeId;
        const armed = confirming === workspace.id;
        const renaming = editing === workspace.id;
        const dropping = dropTarget === workspace.id;
        return (
          <div
            key={workspace.id}
            data-testid={`workspace-tab-drop-${workspace.id}`}
            {...dropHandlersFor(workspace)}
            title={
              onDropFiles
                ? `${workspace.folder} — drop a screenshot or document here to send it to this workspace`
                : workspace.folder
            }
            className={cn(
              "group/tab flex shrink-0 items-center gap-1.5 rounded-lg border px-2 py-1 transition-colors",
              dropping
                ? "border-primary border-dashed bg-primary/15"
                : selected
                  ? "border-primary/50 bg-primary/10"
                  : "border-transparent hover:border-border hover:bg-muted/40",
            )}
          >
            {renaming ? (
              <form
                className="flex items-center gap-1"
                onSubmit={(event) => {
                  event.preventDefault();
                  void commitRename(workspace);
                }}
              >
                <input
                  autoFocus
                  value={draft}
                  maxLength={80}
                  disabled={busy}
                  aria-label={`Rename ${workspace.name}`}
                  data-testid={`workspace-rename-input-${workspace.id}`}
                  onFocus={(event) => event.currentTarget.select()}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setEditing(null);
                  }}
                  className="w-40 rounded border border-primary/40 bg-background px-2 py-0.5 text-sm outline-none focus:border-primary disabled:opacity-60"
                />
                <button
                  type="submit"
                  disabled={busy || !draft.trim()}
                  aria-label={`Save name for ${workspace.name}`}
                  data-testid={`workspace-rename-save-${workspace.id}`}
                  className="flex h-6 w-6 items-center justify-center rounded text-primary hover:bg-primary/15 disabled:opacity-40"
                >
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  disabled={busy}
                  aria-label="Cancel rename"
                  onClick={() => setEditing(null)}
                  className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </form>
            ) : (
              <>
                <button
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  disabled={busy}
                  data-testid={`workspace-tab-${workspace.id}`}
                  title={workspace.folder}
                  onClick={() => {
                    setConfirming(null);
                    if (!selected) onSelect(workspace.id);
                  }}
                  className="flex min-w-0 items-center gap-2 text-left disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <FolderGit2
                    className={cn(
                      "h-3.5 w-3.5 shrink-0",
                      selected ? "text-primary" : "text-muted-foreground",
                    )}
                  />
                  <span
                    className={cn(
                      "max-w-[14rem] truncate text-sm font-medium",
                      selected ? "text-primary" : "text-foreground",
                    )}
                  >
                    {workspace.name}
                  </span>
                  <PaneCount workspace={workspace} selected={selected} />
                </button>

                <button
                  type="button"
                  aria-label={`Rename ${workspace.name}`}
                  title={`Rename ${workspace.name}`}
                  disabled={busy}
                  data-testid={`workspace-rename-${workspace.id}`}
                  onClick={() => beginRename(workspace)}
                  className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover/tab:opacity-100 disabled:opacity-40"
                >
                  <Pencil className="h-3 w-3" />
                </button>
              </>
            )}

            {!renaming && armed ? (
              <span className="flex items-center gap-1">
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Confirm closing ${workspace.name}`}
                  data-testid={`workspace-close-confirm-${workspace.id}`}
                  onClick={() => {
                    setConfirming(null);
                    onClose(workspace.id);
                  }}
                  className="rounded bg-destructive/20 px-2 py-0.5 text-[11px] font-medium text-destructive transition-colors hover:bg-destructive/30 disabled:opacity-50"
                >
                  Close &amp; stop {workspace.live_terminals || workspace.terminals}
                </button>
                <button
                  type="button"
                  aria-label="Keep this workspace open"
                  onClick={() => setConfirming(null)}
                  className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  Keep
                </button>
              </span>
            ) : !renaming ? (
              <button
                type="button"
                aria-label={`Close ${workspace.name}`}
                title={`Close ${workspace.name} and stop its agents`}
                disabled={busy}
                data-testid={`workspace-close-${workspace.id}`}
                onClick={() => setConfirming(workspace.id)}
                className={cn(
                  "flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition-opacity hover:bg-destructive/20 hover:text-destructive",
                  selected
                    ? "opacity-100"
                    : "opacity-0 focus-visible:opacity-100 group-hover/tab:opacity-100",
                )}
              >
                <X className="h-3 w-3" />
              </button>
            ) : null}
          </div>
        );
      })}

      <button
        type="button"
        role="tab"
        aria-selected={addingNew}
        disabled={busy || full}
        data-testid="workspace-add"
        title={
          full
            ? `${maxWorkspaces} workspaces are already open — close one first.`
            : "Open another folder in its own workspace"
        }
        onClick={() => {
          setConfirming(null);
          onAdd();
        }}
        className={cn(
          "flex shrink-0 items-center gap-1.5 rounded-lg border px-2 py-1 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40",
          addingNew
            ? "border-primary/50 bg-primary/10 text-primary"
            : "border-dashed border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
        )}
      >
        <Plus className="h-3.5 w-3.5" />
        New workspace
      </button>
      </div>
      )}

      {actions && (
        <div
          data-testid="workspace-bar-actions"
          className="flex shrink-0 items-center gap-1"
        >
          {actions}
        </div>
      )}
    </div>
  );
}

/** The number of terminal panes currently open in this workspace. */
function PaneCount({
  workspace,
  selected,
}: {
  workspace: WorkspaceCard;
  selected: boolean;
}) {
  return (
    <span
      data-testid={`workspace-panes-${workspace.id}`}
      title={`${workspace.terminals} terminal${workspace.terminals === 1 ? "" : "s"} open`}
      className={cn(
        "shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px]",
        selected
          ? "bg-primary/20 text-primary"
          : "bg-muted text-muted-foreground",
      )}
    >
      {workspace.terminals}
    </span>
  );
}
