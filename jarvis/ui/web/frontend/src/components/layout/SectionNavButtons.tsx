import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

/** The sidebar toggle the caption owns — state lives in the shell (App.tsx). */
export interface SidebarToggleState {
  collapsed: boolean;
  onToggle: () => void;
  /** Test id for the button. Defaults to `section-nav-sidebar`. */
  testId?: string;
}

/** The caption control's shape — theme tokens only, so it reads in light and dark mode alike. */
const NAV_BUTTON =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground " +
  "transition-colors hover:bg-secondary hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/**
 * The caption's leading control: the sidebar toggle.
 *
 * Rendered at the far left of the window caption (see TopBar), so it is on
 * screen for EVERY section — chat, agents, voice, settings — with no second
 * bar per view. The back/forward arrows that used to sit beside it were
 * removed on 2026-10-01 as unused. A detached solo window is pinned to one
 * view and has no sidebar, so the group stays out of it.
 */
export function SectionNavButtons({
  sidebarToggle,
}: {
  sidebarToggle?: SidebarToggleState;
} = {}) {
  const t = useT();
  const solo = useEventStore((s) => s.solo);

  if (solo || !sidebarToggle) return null;

  return (
    <div
      className="flex shrink-0 items-center"
      data-testid="section-nav-buttons"
      role="group"
      aria-label={t("sidebar.sections")}
    >
      <button
        type="button"
        data-testid={sidebarToggle.testId ?? "section-nav-sidebar"}
        onClick={sidebarToggle.onToggle}
        aria-expanded={!sidebarToggle.collapsed}
        aria-label={t(sidebarToggle.collapsed ? "sidebar.expand" : "sidebar.collapse")}
        title={t(sidebarToggle.collapsed ? "sidebar.expand" : "sidebar.collapse")}
        className={NAV_BUTTON}
      >
        {sidebarToggle.collapsed ? (
          <PanelLeftOpen className="h-4 w-4" aria-hidden />
        ) : (
          <PanelLeftClose className="h-4 w-4" aria-hidden />
        )}
      </button>
    </div>
  );
}
