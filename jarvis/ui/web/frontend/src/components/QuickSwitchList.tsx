/**
 * The quick switcher's results — sections, chats, terminals, workspaces and
 * Settings groups — as one cmdk list, shared by its two shells: the centred
 * Spotlight window (`QuickSwitcher`, the chord) and the sidebar search field
 * (`InlineQuickSwitch`), which drops the same list down under itself.
 *
 * Must render inside a cmdk `<Command shouldFilter={false}>` whose input feeds
 * `query`: cmdk drives the keyboard (arrows, Enter, the active row) and the
 * ranking is ours (`@/lib/quickSwitch` for sections, `@/lib/quickSwitchSources`
 * for the live lists) and must not be re-scored. The live lists are fetched
 * once per mount, so the callers mount this only while it is shown.
 */
import { Command } from "cmdk";
import type { LucideIcon } from "lucide-react";
import {
  ChatIcon,
  FolderIcon,
  SettingsIcon,
  TerminalIcon,
  VoiceIcon,
} from "@/components/icons/sectionIcons";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useSettingsJump } from "@/store/settingsJump";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { chatRowLabel, formatChatWhen, useChatRows, type ChatRow } from "@/components/home/chatRows";
import {
  fetchIdeProjects,
  fetchWorkspacePanes,
  type ProjectWorkspace,
  type WorkspacePaneRow,
} from "@/lib/agenticIdeApi";
import {
  ambiguousEntryKeys,
  compareMatches,
  labelStartsWith,
  normalizeQuery,
  SHORT_QUERY,
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
  "group flex cursor-default select-none items-center gap-3 rounded-lg px-2.5",
  "text-foreground outline-none",
  "data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground",
);

const TILE = cn(
  "grid shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground",
  "group-data-[selected=true]:bg-white/20 group-data-[selected=true]:text-accent-foreground",
);

const DETAIL = cn(
  "ml-auto shrink-0 truncate pl-3 text-muted-foreground",
  "group-data-[selected=true]:text-accent-foreground/80",
);

const GROUP = cn(
  "[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2.5",
  "[&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium",
  "[&_[cmdk-group-heading]]:text-muted-foreground",
);

/** How many live results each group may show, so the sections stay in view. */
const LIMIT = { chats: 6, terminals: 6, workspaces: 4 } as const;

/** Row sizes: the Spotlight window is roomier than the sidebar dropdown. */
const SIZE = {
  large: { row: "h-11 text-base", tile: "h-7 w-7", detail: "text-sm" },
  compact: { row: "h-9 text-sm", tile: "h-6 w-6", detail: "text-xs" },
} as const;

type Size = keyof typeof SIZE;

function ResultRow({
  value,
  testId,
  icon: Icon,
  label,
  detail,
  size,
  onSelect,
}: {
  value: string;
  testId: string;
  icon: LucideIcon;
  label: ReactNode;
  detail?: ReactNode;
  size: Size;
  onSelect: () => void;
}) {
  const s = SIZE[size];
  return (
    <Command.Item value={value} onSelect={onSelect} data-testid={testId} className={cn(ROW, s.row)}>
      <span className={cn(TILE, s.tile)}>
        <Icon className="h-4 w-4" aria-hidden />
      </span>
      <span className="min-w-0 truncate">{label}</span>
      {detail ? <span className={cn(DETAIL, s.detail)}>{detail}</span> : null}
    </Command.Item>
  );
}

/**
 * The open workspaces and their panes, fetched once per mount. A headless or
 * older backend simply contributes nothing — the search must never fail to
 * open because the IDE is not there.
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

export function QuickSwitchList({
  query,
  onDone,
  onFirstChange,
  size = "large",
  className,
}: {
  query: string;
  /** Called after a result was opened, so the shell can close and clear. */
  onDone: () => void;
  /**
   * Called with the top row's value whenever the set of rows changes. The
   * terminals and workspaces arrive after the first keystrokes; without this
   * cmdk keeps its earlier pick, and Enter opened a row further down.
   */
  onFirstChange?: (value: string) => void;
  size?: Size;
  className?: string;
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
  const { rows: chatRows, open: openChat } = useChatRows();
  const { panes, workspaces } = useIdeResults();
  const typed = query.trim() !== "";

  // About forty destinations — ranked inline on every keystroke, no memo needed.
  const labelFor = (item: QuickSwitchEntry) => label(item.labelKey, item.fallbackLabel);
  const ambiguous = ambiguousEntryKeys(labelFor);
  // A label two rows share names its area up front, e.g. "Voice › API Keys"
  // beside the API Keys page itself — and the alphabet follows what is SHOWN,
  // so that row sorts under "V…", where the eye looks for it.
  const ranked = rankQuickSwitch(query, labelFor)
    .map((row) => {
      const area = row.entry.parentLabelKey ? label(row.entry.parentLabelKey, "") : "";
      const shown = area && ambiguous.has(row.entry.key) ? `${area} › ${row.label}` : row.label;
      return { ...row, area, shown, prefix: labelStartsWith(shown, normalizeQuery(query)) };
    })
    // One or two letters list only what visibly starts with them: "a" must not
    // show "Voice › API Keys" just because the tab underneath is "API Keys".
    .filter((row) => row.prefix || normalizeQuery(query).length > SHORT_QUERY)
    .sort((a, b) => compareMatches({ ...a, label: a.shown }, { ...b, label: b.shown }));
  const settingsMatches = useMemo(
    () => strongSettingsMatches(query, searchSettingsOptions(language, query, t)),
    [query, language, t],
  );
  const chatHits = rankItems(
    query,
    chatRows,
    (row) => [row.title, row.preview],
    LIMIT.chats,
    (row) => row.title || row.preview,
  );
  const paneHits = rankItems(
    query,
    panes,
    (pane) => [pane.recap, pane.last_prompt, pane.display_name, pane.key, pane.workspace_name],
    LIMIT.terminals,
    paneTitle,
  );
  const workspaceHits = rankItems(
    query,
    workspaces,
    (workspace) => [workspace.name, folderName(workspace.folder), workspace.branch],
    LIMIT.workspaces,
    (workspace) => workspace.name,
  );

  const rowValues = [
    ...ranked.map(({ entry }) => `section:${entry.key}`),
    ...chatHits.map((row) => `chat:${row.kind}:${row.id}`),
    ...paneHits.map((pane) => `pane:${pane.workspace_id}:${pane.name}`),
    ...workspaceHits.map((workspace) => `workspace:${workspace.id}`),
    ...settingsMatches.map((match) => `setting:${match.id}`),
  ];
  const signature = rowValues.join("|");
  useEffect(() => {
    onFirstChange?.(rowValues[0] ?? "");
    // Only when the rows themselves change, never on a mere re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  // Nothing typed: no list at all — the field stands alone, like Spotlight.
  if (!typed) return null;

  const go = (item: QuickSwitchEntry) => {
    if (item.surface) setSurface(item.surface);
    setActiveSection(item.section);
    onDone();
  };

  const goToChat = (row: ChatRow) => {
    openChat(row);
    onDone();
  };

  /** Bring the pane's workspace to the front and frame the pane in the grid. */
  const goToPane = (pane: WorkspacePaneRow) => {
    setActiveSection("agentic-ide");
    if (!pane.workspace_active) activateWorkspace(pane.workspace_id);
    setSpotlight({ workspaceId: pane.workspace_id, pane: pane.name });
    onDone();
  };

  const goToWorkspace = (workspace: ProjectWorkspace) => {
    setActiveSection("agentic-ide");
    if (!workspace.active) activateWorkspace(workspace.id);
    onDone();
  };

  const goToSetting = (groupId: string) => {
    requestSettingsJump(groupId);
    setActiveSection("settings");
    onDone();
  };

  return (
    <Command.List className={cn("overflow-y-auto p-1.5 scrollbar-jarvis", className)}>
      <Command.Empty className="px-3 py-6 text-center text-sm text-muted-foreground">
        {t("quick_switch.no_results")}
      </Command.Empty>
      {ranked.length > 0 && (
        <Command.Group heading={t("quick_switch.group_sections")} className={GROUP}>
          {ranked.map(({ entry: item, label: text, area, shown }) => {
            const here = item.section === activeSection && !item.surface;
            return (
              <ResultRow
                key={item.key}
                value={`section:${item.key}`}
                testId={`quick-switch-${item.key}`}
                icon={item.icon}
                label={shown}
                detail={here ? t("quick_switch.current") : shown === text ? area : ""}
                size={size}
                onSelect={() => go(item)}
              />
            );
          })}
        </Command.Group>
      )}
      {chatHits.length > 0 && (
        <Command.Group heading={t("quick_switch.group_chats")} className={GROUP}>
          {chatHits.map((row) => (
            <ResultRow
              key={`${row.kind}:${row.id}`}
              value={`chat:${row.kind}:${row.id}`}
              testId={`quick-switch-chat-${row.id}`}
              icon={row.kind === "voice" ? VoiceIcon : ChatIcon}
              label={chatRowLabel(row, t).text}
              detail={formatChatWhen(row.updatedMs)}
              size={size}
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
              icon={TerminalIcon}
              label={paneTitle(pane)}
              detail={`${pane.key} · ${pane.workspace_name}`}
              size={size}
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
              icon={FolderIcon}
              label={workspace.name}
              detail={workspace.branch || folderName(workspace.folder)}
              size={size}
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
              icon={SettingsIcon}
              label={match.label}
              detail={label("nav.settings", "Settings")}
              size={size}
              onSelect={() => goToSetting(match.id)}
            />
          ))}
        </Command.Group>
      )}
    </Command.List>
  );
}
