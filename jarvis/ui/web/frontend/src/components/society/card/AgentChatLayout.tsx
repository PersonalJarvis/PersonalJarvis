import { useId, useState, type ReactNode } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";

import { useLocaleChunk, useT } from "@/i18n";
import "./agentChatLayout.css";

/** Keep both panes mounted: folding Options must not reset a browser or draft. */
export function AgentChatLayout({ children, options }: { children: ReactNode; options: ReactNode }) {
  const t = useT();
  useLocaleChunk("society");
  const [open, setOpen] = useState(true);
  const panelId = useId();
  const label = t(open ? "society.card.options_collapse" : "society.card.options_expand");
  const Icon = open ? PanelRightClose : PanelRightOpen;

  return (
    <div className="agent-chat-layout flex min-h-0 min-w-0 flex-col overflow-hidden rounded-tl-[12px] border-l border-border bg-background">
      <div className="flex shrink-0 justify-end border-b border-border px-2 py-1">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          title={label}
          aria-label={label}
          aria-expanded={open}
          aria-controls={panelId}
          className="inline-flex h-8 shrink-0 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon aria-hidden className="h-4 w-4" />
          {t("society.card.options")}
        </button>
      </div>
      <div className="agent-chat-layout-panes" data-options-open={open}>
        {children}
        <div id={panelId} hidden={!open} className="agent-chat-layout-options">
          {options}
        </div>
      </div>
    </div>
  );
}
