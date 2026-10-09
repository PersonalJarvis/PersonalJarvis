/**
 * Portions adapted from pingdotgg/t3code @ 12069ee (apps/web EnvironmentRow),
 * MIT License, Copyright (c) 2026 T3 Tools Inc. Full text:
 * third_party/t3code/LICENSE.
 *
 * One machine in the grouped list: its glyph, its name and ONE subtitle line
 * (how it is reached, its state, its system), then any tight vital, the
 * status dot and a chevron. The whole row opens the detail page.
 */
import { ChevronRight, Loader2 } from "lucide-react";
import { ComputersIcon } from "@/components/icons/sectionIcons";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Computer } from "@/lib/computersApi";
import { formatMemory, loadPct, statusTone } from "./parts";
import { StatusDot } from "./surface";

export function statusLabel(computer: Computer, t: (k: string) => string): string {
  return t(`computers.status_${computer.health.status}`);
}

/** Providers without a brand mark of their own wear the section's cloud. */
const CLOUD_MARK_PROVIDERS = new Set(["generic", "home_server", "strato"]);

/** A vital earns a place in the row only once it gets tight. */
const TIGHT_PCT = 75;

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
  const login = `${computer.username}@${computer.host === "0.0.0.0" ? "…" : computer.host}${computer.port !== 22 ? `:${computer.port}` : ""}`;
  const status = [statusLabel(computer, t), online && health.latency_ms !== null ? `${health.latency_ms} ms` : null]
    .filter(Boolean)
    .join(" ");
  const system = [
    facts?.os_name,
    facts?.cpu_count ? `${facts.cpu_count} ${t("computers.unit_cpu")}` : null,
    formatMemory(facts?.mem_total_mb),
  ]
    .filter(Boolean)
    .join(" · ");
  const tight = online
    ? [
        { label: t("computers.meter_cpu"), pct: loadPct(computer) },
        { label: t("computers.meter_memory"), pct: health.mem_used_pct },
        { label: t("computers.meter_disk"), pct: health.disk_used_pct },
      ].filter((v): v is { label: string; pct: number } => v.pct !== null && v.pct >= TIGHT_PCT)
    : [];

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`computer-row-${computer.id}`}
      className="group grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 px-3 py-2.5 text-left transition-colors hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-4"
    >
      {CLOUD_MARK_PROVIDERS.has(computer.provider) ? (
        <ComputersIcon aria-hidden className="h-4 w-4 text-muted-foreground" />
      ) : (
        <ProviderLogo providerId={computer.provider} label={computer.name} size="sm" />
      )}
      <span className="min-w-0">
        <span className="block truncate text-base font-medium text-foreground">{computer.name}</span>
        <span className="block truncate text-xs text-muted-foreground">
          <span className="font-mono">SSH {login}</span>
          <span aria-hidden> · </span>
          <span className={cn(tone === "error" && "text-destructive", tone === "warn" && "text-warning")}>{status}</span>
          {system && <span> · {system}</span>}
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-2.5">
        {tight.map((v) => (
          <span
            key={v.label}
            className={cn("hidden text-xs tabular-nums sm:inline", v.pct >= 90 ? "text-destructive" : "text-warning")}
          >
            {v.label} {Math.round(Math.min(100, v.pct))} %
          </span>
        ))}
        {busy ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden />
        ) : (
          <StatusDot tone={tone} label={status} />
        )}
        <ChevronRight
          aria-hidden
          className="h-4 w-4 text-foreground-faint transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
        />
      </span>
    </button>
  );
}
