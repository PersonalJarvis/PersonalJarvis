/**
 * One machine as a card: its mark with a live status badge, name and system,
 * a status pill over the address, and three load gauges. The whole card
 * opens the detail page.
 */
import { ChevronRight, Loader2 } from "lucide-react";
import { ComputersIcon } from "@/components/icons/sectionIcons";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Computer } from "@/lib/computersApi";
import { StatusLight, fillClass, formatMemory, loadPct, statusTone, type Tone } from "./parts";

export function statusLabel(computer: Computer, t: (k: string) => string): string {
  return t(`computers.status_${computer.health.status}`);
}

/** Providers without a brand mark of their own wear the section's cloud. */
const CLOUD_MARK_PROVIDERS = new Set(["generic", "home_server", "strato"]);

const TONE_PILL: Record<Tone, string> = {
  ok: "bg-success/10 text-success",
  busy: "bg-info/10 text-info",
  warn: "bg-warning/10 text-warning",
  error: "bg-destructive/10 text-destructive",
  off: "bg-secondary text-muted-foreground",
};

function ComputerMark({ computer }: { computer: Computer }) {
  if (CLOUD_MARK_PROVIDERS.has(computer.provider)) {
    return (
      <span
        aria-hidden
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent ring-1 ring-inset ring-accent/20"
      >
        <ComputersIcon className="h-5 w-5" strokeWidth={1.8} />
      </span>
    );
  }
  return <ProviderLogo providerId={computer.provider} label={computer.name} className="h-11 w-11 rounded-xl" />;
}

/** One vital: label, the value large, the level beneath. */
function Gauge({ label, pct }: { label: string; pct: number | null }) {
  const clamped = pct === null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div className="min-w-0" role="group" aria-label={label}>
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-base font-semibold tabular-nums text-foreground-strong">
        {pct === null ? (
          <span className="text-foreground-faint">—</span>
        ) : (
          <>
            {Math.round(clamped)}
            <span className="ml-0.5 text-xs font-medium text-muted-foreground">%</span>
          </>
        )}
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-secondary"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct === null ? undefined : Math.round(clamped)}
        aria-label={label}
      >
        <div
          className={cn("h-full rounded-full transition-[width] duration-700 ease-out", fillClass(clamped))}
          style={{ width: `${clamped}%` }}
        />
      </div>
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
  const tone = statusTone(health.status);
  const busy = checking || computer.busy;
  const system = [facts?.os_name, facts?.cpu_count ? `${facts.cpu_count} ${t("computers.unit_cpu")}` : null, formatMemory(facts?.mem_total_mb)]
    .filter(Boolean)
    .join(" · ");
  const providerName =
    computer.provider_name ||
    (computer.provider === "generic" ? t("computers.provider_generic") : t(`computers.provider_${computer.provider}`));

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        data-testid={`computer-row-${computer.id}`}
        className={cn(
          "group grid w-full grid-cols-1 items-center gap-x-8 gap-y-4 rounded-xl border border-border bg-card px-5 py-4 text-left transition-colors",
          "md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(15rem,1.1fr)_1rem]",
          "hover:border-border-strong hover:bg-secondary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <span className="flex min-w-0 items-center gap-3.5">
          <span className="relative shrink-0">
            <ComputerMark computer={computer} />
            {!busy && (
              <StatusLight tone={tone} className="absolute -bottom-0.5 -right-0.5 rounded-full ring-[3px] ring-card" />
            )}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-base font-semibold text-foreground-strong">{computer.name}</span>
            <span className="block truncate text-sm text-muted-foreground">
              {providerName}
              {system && <span className="text-foreground-faint"> · {system}</span>}
            </span>
          </span>
        </span>

        <span className="flex min-w-0 flex-col items-start gap-1.5">
          <span
            className={cn(
              "inline-flex h-6 max-w-full items-center gap-1.5 rounded-full px-2.5 text-xs font-medium",
              TONE_PILL[tone],
            )}
          >
            {busy && <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden />}
            <span className="truncate">{statusLabel(computer, t)}</span>
            {online && health.latency_ms !== null && (
              <span className="shrink-0 tabular-nums opacity-70">· {health.latency_ms} ms</span>
            )}
          </span>
          <span className="max-w-full truncate font-mono text-xs text-muted-foreground">
            {computer.username}@{computer.host === "0.0.0.0" ? "…" : computer.host}
            {computer.port !== 22 ? `:${computer.port}` : ""}
          </span>
        </span>

        <span className="grid min-w-0 grid-cols-3 gap-4">
          <Gauge label={t("computers.meter_cpu")} pct={online ? loadPct(computer) : null} />
          <Gauge label={t("computers.meter_memory")} pct={online ? health.mem_used_pct : null} />
          <Gauge label={t("computers.meter_disk")} pct={online ? health.disk_used_pct : null} />
        </span>

        <ChevronRight
          aria-hidden
          className="hidden h-4 w-4 text-foreground-faint transition-transform group-hover:translate-x-0.5 group-hover:text-foreground md:block"
        />
      </button>
    </li>
  );
}
