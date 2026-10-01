import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { SIDE_PANEL_ID, useIdeSidePanelStore } from "@/store/ideSidePanel";

/**
 * Opens and shuts the Agentic IDE's right-hand side panel.
 *
 * It lives in the window caption's right end — the mirror of the sidebar
 * toggle at the left end — so it never sits on top of a terminal pane's own
 * header buttons. Only the Agentic IDE has the panel, so only it shows this.
 */
export function IdeSidePanelToggle({ className }: { className?: string }) {
  const t = useT();
  const activeSection = useEventStore((state) => state.activeSection);
  const open = useIdeSidePanelStore((state) => state.open);
  const toggle = useIdeSidePanelStore((state) => state.toggle);
  if (activeSection !== "agentic-ide") return null;
  const label = t(open ? "ide_side_panel.collapse" : "ide_side_panel.expand");
  const Icon = open ? PanelRightClose : PanelRightOpen;
  return (
    <button
      type="button"
      onClick={toggle}
      title={label}
      aria-label={label}
      aria-expanded={open}
      aria-controls={SIDE_PANEL_ID}
      data-testid="ide-side-panel-toggle"
      className={cn(
        "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
        "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        open && "text-foreground",
        className,
      )}
    >
      <Icon aria-hidden className="h-4 w-4" />
    </button>
  );
}
