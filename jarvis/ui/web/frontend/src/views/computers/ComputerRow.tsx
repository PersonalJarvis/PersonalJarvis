/**
 * One machine as a settings row: name and one quiet line (provider, system,
 * login) on the left, its load and state on the right. The whole row opens
 * the detail page.
 */
import { ChevronRight, Loader2 } from "lucide-react";
import { useT } from "@/i18n";
import type { Computer } from "@/lib/computersApi";
import { StatusLight, formatMemory, loadPct, statusTone } from "./parts";

export function statusLabel(computer: Computer, t: (k: string) => string): string {
  return t(`computers.status_${computer.health.status}`);
}

/** Ink until a vital gets tight, then the status hues. */
function loadClass(pct: number): string {
  if (pct >= 90) return "text-destructive";
  if (pct >= 75) return "text-warning";
  return "text-muted-foreground";
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
  const busy = checking || computer.busy;
  const providerName =
    computer.provider_name ||
    (computer.provider === "generic" ? t("computers.provider_generic") : t(`computers.provider_${computer.provider}`));
  const login = `${computer.username}@${computer.host === "0.0.0.0" ? "…" : computer.host}${computer.port !== 22 ? `:${computer.port}` : ""}`;
  const details = [
    providerName,
    facts?.os_name,
    facts?.cpu_count ? `${facts.cpu_count} ${t("computers.unit_cpu")}` : null,
    formatMemory(facts?.mem_total_mb),
  ]
    .filter(Boolean)
    .join(" · ");
  const loads = online
    ? [
        { label: t("computers.meter_cpu"), pct: loadPct(computer) },
        { label: t("computers.meter_memory"), pct: health.mem_used_pct },
        { label: t("computers.meter_disk"), pct: health.disk_used_pct },
      ].filter((load): load is { label: string; pct: number } => load.pct !== null)
    : [];

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`computer-row-${computer.id}`}
      className="group flex w-full items-center gap-6 px-4 py-3.5 text-left transition-colors hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-base font-medium text-foreground">{computer.name}</span>
        <span className="mt-0.5 block truncate text-sm text-muted-foreground">
          {details}
          <span className="font-mono text-xs text-foreground-faint"> · {login}</span>
        </span>
      </span>

      {loads.length > 0 && (
        <span className="hidden shrink-0 items-center gap-3 text-sm tabular-nums lg:flex">
          {loads.map((load) => (
            <span key={load.label} className={loadClass(load.pct)}>
              {load.label} {Math.round(Math.max(0, Math.min(100, load.pct)))} %
            </span>
          ))}
        </span>
      )}

      <span className="inline-flex shrink-0 items-center gap-2 text-sm text-foreground-secondary">
        {busy ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden />
        ) : (
          <StatusLight tone={statusTone(health.status)} className="h-2 w-2 [&>span]:h-2 [&>span]:w-2" />
        )}
        {statusLabel(computer, t)}
        {online && health.latency_ms !== null && (
          <span className="tabular-nums text-foreground-faint">{health.latency_ms} ms</span>
        )}
      </span>

      <ChevronRight
        aria-hidden
        className="h-4 w-4 shrink-0 text-foreground-faint transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
      />
    </button>
  );
}
