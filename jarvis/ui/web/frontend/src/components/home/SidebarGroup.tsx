import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * A titled block in the sidebar — the uppercase mono label with an optional
 * action on the right ("Show all"), and whatever rows sit under it. Shared by
 * the recent-runs and recent-chats blocks so the two cannot drift apart.
 */
export function SidebarGroup({
  title,
  action,
  children,
  testId,
}: {
  title: string;
  action?: { label: string; onClick: () => void; expanded?: boolean };
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <section data-testid={testId} className="px-1">
      <div className="flex items-center justify-between px-2 pb-1 pt-2">
        <span className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
          {title}
        </span>
        {action && (
          <button
            type="button"
            onClick={action.onClick}
            aria-expanded={action.expanded}
            className="flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {action.label}
            <ChevronDown
              aria-hidden
              className={cn(
                "h-3 w-3 transition-transform",
                action.expanded && "rotate-180",
              )}
            />
          </button>
        )}
      </div>
      {children}
    </section>
  );
}
