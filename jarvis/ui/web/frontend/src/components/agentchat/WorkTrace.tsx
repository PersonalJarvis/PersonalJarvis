import { memo, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Brain, Check, ChevronRight, CircleAlert, CircleDashed, FilePenLine, FileText, FolderSearch, ShieldQuestion, Terminal } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import type { ReasoningBlock, ToolBlock, TurnBlock, TurnItem, TurnStatus } from "./reduce";
import { ChatMarkdown } from "./ChatMarkdown";
import { toolDiff } from "./toolDiff";
import { formatTokens, outputTokens } from "./toolView";
import { activityParts, traceToolIdentity } from "./traceActivity";
import { ToolChoiceIcon } from "./ToolChoiceChips";
import { toolIdentityStyle } from "./toolIdentity";

export type Decide = (id: string, decision: ApprovalDecision) => void | Promise<void>;
type Group = { id: string; blocks: TurnBlock[]; family: string | null };

export function traceDuration(ms: number): string {
  // Keep short, measured calls visible instead of rounding 49 ms to "0.0s".
  if (ms > 0 && ms < 100) return `${Math.ceil(ms)}ms`;
  const seconds = Math.max(0, ms) / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
}

function useClock(start: number, live: boolean) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [start, live]);
  return Math.max(0, now - start);
}

function operation(name: string): string | null {
  const key = name.toLowerCase().replace(/[-_]/g, "");
  if (/^(read|readfile|viewfile|cat|openfile|readmediafile)$/.test(key)) return "read";
  if (/^(ls|listdir|listdirectory|listfiles|glob)$/.test(key)) return "list";
  if (/^(grep|rg|search|searchfiles|grepsearch|codesearch|findbyname)$/.test(key)) return "search";
  return null;
}

function attention(block: ToolBlock) {
  return block.isError || Boolean(block.approval);
}

/** Only adjacent, successful, read-only operations may lose individual rows. */
export function groupTrace(blocks: TurnBlock[]): Group[] {
  const groups: Group[] = [];
  for (const block of blocks) {
    const family = block.kind === "tool" && !attention(block) && block.output !== null ? operation(block.name) : null;
    const previous = groups[groups.length - 1];
    if (family && previous?.family === family) previous.blocks.push(block);
    else groups.push({ id: block.kind === "tool" ? block.callId : block.id, blocks: [block], family });
  }
  return groups;
}

/** Fold adjacent work between replies, keeping failures and decisions in view. */
export function groupConversationTrace(blocks: TurnBlock[]): Group[] {
  const groups: Group[] = [];
  for (const block of blocks) {
    const family = block.kind === "reasoning" || (block.kind === "tool" && !attention(block) && block.output !== null) ? "activity" : null;
    const previous = groups.at(-1);
    if (family && previous?.family === family) previous.blocks.push(block);
    else groups.push({ id: block.kind === "tool" ? block.callId : block.id, blocks: [block], family });
  }
  return groups;
}

/** Summarize completed adjacent work; replies and attention are hard boundaries. */
export function groupActivityTrace(blocks: TurnBlock[]): Group[] {
  const groups: Group[] = [];
  let pending: ToolBlock[] = [];
  const flush = () => {
    if (!pending.length) return;
    const reads = groupTrace(pending);
    groups.push(...(reads.length === 1 || pending.length === 1 ? reads
      : [{ id: pending[0].callId, blocks: pending, family: "activity" }]));
    pending = [];
  };
  for (const block of blocks) {
    if (block.kind === "tool" && !attention(block) && block.output !== null) pending.push(block);
    else {
      flush();
      groups.push({ id: block.kind === "tool" ? block.callId : block.id, blocks: [block], family: null });
    }
  }
  flush();
  return groups;
}

function pretty(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2) ?? "";
}

const rowButton = "group/trace flex w-full min-w-0 items-start gap-2.5 rounded-md py-2 text-left text-[13px] leading-6 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:text-foreground";
const iconClass = "mt-0.5 h-4 w-4 shrink-0";

function Disclosure({ label, children, forced = false, initiallyOpen = false, icon, trailing, tone, summary, resetKey = "" }: {
  label: ReactNode; children?: ReactNode; forced?: boolean; initiallyOpen?: boolean;
  icon: ReactNode; trailing?: ReactNode; tone?: string; summary?: ReactNode; resetKey?: string;
}) {
  const id = useId();
  // A manual choice during a live turn must not prevent completion folding.
  const phase = `${initiallyOpen}:${resetKey}`;
  const [choice, setChoice] = useState<{ phase: string; open: boolean } | null>(null);
  const open = forced || (choice?.phase === phase ? choice.open : initiallyOpen);
  return (
    <div className={cn("min-w-0 text-muted-foreground", tone)}>
      <button type="button" className={rowButton} aria-expanded={children ? open : undefined}
        aria-controls={children ? id : undefined} disabled={!children || forced}
        onClick={() => setChoice({ phase, open: !open })}>
        {icon}
        <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{label}</span>
        {trailing ? <span className="shrink-0 text-xs tabular-nums">{trailing}</span> : null}
        {children ? <ChevronRight aria-hidden className={cn(iconClass, "mt-1 h-3.5 w-3.5 opacity-50 transition-transform group-hover/trace:opacity-100", open && "rotate-90")} /> : null}
      </button>
      {summary}
      {children && open ? <div id={id} className="mb-2 ml-[7px] min-w-0 border-l border-border/70 pb-1 pl-5">{children}</div> : null}
    </div>
  );
}

export function ReasoningTrace({ block, turnLive, compact = false }: { block: ReasoningBlock; turnLive: boolean; compact?: boolean }) {
  const t = useT();
  const live = turnLive && block.live;
  const elapsed = useClock(block.startedMs, live);
  const text = block.text.trim();
  const duration = live ? elapsed : block.durationMs;
  const label = duration === null ? t("work_trace.thought")
    : t(live ? "work_trace.thinking_for" : "work_trace.thought_for").replace("{duration}", traceDuration(duration));
  const gist = text.replace(/```[\s\S]*?```/g, " ").replace(/[`*_#>~]/g, "").replace(/\s+/g, " ").trim();
  return <Disclosure label={label} icon={<Brain aria-hidden className={cn(iconClass, live && "motion-safe:animate-pulse")} />}
    forced={live} initiallyOpen={compact ? live : turnLive}
    resetKey={compact ? String(turnLive) : ""}
    summary={!compact && !turnLive && gist ? <p className="mb-2 ml-6 line-clamp-2 text-xs leading-5">{gist.slice(0, 240)}</p> : undefined}>
    {text ? <div className="prose prose-sm max-h-64 max-w-none overflow-auto text-xs leading-6 text-muted-foreground dark:prose-invert [overflow-wrap:anywhere] prose-p:my-1 prose-pre:overflow-auto">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div> : undefined}
  </Disclosure>;
}

function ToolDetails({ block }: { block: ToolBlock }) {
  const t = useT();
  const diff = useMemo(() => toolDiff(block.name, block.input), [block.name, block.input]);
  return <div className="space-y-3 py-1 text-xs">
    <Detail label={t("work_trace.tool")} text={block.name} />
    {block.input !== undefined && block.input !== null ? <Detail label={t("work_trace.input")} text={pretty(block.input)} /> : null}
    {diff ? <div aria-label={t("work_trace.diff")} className="max-h-72 overflow-auto font-mono text-xs">
      {diff.map((file, i) => <div key={i} className="mb-2">
        <p className="mb-1 [overflow-wrap:anywhere]">{file.path}</p>
        {file.lines.map((line, n) => <div key={n} className={cn("whitespace-pre-wrap [overflow-wrap:anywhere]", line.kind === "add" && "diff-line-add", line.kind === "del" && "diff-line-del")}>
          {line.kind === "add" ? "+ " : line.kind === "del" ? "− " : "  "}{line.text}
        </div>)}
        {file.truncated > 0 ? <p>{t("work_trace.truncated").replace("{count}", String(file.truncated))}</p> : null}
      </div>)}
    </div> : null}
    {block.output !== null ? <Detail label={t(block.isError ? "work_trace.error" : "work_trace.output")} text={block.output || t("work_trace.empty_output")} /> : null}
  </div>;
}

function Detail({ label, text }: { label: string; text: string }) {
  return <div><p className="mb-1 font-medium text-foreground">{label}</p>
    <pre tabIndex={0} className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs leading-5 [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">{text}</pre>
  </div>;
}

export const TraceTool = memo(function TraceTool({ block, status, onDecide }: { block: ToolBlock; status: TurnStatus; onDecide?: Decide }) {
  const t = useT();
  const pending = Boolean(block.approval && block.approval.decision === null);
  const denied = block.approval?.decision === "deny";
  const running = status === "running" && block.output === null && !pending && !denied;
  const elapsed = useClock(block.startedMs, running);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const view = traceToolIdentity(block);
  const { description, action } = view;
  const readable = description.label.charAt(0).toUpperCase() + description.label.slice(1);
  const label = action ? t(`work_trace.${action}`) : description.labelKey ? t(description.labelKey) : readable;
  const detail = description.detail || (typeof block.input === "object" && block.input !== null
    ? String((block.input as Record<string, unknown>).file_path ?? (block.input as Record<string, unknown>).path ?? "") : "");
  const state = pending ? "approval" : denied ? "denied" : block.isError ? "failed" : running ? "running" : block.output === null ? "interrupted" : "completed";
  const ActionIcon = action === "command" ? Terminal : action === "edit" || action === "write" ? FilePenLine
    : action === "read" ? FileText : action === "search" || action === "list" ? FolderSearch : view.identity.Glyph;
  const Icon = pending ? ShieldQuestion : block.isError ? CircleAlert : ActionIcon;
  const decide = async (decision: ApprovalDecision) => {
    if (!onDecide || !block.approval || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try { await onDecide(block.approval.approvalId, decision); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { submitting.current = false; setBusy(false); }
  };
  return <div data-trace-tool={block.callId} data-state={state}>
    <Disclosure label={<><span className={view.integration ? "font-medium" : undefined}>{label}</span>{detail ? <span className="ml-2 text-xs text-muted-foreground">{" "}{detail}</span> : null}</>}
      icon={view.identity.logo && !pending && !block.isError ? <span className={cn("tool-identity mt-1 shrink-0", running && "motion-safe:animate-pulse")} style={toolIdentityStyle(view.row)} data-trace-brand={view.identity.key}><ToolChoiceIcon row={view.row} size={16} /></span> : <Icon aria-hidden className={cn(iconClass, "mt-1", running && "motion-safe:animate-pulse")} />}
      trailing={<span className="inline-flex items-center gap-1.5">{running ? <CircleDashed aria-hidden className="h-3 w-3 motion-safe:animate-spin" /> : null}{running ? traceDuration(elapsed) : block.durationMs !== null ? traceDuration(block.durationMs) : null}</span>}
      tone={block.isError ? "text-destructive" : pending ? "text-foreground" : undefined}
      summary={<>
        {state !== "completed" ? <p className={cn("mb-1 ml-6 text-xs", block.isError && "text-destructive")}>{t(`work_trace.${state}`)}</p> : null}
        {block.isError && block.output ? <p className="mb-2 ml-6 whitespace-pre-wrap text-xs text-destructive [overflow-wrap:anywhere]">{block.output.slice(0, 500)}</p> : null}
      </>}>
      <ToolDetails block={block} />
    </Disclosure>
    {pending ? <div className="mb-3 ml-6 space-y-2 text-sm" role="group" aria-label={t("work_trace.approval")}>
      <p className="[overflow-wrap:anywhere]">{block.approval?.summary}</p>
      {onDecide ? <div className="flex flex-wrap gap-2">
        {(["allow", "allow_always", "deny"] as const).map(decision => <button key={decision} type="button" disabled={busy}
          onClick={() => void decide(decision)} className="rounded-md border border-border px-3 py-1.5 text-xs text-foreground hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
          {t(`work_trace.${decision}`)}
        </button>)}
      </div> : <p className="text-xs text-muted-foreground">{t("work_trace.approval_elsewhere")}</p>}
      {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
    </div> : null}
  </div>;
});

function ActivitySummary({ blocks, live }: { blocks: TurnBlock[]; live: boolean }) {
  const t = useT();
  const parts = activityParts(blocks.filter((block): block is ToolBlock => block.kind === "tool"));
  if (!parts.length) return <>{t("work_trace.thought")}</>;
  return <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1">
    {parts.map((part, index) => <span key={`${part.key}:${part.service ?? ""}`} className="inline-flex items-center gap-1.5">
      {index > 0 ? <span aria-hidden className="text-muted-foreground/50">·</span> : null}
      {part.row && index > 0 ? <span className="tool-identity inline-flex" style={toolIdentityStyle(part.row)}><ToolChoiceIcon row={part.row} size={14} /></span> : null}
      <span>{t(`work_trace.${live ? "live_" : ""}${part.key}`).replace("{service}", part.service ?? "")}</span>
    </span>)}
  </span>;
}

function ActivityIcon({ blocks, live }: { blocks: TurnBlock[]; live: boolean }) {
  const first = activityParts(blocks.filter((block): block is ToolBlock => block.kind === "tool"))[0];
  if (first?.row) return <span className={cn("tool-identity mt-1 shrink-0", live && "motion-safe:animate-pulse")} style={toolIdentityStyle(first.row)}><ToolChoiceIcon row={first.row} size={16} /></span>;
  const Icon = live ? CircleDashed : first?.key === "activity_command" ? Terminal
    : first?.key === "activity_edit" || first?.key === "activity_write" ? FilePenLine : first ? FileText : Brain;
  return <Icon aria-hidden className={cn(iconClass, "mt-1", live && "motion-safe:animate-spin")} />;
}

export function WorkTrace({ blocks, status, startedMs, durationMs, error, onDecide, renderText, className, receipt, completionLabel, conversation = false }: {
  blocks: TurnBlock[]; status: TurnStatus; startedMs: number; durationMs: number | null; error?: string | null;
  onDecide?: Decide; renderText?: (text: string, id: string) => ReactNode; className?: string;
  receipt?: ReactNode; completionLabel?: string; conversation?: boolean;
}) {
  const t = useT();
  const live = status === "running";
  const elapsed = useClock(startedMs, live);
  const groups = useMemo(() => conversation ? groupConversationTrace(blocks) : groupActivityTrace(blocks), [blocks, conversation]);
  const pending = blocks.some(block => block.kind === "tool" && block.approval?.decision === null);
  const outcome = pending ? "approval" : live ? "working" : status === "error" ? "failed" : status === "cancelled" ? "stopped" : "done";
  const Icon = pending ? ShieldQuestion : live ? CircleDashed : status === "error" ? CircleAlert : Check;
  return <div className={cn("min-w-0 space-y-0.5", className)} data-testid="work-trace" data-state={status}>
    {groups.map(group => {
      const first = group.blocks[0];
      if (group.family === "activity" && group.blocks.length > 1) return <div key={group.id} className={cn("py-1", conversation && "w-full max-w-xl")} data-trace-summary>
        <Disclosure label={<ActivitySummary blocks={group.blocks} live={live} />}
          icon={<ActivityIcon blocks={group.blocks} live={live} />} initiallyOpen={live}
          forced={live}>
          {group.blocks.map(block => block.kind === "tool"
            ? <TraceTool key={block.callId} block={block} status={status} onDecide={onDecide} />
            : block.kind === "reasoning" ? <ReasoningTrace key={block.id} block={block} turnLive={live} compact /> : null)}
        </Disclosure>
      </div>;
      if (group.blocks.length > 1) return <Disclosure key={group.id}
        label={t(`work_trace.group_${group.family}`).replace("{count}", String(group.blocks.length))}
        icon={<Check aria-hidden className={iconClass} />} initiallyOpen={live}>
        {group.blocks.map(block => <TraceTool key={(block as ToolBlock).callId} block={block as ToolBlock} status={status} onDecide={onDecide} />)}
      </Disclosure>;
      if (first.kind === "tool") return <div key={group.id} className={conversation ? "mx-auto w-full max-w-xl py-1 text-xs [&_button]:text-xs" : undefined}><TraceTool block={first} status={status} onDecide={onDecide} /></div>;
      if (first.kind === "reasoning") return <div key={group.id} className={conversation ? "max-w-xl text-xs [&_button]:text-xs" : undefined}><ReasoningTrace block={first} turnLive={live} compact={conversation} /></div>;
      return first.text.trim() ? <div key={group.id} className={cn("min-w-0 py-2", conversation && "w-fit max-w-[min(85%,42rem)] rounded-2xl rounded-bl-md bg-secondary px-4 py-2.5")}>{renderText ? renderText(first.text, first.id) : <div className="prose prose-sm max-w-none text-foreground dark:prose-invert [overflow-wrap:anywhere]"><ChatMarkdown text={first.text} /></div>}</div> : null;
    })}
    {error ? <p role="alert" className="py-2 text-sm text-destructive [overflow-wrap:anywhere]">{error}</p> : null}
    <div role="status" aria-live="polite" className={cn("flex flex-wrap items-center gap-2 text-xs text-muted-foreground", conversation ? "px-1 pb-2 pt-1" : "border-t border-border pt-3", status === "error" && "text-destructive")}>
      <Icon aria-hidden className={cn("h-3.5 w-3.5", live && !pending && "motion-safe:animate-spin")} />
      <span>{outcome === "done" && completionLabel ? completionLabel : t(`work_trace.${outcome}`)}</span>
      {(live || durationMs !== null) ? <span aria-live="off" className="tabular-nums">{traceDuration(live ? elapsed : durationMs ?? 0)}</span> : null}
      {receipt ? conversation ? <details className="ml-1"><summary className="cursor-pointer rounded-sm focus-visible:ring-2 focus-visible:ring-ring">{t("society.chat.activity_details")}</summary><div className="flex flex-wrap gap-2 py-1">{receipt}</div></details> : receipt : null}
    </div>
  </div>;
}

export function TurnTrace({ turn, ...props }: { turn: TurnItem; onDecide?: Decide; renderText?: (text: string, id: string) => ReactNode; conversation?: boolean }) {
  const t = useT();
  const tokens = outputTokens(turn.usage ?? turn.liveUsage);
  const answered = turn.blocks.some(block => block.kind === "text" && block.text.trim());
  return <WorkTrace {...props} blocks={turn.blocks} status={turn.status} startedMs={turn.startedMs} durationMs={turn.durationMs} error={turn.error}
    completionLabel={!answered ? t("agent_chat.turn_no_answer") : undefined}
    receipt={tokens !== null && tokens > 0 || turn.costUsd !== null && turn.costUsd > 0 ? <>
      {tokens !== null && tokens > 0 ? <span aria-live="off" className="tabular-nums">{formatTokens(tokens)} {t("agent_chat.tokens")}</span> : null}
      {turn.costUsd !== null && turn.costUsd > 0 ? <span className="tabular-nums">${turn.costUsd.toFixed(4)}</span> : null}
    </> : undefined} />;
}
