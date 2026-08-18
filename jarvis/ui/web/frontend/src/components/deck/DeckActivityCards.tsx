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
import { useElementSize } from "@/components/deck/HudFrame";
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
  if (o.includes("ok") || o.includes("success") || o.includes("done")) return "bg-emerald-600 dark:bg-emerald-400";
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
  success: "bg-emerald-600 dark:bg-emerald-400",
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
// Coding workspace — the crew, not the terminals
// ----------------------------------------------------------------------

/**
 * The Agentic IDE on the deck, WITHOUT mounting a single terminal. The IDE's
 * panes are live xterm instances bound to PTY streams, and a second mounted
 * copy steals every pane's output from the first (MainView's sticky-mount
 * comment explains the defect). Two empty tiles that said "Claude Code" told
 * the maintainer nothing (2026-08-18), so this is a crew roster instead:
 *
 *   RUNNING ─────────────────────   IDLE ─────────────────
 *   ● Claude Code   working · 2m    ○ Codex   idle · 14m
 *     "fix the layout of the…"        "run the tests"
 *
 * Every row is one agent with the things a person actually wants to know —
 * is it doing something, since when, on what — read from the same endpoints
 * the IDE polls. A click takes you INTO that terminal: the section opens and
 * the grid maximizes the pane (store.requestIdePane).
 */
type CrewState = "working" | "waiting" | "asking" | "starting" | "failed" | "exited" | "idle";

// Signal colours come in pairs — a dark tint on black, its deep twin on paper
// (CLOUD.md "Frontend theming").
const CREW_TONE: Record<CrewState, string> = {
  working: "text-primary",
  starting: "text-amber-700 dark:text-amber-400",
  waiting: "text-sky-700 dark:text-sky-400",
  asking: "text-amber-700 dark:text-amber-400",
  failed: "text-destructive",
  exited: "text-muted-foreground",
  idle: "text-muted-foreground",
};

const CREW_RUNNING = new Set<CrewState>(["working", "starting", "waiting", "asking"]);

function crewState(status: string, activity: string | undefined): CrewState {
  if (activity === "working" || activity === "waiting" || activity === "asking" || activity === "starting") {
    return activity;
  }
  if (activity === "failed" || status === "error") return "failed";
  if (activity === "exited" || status === "exited") return "exited";
  if (status === "pending") return "starting";
  return "idle";
}

/** "2m", "48s", "1h" — the age of the current state, short enough for a row. */
function ago(epochSeconds: number | null | undefined, nowMs: number): string {
  if (!epochSeconds) return "";
  const s = Math.max(0, Math.round(nowMs / 1000 - epochSeconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

interface CrewRow {
  key: string;
  name: string;
  title: string;
  agent: string;
  state: CrewState;
  running: boolean;
  since: string;
  prompt: string;
  prompts: number;
}

export function IdeGridCard({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const requestIdePane = useEventStore((s) => s.requestIdePane);

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
  const rows = useMemo<CrewRow[]>(() => {
    const byKey = new Map((activity.data?.terminals ?? []).map((r) => [r.key, r]));
    const now = Date.now();
    return panes.map((p) => {
      const act = byKey.get(p.key);
      const st = crewState(p.status, act?.activity);
      return {
        key: p.key,
        name: p.name,
        title: p.display_name || p.name,
        agent: p.agent,
        state: st,
        running: CREW_RUNNING.has(st),
        since: ago(act?.activity_since || p.last_output_at || p.started_at, now),
        prompt: p.last_prompt || "",
        prompts: p.prompts_sent,
      };
    });
  }, [panes, activity.data]);

  const running = rows.filter((r) => r.running);
  const idle = rows.filter((r) => !r.running);
  const project = state.data?.session?.project;
  const branch = project?.branch ?? state.data?.workspaces?.find((w) => w.active)?.branch ?? null;

  const open = (name: string) => {
    requestIdePane(name);
    setActiveSection("agentic-ide");
  };

  // Two columns side by side need room for a name, a state word and an age
  // on one line; below that the groups stack, so a name is never cut to its
  // first letter (what the first version did in the deck's right column).
  const [bodyRef, bodySize] = useElementSize<HTMLDivElement>();
  const twoColumns = bodySize.w >= 400;

  return (
    <DeckCard
      icon={MessagesSquare}
      title={t("deck.card_ide")}
      meta={rows.length > 0 ? `${running.length}/${rows.length}` : undefined}
      live={running.length > 0}
      variant="bracket"
      onOpen={() => setActiveSection("agentic-ide")}
      openLabel={t("deck.open_section")}
      className={className}
    >
      {rows.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {state.isError ? t("deck.unavailable") : t("deck.ide_empty")}
        </p>
      ) : (
        <div ref={bodyRef} className="flex h-full min-h-0 flex-col gap-1.5">
          {project && (
            <div className="flex items-center gap-2 truncate font-mono text-[10px] text-muted-foreground">
              <span className="truncate text-foreground/80">{project.name}</span>
              {branch && <span className="shrink-0 truncate">⎇ {branch}</span>}
            </div>
          )}
          <div
            className={cn(
              "min-h-0 flex-1 gap-x-3 gap-y-2",
              twoColumns ? "grid grid-cols-2" : "flex flex-col overflow-y-auto",
            )}
          >
            <CrewColumn label={t("deck.ide_running")} rows={running} onOpen={open} hot scroll={twoColumns} />
            <CrewColumn label={t("deck.ide_idle")} rows={idle} onOpen={open} scroll={twoColumns} />
          </div>
        </div>
      )}
    </DeckCard>
  );
}

function CrewColumn({
  label,
  rows,
  onOpen,
  hot,
  scroll,
}: {
  label: string;
  rows: CrewRow[];
  onOpen: (name: string) => void;
  hot?: boolean;
  /** Own scroll region (side-by-side); stacked groups scroll as one. */
  scroll?: boolean;
}) {
  const t = useT();
  return (
    <div className={cn("flex flex-col", scroll && "min-h-0")}>
      <div
        className={cn(
          "flex items-center gap-1.5 border-b pb-1 font-mono text-[9px] uppercase tracking-[0.2em]",
          hot ? "border-primary/50 text-primary" : "border-border text-muted-foreground",
        )}
      >
        <span>{label}</span>
        <span className="ml-auto tabular-nums">{rows.length}</span>
      </div>
      <ul className={cn("space-y-1 pt-1", scroll && "min-h-0 flex-1 overflow-y-auto")}>
        {rows.length === 0 && <li className="text-[10px] text-muted-foreground/70">—</li>}
        {rows.map((r) => (
          <li key={r.key}>
            <button
              type="button"
              onClick={() => onOpen(r.name)}
              title={t("deck.ide_open_pane")}
              className="group/row flex w-full flex-col gap-0.5 rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-primary/10"
            >
              <span className="flex items-center gap-1.5">
                <span
                  className={cn(
                    "h-1.5 w-1.5 shrink-0",
                    r.state === "working" || r.state === "starting"
                      ? "animate-pulse bg-primary shadow-[0_0_6px_1px_hsl(var(--primary)/0.6)]"
                      : r.state === "failed"
                        ? "bg-destructive"
                        : r.state === "waiting" || r.state === "asking"
                          ? "bg-sky-600 dark:bg-sky-400"
                          : "bg-muted-foreground/50",
                  )}
                  aria-hidden
                />
                <span className="min-w-0 flex-1 truncate text-[11px] text-foreground group-hover/row:text-primary">
                  {r.title}
                </span>
                <span className={cn("shrink-0 font-mono text-[9px] uppercase tracking-wider", CREW_TONE[r.state])}>
                  {t(`deck.ide_state_${r.state}`)}
                </span>
                {r.since && (
                  <span className="shrink-0 font-mono text-[9px] tabular-nums text-muted-foreground">
                    · {r.since}
                  </span>
                )}
              </span>
              <span className="flex items-center gap-1.5 pl-3">
                <span className="shrink-0 font-mono text-[9px] text-muted-foreground">{r.agent}</span>
                {r.prompt && (
                  <span className="min-w-0 flex-1 truncate font-mono text-[9.5px] text-muted-foreground/90">
                    &ldquo;{r.prompt}&rdquo;
                  </span>
                )}
                {r.prompts > 0 && !r.prompt && (
                  <span className="font-mono text-[9px] tabular-nums text-muted-foreground">{r.prompts}×</span>
                )}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ----------------------------------------------------------------------
// Terminals — shell and CLI lines as they run
// ----------------------------------------------------------------------

const LINE_TONE: Record<string, string> = {
  cmd: "text-emerald-700 dark:text-emerald-400",
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
