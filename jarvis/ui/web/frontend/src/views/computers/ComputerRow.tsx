/**
 * The computer list as a quiet table: one row per machine with its provider,
 * state, address, system and live load. The whole row opens the detail page.
 */
import { ChevronRight, Loader2 } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Computer } from "@/lib/computersApi";
import { Meter, StatusLight, formatMemory, loadPct, statusTone } from "./parts";

export function statusLabel(computer: Computer, t: (k: string) => string): string {
  return t(`computers.status_${computer.health.status}`);
}

/** Shared column template so the header and every row line up. */
export const TABLE_COLUMNS =
  "md:grid-cols-[minmax(0,1.6fr)_minmax(0,0.9fr)_minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.3fr)_20px]";

export function ComputerTableHead() {
  const t = useT();
  const cells = [
    t("computers.col_computer"),
    t("computers.col_status"),
    t("computers.col_address"),
    t("computers.col_system"),
    t("computers.col_load"),
  ];
  return (
    <div
      role="row"
      className={cn(
        "hidden gap-x-6 border-b border-border px-5 py-2.5 text-xs font-medium uppercase tracking-wide text-foreground-faint md:grid",
        TABLE_COLUMNS,
      )}
    >
      {cells.map((cell) => (
        <span key={cell} role="columnheader">
          {cell}
        </span>
      ))}
      <span aria-hidden />
    </div>
  );
}

export function ComputerRow({
  computer,
  checking,
  onOpen,
}: {
  computer: Computer;
  checking: boolean;
  onOpen: () => void;
}) {
  const t = useT();
  const { facts, health } = computer;
  const online = health.status === "online";
  const system = [facts?.os_name, facts?.cpu_count ? `${facts.cpu_count} ${t("computers.unit_cpu")}` : null, formatMemory(facts?.mem_total_mb)]
    .filter(Boolean)
    .join(" · ");
  const providerName =
    computer.provider_name ||
    (computer.provider === "generic" ? t("computers.provider_generic") : t(`computers.provider_${computer.provider}`));

  return (
    <li role="row">
      <button
        type="button"
        onClick={onOpen}
        data-testid={`computer-row-${computer.id}`}
        className={cn(
          "group grid w-full grid-cols-1 items-center gap-x-6 gap-y-2 px-5 py-3.5 text-left transition-colors",
          TABLE_COLUMNS,
          "hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        )}
      >
        <span className="flex min-w-0 items-center gap-3" role="cell">
          <ProviderLogo providerId={computer.provider} label={computer.name} />
          <span className="min-w-0">
            <span className="block truncate text-base font-medium text-foreground-strong">{computer.name}</span>
            <span className="block truncate text-xs text-muted-foreground">{providerName}</span>
          </span>
        </span>
        <span className="inline-flex min-w-0 items-center gap-2 text-sm text-foreground-secondary" role="cell">
          {checking || computer.busy ? (
            <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" aria-hidden />
          ) : (
            <StatusLight tone={statusTone(health.status)} />
          )}
          <span className="truncate">{statusLabel(computer, t)}</span>
          {online && health.latency_ms !== null && (
            <span className="shrink-0 tabular-nums text-xs text-foreground-faint">{health.latency_ms} ms</span>
          )}
        </span>
        <span className="truncate font-mono text-xs text-muted-foreground" role="cell">
          {computer.username}@{computer.host === "0.0.0.0" ? "…" : computer.host}
          {computer.port !== 22 ? `:${computer.port}` : ""}
        </span>
        <span className="truncate text-sm text-muted-foreground" role="cell">
          {system || "—"}
        </span>
        <span className="grid min-w-0 grid-cols-3 gap-3" role="cell">
          <Meter compact label={t("computers.meter_cpu")} pct={online ? loadPct(computer) : null} />
          <Meter compact label={t("computers.meter_memory")} pct={online ? health.mem_used_pct : null} />
          <Meter compact label={t("computers.meter_disk")} pct={online ? health.disk_used_pct : null} />
        </span>
        <ChevronRight
          aria-hidden
          className="hidden h-4 w-4 text-foreground-faint transition-transform group-hover:translate-x-0.5 group-hover:text-foreground md:block"
        />
      </button>
    </li>
  );
}
