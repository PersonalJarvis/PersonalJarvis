import { useRef, useState } from "react";
import { ChevronLeft, Folder, FolderPlus, Plus } from "lucide-react";

import {
  AgentPickerMenu,
  offersAgentChoice,
  type SplitAgentChoice,
} from "@/components/agentic/AgentPicker";
import { AgentMark } from "@/components/agentic/AgentMark";
import { folderColor } from "@/components/agentic/folderColor";
import { sessionTitle } from "@/components/agentic/sessionTitle";
import { useIdeChatStore, type IdeWorkspaceRow } from "@/store/ideChat";
import { useWorkspacePanes } from "@/store/workspacePanes";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { folderLeaf } from "@/lib/folderPath";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";

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
 * The way back is the first thing in the column, not a hidden gesture: a
 * sidebar that swallows the navigation with no visible exit is a trap, so the
 * "Sections" button sits at the top where a back button belongs.
 */
export function WorkspaceChats() {
  const t = useT();
  const workspaces = useIdeChatStore((s) => s.workspaces);
  const agents = useIdeChatStore((s) => s.agents);
  const setSidebarFace = useIdeChatStore((s) => s.setSidebarFace);
  const requestPane = useIdeChatStore((s) => s.requestPane);
  const requestTerminal = useIdeChatStore((s) => s.requestTerminal);
  const requestWorkspace = useIdeChatStore((s) => s.requestWorkspace);
  const requestSession = useIdeChatStore((s) => s.requestSession);
  const requestAddWorkspace = useIdeChatStore((s) => s.requestAddWorkspace);
  const stagedPane = useIdeChatStore((s) => s.stagedPane);
  // Shares one poll with every other reader of the list (see the store).
  const panes = useWorkspacePanes();

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="workspace-chats">
      <div className="shrink-0 px-2 pb-2 pt-2">
        <button
          type="button"
          data-testid="workspace-chats-back"
          onClick={() => setSidebarFace("sections")}
          className="flex w-full items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <ChevronLeft aria-hidden className="h-3.5 w-3.5 text-muted-foreground" />
          {t("ide_chats.back_to_sections")}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-1 pb-3">
        {workspaces.length === 0 ? (
          <p className="px-3 py-2 text-[11px] text-muted-foreground/70">
            {t("ide_chats.no_workspaces")}
          </p>
        ) : (
          workspaces.map((workspace, index) => (
            <WorkspaceBand
              key={workspace.id}
              index={index + 1}
              workspace={workspace}
              panes={panes.filter((pane) => pane.workspace_id === workspace.id)}
              stagedPane={workspace.active ? stagedPane : null}
              agents={agents}
              onOpenPane={(pane) => requestPane(workspace.id, pane)}
              onOpenWorkspace={() => requestWorkspace(workspace.id)}
              onNewTerminal={(agent) => requestTerminal(workspace.id, agent)}
            />
          ))
        )}
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
          className="mt-3 flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-background/60"
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
  stagedPane,
  agents,
  onOpenPane,
  onOpenWorkspace,
  onNewTerminal,
}: {
  index: number;
  workspace: IdeWorkspaceRow;
  panes: WorkspacePaneRow[];
  stagedPane: string | null;
  agents: SplitAgentChoice[];
  onOpenPane: (pane: string) => void;
  onOpenWorkspace: () => void;
  onNewTerminal: (agent?: string) => void;
}) {
  const t = useT();
  const [picking, setPicking] = useState(false);
  /*
   * The "+" the menu hangs off.
   *
   * Handing the picker an anchor is what detaches it into a portal, and this
   * surface needs that for two separate reasons. The list scrolls
   * (`overflow-y-auto`), so a menu drawn inside it is CLIPPED at the column's
   * edge — a workspace near the bottom would show a sliver of the first entry
   * and nothing else. And a menu placed inside its caller has to be given a
   * z-index by that caller, which is exactly how this menu became unclickable:
   * the class said `z-30`, tailwind-merge replaced the picker's own `z-50`
   * with it, and the picker's full-window dismiss layer (`z-40`) ended up ON
   * TOP of the list. Every click on a CLI hit that layer instead — the menu
   * closed and no terminal opened (maintainer report 2026-08-27). Detached,
   * the picker owns its own stacking and there is no number here to get wrong.
   */
  const plusRef = useRef<HTMLButtonElement | null>(null);
  const label = workspace.name || folderLeaf(workspace.folder) || t("ide_chats.no_folder");
  const openTerminal = () => {
    if (offersAgentChoice(agents)) setPicking((current) => !current);
    else onNewTerminal();
  };

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
        <button
          type="button"
          onClick={openTerminal}
          title={t("ide_chats.new_terminal")}
          aria-label={t("ide_chats.new_terminal")}
          aria-expanded={picking}
          data-testid={`workspace-chats-new-terminal-${workspace.id}`}
          ref={plusRef}
          className={cn(
            "absolute right-1 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-muted-foreground transition-opacity hover:bg-background/60 hover:text-foreground focus-visible:opacity-100 group-hover/folder:opacity-100",
            // Stays lit while its own menu is open: the pointer leaves the row
            // to reach the list, and a button that faded out under it would
            // leave the menu hanging off nothing.
            picking ? "text-foreground opacity-100" : "opacity-0",
          )}
        >
          <Plus className="h-3 w-3" />
        </button>
        {picking && (
          <AgentPickerMenu
            title={t("ide_chats.pick_cli_title")}
            ariaLabel={t("ide_chats.pick_cli_aria")}
            agents={agents}
            testId={`workspace-chats-agent-menu-${workspace.id}`}
            itemTestId={(agent) => `workspace-chats-new-${workspace.id}-${agent}`}
            anchorTo={plusRef.current}
            onDismiss={() => setPicking(false)}
            onPick={(agent) => {
              setPicking(false);
              onNewTerminal(agent);
            }}
          />
        )}
      </div>

      {panes.length === 0 ? (
        <p className="pl-8 pr-2 text-[11px] text-muted-foreground/50">{t("ide_chats.no_sessions")}</p>
      ) : (
        <ul className="space-y-px">
          {panes.map((pane) => (
            <SessionRow
              key={pane.history_id}
              pane={pane}
              active={pane.name === stagedPane}
              logoUrl={agents.find((agent) => agent.name === pane.agent)?.logoUrl}
              onOpen={() => onOpenPane(pane.name)}
            />
          ))}
        </ul>
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
 * `sessionTitle`) — the call-sign says which pane, and the dot carries the
 * state the grid's badge shows: running, waiting to start, or finished. The
 * CLI's name moved into the tooltip: nine rows all reading "Claude Code" under
 * nine Claude logos said the same thing twice and the useful thing never.
 */
function SessionRow({
  pane,
  active,
  logoUrl,
  onOpen,
}: {
  pane: WorkspacePaneRow;
  active: boolean;
  logoUrl?: string;
  onOpen: () => void;
}) {
  const label = sessionTitle(pane);
  const cli = pane.display_name || pane.agent;
  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        title={`${label} · ${cli} · ${pane.name}`}
        data-testid="workspace-session-row"
        data-pane={pane.name}
        data-status={pane.status}
        className={cn(
          "flex w-full items-center gap-2 rounded-lg py-1.5 pl-8 pr-2 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          active ? "bg-card text-foreground shadow-sm" : "hover:bg-background/60",
        )}
      >
        <AgentMark agent={pane.agent} label={cli} logoUrl={logoUrl} variant="plain" size="sm" />
        <span
          className="min-w-0 flex-1 truncate text-xs text-foreground"
          data-testid="workspace-session-title"
        >
          {label}
        </span>
        <span className="shrink-0 truncate font-mono text-[10px] text-muted-foreground/70">
          {pane.name}
        </span>
        <span
          aria-hidden
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            pane.status === "live"
              ? pane.activity === "working" || pane.activity === "starting"
                ? "bg-primary motion-safe:animate-pulse"
                : "bg-primary"
              : pane.status === "pending"
                ? "bg-muted-foreground/50"
                : pane.status === "error"
                  ? "bg-destructive"
                  : "bg-muted-foreground/30",
          )}
        />
      </button>
    </li>
  );
}
