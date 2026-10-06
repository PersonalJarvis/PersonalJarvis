import { PanelRight } from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useWorkspacePanes } from "@/store/workspacePanes";
import { dotKindFor, workspaceAgents } from "./agentStatus";
import { SIDE_PANEL_ID } from "./sidePanelIds";

/**
 * Opens and shuts the Agentic IDE's right-hand side panel.
 *
 * It lives at the right end of the window caption, beside the window buttons,
 * so it stays in the same place whether the panel is open or shut, in the grid
 * and in threads alike. The panel has no other way in while it is shut, so an
 * agent of the active workspace waiting for the user puts an amber dot on this
 * button. Kept free of the panel's own module: the caption is in the startup
 * chunk and must not pull the panel's tabs into it.
 */
export function IdeSidePanelToggle({ className }: { className?: string }) {
  const t = useT();
  const open = useIdeSidePanelStore((state) => state.open);
  const setOpen = useIdeSidePanelStore((state) => state.setOpen);
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  // An open panel shows its agents itself; only a shut one needs the count.
  const panes = useWorkspacePanes(!open);
  const waiting = open ? 0 : workspaceAgents(panes, activeWorkspaceId).filter((pane) => dotKindFor(pane) === "waiting").length;
  const action = t(open ? "ide_side_panel.collapse" : "ide_side_panel.expand");
  const label = waiting > 0 ? `${action} (${fill(t("ide_side_panel.rail_waiting"), { n: waiting })})` : action;
  return (
    <button
      type="button"
      onClick={() => setOpen(!open)}
      title={label}
      aria-label={label}
      aria-expanded={open}
      aria-controls={SIDE_PANEL_ID}
      data-testid="ide-side-panel-toggle"
      className={cn(
        "relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
        "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        open && "bg-secondary text-foreground",
        className,
      )}
    >
      <PanelRight aria-hidden className="h-4 w-4" />
      {waiting > 0 && (
        <span aria-hidden data-testid="ide-side-panel-waiting"
          className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-warning ring-2 ring-background" />
      )}
    </button>
  );
}
