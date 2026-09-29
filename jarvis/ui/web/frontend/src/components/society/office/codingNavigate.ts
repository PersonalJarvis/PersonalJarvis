/** Leave the coding floor for the real session: the Agentic IDE with that pane focused. */
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";

export function openPaneSession(pane: Pick<WorkspacePaneRow, "workspace_id" | "name">): void {
  const events = useEventStore.getState();
  if (events.activeSection !== "agentic-ide") events.setActiveSection("agentic-ide");
  useIdeChatStore.getState().requestPane(pane.workspace_id, pane.name);
}
