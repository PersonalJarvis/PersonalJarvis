import { useEventStore } from "@/store/events";
import { SectionTabBar, type SectionTab } from "@/components/layout/SectionTabBar";
import { ClisView } from "@/views/ClisView";
import { CliTestHubView } from "@/views/CliTestHubView";

/**
 * Combined "CLIs" section.
 *
 * Merges the CLIs list and the CLI Test Hub behind a single sidebar entry with
 * one flat top tab bar:
 *
 *   [ CLIs ] [ CLI Test Hub ]
 *
 * Same thin-wrapper pattern as ExtensionsView (the active sidebar section id
 * `clis` / `cli-test-hub` doubles as the tab state). The child views are
 * embedded verbatim — they rehydrate their own state from React Query / the
 * store, so the unmount/remount on tab switch is harmless.
 */

const TABS = [
  { id: "clis", labelKey: "nav.clis" },
  { id: "cli-test-hub", labelKey: "nav.cli_test_hub" },
] as const satisfies readonly SectionTab[];

export function ClisHubView() {
  const active = useEventStore((s) => s.activeSection);

  // The router only mounts us for clis/cli-test-hub; any other value is
  // unexpected — fall back to the CLIs tab defensively.
  const current = TABS.some((tab) => tab.id === active) ? active : "clis";

  return (
    <div className="flex h-full flex-col">
      <SectionTabBar tabs={TABS} />
      <div className="min-h-0 flex-1 overflow-hidden">
        {current === "clis" && <ClisView />}
        {current === "cli-test-hub" && <CliTestHubView />}
      </div>
    </div>
  );
}
