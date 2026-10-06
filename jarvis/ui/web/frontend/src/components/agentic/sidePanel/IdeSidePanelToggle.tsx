import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { SIDE_PANEL_ID } from "./sidePanelIds";

/**
 * Opens and shuts the Agentic IDE's right-hand side panel.
 *
 * It lives at the right end of the window caption, beside the window buttons,
 * so it stays in the same place whether the panel is open or shut; the closed
 * panel's rail is the second way in. Kept free of the panel's own module: the
 * caption is in the startup chunk and must not pull the panel's tabs into it.
 */
export function IdeSidePanelToggle({ className }: { className?: string }) {
  const t = useT();
  const open = useIdeSidePanelStore((state) => state.open);
  const setOpen = useIdeSidePanelStore((state) => state.setOpen);
  const label = t(open ? "ide_side_panel.collapse" : "ide_side_panel.expand");
  const Icon = open ? PanelRightClose : PanelRightOpen;
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
