import { useEventStore, type SectionId } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

export interface SectionTab {
  id: SectionId;
  labelKey: string;
}

/**
 * Shared flat top tab bar for merged sidebar sections (e.g. "Skills & Tools"
 * fronting skills/plugins/mcps, and "CLIs" fronting clis/cli-test-hub).
 *
 * Each tab maps to a real section id; the active section id (`activeSection` in
 * the event store) doubles as the tab state, so routing, deep-links and voice
 * navigation ("öffne Plugins") keep working unchanged and land on the right i18n-allow
 * tab. Clicking a tab just sets the active section.
 */
export function SectionTabBar({ tabs }: { tabs: readonly SectionTab[] }) {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);

  return (
    <div className="flex items-center gap-6 border-b border-border px-6">
      {tabs.map((tab) => (
        <PrimaryTab
          key={tab.id}
          label={t(tab.labelKey)}
          active={active === tab.id}
          onClick={() => setActive(tab.id)}
        />
      ))}
    </div>
  );
}

function PrimaryTab({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "relative py-3 text-sm font-medium transition-colors",
        active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
      {active && (
        <span
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-primary shadow-[0_0_8px_rgba(255,214,10,0.6)]"
        />
      )}
    </button>
  );
}
