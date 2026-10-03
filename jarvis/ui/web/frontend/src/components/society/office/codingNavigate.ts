/** Leave the coding floor for the real session: the Agentic IDE with that pane maximized. */
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useOfficeStore } from "./officeStore";

export function openPaneSession(pane: Pick<WorkspacePaneRow, "workspace_id" | "name">): void {
  // Retire the small viewer and its size lead, including when this pane is
  // already maximized in the grid and therefore has no new layout transition.
  useOfficeStore.getState().select(null);
  const events = useEventStore.getState();
  if (events.activeSection !== "agentic-ide") events.setActiveSection("agentic-ide");
  // The terminal opens full-size in the grid, live, the way the agent works in it.
  useIdeChatStore.getState().requestPane(pane.workspace_id, pane.name, { maximize: true });
  // A full-view office would hide the very pane it just focused.
  useIdeSidePanelStore.getState().setMaximized(false);
}
