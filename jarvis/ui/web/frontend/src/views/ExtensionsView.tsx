import { useEventStore } from "@/store/events";
import { SectionTabBar, type SectionTab } from "@/components/layout/SectionTabBar";
import { SkillsView } from "@/views/SkillsView";
import { PluginsView } from "@/views/PluginsView";
import { McpsView } from "@/views/McpsView";

/**
 * Combined "Skills & Tools" section (sidebar label "Skills & Tools").
 *
 * Merges the three sidebar entries — Skills, Plugins and MCPs — behind a single
 * sidebar entry with one flat top tab bar:
 *
 *   [ Skills ] [ Plugins ] [ MCPs ]
 *
 * CLIs were split out into their own "CLIs" section (see ClisHubView), which
 * pairs the CLIs list with the CLI Test Hub.
 *
 * Design note — why this is a thin wrapper, not a rewrite:
 * The active sidebar section id (`activeSection` in the event store) *is* the
 * tab state. We deliberately keep the section ids (`skills`, `plugins`, `mcps`)
 * alive in the five-layer nav enum — see `jarvis/plugins/tool/navigate.py` +
 * `store/events.ts` + the parity test `tests/unit/plugins/tool/test_navigate.py`.
 * Only the *sidebar presentation* collapses to one entry; routing, deep-links
 * and voice navigation ("öffne Plugins") keep working unchanged and land on the
 * right tab. The child views are embedded verbatim — they rehydrate their own
 * state from React Query / the store, so the unmount/remount on tab switch is
 * harmless.
 *
 * The file/component name stays `ExtensionsView` for continuity; only the
 * user-facing label is "Skills & Tools".
 */

const TABS = [
  { id: "skills", labelKey: "nav.skills" },
  { id: "plugins", labelKey: "nav.plugins" },
  { id: "mcps", labelKey: "nav.mcps" },
] as const satisfies readonly SectionTab[];

export function ExtensionsView() {
  const active = useEventStore((s) => s.activeSection);

  // The router only mounts us for skills/plugins/mcps; any other value is
  // unexpected — fall back to the Skills tab defensively.
  const current = TABS.some((tab) => tab.id === active) ? active : "skills";

  return (
    <div className="flex h-full flex-col">
      <SectionTabBar tabs={TABS} />
      <div className="min-h-0 flex-1 overflow-hidden">
        {current === "skills" && <SkillsView />}
        {current === "plugins" && <PluginsView />}
        {current === "mcps" && <McpsView />}
      </div>
    </div>
  );
}
