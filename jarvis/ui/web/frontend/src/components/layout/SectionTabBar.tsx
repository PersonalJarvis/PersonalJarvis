import { cn } from "@/lib/utils";
import { useEventStore, type SectionId } from "@/store/events";
import { useT } from "@/i18n";

export interface SectionTab {
  id: SectionId;
  labelKey: string;
}

/**
 * The underline tab bar: base step, 500 weight, a 2 px accent underline under
 * the active tab, a hairline under the row. Never pill segments.
 *
 * Two faces. `SectionTabBar` is bound to the event store — each tab maps to a
 * real section id, and the active section doubles as the tab state, so
 * routing, deep links and voice navigation ("öffne Plugins") keep working i18n-allow
 * unchanged. `TabBar` is the same look for a view's OWN tabs (a local
 * `useState`), so a Preview / Files / Run switch inside one view draws the
 * same bar as the section switch above it.
 */
export function SectionTabBar({ tabs }: { tabs: readonly SectionTab[] }) {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);

  return (
    <TabBar
      tabs={tabs.map((tab) => ({ id: tab.id, label: t(tab.labelKey) }))}
      active={active}
      onChange={(id) => setActive(id as SectionId)}
    />
  );
}

export interface TabBarItem {
  id: string;
  label: string;
  /** A small count after the label, rendered in muted ink. */
  count?: number;
}

export function TabBar({
  tabs,
  active,
  onChange,
  className,
}: {
  tabs: readonly TabBarItem[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn("flex items-center gap-6 border-b border-border", className)}
    >
      {tabs.map((tab) => {
        const isActive = active === tab.id;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-current={isActive ? "page" : undefined}
            onClick={() => onChange(tab.id)}
            className={cn(
              "relative -mb-px flex h-10 items-center gap-2 border-b-2 text-base font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              isActive
                ? "border-accent text-foreground-strong"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="text-sm tabular-nums text-foreground-faint">{tab.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
