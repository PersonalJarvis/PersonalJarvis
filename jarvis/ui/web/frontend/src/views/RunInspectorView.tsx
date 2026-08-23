import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import { useRuns } from "@/hooks/useRuns";
import { RunList } from "@/components/runs/RunList";
import { RunDetail } from "@/components/runs/RunDetail";

export function RunInspectorView() {
  const t = useT();
  const { data: runs, isError } = useRuns();
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (selected === null && runs && runs.length > 0) setSelected(runs[0].session_id);
  }, [runs, selected]);

  if (isError) {
    return <div className="p-6 text-sm text-muted-foreground">{t("run_inspector.unavailable")}</div>;
  }

  return (
    <div className="flex h-full">
      <div className="w-[300px] shrink-0 overflow-y-auto border-r border-border">
        <div className="px-4 py-3">
          <h2 className="text-sm font-semibold">{t("run_inspector.title")}</h2>
          <p className="text-xs text-muted-foreground">{t("run_inspector.subtitle")}</p>
        </div>
        <RunList items={runs ?? []} selectedId={selected} onSelect={setSelected} />
      </div>
      <div className="min-h-0 min-w-0 flex-1">
        {selected ? <RunDetail sessionId={selected} /> : (
          <div className="p-6 text-sm text-muted-foreground">{t("run_inspector.empty")}</div>
        )}
      </div>
    </div>
  );
}
