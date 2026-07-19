/**
 * Phase 9 — Jarvis-Agent worker tab in the MissionsView.
 *
 * Lists per-worker columns (Model / Cost / State-Dir / Logfile /
 * Reattach-Status) for all workers of the selected mission. Data comes from
 * ``missions_routes::extract_worker_missions`` (backend) and is held in the
 * mission store under ``workerSnapshotsByMission``.
 *
 * Empty-state: when the selected mission has no workers (e.g. a pure
 * Claude-/Codex-mission), show a hint instead of an empty table.
 */
import { CircleSlash, Cpu, Copy, ExternalLink } from "lucide-react";
import { useShallow } from "zustand/react/shallow";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { JarvisAgentReattachStatus, JarvisAgentWorkerSnapshot } from "@/types/missions";

import { useMissionsStore } from "./store";

const REATTACH_STYLE: Record<JarvisAgentReattachStatus, string> = {
  live: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300",
  ended: "border-zinc-500/40 bg-zinc-500/10 text-zinc-300",
  killed: "border-destructive/50 bg-destructive/15 text-destructive",
  unknown: "border-amber-400/40 bg-amber-400/10 text-amber-300",
};

const REATTACH_LABEL: Record<JarvisAgentReattachStatus, string> = {
  live: "live",
  ended: "ended",
  killed: "killed",
  unknown: "?",
};

function formatCost(value: number): string {
  if (value <= 0) return "—";
  return `$${value.toFixed(4)}`;
}

function formatTokens(value: number): string {
  if (value <= 0) return "—";
  if (value < 1000) return value.toString();
  if (value < 1_000_000) return `${(value / 1000).toFixed(1)}k`;
  return `${(value / 1_000_000).toFixed(2)}M`;
}

async function copyToClipboard(value: string): Promise<void> {
  await robustCopy(value);
}

export function JarvisAgentPanel() {
  const t = useT();
  const workers = useMissionsStore(
    useShallow((s) => {
      if (!s.selectedMissionId) return [];
      return s.workerSnapshotsByMission[s.selectedMissionId] ?? [];
    }),
  );
  const selectedMissionId = useMissionsStore((s) => s.selectedMissionId);

  if (!selectedMissionId) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
        <Cpu className="h-7 w-7 text-muted-foreground/40" />
        <p>{t("jarvis_agent_panel.select_mission")}</p>
      </div>
    );
  }

  if (workers.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
        <CircleSlash className="h-7 w-7 text-muted-foreground/40" />
        <p>{t("jarvis_agent_panel.no_workers")}</p>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div data-testid="jarvis-agent-worker-list" className="space-y-2 p-3">
        {workers.map((w) => (
          <WorkerRow key={w.worker_id} worker={w} />
        ))}
      </div>
    </ScrollArea>
  );
}

interface WorkerRowProps {
  worker: JarvisAgentWorkerSnapshot;
}

function WorkerRow({ worker }: WorkerRowProps) {
  const t = useT();
  const reattachClass = REATTACH_STYLE[worker.reattach_status] ?? REATTACH_STYLE.unknown;
  const reattachLabel = REATTACH_LABEL[worker.reattach_status] ?? "?";

  return (
    <div
      data-testid="jarvis-agent-worker-row"
      data-worker-id={worker.worker_id}
      className="rounded border border-border/60 bg-card/30 p-2 text-xs"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            <span>w{worker.worker_id.slice(0, 8)}</span>
            {worker.session_id && (
              <span className="font-mono text-muted-foreground/70">
                · sid {worker.session_id.slice(0, 8)}
              </span>
            )}
            {worker.pid > 0 && (
              <span className="font-mono text-muted-foreground/70">
                · pid {worker.pid}
              </span>
            )}
          </div>
          <div
            data-testid="jarvis-agent-model"
            className="mt-1 truncate font-mono text-foreground/90"
            title={worker.model}
          >
            {worker.model || "(unknown model)"}
          </div>
        </div>
        <span
          data-testid="jarvis-agent-reattach-badge"
          data-reattach-status={worker.reattach_status}
          className={cn(
            "rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider",
            reattachClass,
          )}
        >
          {reattachLabel}
        </span>
      </div>

      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[10px]">
        <dt className="text-muted-foreground">Cost</dt>
        <dd
          data-testid="jarvis-agent-cost"
          className="font-mono text-foreground/80 tabular-nums"
        >
          {formatCost(worker.cost_usd)}
          {worker.tokens_used > 0 && (
            <span className="ml-2 text-muted-foreground">
              · {formatTokens(worker.tokens_used)} tok
            </span>
          )}
        </dd>

        <dt className="text-muted-foreground">State-Dir</dt>
        <dd className="flex items-center gap-1 min-w-0">
          <span
            data-testid="jarvis-agent-state-dir"
            className="truncate font-mono text-foreground/80"
            title={worker.state_dir || t("jarvis_agent_panel.not_available")}
          >
            {worker.state_dir || "—"}
          </span>
          {worker.state_dir && (
            <button
              type="button"
              onClick={() => copyToClipboard(worker.state_dir)}
              title={t("jarvis_agent_panel.copy_path")}
              className="shrink-0 text-muted-foreground/70 hover:text-foreground"
            >
              <Copy className="h-3 w-3" />
            </button>
          )}
        </dd>

        <dt className="text-muted-foreground">Logfile</dt>
        <dd className="flex items-center gap-1 min-w-0">
          <span
            data-testid="jarvis-agent-log-path"
            className="truncate font-mono text-foreground/80"
            title={worker.log_path || t("jarvis_agent_panel.not_available")}
          >
            {worker.log_path || "—"}
          </span>
          {worker.log_path && (
            <>
              <button
                type="button"
                onClick={() => copyToClipboard(worker.log_path)}
                title={t("jarvis_agent_panel.copy_path")}
                className="shrink-0 text-muted-foreground/70 hover:text-foreground"
              >
                <Copy className="h-3 w-3" />
              </button>
              <a
                href={`file:///${worker.log_path.replace(/^\//, "")}`}
                onClick={(e) => {
                  // file:// links are often blocked by the browser; copying
                  // to the clipboard on click is the reliable path. The
                  // link stays as a visual hint.
                  e.preventDefault();
                  copyToClipboard(worker.log_path);
                }}
                title={t("jarvis_agent_panel.file_link_copies_path")}
                className="shrink-0 text-muted-foreground/70 hover:text-foreground"
              >
                <ExternalLink className="h-3 w-3" />
              </a>
            </>
          )}
        </dd>

        {worker.ended_reason && (
          <>
            <dt className="text-muted-foreground">{t("jarvis_agent_panel.ended_label")}</dt>
            <dd
              data-testid="jarvis-agent-ended-reason"
              className="font-mono text-foreground/70"
            >
              {worker.ended_reason}
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}
