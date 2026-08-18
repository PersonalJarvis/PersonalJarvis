import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  FolderOpen,
  Gauge,
  MessagesSquare,
  Terminal,
} from "lucide-react";
import { useEventStore } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { useRuns } from "@/hooks/useRuns";
import { useOutputsList, type OutputSummary } from "@/hooks/useOutputs";
import { fetchIdeState, fetchTerminalActivity, type IdeState, type ActivityResponse } from "@/lib/agenticIdeApi";
import { useCommandActivityStore } from "@/store/commandActivity";
import type { RunListItem } from "@/components/runs/types";
import { DeckCard } from "@/components/deck/DeckCard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The deck's activity cards — each one a small window onto a section, fed by
 * the data that section already has. Every figure is real; a card that has
 * nothing to show says so in one line rather than inventing a placeholder.
 */

// ----------------------------------------------------------------------
// Runs — the run inspector's list, newest first
// ----------------------------------------------------------------------

function outcomeTone(outcome: string): string {
  const o = outcome.toLowerCase();
  if (o.includes("error") || o.includes("fail")) return "bg-destructive";
  if (o.includes("ok") || o.includes("success") || o.includes("done")) return "bg-emerald-400";
  if (o.includes("running") || o.includes("open") || o.includes("live")) return "bg-primary animate-pulse";
  return "bg-muted-foreground";
}

function fmtClock(ms: number): string {
  if (!ms) return "";
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function RunsCard({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const runs = useRuns();
  const items: RunListItem[] = (runs.data ?? []).slice(0, 6);
  const live = items.some((r) => r.ended_ms === null);

  return (
    <DeckCard
      icon={Gauge}
      title={t("deck.card_runs")}
      meta={runs.data ? runs.data.length : undefined}
      live={live}
      variant="chamfer"
      onOpen={() => setActiveSection("run_inspector")}
      openLabel={t("deck.open_section")}
      className={className}
      bodyClassName="overflow-y-auto"
    >
      {items.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {runs.isError ? t("deck.unavailable") : t("deck.runs_empty")}
        </p>
      ) : (
        <ul className="space-y-1">
          {items.map((r) => (
            <li key={r.session_id} className="flex items-center gap-2 text-[11px]">
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", outcomeTone(r.outcome))} aria-hidden />
              <span className="min-w-0 flex-1 truncate text-foreground">
                {r.preview || r.outcome || r.session_id.slice(0, 8)}
              </span>
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                {r.turn_count > 0 ? `${r.turn_count}t · ` : ""}
                {fmtClock(r.started_ms)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </DeckCard>
  );
}

// ----------------------------------------------------------------------
// Outputs — what the sub-agents delivered
// ----------------------------------------------------------------------

const OUTPUT_TONE: Record<string, string> = {
  running: "bg-primary animate-pulse",
  success: "bg-emerald-400",
  error: "bg-destructive",
  cancelled: "bg-muted-foreground",
  unknown: "bg-muted-foreground",
};

export function OutputsCard({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const outputs = useOutputsList();
  const items: OutputSummary[] = (outputs.data ?? []).slice(0, 6);
  const running = items.filter((o) => o.status === "running").length;

  return (
    <DeckCard
      icon={FolderOpen}
      title={t("deck.card_outputs")}
      meta={running > 0 ? running : outputs.data ? outputs.data.length : undefined}
      live={running > 0}
      variant="chamfer"
      onOpen={() => setActiveSection("outputs")}
      openLabel={t("deck.open_section")}
      className={className}
      bodyClassName="overflow-y-auto"
    >
      {items.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {outputs.isError ? t("deck.unavailable") : t("deck.outputs_empty")}
        </p>
      ) : (
        <ul className="space-y-1">
          {items.map((o) => (
            <li key={o.slug} className="flex items-center gap-2 text-[11px]">
              <span
                className={cn("h-1.5 w-1.5 shrink-0 rounded-full", OUTPUT_TONE[o.status ?? "unknown"])}
                aria-hidden
              />
              <span className="min-w-0 flex-1 truncate text-foreground">
                {o.utterance || o.summary || o.slug}
              </span>
              {typeof o.artifact_count === "number" && o.artifact_count > 0 && (
                <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                  {o.artifact_count}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </DeckCard>
  );
}

// ----------------------------------------------------------------------
// IDE grid — the coding workspace, shrunk to its panes' states
// ----------------------------------------------------------------------

/**
 * A miniature of the Agentic IDE's terminal grid, WITHOUT mounting a single
 * terminal. The IDE's panes are live xterm instances bound to PTY streams,
 * and a second mounted copy steals every pane's output from the first
 * (MainView's sticky-mount comment explains the defect). So this is a status
 * mirror: one tile per pane, its agent, and what it is doing — read from the
 * same endpoints the IDE polls, and nothing more.
 */
const ACTIVITY_TONE: Record<string, string> = {
  starting: "bg-amber-400 animate-pulse",
  working: "bg-primary animate-pulse",
  waiting: "bg-sky-400",
  done: "bg-emerald-400",
  idle: "bg-muted-foreground",
  stopped: "bg-muted-foreground",
  error: "bg-destructive",
};

export function IdeGridCard({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const state = useQuery<IdeState>({
    queryKey: ["deck", "ide-state"],
    queryFn: fetchIdeState,
    refetchInterval: 5_000,
    retry: false,
  });
  const activeId = state.data?.active_id ?? undefined;
  const activity = useQuery<ActivityResponse>({
    queryKey: ["deck", "ide-activity", activeId ?? "-"],
    queryFn: () => fetchTerminalActivity(activeId),
    enabled: Boolean(state.data?.active),
    refetchInterval: 3_000,
    retry: false,
  });

  const panes = state.data?.session?.terminals ?? [];
  const activityByKey = useMemo(() => {
    const m = new Map<string, string>();
    for (const row of activity.data?.terminals ?? []) {
      if (row.activity) m.set(row.key, row.activity);
    }
    return m;
  }, [activity.data]);
  const working = panes.filter((p) => activityByKey.get(p.key) === "working").length;
  const workspaces = state.data?.workspaces?.length ?? 0;
  const projectName = state.data?.session?.project?.name ?? "";

  return (
    <DeckCard
      icon={MessagesSquare}
      title={t("deck.card_ide")}
      meta={
        panes.length > 0
          ? `${working}/${panes.length}`
          : workspaces > 0
            ? workspaces
            : undefined
      }
      live={working > 0}
      variant="bracket"
      onOpen={() => setActiveSection("agentic-ide")}
      openLabel={t("deck.open_section")}
      className={className}
    >
      {panes.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {state.isError ? t("deck.unavailable") : t("deck.ide_empty")}
        </p>
      ) : (
        <div className="flex h-full min-h-0 flex-col gap-1.5">
          {projectName && (
            <div className="truncate font-mono text-[10px] text-muted-foreground">{projectName}</div>
          )}
          <div
            className="grid min-h-0 flex-1 gap-1.5"
            style={{ gridTemplateColumns: `repeat(${Math.min(4, Math.max(1, Math.ceil(Math.sqrt(panes.length))))}, minmax(0, 1fr))` }}
          >
            {panes.slice(0, 12).map((p) => {
              const act = activityByKey.get(p.key) ?? (p.status === "live" ? "idle" : p.status);
              return (
                <div
                  key={p.key}
                  title={`${p.display_name || p.name} · ${act}`}
                  className={cn(
                    "flex min-h-[2.4rem] flex-col justify-between rounded-md border border-border/60 px-1.5 py-1",
                    act === "working" && "border-primary/50",
                    act === "error" && "border-destructive/60",
                  )}
                >
                  <div className="flex items-center gap-1">
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", ACTIVITY_TONE[act] ?? ACTIVITY_TONE.idle)} aria-hidden />
                    <span className="truncate text-[10px] text-foreground">{p.display_name || p.name}</span>
                  </div>
                  <span className="truncate font-mono text-[9px] text-muted-foreground">{p.agent}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </DeckCard>
  );
}

// ----------------------------------------------------------------------
// Terminals — shell and CLI lines as they run
// ----------------------------------------------------------------------

const LINE_TONE: Record<string, string> = {
  cmd: "text-emerald-400",
  cli: "text-primary",
  out: "text-foreground/90",
  err: "text-destructive",
  note: "text-muted-foreground",
};

export function TerminalsCard({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const lines = useDeckStore((s) => s.termLines);
  // The ToolExecutor's run_shell activity is the same class of thing — the
  // brain running a command — so it belongs on this card too, in front.
  const shell = useCommandActivityStore((s) => s.entries);
  const running = shell.filter((e) => e.status === "running").length;
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [lines.length, shell.length]);

  const shellLines = shell.slice(-6).map((e) => ({
    id: `sh-${e.id}`,
    kind: e.status === "failed" || e.status === "blocked" ? "err" : "cmd",
    text: e.status === "running" ? `$ ${e.command}` : `$ ${e.command} · ${e.status}`,
  }));
  const shown = [...shellLines, ...lines.slice(-40).map((l) => ({ id: l.id, kind: l.kind, text: l.text }))];

  return (
    <DeckCard
      icon={Terminal}
      title={t("deck.card_terminals")}
      meta={running > 0 ? running : undefined}
      live={running > 0}
      variant="rail"
      onOpen={() => setActiveSection("clis")}
      openLabel={t("deck.open_section")}
      className={className}
      bodyClassName="overflow-y-auto"
    >
      {shown.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">{t("deck.terminals_empty")}</p>
      ) : (
        <div className="font-mono text-[10.5px] leading-relaxed">
          {shown.map((l) => (
            <div key={l.id} className={cn("truncate", LINE_TONE[l.kind] ?? LINE_TONE.out)}>
              {l.text}
            </div>
          ))}
          <div ref={endRef} />
        </div>
      )}
    </DeckCard>
  );
}
