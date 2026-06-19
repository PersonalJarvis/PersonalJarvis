import { Trophy } from "lucide-react";
import type { PersonalRecord } from "@/hooks/useBoard";
import { useT } from "@/i18n";

interface PersonalRecordsListProps {
  records: PersonalRecord[];
}

const KNOWN_METRICS = [
  "most_tasks_in_a_day",
  "most_unique_tools_in_a_day",
  "most_voice_commands_in_a_day",
  "most_hours_saved_in_a_day",
  "most_active_events_in_a_day",
  "most_conversation_hours_in_a_day",
];

export function PersonalRecordsList({ records }: PersonalRecordsListProps) {
  const t = useT();
  if (records.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center rounded-lg border border-dashed border-border/60 text-xs text-muted-foreground">
        {t("board_view.no_records_yet")}
      </div>
    );
  }

  return (
    <ul className="grid gap-3 md:grid-cols-2">
      {records.map((rec) => {
        const meta = KNOWN_METRICS.includes(rec.metric)
          ? {
              title: t(`board_view.records.${rec.metric}.title`),
              unit: t(`board_view.records.${rec.metric}.unit`),
            }
          : { title: rec.metric, unit: "" };
        const value = Number.isInteger(rec.value)
          ? rec.value.toString()
          : rec.value.toFixed(1);
        return (
          <li
            key={rec.metric}
            className="flex items-start gap-3 rounded-xl border border-border bg-card/30 px-4 py-3 backdrop-blur"
          >
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary">
              <Trophy className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs uppercase tracking-wider text-muted-foreground">
                {meta.title}
              </div>
              <div className="mt-0.5 font-display text-lg font-semibold">
                {value}
                {meta.unit && (
                  <span className="ml-1 text-xs font-normal text-muted-foreground">
                    {meta.unit}
                  </span>
                )}
              </div>
              <div className="text-[10px] text-muted-foreground">
                {rec.achieved_on}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
