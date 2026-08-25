/**
 * AgentInsight — one agent run, in full.
 *
 * The board row answers "did it land?" in one word. This page answers the
 * questions that word raises: what exactly went wrong, what the agent did
 * step by step, what the reviewer said, and what it left behind. It replaces
 * the board inside the section (a "← Back" link at the top, the same quiet
 * primitives) rather than opening a dialog, because a run's story is a page,
 * not a tooltip.
 *
 * Four sources, all of which already exist — nothing here is a second store:
 *
 * - `GET /api/missions/{id}` — the durable event stream (dispatch, spawns,
 *   the worker's own progress notes, verdicts, kills, the terminal event).
 *   `outcome.ts` turns it into the verdict paragraph and the story timeline.
 * - `GET /api/missions/{id}/result` — the signed summary and the deliverable
 *   files with bounded contents.
 * - `GET /api/outputs` + `GET /api/outputs/{slug}/plan` — the archived worker
 *   transcript, reconstructed into reasoning / tool / spawn steps and the final
 *   answer. Only while the output directory still exists; a cleaned-up run
 *   says so instead of pretending the agent did nothing.
 * - The live registry node the board already holds — tool calls with real
 *   status and a running clock for an agent that is still working.
 *
 * Every visible string goes through i18n; the brand is the wake-word-derived
 * assistant name (lib/agentBrand.ts), never a product name.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpRight,
  Bot,
  Brain,
  CheckCircle2,
  CircleAlert,
  FileText,
  Gavel,
  ListChecks,
  Map as MapIcon,
  Rocket,
  ScrollText,
  StopCircle,
  TerminalSquare,
  Timer,
  Wrench,
  XCircle,
} from "lucide-react";

import type { SubAgentNode, ToolCallEntry } from "@/store/jarvisAgents";
import type { CriticAxisResult, CriticVerdictReady, MissionArtifact } from "@/types/missions";
import { fetchMissionDetail, fetchMissionResult } from "@/components/missions/api";
import { useOutputsList, usePlanForOutput, type PlanStep } from "@/hooks/useOutputs";
import { missionMapUrl } from "@/hooks/useVisualArtifacts";
import { openExternalUrl } from "@/lib/openExternal";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  BackLink,
  DetailHeader,
  FactRows,
  Panel,
  SegmentedFilter,
  SoftButton,
  StatusDot,
  StatTile,
} from "@/components/extensions/primitives";
import { agentBrand, agentsBrand } from "@/lib/agentBrand";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { fill, useT, useUiLanguage } from "@/i18n";
import { failureLabel } from "./failureLabel";
import {
  buildStory,
  deriveOutcome,
  KILL_REASON_LABEL_KEYS,
  missionIdFromTraceId,
  REASON_LABEL_KEYS,
  splitReason,
  type AgentOutcome,
  type StoryEntry,
  type StoryTone,
} from "./outcome";
import { formatBytes, formatClock, formatDuration, formatOffset, formatUsd } from "./format";

type Tab = "story" | "transcript" | "review" | "output";
type T = (key: string) => string;

const STATUS_TONE: Record<SubAgentNode["status"], "busy" | "ok" | "error" | "warn"> = {
  running: "busy",
  completed: "ok",
  failed: "error",
  cancelled: "warn",
};

const TONE_TEXT: Record<StoryTone, string> = {
  neutral: "text-muted-foreground",
  busy: "text-primary",
  ok: "text-emerald-600 dark:text-emerald-400",
  warn: "text-amber-600 dark:text-amber-400",
  error: "text-destructive",
};

const TONE_RING: Record<StoryTone, string> = {
  neutral: "border-border bg-card",
  busy: "border-primary/50 bg-primary/10",
  ok: "border-emerald-500/50 bg-emerald-500/10",
  warn: "border-amber-500/50 bg-amber-500/10",
  error: "border-destructive/50 bg-destructive/10",
};

/** The first paragraph of the request — the part the user actually said. */
export function requestTitle(text: string | null | undefined, fallback: string): string {
  const raw = (text ?? "").trim();
  if (!raw) return fallback;
  const first = raw.split(/\n\s*\n/)[0].replace(/\s+/g, " ").trim();
  return first.length > 180 ? `${first.slice(0, 177).trimEnd()}…` : first || fallback;
}

/** Plain-language label for a mission-level reason, else the raw token. */
export function reasonLabel(reason: string | null | undefined, t: T): string | null {
  const { head, tail } = splitReason(reason);
  if (!head) return null;
  const key = REASON_LABEL_KEYS[head];
  const label = key ? t(key) : head;
  return tail ? `${label} (${tail})` : label;
}

function killLabel(reason: string, t: T): string {
  const key = KILL_REASON_LABEL_KEYS[reason];
  return key ? t(key) : reason;
}

function axisPassed(axis: CriticAxisResult): boolean | null {
  if (axis.pass === true || axis.status === "pass") return true;
  if (axis.pass === false || axis.status === "fail") return false;
  return null;
}

interface Props {
  agent: SubAgentNode;
  onBack: () => void;
  onOpenOutput: (slug: string) => void;
}

export function AgentInsight({ agent, onBack, onOpenOutput }: Props) {
  const t = useT();
  const locale = useUiLanguage();
  const assistantName = useEventStore((s) => s.assistantName);
  const missionId = agent.mission_id ?? missionIdFromTraceId(agent.trace_id);
  const running = agent.status === "running";

  const detail = useQuery({
    queryKey: ["missions", "detail", missionId],
    queryFn: () => fetchMissionDetail(missionId),
    refetchInterval: running ? 3_000 : false,
  });
  const result = useQuery({
    queryKey: ["missions", "result", missionId],
    queryFn: () => fetchMissionResult(missionId),
    refetchInterval: running ? 10_000 : false,
  });
  const outputs = useOutputsList();
  const slug = useMemo(() => {
    const match = outputs.data?.find((o) => o.mission_id === missionId);
    return match?.slug ?? agent.output_slug ?? null;
  }, [outputs.data, missionId, agent.output_slug]);
  const plan = usePlanForOutput(slug);

  const events = detail.data?.events ?? [];
  const language = detail.data?.mission.language ?? "en";
  const outcome = useMemo(() => deriveOutcome(events, language), [events, language]);
  const story = useMemo(() => buildStory(events), [events]);
  const steps: PlanStep[] = plan.data?.steps ?? [];
  const artifacts: MissionArtifact[] = result.data?.artifacts ?? [];
  const finalAnswer = plan.data?.final_answer ?? null;

  const hasOutput = artifacts.length > 0 || !!finalAnswer || !!outcome.summary;
  const [tab, setTab] = useState<Tab | null>(null);
  // Land where the answer is: a delivered run opens on its output, everything
  // else opens on the story of how it got where it got.
  const activeTab: Tab = tab ?? (outcome.terminal === "approved" && hasOutput ? "output" : "story");

  const title = requestTitle(result.data?.prompt ?? agent.utterance, t("subagents_view.task_none"));
  const startedMs = events[0]?.ts_ms ?? Math.floor(agent.started_ns / 1_000_000);
  const endedMs = detail.data?.mission.updated_ms ?? null;
  const runtimeMs =
    agent.duration_ms ??
    (outcome.terminal && endedMs ? endedMs - startedMs : running ? Date.now() - startedMs : null);

  const tabOptions: { id: Tab; label: string; count?: number }[] = [
    { id: "story", label: t("subagents_view.tab_story"), count: story.length },
    { id: "transcript", label: t("subagents_view.tab_transcript"), count: steps.length },
    { id: "review", label: t("subagents_view.tab_review"), count: outcome.verdicts.length },
    { id: "output", label: t("subagents_view.tab_output"), count: artifacts.length },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-4 px-6 py-6">
          <BackLink label={agentsBrand(assistantName)} onClick={onBack} />

          <DetailHeader
            leading={
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-border bg-card/60 text-primary">
                <Bot className="h-5 w-5" />
              </span>
            }
            title={title}
            titleAccessory={
              <StatusDot
                tone={STATUS_TONE[agent.status]}
                pulse={running}
                label={t(`subagents_view.status.${agent.status}`)}
              />
            }
            byline={fill(t("subagents_view.insight_byline"), {
              agent: agentBrand(assistantName),
              started: formatClock(startedMs, locale),
              runtime: runtimeMs != null ? formatDuration(runtimeMs) : "—",
            })}
            actions={
              <>
                {slug && (
                  <SoftButton
                    onClick={() => void openExternalUrl(`${window.location.origin}${missionMapUrl(slug)}`)}
                  >
                    <MapIcon className="h-3.5 w-3.5" />
                    {t("subagents_view.action_map")}
                  </SoftButton>
                )}
                <SoftButton primary disabled={!slug} onClick={() => slug && onOpenOutput(slug)}>
                  <ArrowUpRight className="h-3.5 w-3.5" />
                  {t("subagents_view.action_open_output")}
                </SoftButton>
              </>
            }
          />

          {detail.isError && (
            <Notice tone="error">
              {fill(t("subagents_view.insight_load_error"), {
                detail: detail.error instanceof Error ? detail.error.message : "",
              })}
            </Notice>
          )}

          {/* The verdict — what happened, in words ------------------------ */}
          <OutcomePanel
            agent={agent}
            outcome={outcome}
            loading={detail.isPending}
            artifactCount={result.data?.artifact_count ?? artifacts.length}
            t={t}
          />

          {/* The numbers ------------------------------------------------- */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            <StatTile
              icon={<Timer className="h-4 w-4" />}
              label={t("subagents_view.col_runtime")}
              value={runtimeMs != null ? formatDuration(runtimeMs) : "—"}
              hint={endedMs && outcome.terminal ? formatClock(endedMs, locale) : t("subagents_view.stat_active_hint_idle")}
            />
            <StatTile
              icon={<Bot className="h-4 w-4" />}
              label={t("subagents_view.stat_workers_label")}
              value={String(outcome.workers.length)}
              hint={
                outcome.workers.length > 0
                  ? outcome.workers.map((w) => w.cli).filter(Boolean).join(", ") || "—"
                  : t("subagents_view.stat_workers_hint_none")
              }
            />
            <StatTile
              icon={<Wrench className="h-4 w-4" />}
              label={t("subagents_view.stat_tools_label")}
              value={String(
                Math.max(
                  agent.tool_calls.length,
                  steps.filter((s) => s.kind !== "reasoning").length,
                  outcome.workers.reduce((sum, w) => sum + w.tool_notes, 0),
                ),
              )}
              hint={t("subagents_view.stat_tools_hint_run")}
            />
            <StatTile
              icon={<Gavel className="h-4 w-4" />}
              label={t("subagents_view.stat_reviews_label")}
              value={String(outcome.verdicts.length)}
              tone={outcome.revisions > 0 ? "warn" : "ok"}
              hint={
                outcome.revisions > 0
                  ? fill(t("subagents_view.stat_reviews_hint_revisions"), { n: outcome.revisions })
                  : t("subagents_view.stat_reviews_hint_none")
              }
            />
            <StatTile
              icon={<ScrollText className="h-4 w-4" />}
              label={t("subagents_view.stat_cost_label")}
              value={formatUsd(Math.max(outcome.cost_usd, agent.cost_usd))}
              hint={
                outcome.tokens_used > 0
                  ? fill(t("subagents_view.stat_cost_hint_tokens"), { n: outcome.tokens_used.toLocaleString() })
                  : t("subagents_view.stat_cost_hint_none")
              }
            />
          </div>

          {/* Live tool calls from the registry, while it still holds them --- */}
          {agent.tool_calls.length > 0 && (
            <Panel>
              <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 text-xs font-medium text-muted-foreground">
                <TerminalSquare className="h-3.5 w-3.5 text-primary" />
                {t("subagents_view.drill_tools")}
              </div>
              <div className="divide-y divide-border/70">
                {agent.tool_calls.map((call, idx) => (
                  <LiveToolCall key={`${agent.trace_id}-${idx}`} call={call} t={t} />
                ))}
              </div>
            </Panel>
          )}

          {/* The record ---------------------------------------------------- */}
          <Panel>
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
              <SegmentedFilter
                value={activeTab}
                onChange={setTab}
                options={tabOptions}
                label={t("subagents_view.tabs_label")}
              />
              {slug ? (
                <span className="font-mono text-[11px] text-muted-foreground/70">{slug}</span>
              ) : (
                <span className="text-[11px] text-muted-foreground/70">
                  {t("subagents_view.no_output_dir")}
                </span>
              )}
            </div>

            {activeTab === "story" && (
              <StoryList story={story} startedMs={startedMs} loading={detail.isPending} assistantName={assistantName} t={t} />
            )}
            {activeTab === "transcript" && (
              <Transcript
                steps={steps}
                finalAnswer={finalAnswer}
                truncated={!!plan.data?.truncated}
                dropped={plan.data?.dropped_steps ?? 0}
                hasSlug={!!slug}
                loading={!!slug && plan.isPending}
                t={t}
              />
            )}
            {activeTab === "review" && <Review outcome={outcome} t={t} />}
            {activeTab === "output" && (
              <Output
                outcome={outcome}
                finalAnswer={finalAnswer}
                artifacts={artifacts}
                truncated={!!result.data?.truncated}
                slug={slug}
                onOpenOutput={onOpenOutput}
                t={t}
              />
            )}
          </Panel>

          <FactRows
            className="px-1"
            rows={[
              { label: t("subagents_view.fact_mission_id"), value: <span className="font-mono text-sm">{missionId}</span> },
              {
                label: t("subagents_view.fact_request"),
                value: <ClampedBlock text={result.data?.prompt ?? agent.utterance ?? ""} t={t} lines={3} />,
              },
              ...(outcome.result_uri
                ? [{ label: t("subagents_view.fact_result_uri"), value: <span className="break-all font-mono text-xs">{outcome.result_uri}</span> }]
                : []),
            ]}
          />
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Outcome — the verdict paragraph
// ---------------------------------------------------------------------------

interface OutcomeLine {
  id: string;
  tone: StoryTone;
  label: string;
  text: string;
  mono?: boolean;
}

export function outcomeLines(agent: SubAgentNode, outcome: AgentOutcome, t: T): OutcomeLine[] {
  const lines: OutcomeLine[] = [];
  const reason = reasonLabel(outcome.reason, t);

  if (outcome.terminal === "failed" || outcome.terminal === "timed_out") {
    if (reason) lines.push({ id: "reason", tone: "error", label: t("subagents_view.line_reason"), text: reason });
    const cls = failureLabel({ error: null, error_class: outcome.error_class }, t);
    if (cls) lines.push({ id: "class", tone: "error", label: t("subagents_view.line_error_class"), text: cls });
    if (outcome.error_detail) {
      lines.push({ id: "detail", tone: "neutral", label: t("subagents_view.line_provider_said"), text: outcome.error_detail, mono: true });
    }
    if (outcome.failed_provider) {
      lines.push({ id: "provider", tone: "neutral", label: t("subagents_view.line_provider"), text: outcome.failed_provider });
    }
    for (const kill of outcome.kills) {
      const iter = /::iter(\d+)$/.exec(kill.worker_id)?.[1];
      lines.push({
        id: `kill-${kill.worker_id}`,
        tone: kill.reason === "user" ? "warn" : "error",
        label: iter != null
          ? fill(t("subagents_view.line_worker_stopped_iter"), { n: iter })
          : t("subagents_view.line_worker_stopped"),
        text: kill.error_detail && kill.error_detail !== outcome.error_detail
          ? `${killLabel(kill.reason, t)} — ${kill.error_detail}`
          : killLabel(kill.reason, t),
      });
    }
    if (outcome.lastVerdict && outcome.lastVerdict.verdict !== "approve") {
      lines.push({
        id: "verdict",
        tone: "warn",
        label: fill(t("subagents_view.line_reviewer"), { verdict: t(`subagents_view.verdict.${outcome.lastVerdict.verdict}`) }),
        text: outcome.lastVerdict.summary,
      });
    }
    if (outcome.lastCorrection) {
      lines.push({ id: "correction", tone: "warn", label: t("subagents_view.line_correction"), text: outcome.lastCorrection.correction_instruction });
    }
    if (outcome.last_state) {
      lines.push({ id: "state", tone: "neutral", label: t("subagents_view.line_last_state"), text: t(`mission_state.${outcome.last_state.toLowerCase()}`) });
    }
    if (outcome.partial_artifacts.length > 0) {
      lines.push({
        id: "partial",
        tone: "neutral",
        label: t("subagents_view.line_partial"),
        text: fill(t("subagents_view.line_partial_text"), { n: outcome.partial_artifacts.length }),
      });
    }
    if (outcome.terminal === "timed_out" && outcome.deadline_ms) {
      lines.push({ id: "deadline", tone: "neutral", label: t("subagents_view.line_deadline"), text: formatDuration(outcome.deadline_ms) });
    }
  } else if (outcome.terminal === "cancelled") {
    if (reason) lines.push({ id: "reason", tone: "warn", label: t("subagents_view.line_reason"), text: reason });
    if (outcome.cascade) lines.push({ id: "cascade", tone: "neutral", label: t("subagents_view.line_cascade"), text: t("subagents_view.line_cascade_text") });
    for (const kill of outcome.kills) {
      lines.push({ id: `kill-${kill.worker_id}`, tone: "warn", label: t("subagents_view.line_worker_stopped"), text: killLabel(kill.reason, t) });
    }
  } else if (outcome.terminal === "approved") {
    if (outcome.summary) lines.push({ id: "summary", tone: "ok", label: t("subagents_view.line_summary"), text: outcome.summary });
    if (outcome.lastVerdict) {
      lines.push({
        id: "verdict",
        tone: "ok",
        label: fill(t("subagents_view.line_reviewer"), { verdict: t(`subagents_view.verdict.${outcome.lastVerdict.verdict}`) }),
        text: outcome.lastVerdict.summary,
      });
    }
    if (outcome.revisions > 0) {
      lines.push({ id: "revisions", tone: "warn", label: t("subagents_view.line_revisions"), text: fill(t("subagents_view.line_revisions_text"), { n: outcome.revisions }) });
    }
  } else {
    // Still running, or no terminal event recorded yet.
    const live = failureLabel(agent, t);
    if (live) lines.push({ id: "live-error", tone: "error", label: t("subagents_view.line_reason"), text: live });
    const notes = outcome.workers.reduce((sum, w) => sum + w.notes, 0);
    if (notes > 0) lines.push({ id: "notes", tone: "busy", label: t("subagents_view.line_progress"), text: fill(t("subagents_view.line_progress_text"), { n: notes }) });
    if (outcome.lastCorrection) {
      lines.push({ id: "correction", tone: "warn", label: t("subagents_view.line_correction"), text: outcome.lastCorrection.correction_instruction });
    }
  }
  return lines;
}

function OutcomePanel({
  agent,
  outcome,
  loading,
  artifactCount,
  t,
}: {
  agent: SubAgentNode;
  outcome: AgentOutcome;
  loading: boolean;
  artifactCount: number;
  t: T;
}) {
  const state: "running" | "approved" | "failed" | "cancelled" | "timed_out" =
    outcome.terminal ?? (agent.status === "running" ? "running" : agent.status === "failed" ? "failed" : agent.status === "cancelled" ? "cancelled" : "approved");
  const tone: StoryTone =
    state === "approved" ? "ok" : state === "running" ? "busy" : state === "cancelled" ? "warn" : "error";
  const Icon =
    state === "approved" ? CheckCircle2 : state === "running" ? Rocket : state === "cancelled" ? StopCircle : XCircle;
  const lines = outcomeLines(agent, outcome, t);

  return (
    <Panel>
      <div className="flex items-start gap-3 px-4 py-4">
        <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-full border", TONE_RING[tone], TONE_TEXT[tone])}>
          <Icon className="h-[18px] w-[18px]" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-display text-[15px] font-semibold text-foreground">
            {t(`subagents_view.outcome.${state}`)}
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {loading
              ? t("subagents_view.insight_loading")
              : state === "approved"
                ? fill(t("subagents_view.outcome_approved_hint"), { n: artifactCount })
                : t(`subagents_view.outcome_hint.${state}`)}
          </p>
        </div>
      </div>
      {lines.length > 0 && (
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-2 border-t border-border px-4 py-3 text-sm">
          {lines.map((line) => (
            <div key={line.id} className="contents">
              <dt className={cn("text-xs leading-6", TONE_TEXT[line.tone])}>{line.label}</dt>
              <dd className={cn("min-w-0 leading-6 text-foreground/90", line.mono && "whitespace-pre-wrap break-words font-mono text-[12.5px]")}>
                {line.text}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Story — the run as it unfolded
// ---------------------------------------------------------------------------

const STORY_ICON: Record<StoryEntry["kind"], typeof Bot> = {
  dispatched: Rocket,
  plan: ListChecks,
  spawn: Bot,
  narration: Brain,
  tool: Wrench,
  draft: FileText,
  verdict: Gavel,
  correction: AlertTriangle,
  killed: StopCircle,
  budget: AlertTriangle,
  approved: CheckCircle2,
  failed: XCircle,
  cancelled: StopCircle,
  timed_out: Timer,
};

function storyTitle(entry: StoryEntry, assistantName: string, t: T): string {
  const agent = agentBrand(assistantName);
  const m = entry.meta;
  switch (entry.kind) {
    case "dispatched":
      return fill(t("subagents_view.story.dispatched"), { agent });
    case "plan":
      return fill(t("subagents_view.story.plan"), { n: m.workers ?? "1" });
    case "spawn":
      return fill(t("subagents_view.story.spawn"), {
        cli: m.cli || "—",
        iter: String((entry.iteration ?? 0) + 1),
      }) + (m.model ? ` · ${m.model}` : "");
    case "narration":
      return fill(t("subagents_view.story.narration"), { agent });
    case "tool":
      return entry.tool ?? t("subagents_view.tool_unnamed");
    case "draft":
      return fill(t("subagents_view.story.draft"), { n: m.diff_lines ?? "0" });
    case "verdict":
      return fill(t("subagents_view.story.verdict"), {
        verdict: t(`subagents_view.verdict.${m.verdict}`),
        confidence: m.confidence ?? "",
      });
    case "correction":
      return m.next_model
        ? fill(t("subagents_view.story.correction_model"), { model: m.next_model })
        : t("subagents_view.story.correction");
    case "killed":
      return fill(t("subagents_view.story.killed"), { reason: killLabel(m.reason ?? "", t) });
    case "budget":
      return fill(t("subagents_view.story.budget"), { pct: m.pct ?? "", limit: m.limit ?? "" });
    case "approved":
      return t("subagents_view.story.approved");
    case "failed":
      return fill(t("subagents_view.story.failed"), { reason: reasonLabel(m.reason, t) ?? "" });
    case "cancelled":
      return fill(t("subagents_view.story.cancelled"), { reason: reasonLabel(m.reason, t) ?? "" });
    case "timed_out":
      return t("subagents_view.story.timed_out");
    default:
      return entry.kind;
  }
}

function StoryList({
  story,
  startedMs,
  loading,
  assistantName,
  t,
}: {
  story: StoryEntry[];
  startedMs: number;
  loading: boolean;
  assistantName: string;
  t: T;
}) {
  if (story.length === 0) {
    return <EmptyNote>{loading ? t("subagents_view.insight_loading") : t("subagents_view.story_empty")}</EmptyNote>;
  }
  return (
    <ol className="divide-y divide-border/60">
      {story.map((entry) => {
        const Icon = STORY_ICON[entry.kind];
        const title = storyTitle(entry, assistantName, t);
        return (
          <li key={entry.id} className="grid grid-cols-[64px_28px_minmax(0,1fr)] items-start gap-x-3 px-4 py-2.5">
            <span className="pt-0.5 text-right font-mono text-[11px] tabular-nums text-muted-foreground/70">
              {formatOffset(entry.ts_ms - startedMs)}
            </span>
            <span className={cn("grid h-6 w-6 place-items-center rounded-full border", TONE_RING[entry.tone], TONE_TEXT[entry.tone])}>
              <Icon className="h-3.5 w-3.5" />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className={cn("text-sm font-medium", entry.kind === "tool" ? "font-mono text-[13px] text-foreground" : "text-foreground")}>
                  {title}
                </span>
                {entry.iteration != null && entry.kind !== "spawn" && entry.kind !== "verdict" && entry.kind !== "correction" && (
                  <span className="text-[11px] text-muted-foreground/70">
                    {fill(t("subagents_view.iteration_short"), { n: entry.iteration + 1 })}
                  </span>
                )}
                {entry.meta.pct && <span className="text-[11px] text-muted-foreground/70">{entry.meta.pct}</span>}
              </div>
              {entry.text && (
                <ClampedBlock
                  text={entry.text}
                  t={t}
                  lines={entry.kind === "tool" ? 2 : 3}
                  className={cn("mt-0.5 text-sm", entry.kind === "tool" ? "font-mono text-[12.5px] text-muted-foreground" : "text-foreground/85")}
                />
              )}
              {entry.verdict && <AxisChips verdict={entry.verdict} />}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function AxisChips({ verdict }: { verdict: CriticVerdictReady }) {
  const entries = Object.entries(verdict.axes ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {entries.map(([name, axis]) => {
        const passed = axisPassed(axis);
        return (
          <span
            key={name}
            className={cn(
              "inline-flex h-5 items-center gap-1 rounded-md border px-1.5 font-mono text-[10.5px]",
              passed === true && "border-emerald-500/40 text-emerald-600 dark:text-emerald-400",
              passed === false && "border-destructive/40 text-destructive",
              passed === null && "border-border text-muted-foreground",
            )}
          >
            {passed === true ? <CheckCircle2 className="h-3 w-3" /> : passed === false ? <XCircle className="h-3 w-3" /> : null}
            {name}
          </span>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Transcript — reasoning / tool / spawn steps from the archived stream
// ---------------------------------------------------------------------------

const STEP_TONE: Record<string, "busy" | "ok" | "error" | "warn" | "off"> = {
  pending: "off",
  running: "busy",
  done: "ok",
  failed: "error",
  skipped: "warn",
};

function Transcript({
  steps,
  finalAnswer,
  truncated,
  dropped,
  hasSlug,
  loading,
  t,
}: {
  steps: PlanStep[];
  finalAnswer: string | null;
  truncated: boolean;
  dropped: number;
  hasSlug: boolean;
  loading: boolean;
  t: T;
}) {
  const lanes = useMemo(() => {
    const map = new Map<string, PlanStep[]>();
    for (const step of steps) {
      const key = step.task_key ?? "";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(step);
    }
    return [...map.entries()];
  }, [steps]);

  if (!hasSlug) return <EmptyNote>{t("subagents_view.transcript_no_dir")}</EmptyNote>;
  if (loading) return <EmptyNote>{t("subagents_view.insight_loading")}</EmptyNote>;
  if (steps.length === 0 && !finalAnswer) return <EmptyNote>{t("subagents_view.transcript_empty")}</EmptyNote>;

  return (
    <div>
      {(truncated || dropped > 0) && (
        <div className="border-b border-border px-4 py-2 text-[11px] text-amber-600 dark:text-amber-400">
          {dropped > 0
            ? fill(t("subagents_view.transcript_dropped"), { n: dropped })
            : t("subagents_view.transcript_truncated")}
        </div>
      )}
      {lanes.map(([key, laneSteps], laneIdx) => (
        <div key={key || laneIdx}>
          {lanes.length > 1 && (
            <div className="border-b border-border bg-sheen/[0.03] px-4 py-1.5 text-[11px] font-medium text-muted-foreground">
              {fill(t("subagents_view.transcript_lane"), { n: laneIdx + 1 })}
              <span className="ml-2 font-mono text-muted-foreground/60">{key}</span>
            </div>
          )}
          <ol className="divide-y divide-border/60">
            {laneSteps.map((step, idx) => (
              <TranscriptStep key={step.step_id} step={step} index={idx + 1} t={t} />
            ))}
          </ol>
        </div>
      ))}
      {finalAnswer && (
        <div className="border-t border-border px-4 py-3">
          <div className="mb-1.5 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
            {t("subagents_view.final_answer")}
          </div>
          <ClampedBlock text={finalAnswer} t={t} lines={8} className="whitespace-pre-wrap text-sm text-foreground/90" />
        </div>
      )}
    </div>
  );
}

function TranscriptStep({ step, index, t }: { step: PlanStep; index: number; t: T }) {
  const kind = step.kind ?? "tool";
  const Icon = kind === "reasoning" ? Brain : kind === "spawn" ? Bot : Wrench;
  const body = step.error ?? step.output ?? null;
  return (
    <li className="grid grid-cols-[36px_28px_minmax(0,1fr)_110px] items-start gap-x-3 px-4 py-2.5">
      <span className="pt-0.5 text-right font-mono text-[11px] tabular-nums text-muted-foreground/60">{index}</span>
      <span className={cn("grid h-6 w-6 place-items-center rounded-full border border-border bg-card", kind === "reasoning" ? "text-primary" : "text-muted-foreground")}>
        <Icon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0">
        <div className={cn("text-sm", kind === "reasoning" ? "text-foreground/85" : "font-mono text-[13px] text-foreground")}>
          {kind === "reasoning" ? (
            <ClampedBlock text={step.output ?? step.name} t={t} lines={3} />
          ) : (
            <>
              {step.tool_name && <span className="mr-2 text-muted-foreground">{step.tool_name}</span>}
              <span className="break-all">{step.name}</span>
            </>
          )}
        </div>
        {kind !== "reasoning" && body && (
          <ClampedBlock
            text={body}
            t={t}
            lines={2}
            className={cn("mt-0.5 whitespace-pre-wrap font-mono text-[12px]", step.error ? "text-destructive" : "text-muted-foreground")}
          />
        )}
        {step.writes && step.writes.length > 0 && (
          <div className="mt-0.5 font-mono text-[11px] text-muted-foreground/70">
            {fill(t("subagents_view.step_writes"), { path: step.writes.join(", ") })}
          </div>
        )}
      </div>
      <div className="flex justify-end">
        {kind !== "reasoning" && (
          <StatusDot tone={STEP_TONE[step.status] ?? "off"} pulse={step.status === "running"} label={t(`subagents_view.step_status.${step.status}`)} />
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Review — the critic's verdicts and the corrections it demanded
// ---------------------------------------------------------------------------

function Review({ outcome, t }: { outcome: AgentOutcome; t: T }) {
  if (outcome.verdicts.length === 0 && !outcome.lastCorrection) {
    return <EmptyNote>{t("subagents_view.review_empty")}</EmptyNote>;
  }
  return (
    <div className="divide-y divide-border/60">
      {outcome.verdicts.map((v, idx) => {
        const tone: StoryTone = v.verdict === "approve" ? "ok" : v.verdict === "revise" ? "warn" : "error";
        return (
          <div key={`${v.worker_id}-${v.iteration}-${idx}`} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className={cn("rounded-md border px-1.5 py-0.5 font-mono text-[10.5px] uppercase tracking-wider", TONE_RING[tone], TONE_TEXT[tone])}>
                {t(`subagents_view.verdict.${v.verdict}`)}
              </span>
              <span className="text-[11px] text-muted-foreground">
                {fill(t("subagents_view.iteration_short"), { n: v.iteration + 1 })} · {Math.round(v.confidence * 100)}%
              </span>
            </div>
            <p className="mt-1.5 text-sm text-foreground/90">{v.summary}</p>
            <ul className="mt-2 space-y-1">
              {Object.entries(v.axes ?? {}).map(([name, axis]) => {
                const passed = axisPassed(axis);
                const evidence = (axis.evidence ?? []).filter((e): e is string => typeof e === "string");
                return (
                  <li key={name} className="text-xs">
                    <span className={cn("mr-2 inline-flex items-center gap-1 font-mono", passed === true ? TONE_TEXT.ok : passed === false ? TONE_TEXT.error : "text-muted-foreground")}>
                      {passed === true ? <CheckCircle2 className="h-3 w-3" /> : passed === false ? <XCircle className="h-3 w-3" /> : <CircleAlert className="h-3 w-3" />}
                      {name}
                    </span>
                    {evidence.length > 0 && (
                      <span className="text-muted-foreground">{evidence.join(" · ")}</span>
                    )}
                    {axis.notes && <span className="ml-1 italic text-muted-foreground">{String(axis.notes)}</span>}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      {outcome.lastCorrection && (
        <div className="px-4 py-3">
          <div className="flex items-center gap-2 text-xs font-medium text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            {outcome.lastCorrection.next_model
              ? fill(t("subagents_view.story.correction_model"), { model: outcome.lastCorrection.next_model })
              : t("subagents_view.story.correction")}
          </div>
          <p className="mt-1 whitespace-pre-wrap text-sm text-foreground/90">{outcome.lastCorrection.correction_instruction}</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Output — summary, final answer, deliverables
// ---------------------------------------------------------------------------

function Output({
  outcome,
  finalAnswer,
  artifacts,
  truncated,
  slug,
  onOpenOutput,
  t,
}: {
  outcome: AgentOutcome;
  finalAnswer: string | null;
  artifacts: MissionArtifact[];
  truncated: boolean;
  slug: string | null;
  onOpenOutput: (slug: string) => void;
  t: T;
}) {
  if (!outcome.summary && !finalAnswer && artifacts.length === 0) {
    return <EmptyNote>{t("subagents_view.output_empty")}</EmptyNote>;
  }
  return (
    <div className="divide-y divide-border/60">
      {outcome.summary && (
        <div className="px-4 py-3">
          <div className="mb-1 text-xs font-medium text-muted-foreground">{t("subagents_view.line_summary")}</div>
          <p className="text-sm text-foreground/90">{outcome.summary}</p>
        </div>
      )}
      {finalAnswer && (
        <div className="px-4 py-3">
          <div className="mb-1 text-xs font-medium text-muted-foreground">{t("subagents_view.final_answer")}</div>
          <ClampedBlock text={finalAnswer} t={t} lines={10} className="whitespace-pre-wrap text-sm text-foreground/90" />
        </div>
      )}
      {artifacts.length > 0 && (
        <div>
          <div className="flex items-center justify-between gap-3 px-4 py-2.5">
            <div className="text-xs font-medium text-muted-foreground">
              {fill(t("subagents_view.deliverables"), { n: artifacts.length })}
              {truncated && <span className="ml-2 text-amber-600 dark:text-amber-400">{t("subagents_view.deliverables_truncated")}</span>}
            </div>
            {slug && (
              <SoftButton onClick={() => onOpenOutput(slug)}>
                <ArrowUpRight className="h-3.5 w-3.5" />
                {t("subagents_view.action_open_output")}
              </SoftButton>
            )}
          </div>
          <ul className="divide-y divide-border/60 border-t border-border/60">
            {artifacts.map((a) => (
              <li key={a.path} className="px-4 py-2.5">
                <div className="flex items-center gap-2 text-sm">
                  <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px]" title={a.deliverable_path}>{a.deliverable_path}</span>
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{formatBytes(a.size)}</span>
                </div>
                {a.content && (
                  <ClampedBlock
                    text={a.content}
                    t={t}
                    lines={4}
                    className="mt-1 whitespace-pre-wrap rounded-md bg-sheen/[0.04] px-2.5 py-1.5 font-mono text-[12px] text-muted-foreground"
                  />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

function LiveToolCall({ call, t }: { call: ToolCallEntry; t: T }) {
  const tone: Record<ToolCallEntry["status"], "busy" | "ok" | "error"> = { running: "busy", completed: "ok", failed: "error" };
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_64px_96px] items-center gap-3 px-4 py-2.5 text-sm">
      <div className="min-w-0">
        <div className="truncate font-medium text-foreground">{call.tool_name || t("subagents_view.tool_unnamed")}</div>
        <div className="truncate text-xs text-muted-foreground" title={call.args_preview}>
          {call.error || call.args_preview || call.output_preview || "—"}
        </div>
      </div>
      <div className="text-right text-xs tabular-nums text-muted-foreground">
        {call.duration_ms != null ? formatDuration(call.duration_ms) : "—"}
      </div>
      <div className="flex justify-end">
        <StatusDot tone={tone[call.status]} pulse={call.status === "running"} label={t(`subagents_view.tool_status.${call.status}`)} />
      </div>
    </div>
  );
}

const CLAMP_CLASS: Record<number, string> = {
  2: "line-clamp-2",
  3: "line-clamp-3",
  4: "line-clamp-4",
  8: "line-clamp-[8]",
  10: "line-clamp-[10]",
};

/** Text that clamps to `lines` and expands on a click — the page's own "more". */
function ClampedBlock({ text, t, lines, className }: { text: string; t: T; lines: 2 | 3 | 4 | 8 | 10; className?: string }) {
  const [open, setOpen] = useState(false);
  // Roughly: a text that cannot overflow needs no toggle. Measured by length
  // and line breaks rather than layout so the list never re-flows on mount.
  const long = text.length > lines * 90 || text.split("\n").length > lines;
  return (
    <div className={cn("min-w-0", className)}>
      <div className={cn("break-words", !open && long && CLAMP_CLASS[lines])}>{text}</div>
      {long && (
        <button type="button" onClick={() => setOpen((v) => !v)} className="mt-0.5 text-xs text-primary hover:underline">
          {open ? t("subagents_view.see_less") : t("subagents_view.see_more")}
        </button>
      )}
    </div>
  );
}

function EmptyNote({ children }: { children: React.ReactNode }) {
  return <div className="px-4 py-8 text-center text-sm text-muted-foreground">{children}</div>;
}

function Notice({ tone, children }: { tone: "warn" | "error"; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-sm",
        tone === "error"
          ? "border-destructive/30 bg-destructive/10 text-destructive"
          : "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
      )}
    >
      <CircleAlert className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{children}</span>
    </div>
  );
}
