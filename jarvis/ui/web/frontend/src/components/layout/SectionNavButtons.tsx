import { ArrowLeft, ArrowRight, PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useSectionHistory } from "@/hooks/useSectionHistory";

/** The sidebar toggle the caption owns — state lives in the shell (App.tsx). */
export interface SidebarToggleState {
  collapsed: boolean;
  onToggle: () => void;
  /** Test id for the button. Defaults to `section-nav-sidebar`. */
  testId?: string;
}

/**
 * One shape for every caption control in this group.
 *
 * Theme tokens only, so the buttons read in light and dark mode alike. A
 * disabled direction goes quiet (no hover lift, low opacity) but keeps its
 * tooltip — a forward button that cannot explain itself teaches nothing.
 */
const NAV_BUTTON =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground " +
  "transition-colors hover:bg-secondary hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground";

/**
 * The caption's leading navigation: sidebar toggle, back, forward.
 *
 * Rendered at the far left of the window caption (see TopBar), so it is on
 * screen for EVERY section — chat, agents, voice, settings — with no second
 * bar per view. Back/forward walk the visited-section history
 * (`useSectionHistory`): back returns to the previously visited section, and
 * forward only re-applies an undone step, staying disabled until one exists.
 * A detached solo window is pinned to one view, so the group stays out of it.
 */
export function SectionNavButtons({
  sidebarToggle,
}: {
  sidebarToggle?: SidebarToggleState;
} = {}) {
  const t = useT();
  const solo = useEventStore((s) => s.solo);
  const { canGoBack, canGoForward, goBack, goForward } = useSectionHistory();

  if (solo) return null;

  const backLabel = t("topbar.nav_back");
  const forwardLabel = t("topbar.nav_forward");

  return (
    <div
      className="flex shrink-0 items-center"
      data-testid="section-nav-buttons"
      role="group"
      aria-label={t("sidebar.sections")}
    >
      {sidebarToggle && (
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
      )}
      <button
        type="button"
        data-testid="section-nav-back"
        onClick={() => goBack()}
        disabled={!canGoBack}
        aria-label={backLabel}
        title={backLabel}
        className={NAV_BUTTON}
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
      </button>
      <button
        type="button"
        data-testid="section-nav-forward"
        onClick={() => goForward()}
        disabled={!canGoForward}
        aria-label={forwardLabel}
        title={forwardLabel}
        className={NAV_BUTTON}
      >
        <ArrowRight className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}
