/**
 * "Is everything really in there?" — the completeness proof, per source.
 *
 * A total cannot answer that question. "4 689 items" is equally consistent
 * with *all of it* and with *as much as we managed before something quietly
 * stopped*, and the maintainer asked precisely because the number alone gave
 * no way to tell.
 *
 * So this compares two independently produced numbers: how many items the last
 * finished import actually READ (new + changed + unchanged, counted by the
 * sync run) against how many the store HOLDS for that source. Agreement is a
 * real check — the two are produced by different code paths at different
 * times — and disagreement prints the gap instead of a reassuring total.
 *
 * Four verdicts, because "not complete" has genuinely different causes: never
 * imported, ended early, stored fewer than were read, or fine.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, MinusCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { formatRelativeTime } from "@/components/ultrawiki/relativeTime";
import {
  fetchUltraWikiReconcile,
  type UltraWikiReconcile,
  type UltraWikiReconcileRow,
} from "@/lib/ultrawikiApi";

const VERDICT_STYLE: Record<string, { icon: typeof CheckCircle2; tone: string }> = {
  complete: { icon: CheckCircle2, tone: "text-emerald-500" },
  short: { icon: AlertTriangle, tone: "text-[#ffb84d]" },
  incomplete: { icon: AlertTriangle, tone: "text-[#ffb84d]" },
  never_imported: { icon: MinusCircle, tone: "text-muted-foreground" },
};

export function ReconcileStrip({
  refreshKey,
}: {
  /** Bumped by the parent after an import, so the proof re-runs. */
  refreshKey?: number;
}): JSX.Element | null {
  const t = useT();
  // Plain state + effect rather than react-query on purpose: the Contents tab
  // renders without a QueryClientProvider, and a strip that reports on the
  // data must not dictate how its host is mounted.
  const [data, setData] = useState<UltraWikiReconcile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchUltraWikiReconcile()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch(() => {
        // A failed check must not replace the inventory with an error — it
        // simply says nothing, and the counts below still stand.
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (loading) {
    return (
      <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        {t("ultrawiki.reconcile.checking")}
      </p>
    );
  }
  // Defensive on the SHAPE, not just on the error: this strip sits above the
  // inventory, so an unexpected payload must make it disappear quietly rather
  // than take the whole Contents tab down with it.
  const rows = Array.isArray(data?.sources) ? data.sources : [];
  if (!data || rows.length === 0) return null;

  return (
    <div
      className={cn(
        "rounded-xl border px-4 py-3",
        data.all_complete
          ? "border-emerald-500/30 bg-emerald-500/[0.05]"
          : "border-[#ffb84d]/30 bg-[#ffb84d]/[0.05]",
      )}
      data-testid="ultrawiki-reconcile"
      data-all-complete={data.all_complete ? "true" : "false"}
    >
      <p
        className={cn(
          "text-sm font-medium",
          data.all_complete ? "text-emerald-500" : "text-[#ffb84d]",
        )}
      >
        {data.all_complete
          ? t("ultrawiki.reconcile.all_complete").replace(
              "{0}",
              String(data.total_stored ?? 0),
            )
          : t("ultrawiki.reconcile.partial")
              .replace("{0}", String(data.complete ?? 0))
              .replace("{1}", String(data.total_sources ?? rows.length))}
      </p>
      <ul className="mt-2 space-y-1.5">
        {rows.map((row) => (
          <ReconcileRow key={row.source_id} row={row} />
        ))}
      </ul>
    </div>
  );
}

function ReconcileRow({ row }: { row: UltraWikiReconcileRow }): JSX.Element {
  const t = useT();
  const style = VERDICT_STYLE[row.verdict] ?? VERDICT_STYLE.never_imported;
  const Icon = style.icon;
  return (
    <li
      className="flex items-start gap-2"
      data-testid={`ultrawiki-reconcile-${row.source_id}`}
      data-verdict={row.verdict}
    >
      <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", style.tone)} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-[11px] text-foreground">
          <span className="font-medium">{row.label}</span>
          {" — "}
          {row.detail}
        </p>
        {row.verdict !== "never_imported" && (
          // The raw numbers, because the sentence above is a conclusion and
          // this is the evidence it was drawn from.
          <p className="text-[10px] text-muted-foreground">
            {t("ultrawiki.reconcile.numbers")
              .replace("{0}", String(row.read))
              .replace("{1}", String(row.new))
              .replace("{2}", String(row.changed))
              .replace("{3}", String(row.unchanged))
              .replace("{4}", String(row.stored))}
            {row.finished_at ? ` · ${formatRelativeTime(row.finished_at, t)}` : ""}
          </p>
        )}
      </div>
    </li>
  );
}
