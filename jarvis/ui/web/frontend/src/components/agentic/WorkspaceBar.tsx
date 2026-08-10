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
import { useEffect, useRef, useState } from "react";
import { Check, FolderGit2, Pencil, Plus, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { WorkspaceCard } from "@/lib/agenticIdeApi";
import { dragCarriesFiles, extractPaneDrop, type PaneDropPayload } from "./paneDrop";

/** Names remain useful through six tabs; after that, numbered icons scan better. */
const COMPACT_FROM_WORKSPACES = 7;
const FULL_TAB_TARGET_PX = 112;
const FULL_ADD_BUTTON_PX = 116;
const COMPACT_TAB_TARGET_PX = 36;
const COMPACT_ADD_BUTTON_PX = 28;
const ADD_TAB_FOCUS_ID = "__add_workspace__";

type WorkspaceDensity = "full" | "compact" | "ordinal";

/** Pick the smallest readable presentation for the space the toolbar left us. */
function densityFor(width: number | null, workspaceCount: number): WorkspaceDensity {
  const measured = width !== null;
  if (
    measured &&
    width < workspaceCount * COMPACT_TAB_TARGET_PX + COMPACT_ADD_BUTTON_PX
  ) {
    return "ordinal";
  }
  if (
    workspaceCount >= COMPACT_FROM_WORKSPACES ||
    (measured && width < workspaceCount * FULL_TAB_TARGET_PX + FULL_ADD_BUTTON_PX)
  ) {
    return "compact";
  }
  return "full";
}

interface WorkspaceBarProps {
  workspaces: WorkspaceCard[];
  /** Id of the workspace on screen, or null while the wizard is showing. */
  activeId: string | null;
  /** True while the wizard is open for an ADDITIONAL workspace. */
  addingNew: boolean;
  /** A legacy/backend safety cap, or null when workspace count is unrestricted. */
  maxWorkspaces: number | null;
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
  const t = useT();
  const barRef = useRef<HTMLDivElement>(null);
  // Which tab has its close button armed. One at a time, and cleared on every
  // other interaction, so an armed X can never be clicked by accident later.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  // Which tab a file drag is currently over. One at a time — a drag has one
  // position — so this is an id rather than a set.
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [barWidth, setBarWidth] = useState<number | null>(null);
  const [rovingFocusId, setRovingFocusId] = useState(
    addingNew ? ADD_TAB_FOCUS_ID : (activeId ?? workspaces[0]?.id ?? ADD_TAB_FOCUS_ID),
  );
  const workspaceIdKey = workspaces.map((workspace) => workspace.id).join("\u0000");
  const full = maxWorkspaces !== null && workspaces.length >= maxWorkspaces;

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

  // The surrounding toolbar owns a variable number of icon controls. Count
  // alone cannot know how much width they leave, so the tab density follows the
  // space the bar actually receives and updates as the desktop window changes.
  useEffect(() => {
    const bar = barRef.current;
    if (!hasTabs || !bar) return;
    const update = (width: number) => {
      // A hidden element and jsdom both report zero. That is not usable layout
      // information; keep the count-based tier until the observer sees width.
      if (width > 0 && Number.isFinite(width)) setBarWidth(width);
    };
    update(bar.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      update(entries[0]?.contentRect.width ?? bar.getBoundingClientRect().width);
    });
    observer.observe(bar);
    return () => observer.disconnect();
  }, [hasTabs]);

  // An external workspace switch becomes the roving tab stop too. Keeping the
  // value in React state avoids imperative DOM mutations leaving two tab stops
  // behind after a parent rerender.
  useEffect(() => {
    setRovingFocusId(
      addingNew ? ADD_TAB_FOCUS_ID : (activeId ?? workspaces[0]?.id ?? ADD_TAB_FOCUS_ID),
    );
  }, [activeId, addingNew]);

  // A refreshed array with the same IDs is not a navigation event. Only repair
  // roving focus when its actual target disappeared (or Add became disabled).
  useEffect(() => {
    setRovingFocusId((current) => {
      const stillAvailable =
        current === ADD_TAB_FOCUS_ID
          ? !full
          : workspaces.some((workspace) => workspace.id === current);
      if (stillAvailable) return current;
      return activeId ?? workspaces[0]?.id ?? (!full ? ADD_TAB_FOCUS_ID : current);
    });
  }, [workspaceIdKey, full]);

  const density = densityFor(barWidth, workspaces.length);
  const compact = density !== "full";
  const ordinalOnly = density === "ordinal";

  /** Standard roving focus across every available tab and the Add control. */
  const moveTabFocus = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]:not(:disabled)'),
    );
    const current = (event.target as HTMLElement).closest<HTMLElement>('[role="tab"]');
    const currentIndex = current ? tabs.indexOf(current) : -1;
    if (currentIndex < 0 || tabs.length === 0) return;
    event.preventDefault();
    let nextIndex = currentIndex;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    const next = tabs[nextIndex];
    if (!next) return;
    setRovingFocusId(next.dataset.workspaceFocusId ?? ADD_TAB_FOCUS_ID);
    next.focus();
  };

  // Nothing open, nothing to add to and no controls to carry: the wizard IS the
  // screen, and an empty bar above it would be furniture.
  if (!hasTabs && !actions) return null;

  return (
    <div
      className={cn(
        "flex items-center gap-2",
        embedded ? "min-w-[12rem] flex-1" : "border-b border-border px-2 py-1",
      )}
    >
      {!hasTabs && <div className="min-w-0 flex-1" />}
      {hasTabs && (
      <div
        ref={barRef}
        data-testid="workspace-bar"
        data-density={density}
        className={cn(
          "flex min-w-0 flex-1 items-center overflow-hidden",
          ordinalOnly ? "gap-px" : "gap-1",
        )}
        role="tablist"
        aria-label={t("workspace_bar.open_workspaces")}
        onKeyDown={moveTabFocus}
      >
      {workspaces.map((workspace, index) => {
        const selected = !addingNew && workspace.id === activeId;
        const armed = confirming === workspace.id;
        const renaming = editing === workspace.id;
        const dropping = dropTarget === workspace.id;
        const summary = t(
          workspace.terminals === 1
            ? "workspace_bar.workspace_summary_one"
            : "workspace_bar.workspace_summary_many",
        )
          .replace("{workspace}", workspace.name)
          .replace("{index}", String(index + 1))
          .replace("{count}", String(workspace.terminals));
        const overlayAlignment =
          index === 0
            ? "left-0"
            : index === workspaces.length - 1
              ? "right-0"
              : "left-1/2 -translate-x-1/2";
        const ordinalRenamePosition =
          index === 0
            ? "left-0"
            : index === workspaces.length - 1
              ? "right-5"
              : "left-1/2 -translate-x-full";
        const ordinalClosePosition =
          index === 0
            ? "left-5"
            : index === workspaces.length - 1
              ? "right-0"
              : "left-1/2";
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
            /*
             * The selected tab is a RAISED tab, marked once.
             * It used to be marked three times over — a yellow border, a yellow
             * fill, a yellow folder glyph and yellow label text — which is four
             * ways of saying the same thing and left the accent unable to say
             * anything else in this row. One filled surface carries "you are
             * here"; the glyph keeps the colour because it is the smallest of
             * the four and reads at a glance across a wide bar.
            */
            className={cn(
              "group/tab relative flex items-center gap-1.5 rounded-control border py-1 transition-colors",
              compact
                ? ordinalOnly
                  ? "min-w-0 flex-1 basis-0 justify-center px-0"
                  : renaming
                    ? "min-w-0 flex-[10_1_0%] px-1"
                    : armed
                      ? "min-w-0 flex-[8_1_0%] px-1"
                      : "min-w-0 flex-1 basis-0 justify-center px-1 hover:flex-[3_1_0%] focus-within:flex-[3_1_0%]"
                : "min-w-[2.5rem] max-w-[18rem] shrink px-2",
              dropping
                ? "border-dashed border-primary bg-primary/10"
                : selected
                  ? "border-border/60 bg-secondary"
                  : "border-transparent hover:bg-secondary/50",
            )}
          >
            {renaming ? (
              <form
                className={cn(
                  "flex items-center gap-1",
                  ordinalOnly &&
                    "absolute top-0 z-30 rounded-control border border-border bg-secondary p-0.5 shadow-lg",
                  ordinalOnly && overlayAlignment,
                )}
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
                  className={cn(
                    "rounded border border-primary/40 bg-background px-2 py-0.5 text-sm outline-none focus:border-primary disabled:opacity-60",
                    ordinalOnly ? "w-24" : "w-40",
                  )}
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
                  aria-label={summary}
                  tabIndex={rovingFocusId === workspace.id ? 0 : -1}
                  data-workspace-focus-id={workspace.id}
                  disabled={busy}
                  data-testid={`workspace-tab-${workspace.id}`}
                  title={compact ? `${summary} — ${workspace.folder}` : workspace.folder}
                  onClick={() => {
                    setConfirming(null);
                    if (!selected) onSelect(workspace.id);
                  }}
                  onFocus={() => setRovingFocusId(workspace.id)}
                  className={cn(
                    "flex min-w-0 items-center text-left disabled:cursor-not-allowed disabled:opacity-60",
                    compact ? "flex-1 justify-center gap-1" : "gap-2",
                  )}
                >
                  {!ordinalOnly && (
                    <FolderGit2
                      className={cn(
                        "h-3.5 w-3.5 shrink-0",
                        selected ? "text-primary" : "text-muted-foreground",
                      )}
                    />
                  )}
                  <span
                    className={cn(
                      "max-w-[14rem] truncate text-sm",
                      compact && "sr-only",
                      selected
                        ? "font-medium text-foreground"
                        : "text-muted-foreground",
                    )}
                  >
                    {workspace.name}
                  </span>
                  {compact ? (
                    <span
                      data-testid={`workspace-ordinal-${workspace.id}`}
                      className={cn(
                        "font-mono text-[11px] tabular-nums",
                        selected ? "font-semibold text-foreground" : "text-muted-foreground",
                      )}
                    >
                      {index + 1}
                    </span>
                  ) : (
                    <PaneCount workspace={workspace} selected={selected} />
                  )}
                </button>

                <button
                  type="button"
                  aria-label={`Rename ${workspace.name}`}
                  title={`Rename ${workspace.name}`}
                  disabled={busy}
                  data-testid={`workspace-rename-${workspace.id}`}
                  onClick={() => beginRename(workspace)}
                  className={cn(
                    "flex h-5 w-5 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover/tab:opacity-100 disabled:opacity-40",
                    compact && "absolute z-20 bg-secondary/95",
                    compact && (ordinalOnly ? ordinalRenamePosition : "left-0"),
                  )}
                >
                  <Pencil className="h-3 w-3" />
                </button>
              </>
            )}

            {!renaming && armed ? (
              <span
                className={cn(
                  "flex items-center gap-1",
                  ordinalOnly &&
                    "absolute top-0 z-30 rounded-control border border-border bg-secondary p-0.5 shadow-lg",
                  ordinalOnly && overlayAlignment,
                )}
              >
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
                  compact
                    ? cn(
                        "absolute z-20 bg-secondary/95 opacity-0 focus-visible:opacity-100 group-hover/tab:opacity-100",
                        ordinalOnly ? ordinalClosePosition : "right-0",
                      )
                    : selected
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
        tabIndex={rovingFocusId === ADD_TAB_FOCUS_ID ? 0 : -1}
        data-workspace-focus-id={ADD_TAB_FOCUS_ID}
        disabled={busy || full}
        data-testid="workspace-add"
        title={
          full && maxWorkspaces !== null
            ? `${maxWorkspaces} workspaces are already open — close one first.`
            : "Open another folder in its own workspace"
        }
        onClick={() => {
          setConfirming(null);
          onAdd();
        }}
        onFocus={() => setRovingFocusId(ADD_TAB_FOCUS_ID)}
        className={cn(
          "flex shrink-0 items-center rounded-lg border border-transparent text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40",
          ordinalOnly
            ? "h-7 w-6 justify-center p-0"
            : compact
              ? "h-7 w-7 justify-center p-0"
              : "gap-1.5 px-2 py-1",
          addingNew
            ? "border-primary/50 bg-primary/10 text-primary"
            : "text-muted-foreground hover:bg-secondary hover:text-foreground",
        )}
      >
        <Plus className="h-3.5 w-3.5" />
        <span className={cn(compact && "sr-only")}>
          {t("workspace_bar.new_workspace")}
        </span>
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
        // No fill of its own: the tab it sits in is already a filled surface
        // when selected, and a badge inside it was a second box inside a box.
        "shrink-0 font-mono text-[10px] tabular-nums",
        selected ? "text-primary" : "text-muted-foreground/60",
      )}
    >
      {workspace.terminals}
    </span>
  );
}
