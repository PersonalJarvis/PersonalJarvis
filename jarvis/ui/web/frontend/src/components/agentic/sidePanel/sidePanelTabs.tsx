import type { ReactNode } from "react";
import { Bot, type LucideIcon } from "lucide-react";
import type { SidePanelTabId } from "@/store/ideSidePanel";
import { AgentsOverview } from "./AgentsOverview";

/** One function the side panel can show. */
export interface SidePanelTabDef {
  id: SidePanelTabId;
  /** i18n key of the tab's label. */
  labelKey: string;
  icon: LucideIcon;
  render: () => ReactNode;
}

/**
 * Everything the side panel can hold, in "+" menu order.
 *
 * Adding a function is one entry here plus its id in `SIDE_PANEL_TAB_IDS`
 * (`store/ideSidePanel.ts`); the header, the "+" menu and persistence pick
 * it up from there.
 */
export const SIDE_PANEL_TABS: readonly SidePanelTabDef[] = [
  {
    id: "agents",
    labelKey: "ide_side_panel.tabs.agents",
    icon: Bot,
    render: () => <AgentsOverview />,
  },
];

export function sidePanelTab(id: SidePanelTabId): SidePanelTabDef | undefined {
  return SIDE_PANEL_TABS.find((tab) => tab.id === id);
}
