/**
 * AgentInsight — the dossier of one agent run.
 *
 * Set like the Marketplace catalogue rather than like a dashboard: numbered
 * registers with hairlines between rows, one display-face title, a status
 * stamp, monospace only where a value is a value (timestamps, paths, counts).
 * No icon-in-a-circle timelines, no tabs, no stat tiles — a run is a report,
 * and a report is read top to bottom:
 *
 *   01  What happened     — one paragraph in plain words, the provider's or
 *                           reviewer's own sentence quoted once, then a ledger
 *                           of the facts (when, how long, which worker, …).
 *   02  The agent's report — what the worker itself wrote at the end, as
 *                           rendered Markdown. For most runs this is the most
 *                           detailed account of the work that exists.
 *   03  Timeline           — every recorded moment: what the agent said, what
 *                           it ran (folded per burst), the reviewer's verdict
 *                           with its evidence, where it stopped.
 *   04  Files              — what the workers actually changed, per file with
 *                           added/removed lines (from the archived diff), or
 *                           what they delivered. Every row leads to Artifacts.
 *   05  Details            — ids, raw reason tokens, the full request; folded.
 *
 * Sources, all pre-existing: `GET /api/missions/{id}` (events), `/result`
 * (summary + deliverables), `/changes` (the diff ledger; an older backend
 * answers 404 and the page falls back to the deliverable list), `/api/outputs`
 * + `/{slug}/plan` (the archived transcript) and the live registry node.
 *
 * Every visible string goes through i18n; the brand is the wake-word-derived
 * assistant name (lib/agentBrand.ts), never a product name.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Map as MapIcon,
  XCircle,
} from "lucide-react";

import type { SubAgentNode, ToolCallEntry } from "@/store/jarvisAgents";
import type {
  CriticAxisResult,
  CriticVerdictReady,
  MissionArtifact,
  MissionChangedFile,
} from "@/types/missions";
import { fetchMissionChanges, fetchMissionDetail, fetchMissionResult } from "@/components/missions/api";
import { useOutputsList, usePlanForOutput, type PlanStep } from "@/hooks/useOutputs";
import { missionMapUrl } from "@/hooks/useVisualArtifacts";
import { MarkdownProse } from "@/components/outputs/MarkdownProse";
import { openExternalUrl } from "@/lib/openExternal";
import { ScrollArea } from "@/components/ui/scroll-area";
import { BackLink, SoftButton, StatusDot } from "@/components/extensions/primitives";
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

// ---------------------------------------------------------------------------
// Tone vocabulary — one place, so light and dark read the same everywhere
// ---------------------------------------------------------------------------

const TONE_TEXT: Record<StoryTone, string> = {
  neutral: "text-muted-foreground",
  busy: "text-primary",
  ok: "text-emerald-600 dark:text-emerald-400",
  warn: "text-amber-600 dark:text-amber-400",
  error: "text-destructive",
};

const STAMP: Record<StoryTone, string> = {
  neutral: "border-border text-muted-foreground",
  busy: "border-primary/60 text-primary",
  ok: "border-emerald-500/60 text-emerald-600 dark:text-emerald-400",
  warn: "border-amber-500/60 text-amber-600 dark:text-amber-400",
  error: "border-destructive/60 text-destructive",
};

const STATE_TONE: Record<Narrative["state"], StoryTone> = {
  running: "busy",
  approved: "ok",
  failed: "error",
  cancelled: "warn",
  timed_out: "error",
};

const KIND_KEY: Record<StoryEntry["kind"], string> = {
  dispatched: "start",
  plan: "plan",
  spawn: "worker",
  narration: "said",
  tool: "ran",
  draft: "draft",
  verdict: "review",
  correction: "fix",
  killed: "stop",
  budget: "budget",
  approved: "end",
  failed: "end",
  cancelled: "end",
  timed_out: "end",
};

const AXIS_KEYS: Record<string, string> = {
  correctness: "subagents_view.axis.correctness",
  completeness: "subagents_view.axis.completeness",
  side_effects: "subagents_view.axis.side_effects",
  security: "subagents_view.axis.security",
};

const CHANGE_GLYPH: Record<MissionChangedFile["status"], { glyph: string; tone: StoryTone }> = {
  added: { glyph: "A", tone: "ok" },
  modified: { glyph: "M", tone: "warn" },
  deleted: { glyph: "D", tone: "error" },
  renamed: { glyph: "R", tone: "neutral" },
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

function pad2(n: number): string {
  return String(n).padStart(2, "0");
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
  // An older backend has no /changes route: the query errors once and the
  // file register falls back to the deliverable list. Never retried — a 404
  // is an answer, not a hiccup.
  const changes = useQuery({
    queryKey: ["missions", "changes", missionId],
    queryFn: () => fetchMissionChanges(missionId),
    retry: false,
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
  const changedFiles: MissionChangedFile[] = changes.data?.files ?? [];
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
  const fileCount = changedFiles.length || artifacts.length;

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
  const tone = STATE_TONE[narrative.state];

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
    ...(fileCount > 0
      ? [
          {
            label: t("subagents_view.fact_files"),
            value: String(fileCount),
            hint:
              changes.data && (changes.data.additions || changes.data.deletions)
                ? `+${changes.data.additions} −${changes.data.deletions}`
                : undefined,
          },
        ]
      : []),
    ...(cost > 0 ? [{ label: t("subagents_view.stat_cost_label"), value: formatUsd(cost) }] : []),
  ];

  let register = 0;
  const next = () => pad2(++register);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto flex w-full max-w-[1040px] flex-col gap-5 px-6 py-6">
          <BackLink label={agentsBrand(assistantName)} onClick={onBack} />

          {/* Masthead ------------------------------------------------- */}
          <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4 pb-1">
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                {agentName}
                <span className="mx-2 text-muted-foreground/50">·</span>
                <span className="font-mono normal-case tracking-normal">{missionId.slice(0, 13)}</span>
                <span className="mx-2 text-muted-foreground/50">·</span>
                {formatClock(startedMs, locale)}
              </p>
              <h1 className="mt-2 line-clamp-3 font-display text-[26px] font-semibold leading-[1.15] tracking-tight text-foreground">
                {title}
              </h1>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-3">
              <Stamp tone={tone} pulse={narrative.state === "running"}>
                {narrative.headline}
              </Stamp>
              {slug && (
                <div className="flex items-center gap-2">
                  <SoftButton
                    onClick={() => void openExternalUrl(`${window.location.origin}${missionMapUrl(slug)}`)}
                  >
                    <MapIcon className="h-3.5 w-3.5" />
                    {t("subagents_view.action_map")}
                  </SoftButton>
                  <SoftButton primary onClick={() => onOpenOutput(slug)}>
                    {t("subagents_view.action_open_artifacts")}
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </SoftButton>
                </div>
              )}
            </div>
          </header>

          {detail.isError && (
            <Notice>
              {fill(t("subagents_view.insight_load_error"), {
                detail: detail.error instanceof Error ? detail.error.message : "",
              })}
            </Notice>
          )}

          {/* 01 What happened ------------------------------------------ */}
          <Register number={next()} title={t("subagents_view.register_verdict")}>
            <div className="px-5 pb-5 pt-4">
              <p className={cn("max-w-[68ch] text-[16px] leading-relaxed text-foreground", detail.isPending && "text-muted-foreground")}>
                {detail.isPending ? t("subagents_view.insight_loading") : narrative.paragraph}
              </p>
              {narrative.quote && (
                <blockquote className="mt-4 max-w-[68ch] border-l-2 border-primary/60 pl-4">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    {narrative.quote.label}
                  </p>
                  <p
                    className={cn(
                      "mt-1 whitespace-pre-wrap break-words text-foreground/90",
                      narrative.quote.mono ? "font-mono text-[12.5px] leading-relaxed" : "text-[14.5px] leading-relaxed",
                    )}
                  >
                    {narrative.quote.text}
                  </p>
                </blockquote>
              )}
              {narrative.note && (
                <p className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
                  <CircleAlert className="h-3.5 w-3.5 shrink-0" />
                  {narrative.note}
                </p>
              )}
            </div>
            <dl className="grid grid-cols-2 border-t border-border/70 sm:grid-cols-3 lg:grid-cols-7">
              {facts.map((f, i) => (
                <div
                  key={f.label}
                  className={cn(
                    "px-5 py-3 border-border/70",
                    i > 0 && "sm:border-l",
                    i > 0 && i % 2 === 1 && "border-l sm:border-l",
                    i >= 2 && "border-t sm:border-t-0",
                    i >= 3 && "sm:border-t lg:border-t-0",
                  )}
                >
                  <dt className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    {f.label}
                  </dt>
                  <dd className="mt-1 truncate font-display text-[15px] font-semibold tabular-nums text-foreground" title={f.hint ?? f.value}>
                    {f.value}
                    {f.hint && <span className="ml-1.5 font-mono text-[11px] font-normal text-muted-foreground">{f.hint}</span>}
                  </dd>
                </div>
              ))}
            </dl>
          </Register>

          {/* 02 The agent's report -------------------------------------- */}
          {finalAnswer && (
            <Register number={next()} title={fill(t("subagents_view.register_report"), { agent: agentName })}>
              <Report text={finalAnswer} slug={slug ?? ""} t={t} />
            </Register>
          )}

          {/* Live tool calls, while the registry still holds them --------- */}
          {agent.tool_calls.length > 0 && (
            <Register number={next()} title={t("subagents_view.live_title")} meta={t("subagents_view.live_subtitle")}>
              <div className="divide-y divide-border/70">
                {agent.tool_calls.map((call, idx) => (
                  <LiveToolCall key={`${agent.trace_id}-${idx}`} call={call} t={t} />
                ))}
              </div>
            </Register>
          )}

          {/* 03 Timeline ------------------------------------------------ */}
          <Register
            number={next()}
            title={t("subagents_view.register_timeline")}
            meta={blocks.length > 0 ? fill(t("subagents_view.timeline_meta"), { n: events.length }) : undefined}
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
          </Register>

          {/* 04 Files --------------------------------------------------- */}
          {(changedFiles.length > 0 || artifacts.length > 0) && (
            <Register
              number={next()}
              title={t("subagents_view.register_files")}
              meta={
                changes.data && (changes.data.additions || changes.data.deletions)
                  ? fill(t("subagents_view.files_meta"), {
                      n: changedFiles.length,
                      add: changes.data.additions,
                      del: changes.data.deletions,
                    })
                  : fill(t("subagents_view.deliverables"), { n: artifacts.length })
              }
              actions={
                slug ? (
                  <SoftButton primary onClick={() => onOpenOutput(slug)}>
                    {t("subagents_view.action_open_artifacts")}
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </SoftButton>
                ) : null
              }
            >
              <FilesLedger
                changed={changedFiles}
                artifacts={artifacts}
                truncated={!!changes.data?.truncated || !!result.data?.truncated}
                onOpen={slug ? () => onOpenOutput(slug) : undefined}
                t={t}
              />
            </Register>
          )}

          {/* 05 Details ------------------------------------------------- */}
          <details className="group overflow-hidden rounded-xl border border-border bg-card/60 backdrop-blur-sm">
            <summary className="flex cursor-pointer select-none items-baseline gap-3 px-5 py-3 hover:bg-secondary/40">
              <span className="font-mono text-[11px] text-primary">{next()}</span>
              <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                {t("subagents_view.details_title")}
              </span>
              <ChevronRight className="ml-auto h-4 w-4 text-muted-foreground transition-transform group-open:rotate-90" />
            </summary>
            <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-8 gap-y-2.5 border-t border-border/70 px-5 py-4 text-sm">
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
                  <dd className="whitespace-pre-wrap font-mono text-xs">
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
              <dt className="text-muted-foreground">{t("subagents_view.fact_output_dir")}</dt>
              <dd className={cn("text-xs", slug ? "font-mono" : "text-muted-foreground")}>
                {slug ?? t("subagents_view.no_output_dir")}
              </dd>
            </dl>
          </details>
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The register — a numbered section with a hairline header
// ---------------------------------------------------------------------------

function Register({
  number,
  title,
  meta,
  actions,
  children,
}: {
  number: string;
  title: string;
  meta?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card/60 backdrop-blur-sm">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/70 px-5 py-3">
        <span className="font-mono text-[11px] text-primary">{number}</span>
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">{title}</h2>
        {meta && <span className="text-[11px] text-muted-foreground/70">{meta}</span>}
        {actions && <div className="ml-auto">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

function Stamp({ tone, pulse, children }: { tone: StoryTone; pulse?: boolean; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-md border-2 px-3.5 py-1.5 font-display text-[13px] font-bold uppercase tracking-[0.16em]",
        STAMP[tone],
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full bg-current", pulse && "animate-pulse")} />
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 02 The agent's report — Markdown, folded past a screenful
// ---------------------------------------------------------------------------

function Report({ text, slug, t }: { text: string; slug: string; t: T }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 1_400 || text.split("\n").length > 18;
  return (
    <div className="relative">
      <div className={cn("px-6 py-5", !open && long && "max-h-[380px] overflow-hidden")}>
        <MarkdownProse
          slug={slug}
          path="report.md"
          files={[]}
          text={text}
          className="prose-sm max-w-[72ch] prose-headings:font-display prose-h3:text-[15px]"
          testId="agent-report"
        />
      </div>
      {long && (
        <>
          {!open && (
            <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-11 h-24 bg-gradient-to-t from-card to-transparent" />
          )}
          <div className="border-t border-border/70 px-5 py-2.5">
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
            >
              {open ? t("subagents_view.report_collapse") : t("subagents_view.report_expand")}
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 03 Timeline — a ledger of moments, not a chain of badges
// ---------------------------------------------------------------------------

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
  /** The sentence register 01 already quotes — not repeated down here. */
  quoteText: string | null;
  t: T;
}) {
  if (blocks.length === 0) {
    return <EmptyNote>{loading ? t("subagents_view.insight_loading") : t("subagents_view.story_empty")}</EmptyNote>;
  }
  return (
    <ol className="divide-y divide-border/70">
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

/** One ledger line: offset · KIND · content. */
function Line({
  offset,
  kind,
  tone,
  children,
}: {
  offset: string;
  kind: string;
  tone: StoryTone;
  children: React.ReactNode;
}) {
  return (
    <li className="grid grid-cols-[60px_76px_minmax(0,1fr)] items-baseline gap-x-4 px-5 py-3">
      <span className="font-mono text-[11px] tabular-nums text-muted-foreground/70">{offset}</span>
      <span className={cn("text-[10.5px] font-semibold uppercase tracking-[0.16em]", TONE_TEXT[tone])}>{kind}</span>
      <div className="min-w-0">{children}</div>
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
  const kind = t(`subagents_view.kind.${KIND_KEY[entry.kind]}`);
  const offset = formatOffset(entry.ts_ms - startedMs);
  const text = hideText ? "" : entry.text;

  if (entry.kind === "narration") {
    return (
      <Line offset={offset} kind={kind} tone="neutral">
        <div className="border-l-2 border-primary/50 pl-3">
          <span className="text-[11px] font-medium text-muted-foreground">{agentName}</span>
          <ClampedBlock text={text} t={t} lines={3} className="mt-0.5 max-w-[72ch] text-[14.5px] leading-relaxed text-foreground/90" />
        </div>
      </Line>
    );
  }
  if (entry.kind === "verdict" && entry.verdict) {
    return (
      <Line offset={offset} kind={kind} tone={entry.tone}>
        <VerdictBody verdict={entry.verdict} t={t} />
      </Line>
    );
  }
  const terminal = ["approved", "failed", "cancelled", "timed_out", "killed"].includes(entry.kind);
  return (
    <Line offset={offset} kind={kind} tone={entry.tone}>
      <div className={cn("text-[14px]", terminal ? "font-semibold text-foreground" : "font-medium text-foreground/90")}>
        {entryTitle(entry, agentName, t)}
      </div>
      {text && (
        <ClampedBlock
          text={text}
          t={t}
          lines={3}
          className={cn("mt-1 max-w-[72ch] text-sm leading-relaxed", entry.tone === "error" ? "text-destructive/90" : "text-muted-foreground")}
        />
      )}
    </Line>
  );
}

function VerdictBody({ verdict, t }: { verdict: CriticVerdictReady; t: T }) {
  const tone: StoryTone = verdict.verdict === "approve" ? "ok" : verdict.verdict === "revise" ? "warn" : "error";
  const axes = Object.entries(verdict.axes ?? {});
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2.5">
        <span className={cn("rounded border px-1.5 py-0.5 font-display text-[11px] font-bold uppercase tracking-[0.14em]", STAMP[tone])}>
          {t(`subagents_view.verdict.${verdict.verdict}`)}
        </span>
        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
          {fill(t("subagents_view.confidence"), { pct: Math.round(verdict.confidence * 100) })}
        </span>
      </div>
      <p className="mt-1.5 max-w-[72ch] text-[14px] leading-relaxed text-foreground/90">{verdict.summary}</p>
      {axes.length > 0 && (
        <ul className="mt-2.5 divide-y divide-border/50 rounded-lg border border-border/70">
          {axes.map(([name, axis]) => {
            const passed = axisPassed(axis);
            const label = AXIS_KEYS[name] ? t(AXIS_KEYS[name]) : name;
            const evidence = (axis.evidence ?? []).filter((e): e is string => typeof e === "string");
            return (
              <li key={name} className="grid grid-cols-[20px_140px_minmax(0,1fr)] items-start gap-x-3 px-3 py-2">
                <span className={cn("pt-0.5", passed === true ? TONE_TEXT.ok : passed === false ? TONE_TEXT.error : "text-muted-foreground")}>
                  {passed === true ? <CheckCircle2 className="h-3.5 w-3.5" /> : passed === false ? <XCircle className="h-3.5 w-3.5" /> : <CircleAlert className="h-3.5 w-3.5" />}
                </span>
                <span className="text-[13px] font-medium text-foreground">{label}</span>
                <span className="min-w-0 break-words font-mono text-[11.5px] leading-relaxed text-muted-foreground">
                  {evidence.length > 0 ? evidence.join("  ·  ") : axis.notes ? String(axis.notes) : "—"}
                </span>
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
    <Line offset={formatOffset(block.ts_ms - startedMs)} kind={t("subagents_view.kind.ran")} tone="neutral">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group/actions flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 text-left"
      >
        <span className="text-[14px] font-medium text-foreground/90">
          {fill(t(block.entries.length === 1 ? "subagents_view.actions_one" : "subagents_view.actions_many"), {
            n: block.entries.length,
            span: formatDuration(span),
          })}
        </span>
        <span className="flex flex-wrap gap-1.5">
          {block.counts.map((c) => (
            <span key={c.tool} className="inline-flex h-5 items-center gap-1 rounded border border-border/70 px-1.5 font-mono text-[11px] text-foreground/80">
              {c.tool}
              {c.n > 1 && <span className="text-muted-foreground">×{c.n}</span>}
            </span>
          ))}
        </span>
        <ChevronDown className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ol className="mt-2.5 divide-y divide-border/50 rounded-lg border border-border/70">
          {block.entries.map((e) => (
            <li key={e.id} className="grid grid-cols-[52px_72px_minmax(0,1fr)] gap-x-3 px-3 py-1.5 font-mono text-[11.5px]">
              <span className="tabular-nums text-muted-foreground/60">{formatOffset(e.ts_ms - startedMs)}</span>
              <span className="truncate text-muted-foreground">{e.tool}</span>
              <span className="min-w-0 break-all text-foreground/85">{e.text}</span>
            </li>
          ))}
        </ol>
      )}
    </Line>
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
      <div className="border-t border-border/70 px-5 py-2.5 text-xs text-muted-foreground">{t("subagents_view.transcript_no_dir")}</div>
    ) : null;
  }
  if (loading || steps.length === 0) return null;

  return (
    <div className="border-t border-border/70">
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
        <div className="border-t border-border/70">
          {(truncated || dropped > 0) && (
            <div className="px-5 py-2 text-xs text-amber-600 dark:text-amber-400">
              {dropped > 0 ? fill(t("subagents_view.transcript_dropped"), { n: dropped }) : t("subagents_view.transcript_truncated")}
            </div>
          )}
          <ol className="divide-y divide-border/50">
            {steps.map((step, idx) => {
              const kind = step.kind ?? "tool";
              const body = step.error ?? step.output ?? null;
              return (
                <li key={step.step_id} className="grid grid-cols-[60px_76px_minmax(0,1fr)_100px] items-baseline gap-x-4 px-5 py-2.5">
                  <span className="font-mono text-[11px] tabular-nums text-muted-foreground/60">{pad2(idx + 1)}</span>
                  <span className={cn("text-[10.5px] font-semibold uppercase tracking-[0.16em]", kind === "reasoning" ? "text-primary" : "text-muted-foreground")}>
                    {t(`subagents_view.kind.${kind === "reasoning" ? "thought" : kind === "spawn" ? "worker" : "ran"}`)}
                  </span>
                  <div className="min-w-0">
                    {kind === "reasoning" ? (
                      <ClampedBlock text={step.output ?? step.name} t={t} lines={3} className="max-w-[72ch] text-sm leading-relaxed text-foreground/85" />
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
// 04 Files — the diff ledger, every row a door into Artifacts
// ---------------------------------------------------------------------------

function FilesLedger({
  changed,
  artifacts,
  truncated,
  onOpen,
  t,
}: {
  changed: MissionChangedFile[];
  artifacts: MissionArtifact[];
  truncated: boolean;
  onOpen?: () => void;
  t: T;
}) {
  const sizes = useMemo(() => new Map(artifacts.map((a) => [a.deliverable_path, a.size])), [artifacts]);
  const rows: Array<{ key: string; glyph: string; tone: StoryTone; path: string; from: string | null; add: number | null; del: number | null; size: number | null; binary: boolean; title: string }> =
    changed.length > 0
      ? changed.map((f) => ({
          key: `${f.status}:${f.path}`,
          glyph: CHANGE_GLYPH[f.status].glyph,
          tone: CHANGE_GLYPH[f.status].tone,
          path: f.path,
          from: f.previous_path,
          add: f.additions,
          del: f.deletions,
          size: sizes.get(f.path) ?? null,
          binary: f.binary,
          title: t(`subagents_view.change.${f.status}`),
        }))
      : artifacts.map((a) => ({
          key: a.path,
          glyph: "·",
          tone: "neutral" as StoryTone,
          path: a.deliverable_path,
          from: null,
          add: null,
          del: null,
          size: a.size,
          binary: !a.is_text,
          title: t("subagents_view.change.delivered"),
        }));

  const Row = onOpen ? "button" : "div";
  return (
    <div>
      <ol className="divide-y divide-border/70">
        {rows.map((r) => (
          <li key={r.key}>
            <Row
              {...(onOpen ? { type: "button", onClick: onOpen } : {})}
              className={cn(
                "grid w-full grid-cols-[28px_minmax(0,1fr)_auto_auto_16px] items-center gap-x-3 px-5 py-2.5 text-left",
                onOpen && "transition-colors hover:bg-secondary/50 focus-visible:bg-secondary/50 focus-visible:outline-none",
              )}
            >
              <span
                title={r.title}
                className={cn("grid h-5 w-5 place-items-center rounded border font-mono text-[10.5px] font-bold", STAMP[r.tone])}
              >
                {r.glyph}
              </span>
              <span className="min-w-0">
                <span className="block truncate font-mono text-[12.5px] text-foreground" title={r.path}>
                  {r.path}
                </span>
                {r.from && (
                  <span className="block truncate font-mono text-[11px] text-muted-foreground">← {r.from}</span>
                )}
              </span>
              <span className="font-mono text-[11.5px] tabular-nums">
                {r.binary ? (
                  <span className="text-muted-foreground">{t("subagents_view.change.binary")}</span>
                ) : r.add != null && r.del != null ? (
                  <>
                    <span className={TONE_TEXT.ok}>+{r.add}</span>
                    <span className="mx-1 text-muted-foreground/50">/</span>
                    <span className={TONE_TEXT.error}>−{r.del}</span>
                  </>
                ) : null}
              </span>
              <span className="w-14 text-right font-mono text-[11.5px] tabular-nums text-muted-foreground">
                {r.size != null ? formatBytes(r.size) : ""}
              </span>
              {onOpen ? <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground/50" /> : <span />}
            </Row>
          </li>
        ))}
      </ol>
      {truncated && (
        <div className="border-t border-border/70 px-5 py-2 text-xs text-muted-foreground">{t("subagents_view.deliverables_truncated")}</div>
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
    <div className="grid grid-cols-[minmax(0,1fr)_64px_96px] items-center gap-3 px-5 py-2.5 text-sm">
      <div className="min-w-0">
        <div className="truncate font-mono text-[12.5px] text-foreground">{call.tool_name || t("subagents_view.tool_unnamed")}</div>
        <div className="truncate text-xs text-muted-foreground" title={call.args_preview}>
          {call.error || call.args_preview || call.output_preview || "—"}
        </div>
      </div>
      <div className="text-right font-mono text-[11px] tabular-nums text-muted-foreground">
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
};

/** Text that clamps to `lines` and expands on a click — the page's own "more". */
function ClampedBlock({ text, t, lines, className }: { text: string; t: T; lines: 2 | 3; className?: string }) {
  const [open, setOpen] = useState(false);
  // Judged by length and line breaks rather than layout, so the list never
  // re-flows on mount.
  const long = text.length > lines * 100 || text.split("\n").length > lines;
  return (
    <div className={cn("min-w-0", className)}>
      <div className={cn("break-words", !open && long && CLAMP_CLASS[lines])}>{text}</div>
      {long && (
        <button type="button" onClick={() => setOpen((v) => !v)} className="mt-0.5 text-xs font-medium text-primary hover:underline">
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
