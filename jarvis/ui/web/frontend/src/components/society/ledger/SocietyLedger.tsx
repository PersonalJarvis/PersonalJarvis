import { useQuery } from "@tanstack/react-query";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import type { SocietyEnvelope } from "@/lib/societyApi";

const LEDGER_LIMIT = 200;

async function fetchLedger(): Promise<SocietyEnvelope[]> {
  const res = await fetch(`/api/society/events?limit=${LEDGER_LIMIT}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`society ledger ${res.status}`);
  const body = (await res.json()) as { events?: SocietyEnvelope[] };
  return Array.isArray(body.events) ? body.events : [];
}

async function fetchSocietyCost(): Promise<number> {
  const res = await fetch("/api/costs/summary?days=0&surface=society", { cache: "no-store" });
  if (!res.ok) throw new Error(`society cost ledger ${res.status}`);
  const body = (await res.json()) as { totals?: { cost_usd?: number } };
  const value = body.totals?.cost_usd;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

export function ledgerCost(costUsd: number): string {
  if (!(costUsd > 0)) return "—";
  return costUsd < 0.01 ? `$${costUsd.toFixed(4)}` : `$${costUsd.toFixed(2)}`;
}

export function ledgerTotalCost(costUsd: number): string {
  if (!(costUsd > 0)) return "$0.00";
  return costUsd < 0.01 ? `$${costUsd.toFixed(4)}` : `$${costUsd.toFixed(2)}`;
}

/**
 * Row-level display cost. Migrated pre-Society missions deliberately keep
 * cost_usd at zero so they cannot affect current budgets; their historical
 * spend survives only as provenance in the payload and is still useful in the
 * Ledger row.
 */
export function ledgerEventCost(event: SocietyEnvelope): number {
  if (Number.isFinite(event.cost_usd) && event.cost_usd > 0) return event.cost_usd;
  const historical = event.payload?.historic_cost_usd;
  return typeof historical === "number" && Number.isFinite(historical) && historical > 0
    ? historical
    : 0;
}

function detail(event: SocietyEnvelope): string {
  const payload = event.payload ?? {};
  for (const key of ["text", "done", "reason", "status"]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export function SocietyLedger() {
  const t = useT();
  const query = useQuery({
    queryKey: ["society", "ledger"],
    queryFn: fetchLedger,
    staleTime: 2_000,
    refetchInterval: 5_000,
    retry: false,
  });
  const costQuery = useQuery({
    queryKey: ["society", "ledger", "cost"],
    queryFn: fetchSocietyCost,
    staleTime: 10_000,
    refetchInterval: 15_000,
    retry: false,
  });
  const events = [...(query.data ?? [])].reverse();

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="society-ledger">
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-3">
        <div>
          <h2 className="font-display text-base font-semibold text-foreground">
            {t("society.ledger.title")}
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("society.ledger.subtitle")}
          </p>
        </div>
        <div className="shrink-0 text-right" data-testid="society-ledger-total-cost">
          <div className="text-[11px] text-muted-foreground">
            {t("society.ledger.total_cost")}
          </div>
          <div className="font-mono text-sm font-medium text-foreground">
            {costQuery.isLoading || costQuery.isError
              ? "—"
              : ledgerTotalCost(costQuery.data ?? 0)}
          </div>
        </div>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {query.isError ? (
          <p role="alert" className="px-5 py-6 text-sm text-destructive">
            {t("society.ledger.error")}
          </p>
        ) : events.length === 0 ? (
          <p className="px-5 py-6 text-sm text-muted-foreground">
            {query.isLoading ? t("society.world.loading") : t("society.ledger.empty")}
          </p>
        ) : (
          <div className="overflow-x-auto px-5 py-4">
            <table className="w-full min-w-[760px] border-collapse text-left text-xs">
              <caption className="sr-only">{t("society.ledger.title")}</caption>
              <thead className="text-muted-foreground">
                <tr className="border-b border-border">
                  <th scope="col" className="px-2 py-2 font-medium">{t("society.ledger.time")}</th>
                  <th scope="col" className="px-2 py-2 font-medium">{t("society.ledger.type")}</th>
                  <th scope="col" className="px-2 py-2 font-medium">{t("society.ledger.from")}</th>
                  <th scope="col" className="px-2 py-2 font-medium">{t("society.ledger.to")}</th>
                  <th scope="col" className="px-2 py-2 font-medium">{t("society.ledger.detail")}</th>
                  <th scope="col" className="px-2 py-2 text-right font-medium">{t("society.ledger.cost")}</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.event_id} className="border-b border-border/60 align-top">
                    <td className="whitespace-nowrap px-2 py-2 text-muted-foreground">
                      {new Date(event.ts_ms).toLocaleString()}
                    </td>
                    <td className="px-2 py-2 font-mono text-foreground">{event.msg_type}</td>
                    <td className="px-2 py-2 text-foreground">{event.from_agent}</td>
                    <td className="px-2 py-2 text-foreground">
                      {event.to_agent ?? t("society.ledger.board")}
                    </td>
                    <td className="max-w-[420px] px-2 py-2 text-muted-foreground">
                      <span className="line-clamp-2">{detail(event) || "—"}</span>
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 text-right font-mono text-foreground">
                      {ledgerCost(ledgerEventCost(event))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

export default SocietyLedger;
