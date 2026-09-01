import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import { useRuns } from "@/hooks/useRuns";
import { EmptyState } from "@/components/layout/EmptyState";
import { PanelSkeleton } from "@/components/layout/PanelSkeleton";
import { RunList } from "@/components/runs/RunList";
import { RunDetail } from "@/components/runs/RunDetail";

export function RunInspectorView() {
  const t = useT();
  const { data: runs, isError, isLoading } = useRuns();
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (selected === null && runs && runs.length > 0) setSelected(runs[0].session_id);
  }, [runs, selected]);

  if (isError) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState body={t("run_inspector.unavailable")} />
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* The rail: standing chrome, so it sits one step below the page and
          never rises to --card no matter how tall it gets. */}
      <div className="w-[300px] shrink-0 overflow-y-auto border-r border-border bg-sidebar">
        <div className="px-4 py-4">
          <h2 className="text-title font-semibold text-foreground-strong">
            {t("run_inspector.title")}
          </h2>
          <p className="mt-1 text-meta text-muted-foreground">
            {t("run_inspector.subtitle")}
          </p>
        </div>
        {isLoading ? (
          <div className="px-2">
            <PanelSkeleton rows={6} rowHeight={64} label={t("run_inspector.title")} />
          </div>
        ) : (
          <RunList items={runs ?? []} selectedId={selected} onSelect={setSelected} />
        )}
      </div>
      <div className="min-h-0 min-w-0 flex-1">
        {selected ? (
          <RunDetail sessionId={selected} />
        ) : (
          <div className="flex h-full items-center justify-center p-5">
            <EmptyState body={t("run_inspector.empty")} />
          </div>
        )}
      </div>
    </div>
  );
}
