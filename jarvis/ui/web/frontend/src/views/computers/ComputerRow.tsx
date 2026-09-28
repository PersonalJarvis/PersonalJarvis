/**
 * One machine in the list — a "rack unit": identity on the left, its state
 * in the middle, three live meters on the right. The whole row opens the
 * detail page.
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
  const tone = statusTone(health.status);
  const online = health.status === "online";
  const specs = [
    facts?.os_name,
    facts?.cpu_count ? `${facts.cpu_count} ${t("computers.unit_cpu")}` : null,
    formatMemory(facts?.mem_total_mb),
    computer.region,
  ].filter(Boolean);

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        data-testid={`computer-row-${computer.id}`}
        className={cn(
          "group grid w-full grid-cols-1 items-center gap-x-6 gap-y-3 px-5 py-4 text-left transition-colors",
          "md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_20px]",
          "hover:bg-secondary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        )}
      >
        <div className="flex min-w-0 items-center gap-3.5">
          <ProviderLogo providerId={computer.provider} label={computer.name} />
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2.5">
              <span className="truncate text-title font-medium text-foreground-strong">
                {computer.name}
              </span>
              <span className="inline-flex shrink-0 items-center gap-1.5 text-sm text-muted-foreground">
                {checking || computer.busy ? (
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                ) : (
                  <StatusLight tone={tone} />
                )}
                {statusLabel(computer, t)}
                {online && health.latency_ms !== null && (
                  <span className="tabular-nums text-foreground-faint">· {health.latency_ms} ms</span>
                )}
              </span>
            </div>
            <div className="mt-0.5 flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
              <span className="truncate font-mono text-xs">
                {computer.username}@{computer.host === "0.0.0.0" ? "…" : computer.host}
                {computer.port !== 22 ? `:${computer.port}` : ""}
              </span>
              {specs.length > 0 && (
                <span className="hidden truncate lg:inline">· {specs.join(" · ")}</span>
              )}
            </div>
          </div>
        </div>

        <div className="grid min-w-0 grid-cols-3 gap-4">
          <Meter compact label={t("computers.meter_cpu")} pct={online ? loadPct(computer) : null} />
          <Meter compact label={t("computers.meter_memory")} pct={online ? health.mem_used_pct : null} />
          <Meter compact label={t("computers.meter_disk")} pct={online ? health.disk_used_pct : null} />
        </div>

        <ChevronRight
          aria-hidden
          className="hidden h-4 w-4 text-foreground-faint transition-transform group-hover:translate-x-0.5 group-hover:text-foreground md:block"
        />
      </button>
    </li>
  );
}
