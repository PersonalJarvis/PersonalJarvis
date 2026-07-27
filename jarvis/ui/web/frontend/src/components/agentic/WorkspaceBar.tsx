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
 */
import { useState } from "react";
import { Check, FolderGit2, Pencil, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WorkspaceCard } from "@/lib/agenticIdeApi";

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
  /** Disable every control while a switch or a close is in flight. */
  busy?: boolean;
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
  busy = false,
}: WorkspaceBarProps) {
  // Which tab has its close button armed. One at a time, and cleared on every
  // other interaction, so an armed X can never be clicked by accident later.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const full = workspaces.length >= maxWorkspaces;

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

  // Nothing open and nothing to add to: the wizard IS the screen, and an empty
  // bar above it would be furniture.
  if (workspaces.length === 0) return null;

  return (
    <div
      data-testid="workspace-bar"
      className="flex items-center gap-1 overflow-x-auto border-b border-border px-3 py-1.5 scrollbar-jarvis"
      role="tablist"
      aria-label="Open workspaces"
    >
      {workspaces.map((workspace) => {
        const selected = !addingNew && workspace.id === activeId;
        const armed = confirming === workspace.id;
        const renaming = editing === workspace.id;
        return (
          <div
            key={workspace.id}
            className={cn(
              "group/tab flex shrink-0 items-center gap-2 rounded-lg border px-3 py-1.5 transition-colors",
              selected
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
          "flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40",
          addingNew
            ? "border-primary/50 bg-primary/10 text-primary"
            : "border-dashed border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
        )}
      >
        <Plus className="h-3.5 w-3.5" />
        New workspace
      </button>
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
