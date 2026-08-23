import { TerminalSquare, FlaskConical } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

import { useEventStore, type SectionId } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { ClisView } from "@/views/ClisView";
import { CliTestHubView } from "@/views/CliTestHubView";

/**
 * Combined "CLIs & CLI Test Hub" section.
 *
 * One settings-style surface, the same shape as "Skills, Plugins & MCPs": a
 * narrow navigation column on the left under one heading, and the chosen area
 * on the right. The two areas are the catalog (a quiet table of every CLI, with
 * a detail page in place) and the test hub (a composer that drives any
 * connected CLI in plain language).
 *
 * Design note — why this is a thin wrapper, not a rewrite:
 * The active sidebar section id (`activeSection` in the event store) *is* the
 * navigation state. The section ids `clis` and `cli-test-hub` stay alive in the
 * five-layer nav enum, so routing, deep links and voice navigation ("open the
 * CLI Test Hub") keep working unchanged; only the presentation collapses to one
 * sidebar entry. The child views rehydrate their own state from React Query and
 * the store, so the unmount/remount on a switch is harmless.
 */

interface AreaSpec {
  id: SectionId;
  labelKey: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}

const AREAS = [
  { id: "clis", labelKey: "nav.clis", icon: TerminalSquare },
  // A bench where you try a command before trusting it — not a wand.
  { id: "cli-test-hub", labelKey: "nav.cli_test_hub", icon: FlaskConical },
] as const satisfies readonly AreaSpec[];

type AreaId = (typeof AREAS)[number]["id"];

export function ClisHubView() {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);

  // The router only mounts us for clis/cli-test-hub; any other value is
  // unexpected — fall back to the catalog defensively.
  const current: AreaId = AREAS.some((a) => a.id === active) ? (active as AreaId) : "clis";

  return (
    <div className="flex h-full min-h-0">
      <nav
        aria-label={t("clis_view.group_label")}
        className="flex w-[200px] shrink-0 flex-col border-r border-border px-3 py-5"
      >
        <p className="px-2.5 pb-2 text-[11px] font-medium text-muted-foreground">
          {t("clis_view.group_label")}
        </p>
        <ul className="space-y-0.5">
          {AREAS.map((area) => {
            const Icon = area.icon;
            const isActive = area.id === current;
            return (
              <li key={area.id}>
                <button
                  type="button"
                  onClick={() => setActive(area.id)}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                    isActive
                      ? "bg-sheen/[0.08] font-medium text-foreground"
                      : "text-foreground/75 hover:bg-sheen/[0.05] hover:text-foreground",
                  )}
                >
                  <Icon
                    className={cn(
                      "h-4 w-4 shrink-0",
                      isActive ? "text-foreground" : "text-muted-foreground",
                    )}
                  />
                  <span className="flex-1 truncate">{t(area.labelKey)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="min-h-0 min-w-0 flex-1">
        {current === "clis" && <ClisView />}
        {current === "cli-test-hub" && <CliTestHubView />}
      </div>
    </div>
  );
}
