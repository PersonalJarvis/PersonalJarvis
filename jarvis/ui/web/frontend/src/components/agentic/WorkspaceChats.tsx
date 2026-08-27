import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import { Archive, Folder, FolderPlus, Plus, RotateCcw, Search, Trash2 } from "lucide-react";

import type { SplitAgentChoice } from "@/components/agentic/AgentPicker";
import { AgentMark } from "@/components/agentic/AgentMark";
import { folderColor } from "@/components/agentic/folderColor";
import { PaneActivityPill } from "@/components/agentic/PaneActivityPill";
import { sessionTitle } from "@/components/agentic/sessionTitle";
import { useEventStore } from "@/store/events";
import { useIdeChatStore, type IdeWorkspaceRow } from "@/store/ideChat";
import {
  dropWorkspacePane,
  patchWorkspacePane,
  useWorkspacePanes,
} from "@/store/workspacePanes";
import { archiveTerminal, closeTerminal, type WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { folderLeaf } from "@/lib/folderPath";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";

const MENU_WIDTH = 220;
const VIEWPORT_MARGIN = 8;
/** Search earns its place once the archive is too long to scan. */
const ARCHIVED_SEARCH_AT = 5;

interface SessionMenuState {
  pane: WorkspacePaneRow;
  x: number;
  y: number;
}

/**
 * The sidebar, wearing the Agentic IDE's chat face — the ONE list of sessions.
 *
 * Every open workspace is a band, in the workspace bar's order and numbered
 * the same way: "Workspace 1", then its folder, then the coding sessions
 * running in it (the grid's panes), then a row to open another. The tab at
 * the front says so. That is the maintainer's sketch (2026-08-26): the folder
 * first, every workspace, the active one marked — and it replaced the second
 * list the grid used to draw beside the stage, which said the same things
 * twice in two orders.
 *
 * A session row brings that pane to the front through the store
 * (`requestPane`): the view switches workspace when the row belongs to
 * another tab, and the stage shows the pane. The folder row brings the whole
 * workspace to the front. Opening a terminal asks WHICH CLI first when the
 * machine offers more than one, exactly like the grid's own split menus.
 *
 * Right-click on a session offers two ways to thin the list: archive hides
 * the chat (the terminal keeps running, and an "Archived chats" row under
 * the workspace finds it again) and close stops the agent. The desktop
 * WebView has no native context menu, so without this the list had no
 * way to drop a row at all.
 *
 * This block does NOT own the column — it sits at the top of it and the
 * sections follow underneath (see `Sidebar`). It used to REPLACE them, with a
 * "Sections" button as the way back, and that button was a one-way door in
 * both directions: pressing it took the sessions away, and there was no state
 * in which the maintainer could see a section and a session at once
 * (maintainer report 2026-08-27). So there is no back button any more, and
 * nothing to go back from. The list scrolls with the sections rather than
 * inside its own frame — two scrollbars in one 320 px column read as a
 * rendering fault.
 */
export function WorkspaceChats() {
  const t = useT();
  const workspaces = useIdeChatStore((s) => s.workspaces);
  const agents = useIdeChatStore((s) => s.agents);
  const requestPane = useIdeChatStore((s) => s.requestPane);
  const requestNewChat = useIdeChatStore((s) => s.requestNewChat);
  const requestWorkspace = useIdeChatStore((s) => s.requestWorkspace);
  const requestSession = useIdeChatStore((s) => s.requestSession);
  const requestAddWorkspace = useIdeChatStore((s) => s.requestAddWorkspace);
  const stagedPane = useIdeChatStore((s) => s.stagedPane);
  const pushToast = useEventStore((s) => s.pushToast);
  // Shares one poll with every other reader of the list (see the store).
  const panes = useWorkspacePanes();
  const [menu, setMenu] = useState<SessionMenuState | null>(null);
  const [pendingClose, setPendingClose] = useState<WorkspacePaneRow | null>(null);
  const [closing, setClosing] = useState(false);

  const openMenu = useCallback((pane: WorkspacePaneRow, x: number, y: number) => {
    setMenu({ pane, x, y });
  }, []);

  const setArchived = useCallback(
    async (pane: WorkspacePaneRow, archived: boolean) => {
      patchWorkspacePane(pane.history_id, { archived });
      try {
        await archiveTerminal(pane.name, archived, pane.workspace_id);
      } catch (error) {
        patchWorkspacePane(pane.history_id, { archived: !archived });
        pushToast("error", (error as Error).message);
      }
    },
    [pushToast],
  );

  const confirmClose = useCallback(async () => {
    if (!pendingClose) return;
    setClosing(true);
    try {
      await closeTerminal(pendingClose.name, pendingClose.workspace_id);
      dropWorkspacePane(pendingClose.history_id);
      setPendingClose(null);
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setClosing(false);
    }
  }, [pendingClose, pushToast]);

  return (
    <div
      data-testid="workspace-chats"
      // A framed panel with a heading, not the first rows of the column: the
      // workspaces are one area and the sections under them are another, and
      // the frame is the edge between the two. `shrink-0` because the panel
      // is a child of the sidebar's flex scroll body: an `overflow-hidden`
      // flex item may shrink to nothing when the column is taller than the
      // window, and this one did — a two-pixel line where the list should be.
      className="mx-2 mt-2 shrink-0 overflow-hidden rounded-lg border border-border/60 bg-card/40"
    >
      <div className="flex items-center justify-between px-3 pb-1 pt-2">
        <span className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
          {t("ide_chats.heading")}
        </span>
        {workspaces.length > 0 && (
          <span
            data-testid="workspace-chats-count"
            className="font-mono text-[10px] tabular-nums text-muted-foreground/60"
          >
            {workspaces.length}
          </span>
        )}
      </div>
      <div className="px-1 pb-2">
        {workspaces.length === 0 ? (
          <p className="px-3 py-2 text-[11px] text-muted-foreground/70">
            {t("ide_chats.no_workspaces")}
          </p>
        ) : (
          workspaces.map((workspace, index) => {
            const mine = panes.filter((pane) => pane.workspace_id === workspace.id);
            return (
              <WorkspaceBand
                key={workspace.id}
                index={index + 1}
                workspace={workspace}
                panes={mine.filter((pane) => !pane.archived)}
                archived={mine.filter((pane) => pane.archived)}
                stagedPane={workspace.active ? stagedPane : null}
                agents={agents}
                onOpenPane={(pane) => requestPane(workspace.id, pane)}
                onOpenWorkspace={() => requestWorkspace(workspace.id)}
                onNewChat={() => requestNewChat(workspace.id)}
                onMenu={openMenu}
              />
            );
          })
        )}
      </div>
      {/* The panel's foot: the two ways to add a workspace, on their own
          shelf under a rule so they are not read as rows of the last band. */}
      <div className="border-t border-border/50 px-1 py-1">
        {/* One more project.
            First of the two closing rows and in reading ink rather than muted,
            because opening a second folder is the ordinary next thing to want
            from a list of workspaces. Until now this list could not say it at
            all: the "+" that opens a workspace lives in the workspace bar,
            which is the surface chat mode replaces, so from inside chat there
            was no way to start one (maintainer report 2026-08-27). It opens
            the same launcher the bar's "+" does — folder, pane count and
            agents chosen exactly as they are from the grid, rather than a
            second, thinner way of doing it that would drift from the first. */}
        <button
          type="button"
          onClick={requestAddWorkspace}
          data-testid="workspace-chats-new-workspace"
          className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-background/60"
        >
          <FolderPlus aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          {t("ide_chats.new_workspace")}
        </button>
        {/* A workspace with no project folder — the scratch session the grid's
            rail used to offer. Last, quiet: a fresh folder is the usual ask. */}
        <button
          type="button"
          onClick={requestSession}
          data-testid="workspace-chats-new-session"
          className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground"
        >
          <Plus aria-hidden className="h-3.5 w-3.5 shrink-0" />
          {t("ide_chats.new_session")}
        </button>
      </div>
      {menu && (
        <SessionMenu
          pane={menu.pane}
          x={menu.x}
          y={menu.y}
          onDismiss={() => setMenu(null)}
          onArchive={() => {
            const { pane } = menu;
            setMenu(null);
            void setArchived(pane, !pane.archived);
          }}
          onClose={() => {
            setPendingClose(menu.pane);
            setMenu(null);
          }}
        />
      )}
      {pendingClose && (
        <ConfirmCloseTerminal
          pane={pendingClose}
          busy={closing}
          onCancel={() => {
            if (!closing) setPendingClose(null);
          }}
          onConfirm={() => void confirmClose()}
        />
      )}
    </div>
  );
}

/**
 * One workspace: its number and state, its folder, its sessions, its "+".
 *
 * The folder is drawn under the band rather than in it because that is how
 * the maintainer reads the list — "Workspace 2, then the folder Personal
 * Jarvis" — and because two workspaces can be open on the SAME folder, so
 * the folder alone would not tell them apart.
 */
function WorkspaceBand({
  index,
  workspace,
  panes,
  archived,
  stagedPane,
  agents,
  onOpenPane,
  onOpenWorkspace,
  onNewChat,
  onMenu,
}: {
  index: number;
  workspace: IdeWorkspaceRow;
  panes: WorkspacePaneRow[];
  archived: WorkspacePaneRow[];
  stagedPane: string | null;
  agents: SplitAgentChoice[];
  onOpenPane: (pane: string) => void;
  onOpenWorkspace: () => void;
  onNewChat: () => void;
  onMenu: (pane: WorkspacePaneRow, x: number, y: number) => void;
}) {
  const t = useT();
  const label = workspace.name || folderLeaf(workspace.folder) || t("ide_chats.no_folder");

  return (
    <section
      data-testid="workspace-chats-band"
      data-workspace={workspace.id}
      data-active={workspace.active ? "true" : "false"}
      className="mb-1"
    >
      <div className="flex items-center gap-2 px-3 pb-1 pt-3">
        <span
          aria-hidden
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            workspace.active ? "bg-primary" : "bg-muted-foreground/30",
          )}
        />
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">
          {fill(t("ide_chats.workspace_band"), { n: index })}
        </span>
        {workspace.active && (
          <span
            data-testid="workspace-chats-active"
            className="rounded bg-primary/15 px-1.5 py-px text-[10px] font-medium text-primary"
          >
            {t("ide_chats.active")}
          </span>
        )}
      </div>

      <div className="group/folder relative">
        <button
          type="button"
          onClick={onOpenWorkspace}
          title={workspace.folder || t("ide_chats.no_folder")}
          data-testid="workspace-chats-folder"
          className={cn(
            "flex w-full items-center gap-1.5 rounded-md py-1.5 pl-2 pr-8 text-left transition-colors",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            workspace.active ? "text-foreground" : "hover:bg-background/60",
          )}
        >
          <Folder
            aria-hidden
            className="h-3.5 w-3.5 shrink-0"
            style={{ color: folderColor(workspace.folder || workspace.name) }}
          />
          <span className="min-w-0 flex-1 truncate text-xs font-medium">{label}</span>
        </button>
        {/*
            One click, no menu: a new chat here opens an EMPTY chat window and
            the coding agent is chosen in its composer, beside the model, the
            effort and the permission stance (maintainer report, 2026-08-27).
            The menu that used to hang off this button asked the same question
            in a worse place — first, alone, and unchangeable afterwards — so
            it is gone from this surface. The grid's split menus keep it:
            there a pane is a pane, and asking four questions before placing a
            terminal would be four too many.
        */}
        <button
          type="button"
          onClick={onNewChat}
          title={t("ide_chats.new_chat")}
          aria-label={t("ide_chats.new_chat")}
          data-testid={`workspace-chats-new-terminal-${workspace.id}`}
          className="absolute right-1 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-background/60 hover:text-foreground focus-visible:opacity-100 group-hover/folder:opacity-100"
        >
          <Plus className="h-3 w-3" />
        </button>
      </div>

      {panes.length === 0 && archived.length === 0 ? (
        <p className="pl-8 pr-2 text-[11px] text-muted-foreground/50">{t("ide_chats.no_sessions")}</p>
      ) : (
        // The rows hang off the folder on a thin guide line drawn at the
        // centre of the folder's icon, so "this folder, these sessions"
        // is visible as a tree and not inferred from an indent.
        <ul className="relative space-y-px before:pointer-events-none before:absolute before:bottom-1 before:left-[15px] before:top-0 before:w-px before:bg-border/70 before:content-['']">
          {panes.map((pane) => (
            <SessionRow
              key={pane.history_id}
              pane={pane}
              active={pane.name === stagedPane}
              logoUrl={agents.find((agent) => agent.name === pane.agent)?.logoUrl}
              onOpen={() => onOpenPane(pane.name)}
              onMenu={onMenu}
            />
          ))}
        </ul>
      )}
      {archived.length > 0 && (
        <ArchivedChats
          workspaceId={workspace.id}
          panes={archived}
          stagedPane={stagedPane}
          agents={agents}
          onOpenPane={onOpenPane}
          onMenu={onMenu}
        />
      )}
    </section>
  );
}

/**
 * One coding session of a workspace.
 *
 * Same height and indent as a chat row anywhere else in the app, because from
 * where the user sits these are the same kind of thing: a conversation with
 * an agent. The mark is the CLI's logo, the label is what the conversation is
 * ABOUT — the pane's title, the same sentence its grid header wears (see
 * `sessionTitle`) — the call-sign says which pane, and the badge at the end
 * is the grid's own activity pill: still working, finished, never asked, or
 * holding a question for you. The CLI's name moved into the tooltip: nine
 * rows all reading "Claude Code" under nine Claude logos said the same thing
 * twice and the useful thing never.
 */
function SessionRow({
  pane,
  active,
  logoUrl,
  onOpen,
  onMenu,
}: {
  pane: WorkspacePaneRow;
  active: boolean;
  logoUrl?: string;
  onOpen: () => void;
  onMenu: (pane: WorkspacePaneRow, x: number, y: number) => void;
}) {
  const label = sessionTitle(pane);
  const cli = pane.display_name || pane.agent;
  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        onContextMenu={(event) => {
          // The app-wide Cut/Copy/Paste menu lives on document. Stop this
          // click here so a session row never offers paste over an action
          // that is actually about the chat.
          event.preventDefault();
          event.stopPropagation();
          onMenu(pane, event.clientX, event.clientY);
        }}
        title={`${label} · ${cli} · ${pane.name}`}
        data-testid="workspace-session-row"
        data-pane={pane.name}
        data-status={pane.status}
        data-activity={pane.activity}
        data-archived={pane.archived ? "true" : "false"}
        className={cn(
          "flex w-full items-start gap-2 rounded-lg py-1.5 pl-8 pr-2 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          // The staged one wears the same yellow edge as the active nav row —
          // one way of saying "this is where you are" for the whole column.
          active
            ? "bg-card text-foreground shadow-[inset_2px_0_0_hsl(var(--primary))]"
            : "hover:bg-background/60",
        )}
      >
        <AgentMark
          agent={pane.agent}
          label={cli}
          logoUrl={logoUrl}
          variant="plain"
          size="sm"
          className="mt-0.5"
        />
        <span
          className="min-w-0 flex-1 line-clamp-2 text-xs text-foreground"
          data-testid="workspace-session-title"
        >
          {label}
        </span>
        <span className="mt-0.5 shrink-0 truncate font-mono text-[10px] text-muted-foreground/70">
          {pane.name}
        </span>
        {/* The grid's own badge, not a second reading of the same facts.
            The dot this row used to draw was amber for every live pane and
            pulsed for a working one — and at six pixels a slow pulse and a
            still dot are the same silhouette, so twelve sessions in twelve
            states read as twelve identical dots (maintainer report
            2026-08-27). The pill tells busy from finished by SHAPE: a turning
            spinner while the agent works, a check mark once it has finished, a
            hollow ring for a pane nobody has asked anything, a beacon for one
            holding a question. Same component as the pane's header, so the
            list and the grid can never disagree about one pane — and both
            follow the socket's word the moment the backend decides it (see
            the store), so the row changes with the pane, not with the poll. */}
        <span className="mt-0.5 shrink-0">
          <PaneActivityPill
            status={pane.status}
            activity={pane.activity}
            since={pane.activity_since}
            worked={pane.worked}
          />
        </span>
      </button>
    </li>
  );
}

/**
 * The chats taken off the main list, still findable under the workspace.
 *
 * Folded by default so a cleaned-up list stays a cleaned-up list. Expanding
 * it is how an archived chat is found again; restoring it (from the same
 * right-click menu) puts it back among the live rows.
 */
function ArchivedChats({
  workspaceId,
  panes,
  stagedPane,
  agents,
  onOpenPane,
  onMenu,
}: {
  workspaceId: string;
  panes: WorkspacePaneRow[];
  stagedPane: string | null;
  agents: SplitAgentChoice[];
  onOpenPane: (pane: string) => void;
  onMenu: (pane: WorkspacePaneRow, x: number, y: number) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const visible = query.trim()
    ? panes.filter((pane) => {
        const haystack = `${sessionTitle(pane)} ${pane.display_name} ${pane.name}`.toLowerCase();
        return haystack.includes(query.trim().toLowerCase());
      })
    : panes;

  return (
    <div className="mt-1" data-testid="workspace-chats-archived" data-workspace={workspaceId}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        data-testid={`workspace-chats-archived-toggle-${workspaceId}`}
        className="flex w-full items-center gap-2 rounded-md py-1.5 pl-8 pr-2 text-left text-[11px] text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground"
      >
        <Archive aria-hidden className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate">
          {fill(t("ide_chats.archived_count"), { n: panes.length })}
        </span>
      </button>
      {open && (
        <div className="pb-1">
          {panes.length >= ARCHIVED_SEARCH_AT && (
            <div className="relative mx-2 mb-1">
              <Search
                aria-hidden
                className="pointer-events-none absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground"
              />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("ide_chats.archived_search")}
                aria-label={t("ide_chats.archived_search")}
                data-testid="workspace-chats-archived-search"
                className="w-full rounded-md border border-border bg-background/60 py-1 pl-7 pr-2 text-[11px] text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
            </div>
          )}
          <ul className="space-y-px">
            {visible.map((pane) => (
              <SessionRow
                key={pane.history_id}
                pane={pane}
                active={pane.name === stagedPane}
                logoUrl={agents.find((agent) => agent.name === pane.agent)?.logoUrl}
                onOpen={() => onOpenPane(pane.name)}
                onMenu={onMenu}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function SessionMenu({
  pane,
  x,
  y,
  onDismiss,
  onArchive,
  onClose,
}: {
  pane: WorkspacePaneRow;
  x: number;
  y: number;
  onDismiss: () => void;
  onArchive: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onDismiss();
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) onDismiss();
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", onDismiss);
    window.addEventListener("blur", onDismiss);
    document.addEventListener("scroll", onDismiss, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", onDismiss);
      window.removeEventListener("blur", onDismiss);
      document.removeEventListener("scroll", onDismiss, true);
    };
  }, [onDismiss]);

  useLayoutEffect(() => {
    const node = menuRef.current;
    if (!node) return;
    const { width, height } = node.getBoundingClientRect();
    const maxX = window.innerWidth - width - VIEWPORT_MARGIN;
    const maxY = window.innerHeight - height - VIEWPORT_MARGIN;
    node.style.left = `${Math.max(VIEWPORT_MARGIN, Math.min(x, maxX))}px`;
    node.style.top = `${Math.max(VIEWPORT_MARGIN, Math.min(y, maxY))}px`;
    node.style.visibility = "visible";
    node.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
  }, [x, y]);

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [],
    );
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    const next = (current + step + items.length) % items.length;
    items[next]?.focus();
  };

  const archived = pane.archived;

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={t("ide_chats.menu_aria")}
      data-testid="workspace-session-menu"
      data-pane={pane.name}
      onKeyDown={onMenuKeyDown}
      style={{ width: MENU_WIDTH, visibility: "hidden" }}
      className="fixed z-[100] overflow-hidden rounded-md border border-border bg-background py-1 shadow-lg"
    >
      <button
        type="button"
        role="menuitem"
        data-testid="workspace-session-archive"
        title={archived ? t("ide_chats.restore_hint") : t("ide_chats.archive_hint")}
        onClick={onArchive}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-muted"
      >
        {archived ? (
          <RotateCcw aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <Archive aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        {archived ? t("ide_chats.restore") : t("ide_chats.archive")}
      </button>
      <button
        type="button"
        role="menuitem"
        data-testid="workspace-session-close"
        title={t("ide_chats.close_hint")}
        onClick={onClose}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-destructive transition-colors hover:bg-destructive/10"
      >
        <Trash2 aria-hidden className="h-3.5 w-3.5 shrink-0" />
        {t("ide_chats.close_terminal")}
      </button>
    </div>,
    document.body,
  );
}

function ConfirmCloseTerminal({
  pane,
  busy,
  onCancel,
  onConfirm,
}: {
  pane: WorkspacePaneRow;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={fill(t("ide_chats.close_title"), { name: pane.name })}
      data-testid="workspace-chats-confirm-close"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-background/70 p-6 backdrop-blur-sm"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) onCancel();
      }}
    >
      <div className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-xl">
        <h3 className="font-display text-base font-semibold">
          {fill(t("ide_chats.close_title"), { name: pane.name })}
        </h3>
        <p className="mt-2 text-sm text-muted-foreground">{t("ide_chats.close_body")}</p>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            className="rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            autoFocus
            disabled={busy}
            onClick={onCancel}
          >
            {t("ide_chats.close_keep")}
          </button>
          <button
            type="button"
            data-testid="workspace-chats-confirm-close-confirm"
            className="rounded-lg bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            disabled={busy}
            onClick={onConfirm}
          >
            {fill(t("ide_chats.close_confirm"), { name: pane.name })}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
