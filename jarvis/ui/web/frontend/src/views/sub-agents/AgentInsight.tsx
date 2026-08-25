/**
 * AgentInsight — one agent run, explained.
 *
 * The board row answers "did it land?" in one word. This page answers what a
 * person asks next, in the order they ask it:
 *
 *   1. What happened?      — one paragraph in plain words, with the provider's
 *                             or the reviewer's own sentence when they left one.
 *   2. How did it go?      — a timeline: what the agent said, what it ran
 *                             (folded: "ran 14 actions"), what the reviewer
 *                             said, where it stopped.
 *   3. What came out?      — the answer and the files, with the way to them.
 *
 * Everything else (ids, raw reason tokens, the full request) sits behind a
 * "Details" fold at the bottom. No tabs: a run's story is read top to bottom.
 *
 * Four sources, all of which already exist — nothing here is a second store:
 * `GET /api/missions/{id}` (events), `GET /api/missions/{id}/result` (summary
 * + files), `GET /api/outputs` + `/{slug}/plan` (the archived transcript, only
 * while the directory exists) and the live registry node the board holds.
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
  ChevronDown,
  ChevronRight,
  CircleAlert,
  FileText,
  Gavel,
  ListChecks,
  Map as MapIcon,
  Rocket,
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
import { BackLink, Panel, SoftButton, StatusDot } from "@/components/extensions/primitives";
import { agentBrand, agentsBrand } from "@/lib/agentBrand";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { fill, useT, useUiLanguage } from "@/i18n";
import {
  buildStory,
  deriveOutcome,
  groupStory,
  KILL_REASON_LABEL_KEYS,
  missionIdFromTraceId,
  type ActionsBlock,
  type StoryBlock,
  type StoryEntry,
  type StoryTone,
} from "./outcome";
import { composeNarrative, reasonText, type Narrative } from "./narrative";
import { formatBytes, formatClock, formatDuration, formatOffset, formatUsd } from "./format";

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

const STATE_TONE: Record<Narrative["state"], StoryTone> = {
  running: "busy",
  approved: "ok",
  failed: "error",
  cancelled: "warn",
  timed_out: "error",
};

const STATE_ICON: Record<Narrative["state"], typeof Bot> = {
  running: Rocket,
  approved: CheckCircle2,
  failed: XCircle,
  cancelled: StopCircle,
  timed_out: Timer,
};

/** The first paragraph of the request — the part the user actually said. */
export function requestTitle(text: string | null | undefined, fallback: string): string {
  const raw = (text ?? "").trim();
  if (!raw) return fallback;
  const first = raw.split(/\n\s*\n/)[0].replace(/\s+/g, " ").trim();
  return first.length > 180 ? `${first.slice(0, 177).trimEnd()}…` : first || fallback;
}

function axisPassed(axis: CriticAxisResult): boolean | null {
  if (axis.pass === true || axis.status === "pass") return true;
  if (axis.pass === false || axis.status === "fail") return false;
  return null;
}

function killLabel(reason: string, t: T): string {
  const key = KILL_REASON_LABEL_KEYS[reason];
  return key ? t(key) : reason;
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
  const agentName = agentBrand(assistantName);
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
  const blocks = useMemo(() => groupStory(buildStory(events)), [events]);
  const steps: PlanStep[] = plan.data?.steps ?? [];
  const artifacts: MissionArtifact[] = result.data?.artifacts ?? [];
  const finalAnswer = plan.data?.final_answer ?? null;

  const title = requestTitle(result.data?.prompt ?? agent.utterance, t("subagents_view.task_none"));
  const startedMs = events[0]?.ts_ms ?? Math.floor(agent.started_ns / 1_000_000);
  const endedMs = detail.data?.mission.updated_ms ?? null;
  const runtimeMs =
    agent.duration_ms ??
    (outcome.terminal && endedMs ? endedMs - startedMs : running ? Date.now() - startedMs : null);
  const runtime = runtimeMs != null ? formatDuration(runtimeMs) : "—";
  const notes = outcome.workers.reduce((sum, w) => sum + w.notes, 0);
  const actions = Math.max(
    agent.tool_calls.length,
    steps.filter((s) => s.kind !== "reasoning").length,
    outcome.workers.reduce((sum, w) => sum + w.tool_notes, 0),
  );

  const narrative = useMemo(
    () =>
      composeNarrative(
        agent,
        outcome,
        { agentName, runtime, artifactCount: result.data?.artifact_count ?? artifacts.length, notes },
        t,
      ),
    [agent, outcome, agentName, runtime, result.data?.artifact_count, artifacts.length, notes, t],
  );

  const worker = outcome.workers.at(0) ?? null;
  const rounds = Math.max(outcome.revisions, outcome.verdicts.at(-1)?.iteration ?? 0) + 1;
  const cost = Math.max(outcome.cost_usd, agent.cost_usd);
  const facts: Array<{ label: string; value: string; hint?: string }> = [
    { label: t("subagents_view.fact_started"), value: formatClock(startedMs, locale) },
    { label: t("subagents_view.col_runtime"), value: runtime },
    ...(worker
      ? [{ label: t("subagents_view.fact_worker"), value: worker.cli || "—", hint: worker.model || undefined }]
      : []),
    ...(outcome.verdicts.length > 0 || outcome.revisions > 0
      ? [{ label: t("subagents_view.fact_rounds"), value: String(rounds) }]
      : []),
    ...(actions > 0 ? [{ label: t("subagents_view.fact_actions"), value: String(actions) }] : []),
    ...(cost > 0 ? [{ label: t("subagents_view.stat_cost_label"), value: formatUsd(cost) }] : []),
  ];

  const hasOutput = !!finalAnswer || artifacts.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto flex w-full max-w-[980px] flex-col gap-5 px-6 py-6">
          <BackLink label={agentsBrand(assistantName)} onClick={onBack} />

          {/* Header ---------------------------------------------------- */}
          <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {agentName}
              </div>
              <h1 className="mt-1 line-clamp-2 font-display text-[22px] font-semibold leading-snug tracking-tight text-foreground">
                {title}
              </h1>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
                <StatusDot
                  tone={STATUS_TONE[agent.status]}
                  pulse={running}
                  label={t(`subagents_view.status.${agent.status}`)}
                />
                <span aria-hidden>·</span>
                <span>{formatClock(startedMs, locale)}</span>
                <span aria-hidden>·</span>
                <span>{runtime}</span>
              </div>
            </div>
            {slug && (
              <div className="flex shrink-0 items-center gap-2">
                <SoftButton
                  onClick={() => void openExternalUrl(`${window.location.origin}${missionMapUrl(slug)}`)}
                >
                  <MapIcon className="h-3.5 w-3.5" />
                  {t("subagents_view.action_map")}
                </SoftButton>
                <SoftButton primary onClick={() => onOpenOutput(slug)}>
                  <ArrowUpRight className="h-3.5 w-3.5" />
                  {t("subagents_view.action_open_output")}
                </SoftButton>
              </div>
            )}
          </div>

          {detail.isError && (
            <Notice>
              {fill(t("subagents_view.insight_load_error"), {
                detail: detail.error instanceof Error ? detail.error.message : "",
              })}
            </Notice>
          )}

          {/* 1. What happened ----------------------------------------- */}
          <VerdictCard narrative={narrative} loading={detail.isPending} facts={facts} t={t} />

          {/* Live tool calls, while the registry still holds them ------ */}
          {agent.tool_calls.length > 0 && (
            <Section icon={TerminalSquare} title={t("subagents_view.live_title")} subtitle={t("subagents_view.live_subtitle")}>
              <div className="divide-y divide-border/60">
                {agent.tool_calls.map((call, idx) => (
                  <LiveToolCall key={`${agent.trace_id}-${idx}`} call={call} t={t} />
                ))}
              </div>
            </Section>
          )}

          {/* 2. How it went ------------------------------------------- */}
          <Section
            icon={ListChecks}
            title={t("subagents_view.timeline_title")}
            subtitle={fill(t("subagents_view.timeline_subtitle"), { agent: agentName })}
          >
            <Timeline
              blocks={blocks}
              startedMs={startedMs}
              loading={detail.isPending}
              agentName={agentName}
              quoteText={narrative.quote?.text ?? null}
              t={t}
            />
            <TranscriptFold
              steps={steps}
              hasSlug={!!slug}
              hadWorkers={outcome.workers.length > 0}
              loading={!!slug && plan.isPending}
              truncated={!!plan.data?.truncated}
              dropped={plan.data?.dropped_steps ?? 0}
              t={t}
            />
          </Section>

          {/* 3. What came out ----------------------------------------- */}
          {hasOutput && (
            <Section
              icon={FileText}
              title={t("subagents_view.output_title")}
              subtitle={
                artifacts.length > 0
                  ? fill(t("subagents_view.deliverables"), { n: artifacts.length })
                  : t("subagents_view.final_answer")
              }
              actions={
                slug ? (
                  <SoftButton primary onClick={() => onOpenOutput(slug)}>
                    <ArrowUpRight className="h-3.5 w-3.5" />
                    {t("subagents_view.action_open_output")}
                  </SoftButton>
                ) : null
              }
            >
              {finalAnswer && (
                <div className="px-5 py-4">
                  <ClampedBlock text={finalAnswer} t={t} lines={8} className="whitespace-pre-wrap text-[15px] leading-relaxed text-foreground/90" />
                </div>
              )}
              {artifacts.length > 0 && (
                <ul className={cn("divide-y divide-border/60", finalAnswer && "border-t border-border/60")}>
                  {artifacts.map((a) => (
                    <li key={a.path} className="px-5 py-3">
                      <div className="flex items-center gap-2.5 text-sm">
                        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate font-medium" title={a.deliverable_path}>
                          {a.deliverable_path}
                        </span>
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{formatBytes(a.size)}</span>
                      </div>
                    </li>
                  ))}
                  {result.data?.truncated && (
                    <li className="px-5 py-2 text-xs text-muted-foreground">{t("subagents_view.deliverables_truncated")}</li>
                  )}
                </ul>
              )}
            </Section>
          )}

          {/* Details, for the curious ---------------------------------- */}
          <details className="group rounded-xl border border-border/70 bg-card/30 px-5 py-3 text-sm">
            <summary className="flex cursor-pointer select-none items-center gap-2 text-muted-foreground hover:text-foreground">
              <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90" />
              {t("subagents_view.details_title")}
            </summary>
            <dl className="mt-3 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
              <dt className="text-muted-foreground">{t("subagents_view.fact_request")}</dt>
              <dd className="whitespace-pre-wrap break-words text-foreground/85">{result.data?.prompt ?? agent.utterance ?? "—"}</dd>
              <dt className="text-muted-foreground">{t("subagents_view.fact_mission_id")}</dt>
              <dd className="font-mono text-xs">{missionId}</dd>
              {outcome.reason && (
                <>
                  <dt className="text-muted-foreground">{t("subagents_view.fact_reason")}</dt>
                  <dd className="font-mono text-xs">
                    {outcome.reason}
                    {outcome.error_class ? ` · ${outcome.error_class}` : ""}
                    {outcome.last_state ? ` · ${outcome.last_state}` : ""}
                  </dd>
                </>
              )}
              {worker && (
                <>
                  <dt className="text-muted-foreground">{t("subagents_view.fact_worker")}</dt>
                  <dd className="font-mono text-xs">
                    {outcome.workers.map((w) => `${w.cli}${w.model ? ` (${w.model})` : ""} · ${w.worker_id}`).join("\n")}
                  </dd>
                </>
              )}
              {outcome.result_uri && (
                <>
                  <dt className="text-muted-foreground">{t("subagents_view.fact_result_uri")}</dt>
                  <dd className="break-all font-mono text-xs">{outcome.result_uri}</dd>
                </>
              )}
              {slug ? (
                <>
                  <dt className="text-muted-foreground">{t("subagents_view.fact_output_dir")}</dt>
                  <dd className="font-mono text-xs">{slug}</dd>
                </>
              ) : (
                <>
                  <dt className="text-muted-foreground">{t("subagents_view.fact_output_dir")}</dt>
                  <dd className="text-muted-foreground">{t("subagents_view.no_output_dir")}</dd>
                </>
              )}
            </dl>
          </details>
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. The verdict card
// ---------------------------------------------------------------------------

function VerdictCard({
  narrative,
  loading,
  facts,
  t,
}: {
  narrative: Narrative;
  loading: boolean;
  facts: Array<{ label: string; value: string; hint?: string }>;
  t: T;
}) {
  const tone = STATE_TONE[narrative.state];
  const Icon = STATE_ICON[narrative.state];
  return (
    <Panel>
      <div className="flex items-start gap-4 px-5 pb-4 pt-5">
        <span className={cn("grid h-11 w-11 shrink-0 place-items-center rounded-full border", TONE_RING[tone], TONE_TEXT[tone])}>
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-lg font-semibold leading-tight text-foreground">{narrative.headline}</h2>
          <p className="mt-1.5 text-[15px] leading-relaxed text-foreground/90">
            {loading ? t("subagents_view.insight_loading") : narrative.paragraph}
          </p>
          {narrative.quote && (
            <blockquote className="mt-3 border-l-2 border-border pl-3">
              <div className="text-xs font-medium text-muted-foreground">{narrative.quote.label}</div>
              <div
                className={cn(
                  "mt-0.5 whitespace-pre-wrap break-words text-foreground/85",
                  narrative.quote.mono ? "font-mono text-[12.5px] leading-relaxed" : "text-[14px] leading-relaxed",
                )}
              >
                {narrative.quote.text}
              </div>
            </blockquote>
          )}
          {narrative.note && (
            <p className="mt-3 flex items-center gap-1.5 text-sm text-muted-foreground">
              <CircleAlert className="h-3.5 w-3.5 shrink-0" />
              {narrative.note}
            </p>
          )}
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-px border-t border-border bg-border/60 sm:grid-cols-3 lg:grid-cols-6">
        {facts.map((f) => (
          <div key={f.label} className="bg-card/80 px-4 py-2.5">
            <dt className="text-[11px] uppercase tracking-wider text-muted-foreground">{f.label}</dt>
            <dd className="mt-0.5 truncate text-sm font-medium text-foreground" title={f.hint ?? f.value}>
              {f.value}
              {f.hint && <span className="ml-1.5 font-normal text-muted-foreground">{f.hint}</span>}
            </dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// 2. The timeline
// ---------------------------------------------------------------------------

const ENTRY_ICON: Record<StoryEntry["kind"], typeof Bot> = {
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

const AXIS_KEYS: Record<string, string> = {
  correctness: "subagents_view.axis.correctness",
  completeness: "subagents_view.axis.completeness",
  side_effects: "subagents_view.axis.side_effects",
  security: "subagents_view.axis.security",
};

function entryTitle(entry: StoryEntry, agentName: string, t: T): string {
  const m = entry.meta;
  switch (entry.kind) {
    case "dispatched":
      return fill(t("subagents_view.story.dispatched"), { agent: agentName });
    case "plan":
      return fill(t("subagents_view.story.plan"), { n: m.workers ?? "1" });
    case "spawn":
      return (
        fill(t("subagents_view.story.spawn"), { cli: m.cli || "—", iter: String((entry.iteration ?? 0) + 1) }) +
        (m.model ? ` · ${m.model}` : "")
      );
    case "narration":
      return agentName;
    case "draft":
      return t("subagents_view.story.draft");
    case "verdict":
      return t("subagents_view.story.verdict");
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
      return fill(t("subagents_view.story.failed"), { reason: reasonText(m.reason, t) });
    case "cancelled":
      return fill(t("subagents_view.story.cancelled"), { reason: reasonText(m.reason, t) });
    case "timed_out":
      return t("subagents_view.story.timed_out");
    default:
      return entry.kind;
  }
}

function Timeline({
  blocks,
  startedMs,
  loading,
  agentName,
  quoteText,
  t,
}: {
  blocks: StoryBlock[];
  startedMs: number;
  loading: boolean;
  agentName: string;
  /** The sentence the verdict card already quotes — not repeated down here. */
  quoteText: string | null;
  t: T;
}) {
  if (blocks.length === 0) {
    return <EmptyNote>{loading ? t("subagents_view.insight_loading") : t("subagents_view.story_empty")}</EmptyNote>;
  }
  return (
    <ol className="relative py-2">
      {/* The rail the nodes sit on. */}
      <span aria-hidden className="absolute bottom-6 left-[83px] top-6 w-px bg-border" />
      {blocks.map((block) =>
        block.kind === "actions" ? (
          <ActionsRow key={block.id} block={block} startedMs={startedMs} t={t} />
        ) : (
          <EntryRow
            key={block.entry.id}
            entry={block.entry}
            startedMs={startedMs}
            agentName={agentName}
            hideText={!!quoteText && block.entry.text === quoteText && block.entry.kind !== "narration"}
            t={t}
          />
        ),
      )}
    </ol>
  );
}

function RowFrame({
  offset,
  icon: Icon,
  tone,
  children,
}: {
  offset: string;
  icon: typeof Bot;
  tone: StoryTone;
  children: React.ReactNode;
}) {
  return (
    <li className="relative grid grid-cols-[56px_28px_minmax(0,1fr)] items-start gap-x-3 px-5 py-2.5">
      <span className="pt-1 text-right font-mono text-[11px] tabular-nums text-muted-foreground/70">{offset}</span>
      <span className={cn("relative z-[1] grid h-7 w-7 place-items-center rounded-full border", TONE_RING[tone], TONE_TEXT[tone])}>
        <Icon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 pt-0.5">{children}</div>
    </li>
  );
}

function EntryRow({
  entry,
  startedMs,
  agentName,
  hideText,
  t,
}: {
  entry: StoryEntry;
  startedMs: number;
  agentName: string;
  hideText: boolean;
  t: T;
}) {
  const title = entryTitle(entry, agentName, t);
  const strong = ["approved", "failed", "cancelled", "timed_out", "killed", "verdict", "spawn"].includes(entry.kind);
  const text = hideText ? "" : entry.text;

  return (
    <RowFrame offset={formatOffset(entry.ts_ms - startedMs)} icon={ENTRY_ICON[entry.kind]} tone={entry.tone}>
      {entry.kind === "narration" ? (
        <div className="border-l-2 border-primary/40 pl-3">
          <div className="text-xs font-medium text-muted-foreground">{title}</div>
          <ClampedBlock text={text} t={t} lines={3} className="mt-0.5 text-[14.5px] leading-relaxed text-foreground/90" />
        </div>
      ) : entry.kind === "verdict" && entry.verdict ? (
        <VerdictRow verdict={entry.verdict} t={t} />
      ) : (
        <>
          <div className={cn("text-sm", strong ? "font-semibold text-foreground" : "font-medium text-foreground/85")}>{title}</div>
          {text && (
            <ClampedBlock
              text={text}
              t={t}
              lines={3}
              className={cn("mt-0.5 text-sm leading-relaxed", entry.tone === "error" ? "text-destructive/90" : "text-muted-foreground")}
            />
          )}
        </>
      )}
    </RowFrame>
  );
}

function VerdictRow({ verdict, t }: { verdict: CriticVerdictReady; t: T }) {
  const tone: StoryTone = verdict.verdict === "approve" ? "ok" : verdict.verdict === "revise" ? "warn" : "error";
  const axes = Object.entries(verdict.axes ?? {});
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-foreground">{t("subagents_view.story.verdict")}</span>
        <span className={cn("rounded-md border px-1.5 py-0.5 text-[11px] font-medium", TONE_RING[tone], TONE_TEXT[tone])}>
          {t(`subagents_view.verdict.${verdict.verdict}`)}
        </span>
        <span className="text-xs text-muted-foreground">{Math.round(verdict.confidence * 100)}%</span>
      </div>
      <p className="mt-1 text-sm leading-relaxed text-foreground/85">{verdict.summary}</p>
      {axes.length > 0 && (
        <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
          {axes.map(([name, axis]) => {
            const passed = axisPassed(axis);
            const label = AXIS_KEYS[name] ? t(AXIS_KEYS[name]) : name;
            return (
              <li
                key={name}
                className={cn(
                  "inline-flex items-center gap-1 text-xs",
                  passed === true ? TONE_TEXT.ok : passed === false ? TONE_TEXT.error : "text-muted-foreground",
                )}
              >
                {passed === true ? <CheckCircle2 className="h-3.5 w-3.5" /> : passed === false ? <XCircle className="h-3.5 w-3.5" /> : <CircleAlert className="h-3.5 w-3.5" />}
                {label}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ActionsRow({ block, startedMs, t }: { block: ActionsBlock; startedMs: number; t: T }) {
  const [open, setOpen] = useState(false);
  const span = block.end_ms - block.ts_ms;
  return (
    <RowFrame offset={formatOffset(block.ts_ms - startedMs)} icon={Wrench} tone="neutral">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left"
      >
        <span className="text-sm font-medium text-foreground/85">
          {fill(t(block.entries.length === 1 ? "subagents_view.actions_one" : "subagents_view.actions_many"), {
            n: block.entries.length,
            span: formatDuration(span),
          })}
        </span>
        <ChevronDown className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {block.counts.map((c) => (
          <span key={c.tool} className="inline-flex h-6 items-center gap-1 rounded-md border border-border bg-sheen/[0.04] px-2 font-mono text-[11.5px] text-foreground/80">
            {c.tool}
            {c.n > 1 && <span className="text-muted-foreground">×{c.n}</span>}
          </span>
        ))}
      </div>
      {open && (
        <ol className="mt-2 divide-y divide-border/50 rounded-lg border border-border/70 bg-sheen/[0.03]">
          {block.entries.map((e) => (
            <li key={e.id} className="grid grid-cols-[52px_minmax(0,1fr)] gap-x-3 px-3 py-1.5 font-mono text-[12px]">
              <span className="text-muted-foreground/60">{formatOffset(e.ts_ms - startedMs)}</span>
              <span className="min-w-0 break-all text-foreground/85">
                <span className="mr-2 text-muted-foreground">{e.tool}</span>
                {e.text}
              </span>
            </li>
          ))}
        </ol>
      )}
    </RowFrame>
  );
}

// ---------------------------------------------------------------------------
// The archived transcript, folded under the timeline
// ---------------------------------------------------------------------------

const STEP_TONE: Record<string, "busy" | "ok" | "error" | "warn" | "off"> = {
  pending: "off",
  running: "busy",
  done: "ok",
  failed: "error",
  skipped: "warn",
};

function TranscriptFold({
  steps,
  hasSlug,
  hadWorkers,
  loading,
  truncated,
  dropped,
  t,
}: {
  steps: PlanStep[];
  hasSlug: boolean;
  hadWorkers: boolean;
  loading: boolean;
  truncated: boolean;
  dropped: number;
  t: T;
}) {
  const [open, setOpen] = useState(false);
  if (!hasSlug) {
    return hadWorkers ? (
      <div className="border-t border-border/60 px-5 py-2.5 text-xs text-muted-foreground">{t("subagents_view.transcript_no_dir")}</div>
    ) : null;
  }
  if (loading || steps.length === 0) return null;

  return (
    <div className="border-t border-border/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-5 py-3 text-left text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronRight className={cn("h-4 w-4 transition-transform", open && "rotate-90")} />
        {fill(t("subagents_view.transcript_fold"), { n: steps.length })}
      </button>
      {open && (
        <div className="border-t border-border/60">
          {(truncated || dropped > 0) && (
            <div className="px-5 py-2 text-xs text-amber-600 dark:text-amber-400">
              {dropped > 0 ? fill(t("subagents_view.transcript_dropped"), { n: dropped }) : t("subagents_view.transcript_truncated")}
            </div>
          )}
          <ol className="divide-y divide-border/50">
            {steps.map((step, idx) => {
              const kind = step.kind ?? "tool";
              const Icon = kind === "reasoning" ? Brain : kind === "spawn" ? Bot : Wrench;
              const body = step.error ?? step.output ?? null;
              return (
                <li key={step.step_id} className="grid grid-cols-[36px_28px_minmax(0,1fr)_100px] items-start gap-x-3 px-5 py-2.5">
                  <span className="pt-0.5 text-right font-mono text-[11px] tabular-nums text-muted-foreground/60">{idx + 1}</span>
                  <span className={cn("grid h-6 w-6 place-items-center rounded-full border border-border bg-card", kind === "reasoning" ? "text-primary" : "text-muted-foreground")}>
                    <Icon className="h-3.5 w-3.5" />
                  </span>
                  <div className="min-w-0">
                    {kind === "reasoning" ? (
                      <ClampedBlock text={step.output ?? step.name} t={t} lines={3} className="text-sm leading-relaxed text-foreground/85" />
                    ) : (
                      <>
                        <div className="font-mono text-[12.5px] text-foreground">
                          {step.tool_name && <span className="mr-2 text-muted-foreground">{step.tool_name}</span>}
                          <span className="break-all">{step.name}</span>
                        </div>
                        {body && (
                          <ClampedBlock
                            text={body}
                            t={t}
                            lines={2}
                            className={cn("mt-0.5 whitespace-pre-wrap font-mono text-[11.5px]", step.error ? "text-destructive" : "text-muted-foreground")}
                          />
                        )}
                      </>
                    )}
                  </div>
                  <div className="flex justify-end">
                    {kind !== "reasoning" && (
                      <StatusDot tone={STEP_TONE[step.status] ?? "off"} pulse={step.status === "running"} label={t(`subagents_view.step_status.${step.status}`)} />
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

function Section({
  icon: Icon,
  title,
  subtitle,
  actions,
  children,
}: {
  icon: typeof Bot;
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Panel>
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-3.5">
        <div className="flex min-w-0 items-start gap-2.5">
          <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0">
            <h2 className="font-display text-[15px] font-semibold text-foreground">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>}
          </div>
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </div>
      {children}
    </Panel>
  );
}

function LiveToolCall({ call, t }: { call: ToolCallEntry; t: T }) {
  const tone: Record<ToolCallEntry["status"], "busy" | "ok" | "error"> = { running: "busy", completed: "ok", failed: "error" };
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_64px_96px] items-center gap-3 px-5 py-2.5 text-sm">
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
  8: "line-clamp-[8]",
};

/** Text that clamps to `lines` and expands on a click — the page's own "more". */
function ClampedBlock({ text, t, lines, className }: { text: string; t: T; lines: 2 | 3 | 8; className?: string }) {
  const [open, setOpen] = useState(false);
  // Judged by length and line breaks rather than layout, so the list never
  // re-flows on mount.
  const long = text.length > lines * 100 || text.split("\n").length > lines;
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
  return <div className="px-5 py-8 text-center text-sm text-muted-foreground">{children}</div>;
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-sm text-destructive">
      <CircleAlert className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{children}</span>
    </div>
  );
}
