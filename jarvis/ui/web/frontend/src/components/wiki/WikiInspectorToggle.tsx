import { PanelRightClose, PanelRightOpen } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { WIKI_INSPECTOR_ID, useWikiPanelStore } from "@/store/wikiPanel";

/**
 * Opens and shuts the Wiki section's right-hand inspector from the window
 * caption — the same place and the same glyph as the Agentic IDE's side-panel
 * toggle, so the two sections share one habit. Kept free of the Wiki's own
 * modules: the caption is in the startup chunk.
 */
export function WikiInspectorToggle({ className }: { className?: string }) {
  const t = useT();
  const open = useWikiPanelStore((state) => state.open);
  const setOpen = useWikiPanelStore((state) => state.setOpen);
  const label = t(open ? "wiki_ui.inspector_collapse" : "wiki_ui.inspector_expand");
  const Icon = open ? PanelRightClose : PanelRightOpen;
  return (
    <button
      type="button"
      onClick={() => setOpen(!open)}
      title={label}
      aria-label={label}
      aria-expanded={open}
      aria-controls={WIKI_INSPECTOR_ID}
      data-testid="wiki-inspector-toggle"
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
