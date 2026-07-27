/**
 * The section's home screen: four questions, in the order a person asks them.
 *
 * Can I use it → where does it come from → what happened → does anything need
 * me. The full diagnostic checklist is still here, one disclosure down,
 * because it is the right tool once you already know something is wrong — it
 * was simply never the right thing to open with.
 *
 * All numbers on this screen come from ONE backend model
 * (`jarvis/ultrawiki/progress.py`), which is why the headline, the bar and the
 * checklist cannot disagree the way they did before. If the backend has not
 * answered yet, the screen says so instead of rendering zeros that look like
 * measurements.
 */
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Loader2 } from "lucide-react";

import { useT } from "@/i18n";
import { HealthPanel } from "@/components/ultrawiki/HealthPanel";
import { VerdictCard } from "@/components/ultrawiki/overview/VerdictCard";
import { SourceRoster } from "@/components/ultrawiki/overview/SourceRoster";
import { ActivityFeed } from "@/components/ultrawiki/overview/ActivityFeed";
import { ProblemList } from "@/components/ultrawiki/overview/ProblemList";
import {
  fetchUltraWikiHealth,
  type UltraWikiHealth,
  type UltraWikiStatus,
} from "@/lib/ultrawikiApi";

export function OverviewTab({
  status,
  onChanged,
  onOpenSources,
  onOpenSettings,
}: {
  status: UltraWikiStatus;
  onChanged: () => void;
  onOpenSources: () => void;
  onOpenSettings: () => void;
}): JSX.Element {
  const t = useT();

  const healthQuery = useQuery({
    queryKey: ["ultrawiki", "health"],
    queryFn: fetchUltraWikiHealth,
    staleTime: 2_000,
    // Follow a running import closely, idle calmly — the same cadence the
    // status poll uses, so the two never disagree for longer than a tick.
    refetchInterval: (query) =>
      (query.state.data as UltraWikiHealth | undefined)?.checks?.some(
        (c) => c.state === "working",
      )
        ? 4_000
        : 20_000,
  });

  const health = healthQuery.data ?? null;
  const progress = status.progress ?? health?.progress ?? null;

  const refresh = () => {
    onChanged();
    void healthQuery.refetch();
  };

  if (!progress) {
    return (
      <div className="p-4" data-testid="ultrawiki-overview-waiting">
        <p className="flex items-center gap-2 rounded-xl border border-border bg-card/40 px-4 py-3 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("ultrawiki.overview.waiting")}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5 p-4" data-testid="ultrawiki-overview">
      <VerdictCard
        progress={progress}
        pipeline={status.pipeline}
        usable={health?.usable ?? false}
        started={status.started}
        throughput={status.throughput}
      />

      <SourceRoster
        sources={status.sources}
        onChanged={refresh}
        onOpenSources={onOpenSources}
      />

      <ActivityFeed jobs={status.jobs} sources={status.sources} />

      {health && (
        <ProblemList
          checks={health.checks}
          handlers={{
            onOpenSources,
            onOpenSettings,
            onChanged: refresh,
          }}
        />
      )}

      <details className="group rounded-xl border border-border bg-card/30">
        <summary
          className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2.5 text-xs text-muted-foreground hover:text-foreground"
          data-testid="ultrawiki-all-checks-toggle"
        >
          <ChevronRight
            className="h-3.5 w-3.5 transition-transform group-open:rotate-90"
            aria-hidden
          />
          {t("ultrawiki.overview.all_checks")}
        </summary>
        <div className="border-t border-border/60">
          <HealthPanel
            showVerdict={false}
            onOpenSources={onOpenSources}
            onOpenSettings={onOpenSettings}
          />
        </div>
      </details>
    </div>
  );
}
