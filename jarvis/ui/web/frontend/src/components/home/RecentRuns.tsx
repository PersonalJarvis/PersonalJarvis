import { useState } from "react";

import { useRuns } from "@/hooks/useRuns";
import { OutcomeDot } from "@/components/runs/OutcomeBadge";
import type { RunListItem } from "@/components/runs/types";
import { useEventStore } from "@/store/events";
import { useRunFocusStore } from "@/store/runFocus";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { SidebarGroup } from "@/components/home/SidebarGroup";

/** How many runs the block shows folded. The sketch says three. */
export const RECENT_RUNS_FOLDED = 3;
/** How many it shows unfolded — enough to scan, never a second inspector. */
export const RECENT_RUNS_UNFOLDED = 12;

/**
 * The last runs, at a glance, from the sidebar (maintainer sketch,
 * 2026-08-23): outcome dot, what was said, when, and how long. A row opens
 * the Run Inspector on that run. Reads the same query the inspector uses, so
 * there is one fetch and one cache for both.
 */
export function RecentRuns() {
  const t = useT();
  const { data: runs } = useRuns();
  const [open, setOpen] = useState(false);
  const setActive = useEventStore((s) => s.setActiveSection);
  const focus = useRunFocusStore((s) => s.focus);

  const all = runs ?? [];
  const shown = all.slice(0, open ? RECENT_RUNS_UNFOLDED : RECENT_RUNS_FOLDED);
  const canExpand = all.length > RECENT_RUNS_FOLDED;

  return (
    <SidebarGroup
      title={t("sidebar.recent_runs")}
      action={
        canExpand
          ? {
              label: open ? t("sidebar.show_less") : t("sidebar.show_all"),
              onClick: () => setOpen((v) => !v),
              expanded: open,
            }
          : undefined
      }
      testId="recent-runs"
    >
      {shown.length === 0 ? (
        <p className="px-2 py-1 text-[11px] text-muted-foreground/70">{t("sidebar.no_runs")}</p>
      ) : (
        <ul className="space-y-px">
          {shown.map((run) => (
            <RunRow
              key={run.session_id}
              run={run}
              onOpen={() => {
                focus(run.session_id);
                setActive("run_inspector");
              }}
            />
          ))}
        </ul>
      )}
    </SidebarGroup>
  );
}

function RunRow({ run, onOpen }: { run: RunListItem; onOpen: () => void }) {
  const t = useT();
  const live = run.ended_ms === null;
  const title = run.preview || run.session_id.slice(0, 8);
  const meta = live
    ? t("sidebar.run_live")
    : fill(t("sidebar.run_meta"), {
        turns: run.turn_count,
        duration: formatDuration(run.duration_s),
      });
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        title={title}
        data-testid="recent-run-row"
        className={cn(
          "group grid w-full grid-cols-[auto_1fr_auto] items-center gap-x-2 rounded-lg px-2 py-1.5 text-left transition-colors",
          "hover:bg-background/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        )}
      >
        <OutcomeDot
          outcome={live ? "live" : run.outcome}
          className={cn(live && "bg-primary animate-jarvis-pulse")}
        />
        <span className="truncate text-xs text-foreground">{title}</span>
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
          {formatClock(run.started_ms)}
        </span>
        <span className="col-start-2 truncate font-mono text-[10px] text-muted-foreground/80">
          {meta}
        </span>
      </button>
    </li>
  );
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m < 60) return rest ? `${m}m ${rest}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatClock(ms: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (d.getTime() >= today.getTime()) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
