/**
 * The quick switcher — Ctrl+Space anywhere (or the search bar at the top of
 * the sidebar), type where you want to go, Enter.
 *
 * Modelled on the Mac's Spotlight bar: one large field floating high on the
 * screen over a frosted panel, results directly underneath with the first one
 * already selected, the app still visible behind it (no dimming scrim). What it
 * lists and how it ranks lives in `@/lib/quickSwitch` (sections) and
 * `@/lib/quickSwitchSources` (chats, terminals, workspaces); this file draws it
 * and fetches the live lists once per opening.
 *
 * cmdk drives the keyboard (arrows, Enter, the active row) with its own
 * filtering switched OFF — the ranking is ours and must not be re-scored
 * (the same reason WikiSearch turns it off). Radix Dialog owns Escape, the
 * focus trap and handing focus back to the pane or field you came from.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import {
  CornerDownLeft,
  FolderOpen,
  MessageSquare,
  Mic,
  Search,
  Settings2,
  SquareTerminal,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useSettingsJump } from "@/store/settingsJump";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { formatChatWhen, useChatRows, type ChatRow } from "@/components/home/chatRows";
import {
  fetchIdeProjects,
  fetchWorkspacePanes,
  type ProjectWorkspace,
  type WorkspacePaneRow,
} from "@/lib/agenticIdeApi";
import {
  ambiguousEntryKeys,
  rankQuickSwitch,
  strongSettingsMatches,
  type QuickSwitchEntry,
} from "@/lib/quickSwitch";
import { folderName, rankItems } from "@/lib/quickSwitchSources";
import { searchSettingsOptions } from "@/views/settings/settingsSearch";
import { cn } from "@/lib/utils";

/** Resolve a label, falling back when the key is missing (the resolver echoes it). */
function useLabel() {
  const t = useT();
  return (key: string, fallback: string) => {
    const value = t(key);
    return value === key ? fallback : value;
  };
}

const ROW = cn(
  "group flex h-11 cursor-default select-none items-center gap-3 rounded-lg px-2.5",
  "text-base text-foreground outline-none",
  "data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground",
);

const TILE = cn(
  "grid h-7 w-7 shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground",
  "group-data-[selected=true]:bg-white/20 group-data-[selected=true]:text-accent-foreground",
);

const DETAIL = cn(
  "ml-auto shrink-0 truncate pl-3 text-sm text-muted-foreground",
  "group-data-[selected=true]:text-accent-foreground/80",
);

const GROUP = cn(
  "[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2.5",
  "[&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium",
  "[&_[cmdk-group-heading]]:text-muted-foreground",
);

/** How many live results each group may show, so the sections stay in view. */
const LIMIT = { chats: 6, terminals: 6, workspaces: 4, recentChats: 4 } as const;

/** One result row: icon tile, a label that truncates, a quiet detail on the right. */
function ResultRow({
  value,
  testId,
  icon: Icon,
  label,
  detail,
  onSelect,
}: {
  value: string;
  testId: string;
  icon: LucideIcon;
  label: ReactNode;
  detail?: ReactNode;
  onSelect: () => void;
}) {
  return (
    <Command.Item value={value} onSelect={onSelect} data-testid={testId} className={ROW}>
      <span className={TILE}>
        <Icon className="h-4 w-4" aria-hidden />
      </span>
      <span className="min-w-0 truncate">{label}</span>
      {detail ? <span className={DETAIL}>{detail}</span> : null}
    </Command.Item>
  );
}

/**
 * The open workspaces and their panes, fetched once each time the switcher
 * opens. A headless or older backend simply contributes nothing — the
 * switcher must never fail to open because the IDE is not there.
 */
function useIdeResults(): { panes: WorkspacePaneRow[]; workspaces: ProjectWorkspace[] } {
  const [panes, setPanes] = useState<WorkspacePaneRow[]>([]);
  const [workspaces, setWorkspaces] = useState<ProjectWorkspace[]>([]);
  useEffect(() => {
    let live = true;
    fetchWorkspacePanes()
      .then((found) => {
        if (live) setPanes(found.panes.filter((pane) => !pane.archived));
      })
      .catch(() => {
        /* no IDE on this backend — no terminal results */
      });
    fetchIdeProjects()
      .then((found) => {
        if (!live) return;
        const open = found.projects.flatMap((project) =>
          project.workspaces.filter((workspace) => workspace.status === "open"),
        );
        setWorkspaces(open.sort((a, b) => b.last_active_at - a.last_active_at));
      })
      .catch(() => {
        /* no IDE on this backend — no workspace results */
      });
    return () => {
      live = false;
    };
  }, []);
  return { panes, workspaces };
}

/** What a terminal row is called: its title, else its last prompt, else its agent. */
function paneTitle(pane: WorkspacePaneRow): string {
  return pane.recap || pane.last_prompt || pane.display_name || pane.key;
}

export function QuickSwitcher({
  open,
  onOpenChange,
  initialQuery = "",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Text already typed into the sidebar search bar that opened this. */
  initialQuery?: string;
}) {
  const t = useT();
  const label = useLabel();
  const language = useUiLanguage();
  const activeSection = useEventStore((s) => s.activeSection);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const setSurface = useHomeStore((s) => s.setSurface);
  const requestSettingsJump = useSettingsJump((s) => s.request);
  const activateWorkspace = useIdeProjectsStore((s) => s.activateWorkspace);
  const setSpotlight = useIdeSidePanelStore((s) => s.setSpotlight);
  const [query, setQuery] = useState(initialQuery);
  const { rows: chatRows, open: openChat } = useChatRows();
  const { panes, workspaces } = useIdeResults();
  const typed = query.trim() !== "";

  // About forty destinations — ranked inline on every keystroke, no memo needed.
  const labelFor = (item: QuickSwitchEntry) => label(item.labelKey, item.fallbackLabel);
  const ranked = rankQuickSwitch(query, labelFor);
  const ambiguous = ambiguousEntryKeys(labelFor);
  const settingsMatches = useMemo(
    () => strongSettingsMatches(query, searchSettingsOptions(language, query, t)),
    [query, language, t],
  );
  // Nothing typed yet: the most recent chats, like Spotlight's recents.
  const chatHits = typed
    ? rankItems(query, chatRows, (row) => [row.title, row.preview], LIMIT.chats)
    : chatRows.slice(0, LIMIT.recentChats);
  const paneHits = typed
    ? rankItems(
        query,
        panes,
        (pane) => [pane.recap, pane.last_prompt, pane.display_name, pane.key, pane.workspace_name],
        LIMIT.terminals,
      )
    : [];
  const workspaceHits = typed
    ? rankItems(
        query,
        workspaces,
        (workspace) => [workspace.name, folderName(workspace.folder), workspace.branch],
        LIMIT.workspaces,
      )
    : [];

  const close = () => {
    onOpenChange(false);
    setQuery("");
  };

  const go = (item: QuickSwitchEntry) => {
    if (item.surface) setSurface(item.surface);
    setActiveSection(item.section);
    close();
  };

  const goToChat = (row: ChatRow) => {
    openChat(row);
    close();
  };

  /** Bring the pane's workspace to the front and frame the pane in the grid. */
  const goToPane = (pane: WorkspacePaneRow) => {
    setActiveSection("agentic-ide");
    if (!pane.workspace_active) activateWorkspace(pane.workspace_id);
    setSpotlight({ workspaceId: pane.workspace_id, pane: pane.name });
    close();
  };

  const goToWorkspace = (workspace: ProjectWorkspace) => {
    setActiveSection("agentic-ide");
    if (!workspace.active) activateWorkspace(workspace.id);
    close();
  };

  const goToSetting = (groupId: string) => {
    requestSettingsJump(groupId);
    setActiveSection("settings");
    close();
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) setQuery("");
        onOpenChange(next);
      }}
    >
      <Dialog.Portal>
        {/* Spotlight never dims the desktop: the overlay only catches the
            click that closes it. */}
        <Dialog.Overlay className="fixed inset-0 z-[80]" />
        <Dialog.Content
          data-testid="quick-switcher"
          aria-describedby={undefined}
          className={cn(
            "fixed left-1/2 top-[18%] z-[90] w-[min(640px,calc(100vw-2rem))] -translate-x-1/2",
            "overflow-hidden rounded-2xl border border-border shadow-float",
            "backdrop-blur-2xl backdrop-saturate-150",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            "motion-reduce:animate-none",
          )}
          style={{ backgroundColor: "hsl(var(--popover) / 0.86)" }}
        >
          <Dialog.Title className="sr-only">{t("quick_switch.title")}</Dialog.Title>
          <Command shouldFilter={false} loop label={t("quick_switch.title")}>
            <div className="flex items-center gap-3 px-4">
              <Search className="h-6 w-6 shrink-0 text-muted-foreground" aria-hidden />
              <Command.Input
                autoFocus
                value={query}
                onValueChange={setQuery}
                placeholder={t("quick_switch.placeholder")}
                data-testid="quick-switcher-input"
                className="h-14 flex-1 bg-transparent text-2xl font-light text-foreground outline-none placeholder:text-muted-foreground"
              />
            </div>
            <Command.List className="max-h-[min(30rem,62dvh)] overflow-y-auto border-t border-border p-1.5 scrollbar-jarvis">
              <Command.Empty className="px-3 py-6 text-center text-base text-muted-foreground">
                {t("quick_switch.no_results")}
              </Command.Empty>
              {ranked.length > 0 && (
                <Command.Group heading={t("quick_switch.group_sections")} className={GROUP}>
                  {ranked.map(({ entry: item, label: text }) => {
                    const here = item.section === activeSection && !item.surface;
                    const area = item.parentLabelKey ? label(item.parentLabelKey, "") : "";
                    // A label two rows share names its area up front, e.g.
                    // "Voice › API Keys" beside the API Keys page itself.
                    const shown = area && ambiguous.has(item.key) ? `${area} › ${text}` : text;
                    return (
                      <ResultRow
                        key={item.key}
                        value={`section:${item.key}`}
                        testId={`quick-switch-${item.key}`}
                        icon={item.icon}
                        label={shown}
                        detail={here ? t("quick_switch.current") : shown === text ? area : ""}
                        onSelect={() => go(item)}
                      />
                    );
                  })}
                </Command.Group>
              )}
              {chatHits.length > 0 && (
                <Command.Group
                  heading={t(typed ? "quick_switch.group_chats" : "quick_switch.group_recent_chats")}
                  className={GROUP}
                >
                  {chatHits.map((row) => (
                    <ResultRow
                      key={`${row.kind}:${row.id}`}
                      value={`chat:${row.kind}:${row.id}`}
                      testId={`quick-switch-chat-${row.id}`}
                      icon={row.kind === "voice" ? Mic : MessageSquare}
                      label={row.title || t("quick_switch.untitled_chat")}
                      detail={formatChatWhen(row.updatedMs)}
                      onSelect={() => goToChat(row)}
                    />
                  ))}
                </Command.Group>
              )}
              {paneHits.length > 0 && (
                <Command.Group heading={t("quick_switch.group_terminals")} className={GROUP}>
                  {paneHits.map((pane) => (
                    <ResultRow
                      key={`${pane.workspace_id}:${pane.name}`}
                      value={`pane:${pane.workspace_id}:${pane.name}`}
                      testId={`quick-switch-pane-${pane.workspace_id}-${pane.name}`}
                      icon={SquareTerminal}
                      label={paneTitle(pane)}
                      detail={`${pane.key} · ${pane.workspace_name}`}
                      onSelect={() => goToPane(pane)}
                    />
                  ))}
                </Command.Group>
              )}
              {workspaceHits.length > 0 && (
                <Command.Group heading={t("quick_switch.group_workspaces")} className={GROUP}>
                  {workspaceHits.map((workspace) => (
                    <ResultRow
                      key={workspace.id}
                      value={`workspace:${workspace.id}`}
                      testId={`quick-switch-workspace-${workspace.id}`}
                      icon={FolderOpen}
                      label={workspace.name}
                      detail={workspace.branch || folderName(workspace.folder)}
                      onSelect={() => goToWorkspace(workspace)}
                    />
                  ))}
                </Command.Group>
              )}
              {settingsMatches.length > 0 && (
                <Command.Group heading={t("quick_switch.group_settings")} className={GROUP}>
                  {settingsMatches.map((match) => (
                    <ResultRow
                      key={match.id}
                      value={`setting:${match.id}`}
                      testId={`quick-switch-setting-${match.id}`}
                      icon={Settings2}
                      label={match.label}
                      detail={label("nav.settings", "Settings")}
                      onSelect={() => goToSetting(match.id)}
                    />
                  ))}
                </Command.Group>
              )}
            </Command.List>
            <div className="flex items-center justify-end gap-4 border-t border-border px-4 py-2 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <kbd className="inline-flex h-5 items-center rounded border border-border px-1 font-sans">
                  <CornerDownLeft className="h-3 w-3" aria-hidden />
                </kbd>
                {t("quick_switch.hint_open")}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <kbd className="inline-flex h-5 items-center rounded border border-border px-1 font-sans">esc</kbd>
                {t("quick_switch.hint_close")}
              </span>
            </div>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
