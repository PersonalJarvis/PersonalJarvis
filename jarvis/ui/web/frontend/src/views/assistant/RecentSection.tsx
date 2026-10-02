/**
 * Recently learned — the learning ledger, newest first, set like a journal:
 * when it happened in the label column, what was written beside it, where
 * it went in quiet ink, and the person's own words when there are some —
 * so "why does it think that?" has an answer on the page.
 */
import { useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import type { SoulActivity } from "@/views/assistant/api";
import { parseLedgerTime, whenShort } from "@/views/assistant/format";
import { splitDated } from "@/views/assistant/MemorySection";
import { Section, SpecRow } from "@/views/assistant/Section";

const KNOWN_OPS = new Set(["add", "replace", "remove"]);
const KNOWN_PLACES = new Set(["soul", "memory", "user"]);

export function RecentSection({ activity }: { activity: readonly SoulActivity[] }) {
  const t = useT();
  const lang = useUiLanguage();

  return (
    <Section testId="assistant-recent" title={t("assistant_view.recent_title")}>
      {activity.length === 0 ? (
        <p className="text-base text-muted-foreground">{t("assistant_view.recent_empty")}</p>
      ) : (
        <div className="divide-y divide-border">
          {activity.map((item, i) => {
            const op = KNOWN_OPS.has(item.operation) ? item.operation : "add";
            const place = KNOWN_PLACES.has(item.target)
              ? t(`assistant_view.place_${item.target}`)
              : item.target;
            const said = item.evidence && item.evidence !== "[withheld]" ? item.evidence : "";
            return (
              <SpecRow
                key={`${item.ts}-${i}`}
                label={<span className="tabular-nums">{whenShort(parseLedgerTime(item.ts), lang) ?? "–"}</span>}
              >
                <p className={cn(op === "remove" && "text-muted-foreground line-through decoration-foreground-faint")}>
                  {splitDated(item.text).body || "–"}
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t(`assistant_view.op_${op}`).replace("{0}", place)}
                </p>
                {said && (
                  <p className="mt-2 text-sm italic leading-6 text-muted-foreground">
                    {t("assistant_view.said").replace("{0}", said)}
                  </p>
                )}
              </SpecRow>
            );
          })}
        </div>
      )}
    </Section>
  );
}
