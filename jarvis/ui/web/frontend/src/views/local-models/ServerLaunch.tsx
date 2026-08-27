/**
 * The bar under the grid: what the four picks add up to, and the one button
 * that puts the server behind them.
 *
 * It closes the page the way the grid opens it. The cards ask "which model
 * for this job"; this answers "and can this machine run all of them" — the
 * total on disk, the largest single model against the graphics memory,
 * because that largest one is what has to fit at once. Then one button:
 * install or start the server, download whatever a job still needs, assign
 * every pick, tune it, prove it answers, and remember the autostart.
 *
 * The button never disables itself for an unfinished grid. A job with no
 * model is exactly what the flow is for — refusing to run until the user has
 * done the flow's own job by hand would be backwards. It only waits while a
 * run is underway.
 */
import { Loader2, Play, RotateCw } from "lucide-react";

import { SoftButton, StatusDot } from "@/components/extensions/primitives";
import type { LocalModelRow, RoleRow, ServerResponse } from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";

import { formatGb } from "./localModelsFormat";

export interface ServerLaunchProps {
  /** The writable jobs shown in the grid. */
  rows: RoleRow[];
  models: LocalModelRow[];
  acceleratorGb: number;
  server: ServerResponse | undefined;
  /** Label of the pull-capable card ("Ollama"). */
  serverLabel: string;
  running: boolean;
  busy: boolean;
  onRun: () => void;
  children?: React.ReactNode;
}

/** Distinct models across the jobs, so one model on two jobs counts once. */
export function footprint(
  rows: RoleRow[],
  models: LocalModelRow[],
): { totalBytes: number; largestBytes: number; distinct: number } {
  const byName = new Map(models.map((m) => [m.name, m]));
  const seen = new Set<string>();
  let totalBytes = 0;
  let largestBytes = 0;
  for (const row of rows) {
    if (!row.current || seen.has(row.current)) continue;
    seen.add(row.current);
    const size = byName.get(row.current)?.size_bytes ?? 0;
    totalBytes += size;
    largestBytes = Math.max(largestBytes, size);
  }
  return { totalBytes, largestBytes, distinct: seen.size };
}

export function ServerLaunch({
  rows,
  models,
  acceleratorGb,
  server,
  serverLabel,
  running,
  busy,
  onRun,
  children,
}: ServerLaunchProps) {
  const t = useT();
  const k = (key: string) => t(`local_models.launch.${key}`);
  const { totalBytes, largestBytes } = footprint(rows, models);
  const filled = rows.filter((r) => r.current !== "").length;
  const remote = server?.host_kind === "remote";
  const needsInstall = Boolean(server && !server.installed && !remote);

  const headline =
    filled === 0
      ? k("nothing_picked")
      : filled < rows.length
        ? fill(k("partly_picked"), { filled, total: rows.length })
        : k("all_picked");

  // The largest model is what must be resident at once; the total is what the
  // downloads cost on disk. Two different questions, so two different figures.
  const budgetGb = acceleratorGb;
  const largestGb = largestBytes / 1024 ** 3;
  const fits = budgetGb > 0 && largestGb > 0 ? largestGb <= budgetGb : null;
  const facts = [
    totalBytes > 0 ? fill(k("total_on_disk"), { size: formatGb(totalBytes) }) : "",
    largestBytes > 0 && budgetGb > 0
      ? fill(k(fits ? "largest_fits" : "largest_over"), {
          size: formatGb(largestBytes),
          gb: budgetGb.toFixed(1),
        })
      : "",
  ].filter(Boolean);

  const label = needsInstall
    ? fill(k("action_install"), { server: serverLabel })
    : running
      ? k("action_rerun")
      : fill(k("action_start"), { server: serverLabel });

  return (
    <section
      className="rounded-2xl border border-border bg-card/50 p-4"
      data-testid="server-launch"
      aria-label={k("aria")}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="font-display text-[15px] font-semibold tracking-tight text-foreground">
            {headline}
          </p>
          {facts.length > 0 && (
            <p
              className="mt-1 text-xs text-muted-foreground"
              data-testid="launch-facts"
            >
              {facts.join(" · ")}
            </p>
          )}
          <div className="mt-1.5">
            <StatusDot
              tone={running ? "ok" : needsInstall ? "warn" : "off"}
              label={
                running
                  ? fill(k("server_running"), {
                      server: serverLabel,
                      version: server?.version ?? "",
                    }).trim()
                  : needsInstall
                    ? fill(k("server_absent"), { server: serverLabel })
                    : fill(k("server_stopped"), { server: serverLabel })
              }
            />
          </div>
        </div>

        <span className="shrink-0" data-testid="launch-run">
          <SoftButton
            primary
            onClick={onRun}
            disabled={busy}
            className="h-11 px-5 text-sm"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : running ? (
              <RotateCw className="h-4 w-4" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {label}
          </SoftButton>
        </span>
      </div>
      {children}
    </section>
  );
}
