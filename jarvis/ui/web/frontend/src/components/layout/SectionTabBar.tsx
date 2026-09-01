import { clsx } from "clsx";

import { useEventStore, type SectionId } from "@/store/events";
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
    // The horizontal padding stays matched to the header this bar sits under
    // (`ViewHeader`, px-6). It comes off the day the views take the shared
    // `SectionHeader`, which carries none — the shell supplies the page
    // padding then, and a second inset here would push the tabs off the title.
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
      // `clsx`, not `cn`: tailwind-merge classifies the design system's
      // `text-body` as a text COLOUR, so merging it with `text-muted-foreground`
      // drops the size and the tab jumps to the inherited 16 px.
      className={clsx(
        "relative py-3 text-body font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
        active
          ? "text-foreground-strong"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
      {active && (
        // An active indicator is one of the four things `--primary` is for.
        <span
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-primary"
        />
      )}
    </button>
  );
}
