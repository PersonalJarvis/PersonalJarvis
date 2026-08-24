import { LayoutGrid, MessageSquare } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import type { WorkspaceView } from "@/components/agentic/workspaceView";

/**
 * `Chat | Grid` — the Agentic IDE's one switch, at the top of the view.
 *
 * A segmented control in a pill rather than two toolbar glyphs, because the
 * two halves are no longer two ways of LOOKING at the same thing: since
 * 2026-08-24 they are two surfaces. Grid is the wall of terminals; Chat is the
 * agent chat, in this workspace's folder. A control that switches what the
 * page IS has to say so in words — an icon pair asks the user to remember
 * which glyph meant which surface.
 *
 * Chat sits first, matching the maintainer's sketch and the reading order of
 * the sidebar underneath it, which is chats in chat mode.
 */
export function ViewSwitch({
  view,
  onView,
  className,
}: {
  view: WorkspaceView;
  onView: (next: WorkspaceView) => void;
  className?: string;
}) {
  const t = useT();
  return (
    <div
      role="tablist"
      aria-label={t("agentic_grid.view_switch.hint")}
      data-testid="agentic-view-switch"
      className={cn(
        "flex shrink-0 items-center gap-0.5 rounded-full border border-border bg-secondary p-0.5",
        className,
      )}
    >
      <ViewTab
        active={view === "chat"}
        onClick={() => onView("chat")}
        icon={<MessageSquare aria-hidden className="h-3.5 w-3.5" />}
        label={t("agentic_grid.view_switch.chat")}
        title={t("agentic_grid.view_switch.chat_hint")}
        testId="agentic-view-mode-toggle"
      />
      <ViewTab
        active={view === "grid"}
        onClick={() => onView("grid")}
        icon={<LayoutGrid aria-hidden className="h-3.5 w-3.5" />}
        label={t("agentic_grid.view_switch.grid")}
        title={t("agentic_grid.view_switch.grid_hint")}
        testId="agentic-view-mode-grid"
      />
    </div>
  );
}

function ViewTab({
  active,
  onClick,
  icon,
  label,
  title,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  title: string;
  testId: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      aria-pressed={active}
      data-testid={testId}
      onClick={onClick}
      title={title}
      className={cn(
        "flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        active
          ? "bg-card text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
