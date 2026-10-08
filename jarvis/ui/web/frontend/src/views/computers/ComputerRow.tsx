/**
 * Portions adapted from pingdotgg/t3code @ 12069ee (apps/web EnvironmentRow,
 * SavedBackendListRow: a dimmed switched-off row, a routes toggle in the
 * subtitle, the status tooltip), MIT License, Copyright (c) 2026 T3 Tools Inc.
 * Full text: third_party/t3code/LICENSE.
 *
 * One machine in the grouped list: the glyph of its kind, its name and ONE
 * subtitle line (how it is reached, its state, its system), then any tight
 * vital, its status dot, the on/off switch and a chevron. The name opens the
 * detail page; "N addresses" unfolds the routes under the row.
 */
import { useState } from "react";
import { Check, ChevronRight, Copy } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useUpdateComputer } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { Computer, RouteSuggestion } from "@/lib/computersApi";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { MachineIcon, wearsMachineGlyph } from "./machineKind";
import { formatAgo, formatMemory, loadPct, statusTone } from "./parts";
import { RouteList } from "./RouteList";
import { StatusDot } from "./surface";

export function statusLabel(computer: Computer, t: (k: string) => string): string {
  return t(`computers.status_${computer.health.status}`);
}

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_m, key: string) => values[key] ?? "");
}

/** A vital earns a place in the row only once it gets tight. */
const TIGHT_PCT = 75;

export function ComputerRow({
  computer,
  checking,
  onOpen,
  suggestions = [],
}: {
  computer: Computer;
  checking: boolean;
  onOpen: () => void;
  /** Tailscale addresses this computer could add. */
  suggestions?: RouteSuggestion[];
}) {
  const t = useT();
  const update = useUpdateComputer();
  const [routesOpen, setRoutesOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const { facts, health } = computer;
  const enabled = computer.enabled !== false;
  const online = enabled && health.status === "online";
  const tone = enabled ? statusTone(health.status) : "off";
  const busy = enabled && (checking || computer.busy);
  const login = `${computer.username}@${computer.host === "0.0.0.0" ? "…" : computer.host}${computer.port !== 22 ? `:${computer.port}` : ""}`;
  const status = enabled
    ? [statusLabel(computer, t), online && health.latency_ms !== null ? `${health.latency_ms} ms` : null]
        .filter(Boolean)
        .join(" ")
    : t("computers.row_switched_off");
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
  const routeCount = computer.routes?.length ?? 1;
  const showRoutes = computer.kind !== "local_vm";
  const failed = enabled && !online && Boolean(health.trace_id);
  const tooltip = [
    enabled && health.message && !online ? health.message : status,
    health.checked_at ? fill(t("computers.status_last_check"), { ago: formatAgo(health.checked_at, t) }) : null,
    failed && health.trace_id ? fill(t("computers.status_error_id"), { id: health.trace_id }) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div data-testid={`computer-item-${computer.id}`} data-enabled={enabled}>
      <div className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 px-3 py-2.5 transition-colors hover:bg-secondary/50 sm:px-4">
        <button
          type="button"
          onClick={onOpen}
          data-testid={`computer-row-${computer.id}`}
          className={cn(
            "flex min-w-0 items-center gap-3 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            !enabled && "opacity-60",
          )}
        >
          {wearsMachineGlyph(computer.provider) ? (
            <MachineIcon computer={computer} aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
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
        </button>
        <span className="flex shrink-0 items-center gap-2.5">
          {tight.map((v) => (
            <span
              key={v.label}
              className={cn("hidden text-xs tabular-nums md:inline", v.pct >= 90 ? "text-destructive" : "text-warning")}
            >
              {v.label} {Math.round(Math.min(100, v.pct))} %
            </span>
          ))}
          {showRoutes && (
            <button
              type="button"
              aria-expanded={routesOpen}
              onClick={() => setRoutesOpen((open) => !open)}
              data-testid={`computer-routes-toggle-${computer.id}`}
              className="inline-flex items-center gap-0.5 rounded-sm text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-1 focus-visible:ring-ring"
            >
              {routeCount === 1
                ? t("computers.row_routes_one")
                : fill(t("computers.row_routes_many"), { count: String(routeCount) })}
              {suggestions.length > 0 && <span aria-hidden className="ml-1 h-1.5 w-1.5 rounded-full bg-accent" />}
              <ChevronRight
                aria-hidden
                className={cn("h-3 w-3 transition-transform motion-reduce:transition-none", routesOpen && "rotate-90")}
              />
            </button>
          )}
          {failed && health.trace_id && (
            <button
              type="button"
              aria-label={copied ? t("computers.copied_error_id") : t("computers.copy_error_id")}
              onClick={() => {
                void robustCopy(health.trace_id ?? "").then((ok) => {
                  if (!ok) return;
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1600);
                });
              }}
              className="rounded p-0.5 text-foreground-faint hover:bg-secondary hover:text-foreground"
              data-testid={`computer-copy-error-${computer.id}`}
            >
              {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          )}
          <QuickTooltip content={tooltip} side="top" className="inline-flex">
            {/* A check in flight pings the dot instead of swapping it for a spinner. */}
            <StatusDot tone={busy ? "busy" : tone} label={status} />
          </QuickTooltip>
          <Switch
            checked={enabled}
            disabled={update.isPending}
            onCheckedChange={(next) => update.mutate({ id: computer.id, patch: { enabled: next } })}
            aria-label={fill(t("computers.row_switch_label"), { computer: computer.name })}
            data-testid={`computer-switch-${computer.id}`}
          />
          <button
            type="button"
            tabIndex={-1}
            aria-hidden
            onClick={onOpen}
            className="text-foreground-faint transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </span>
      </div>
      {showRoutes && routesOpen && (
        <div className="pl-10 pr-3 sm:pl-11 sm:pr-4">
          <RouteList computer={computer} suggestions={suggestions} />
        </div>
      )}
    </div>
  );
}
