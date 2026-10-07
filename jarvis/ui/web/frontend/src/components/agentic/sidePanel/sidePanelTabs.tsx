import type { ReactNode } from "react";
import { Bot, Building2, CreditCard, FileDiff, Files, FolderTree, GitBranch, Globe, Search, type LucideIcon } from "lucide-react";
import type { SidePanelTabId } from "@/store/ideSidePanel";
import { AgentsOverview } from "./AgentsOverview";
import { BrowserTab } from "./browser/BrowserTab";
import { ExplorerPanel } from "./explorer/ExplorerPanel";
import { GitOverviewTab } from "./git/GitOverviewTab";
import { OfficeTab } from "./OfficeTab";
import { SearchPanel } from "./search/SearchPanel";
import { SkillsTab } from "./skills/SkillsTab";
import { SubscriptionsTab } from "./subscriptions/SubscriptionsTab";

/** One function the side panel can show. */
export interface SidePanelTabDef {
  id: SidePanelTabId;
  /** i18n key of the tab's label. */
  labelKey: string;
  icon: LucideIcon;
  /**
   * Letter that opens the tab from the "Open a surface" launcher and the "+"
   * menu. Unique across the registry and "T" (reserved for a plain terminal).
   */
  shortcut: string;
  render: () => ReactNode;
  /**
   * Stay mounted while the tab is open but not in front, so switching away
   * keeps its state (a browser page would otherwise reload on every switch).
   */
  keepAlive?: boolean;
}

/**
 * Everything the side panel can hold, in launcher and "+" menu order.
 *
 * Adding a function is one entry here plus its id in `SIDE_PANEL_TAB_IDS`
 * (`store/ideSidePanel.ts`); the header, the "+" menu and persistence pick
 * it up from there.
 */
export const SIDE_PANEL_TABS: readonly SidePanelTabDef[] = [
  {
    id: "browser",
    labelKey: "ide_side_panel.tabs.browser",
    icon: Globe,
    shortcut: "B",
    render: () => <BrowserTab />,
    keepAlive: true,
  },
  {
    id: "agents",
    labelKey: "ide_side_panel.tabs.agents",
    shortcut: "A",
    icon: Bot,
    render: () => <AgentsOverview />,
  },
  {
    id: "changes",
    labelKey: "ide_side_panel.tabs.changes",
    shortcut: "C",
    icon: FileDiff,
    render: () => <ExplorerPanel view="changes" />,
  },
  {
    id: "files",
    labelKey: "ide_side_panel.tabs.files",
    shortcut: "F",
    icon: FolderTree,
    render: () => <ExplorerPanel view="files" />,
  },
  {
    id: "search",
    labelKey: "ide_side_panel.tabs.search",
    shortcut: "S",
    icon: Search,
    render: () => <SearchPanel />,
  },
  {
    id: "git",
    labelKey: "ide_side_panel.tabs.git",
    shortcut: "G",
    icon: GitBranch,
    render: () => <GitOverviewTab />,
  },
  {
    id: "skills",
    labelKey: "ide_side_panel.tabs.skills",
    shortcut: "K",
    icon: Files,
    render: () => <SkillsTab />,
  },
  {
    id: "subscriptions",
    labelKey: "ide_side_panel.tabs.subscriptions",
    shortcut: "U",
    icon: CreditCard,
    render: () => <SubscriptionsTab />,
  },
  {
    id: "office",
    labelKey: "ide_side_panel.tabs.office",
    shortcut: "V",
    icon: Building2,
    render: () => <OfficeTab />,
  },
];

export function sidePanelTab(id: SidePanelTabId): SidePanelTabDef | undefined {
  return SIDE_PANEL_TABS.find((tab) => tab.id === id);
}
