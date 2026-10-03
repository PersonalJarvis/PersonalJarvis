import { createContext, Fragment, memo, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Brain, Check, ChevronRight, CircleAlert, CircleDashed, FilePenLine, FileText, FolderSearch, MessageCircleQuestion, ShieldQuestion, Terminal } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useT } from "@/i18n";
import { PetMark } from "@/components/pets/PetMark";
import type { PetState } from "@/lib/petStates";
import { cn } from "@/lib/utils";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import { isQuestionTool, type ReasoningBlock, type TextBlock, type ToolBlock, type TurnBlock, type TurnItem, type TurnStatus } from "./reduce";
import { ChatMarkdown } from "./ChatMarkdown";
import { QuestionCard } from "./QuestionCard";
import { toolDiff } from "./toolDiff";
import { formatTokens, outputTokens } from "./toolView";
import { activityParts, traceToolIdentity, traceToolName } from "./traceActivity";
import { ToolChoiceIcon } from "./ToolChoiceChips";
import type { ToolChoice } from "./toolChoices";
import { toolIdentityStyle } from "./toolIdentity";
import { CopyTimeline, timelineItems, useTimeline, type TimelineRenderers } from "./TraceTimeline";
import { headerLabel, timelineMarkdown, traceDuration, type Timeline } from "./traceEntries";
import "./WorkTrace.css";

export type Decide = (id: string, decision: ApprovalDecision) => void | Promise<void>;
type Group = { id: string; blocks: TurnBlock[]; family: string | null };

/**
 * How a trace is drawn.
 *
 * "rail" (the default since 2026-10-01) threads every step of a turn on one
 * quiet vertical line: each thought or tool call is a node on it, the live
 * step shimmers, and the turn's state line closes the thread. "classic" is
 * the earlier row list, kept only for the Agentic IDE, whose coding panes
 * mirror Claude Code and Codex and were left as they are on purpose.
 */
export type TraceLook = "rail" | "classic";
const TraceLookContext = createContext<TraceLook>("rail");
const useRail = () => useContext(TraceLookContext) === "rail";

export { traceDuration };

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
  return block.isError || Boolean(block.approval) || Boolean(block.question);
}

/** An agent's question still waiting for the person — it never folds away. */
function isOpenQuestion(block: TurnBlock): block is ToolBlock {
  return block.kind === "tool" && Boolean(block.question && !block.question.closed);
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

/**
 * The last assistant reply stays in the conversation. Narration between
 * tools, every tool or thought before that reply, and every tool or thought
 * after it is foldable work — including failures and interruptions. Only
 * pending approvals stay out: they ask the person to act.
 */
export function splitConversationTurn(blocks: TurnBlock[]): { work: TurnBlock[]; answer: TextBlock[]; after: TurnBlock[] } {
  let lastText = -1;
  for (let i = blocks.length - 1; i >= 0; i--) {
    const block = blocks[i];
    if (block.kind === "text" && block.text.trim()) {
      lastText = i;
      break;
    }
  }
  if (lastText < 0) return { work: blocks, answer: [], after: [] };
  let firstText = lastText;
  while (firstText > 0 && blocks[firstText - 1].kind === "text") firstText--;
  return {
    work: blocks.slice(0, firstText),
    answer: blocks.slice(firstText, lastText + 1) as TextBlock[],
    after: blocks.slice(lastText + 1),
  };
}

function isPendingApproval(block: TurnBlock): block is ToolBlock {
  return (block.kind === "tool" && Boolean(block.approval && block.approval.decision === null)) || isOpenQuestion(block);
}

function needsAttention(block: TurnBlock): block is ToolBlock {
  return block.kind === "tool" && (block.isError || isPendingApproval(block));
}

function hasFoldableWork(blocks: TurnBlock[]): boolean {
  return blocks.some((block) => {
    // Decisions and failed tools remain visible after a turn completes.
    if (needsAttention(block)) return false;
    return block.kind !== "text" || Boolean(block.text.trim());
  });
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
const railRowButton = "group/trace flex w-full min-w-0 items-start gap-3 rounded-md py-1 text-left text-sm leading-6 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:text-foreground disabled:cursor-default";
const iconClass = "mt-0.5 h-4 w-4 shrink-0";
/** A glyph sitting in a rail node: the node box centres it on the first line. */
const nodeIcon = "h-3.5 w-3.5 shrink-0";

/**
 * One item of a rail. Steps on the thread (thoughts, tool calls, the live
 * state line) set `rail`; replies and question cards break the thread, so
 * the work around them reads as separate stretches.
 */
type RailItem = { key: string; node: ReactNode; rail: boolean };

/** Draw consecutive rail items as one thread; everything else stands alone. */
function Rail({ items }: { items: RailItem[] }) {
  const out: ReactNode[] = [];
  let run: RailItem[] = [];
  const flush = () => {
    if (!run.length) return;
    out.push(<div key={`rail:${run[0].key}`} className="trace-rail">
      {run.map((item) => <div key={item.key} className="trace-step">{item.node}</div>)}
    </div>);
    run = [];
  };
  for (const item of items) {
    if (item.rail) run.push(item);
    else {
      flush();
      out.push(<Fragment key={item.key}>{item.node}</Fragment>);
    }
  }
  flush();
  return <>{out}</>;
}

/** The node a step hangs on: a 16 px column centred on the row's first line. */
function Node({ children, live = false }: { children: ReactNode; live?: boolean }) {
  return <span aria-hidden className={cn("trace-node", live && "trace-node-live")}>{children}</span>;
}

/**
 * What the user's pet does on a live Jarvis trace: it plays its working row
 * whenever Jarvis thinks or works — reasoning, a tool call, the wait before
 * anything has arrived (the maintainer's choice, 2026-10-02) — and talks
 * while the reply streams in.
 */
export function livePetState(blocks: TurnBlock[]): PetState {
  const last = blocks[blocks.length - 1];
  return last?.kind === "text" && last.text.trim() ? "talking" : "working";
}

/** The words of whatever is happening right now, with a slow light sweep. */
function Live({ on, children }: { on: boolean; children: ReactNode }) {
  return on ? <span className="trace-shimmer">{children}</span> : <>{children}</>;
}

/**
 * While a conversation work fold is open, every disclosure inside it starts
 * open — one tap on "Thought for …" reveals the whole chain, not one more
 * level of chevrons. The sequence counts fold openings (0 = no open fold
 * above): reopening the fold resets inner rows to open, while a row the
 * person toggled by hand keeps its choice until then.
 */
const FoldExpandContext = createContext(0);

function Disclosure({ label, children, forced = false, initiallyOpen = false, icon, trailing, tone, summary, resetKey = "", live = false }: {
  label: ReactNode; children?: ReactNode; forced?: boolean; initiallyOpen?: boolean;
  icon?: ReactNode; trailing?: ReactNode; tone?: string; summary?: ReactNode; resetKey?: string;
  /** Rail look only: the node glows while this step is the one working. */
  live?: boolean;
}) {
  const id = useId();
  const rail = useRail();
  const foldSeq = useContext(FoldExpandContext);
  // A manual choice during a live turn must not prevent completion folding.
  const phase = `${initiallyOpen}:${resetKey}:${foldSeq}`;
  const [choice, setChoice] = useState<{ phase: string; open: boolean } | null>(null);
  const open = forced || (choice?.phase === phase ? choice.open : foldSeq > 0 || initiallyOpen);
  if (rail) return (
    <div className={cn("min-w-0 text-muted-foreground", tone)}>
      <button type="button" className={railRowButton} aria-expanded={children ? open : undefined}
        aria-controls={children ? id : undefined} disabled={!children || forced}
        onClick={() => setChoice({ phase, open: !open })}>
        {icon ? <Node live={live}>{icon}</Node> : null}
        <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{label}</span>
        {trailing ? <span className="shrink-0 text-xs leading-6 tabular-nums text-muted-foreground">{trailing}</span> : null}
        {children && !forced ? <ChevronRight aria-hidden className={cn("mt-[5px] h-3.5 w-3.5 shrink-0 opacity-40 transition group-hover/trace:opacity-90", open && "rotate-90")} /> : null}
      </button>
      {summary}
      {children && open ? <div id={id} className={cn("min-w-0 pb-1.5", icon ? "pl-7" : "pl-0")}>{children}</div> : null}
    </div>
  );
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

function ReasoningBody({ text, live }: { text: string; live: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const rail = useRail();
  // The fade at the top only means "there is more above" — a thought that
  // still fits its box keeps its first line crisp.
  const [clipped, setClipped] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !live) return;
    el.scrollTop = el.scrollHeight;
    if (rail) setClipped(el.scrollHeight > el.clientHeight + 1);
  }, [text, live, rail]);
  return (
    <div
      ref={ref}
      data-testid="reasoning-body"
      className={rail
        ? cn("prose prose-sm max-h-60 max-w-none overflow-auto pb-1 text-sm leading-6 text-foreground-secondary dark:prose-invert [overflow-wrap:anywhere] prose-p:my-1 prose-p:text-foreground-secondary prose-li:text-foreground-secondary prose-strong:text-foreground prose-pre:overflow-auto", live && clipped && "trace-scratch-live")
        : "prose prose-sm max-h-64 max-w-none overflow-auto text-xs leading-6 text-muted-foreground dark:prose-invert [overflow-wrap:anywhere] prose-p:my-1 prose-pre:overflow-auto"}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

export function ReasoningTrace({ block, turnLive, compact = false }: { block: ReasoningBlock; turnLive: boolean; compact?: boolean }) {
  const t = useT();
  const rail = useRail();
  const live = turnLive && block.live;
  const elapsed = useClock(block.startedMs, live);
  const text = block.text.trim();
  const duration = live ? elapsed : block.durationMs;
  const label = duration === null ? t("work_trace.thought")
    : t(live ? "work_trace.thinking_for" : "work_trace.thought_for").replace("{duration}", traceDuration(duration));
  const gist = text.replace(/```[\s\S]*?```/g, " ").replace(/[`*_#>~]/g, "").replace(/\s+/g, " ").trim();
  const showGist = !compact && !turnLive && gist;
  if (rail) return <Disclosure label={<Live on={live}>{label}</Live>}
    live={live} forced={live} initiallyOpen={compact ? live : turnLive}
    resetKey={compact ? String(turnLive) : ""}
    summary={showGist ? <p className="mb-1.5 line-clamp-2 text-sm leading-6 text-muted-foreground">{gist.slice(0, 280)}</p> : undefined}>
    {text ? <ReasoningBody text={text} live={live} /> : undefined}
  </Disclosure>;
  return <Disclosure label={label} icon={<Brain aria-hidden className={cn(iconClass, live && "motion-safe:animate-pulse")} />}
    forced={live} initiallyOpen={compact ? live : turnLive}
    resetKey={compact ? String(turnLive) : ""}
    summary={showGist ? <p className="mb-2 ml-6 line-clamp-2 text-xs leading-5">{gist.slice(0, 240)}</p> : undefined}>
    {text ? <ReasoningBody text={text} live={live} /> : undefined}
  </Disclosure>;
}

function ToolDetails({ block }: { block: ToolBlock }) {
  const t = useT();
  const rail = useRail();
  const diff = useMemo(() => toolDiff(block.name, block.input, block.output), [block.name, block.input, block.output]);
  const memoryFile = useMemo(() => memoryFileFromBlock(block), [block]);
  const [memoryOpen, setMemoryOpen] = useState(false);
  return <div className={cn("text-xs", rail ? "space-y-2.5 pb-1 pt-0.5" : "space-y-3 py-1")}>
    <Detail label={t("work_trace.tool")} text={block.name} />
    {block.input !== undefined && block.input !== null ? <Detail label={t("work_trace.input")} text={pretty(block.input)} /> : null}
    {diff ? <div aria-label={t("work_trace.diff")} className={cn("max-h-72 overflow-auto font-mono text-xs", rail && "rounded-md bg-muted/60 px-3 py-2")}>
      {diff.map((file, i) => <div key={i} className="mb-2 last:mb-0">
        <div className="mb-1 flex items-center gap-2 [overflow-wrap:anywhere]">
          <p className="min-w-0 flex-1">{file.path}</p>
          {memoryFile && file.path === memoryFile.path ? <button
            type="button"
            onClick={() => setMemoryOpen(true)}
            className="shrink-0 rounded-md border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >{t("society.chat.memory_open_file")}</button> : null}
        </div>
        {file.lines.map((line, n) => <div key={n} className={cn("whitespace-pre-wrap [overflow-wrap:anywhere]", line.kind === "add" && "diff-line-add", line.kind === "del" && "diff-line-del")}>
          {line.kind === "add" ? "+ " : line.kind === "del" ? "− " : "  "}{line.text}
        </div>)}
        {file.truncated > 0 ? <p>{t("work_trace.truncated").replace("{count}", String(file.truncated))}</p> : null}
      </div>)}
    </div> : null}
    {memoryFile && !diff ? <button
      type="button"
      onClick={() => setMemoryOpen(true)}
      className="rounded-md border border-border px-2 py-1 text-[11px] text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >{t("society.chat.memory_open_file")}: {memoryFile.path}</button> : null}
    {block.output !== null ? <Detail label={t(block.isError ? "work_trace.error" : "work_trace.output")} text={block.output || t("work_trace.empty_output")} /> : null}
    {memoryOpen && memoryFile ? <MemoryFileDialog path={memoryFile.path} before={memoryFile.before} after={memoryFile.after} onClose={() => setMemoryOpen(false)} /> : null}
  </div>;
}

function memoryFileFromBlock(block: ToolBlock): { path: string; before?: string; after?: string } | null {
  const key = traceToolName(block.name).toLowerCase().replace(/[-_]/g, "");
  if (!/^(societywikinote|societymemoryrecall|remember|societymemory)$/.test(key)) return null;
  const out = parseToolOutput(block.output);
  const inputPath = typeof block.input === "object" && block.input !== null
    ? String((block.input as Record<string, unknown>).path ?? "") : "";
  const path = out?.path || inputPath;
  if (!path || !path.startsWith("society/")) return null;
  return { path, before: out?.before, after: out?.after };
}

function parseToolOutput(output: string | null): { path?: string; before?: string; after?: string } | null {
  if (!output || !output.trim().startsWith("{")) return null;
  try {
    const parsed = JSON.parse(output) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return null;
    const path = typeof parsed.path === "string" ? parsed.path : undefined;
    const before = typeof parsed.before === "string" ? parsed.before : undefined;
    const after = typeof parsed.after === "string" ? parsed.after : undefined;
    if (!path && before === undefined && after === undefined) return null;
    return { path, before, after };
  } catch {
    return null;
  }
}

function MemoryFileDialog({ path, before, after, onClose }: { path: string; before?: string; after?: string; onClose: () => void }) {
  const [Viewer, setViewer] = useState<React.ComponentType<{ path: string; before?: string; after?: string; onClose: () => void }> | null>(null);
  useEffect(() => {
    let alive = true;
    void import("@/components/society/chat/MemoryFileViewer").then((mod) => {
      if (alive) setViewer(() => mod.MemoryFileViewer);
    });
    return () => { alive = false; };
  }, []);
  if (!Viewer) return null;
  return <Viewer path={path} before={before} after={after} onClose={onClose} />;
}

function Detail({ label, text }: { label: string; text: string }) {
  const rail = useRail();
  if (rail) return <div><p className="mb-1 text-xs font-medium text-muted-foreground">{label}</p>
    <pre tabIndex={0} className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/60 px-3 py-2 font-mono text-xs leading-5 text-foreground-secondary [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">{text}</pre>
  </div>;
  return <div><p className="mb-1 font-medium text-foreground">{label}</p>
    <pre tabIndex={0} className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs leading-5 [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">{text}</pre>
  </div>;
}

export const TraceTool = memo(function TraceTool({ block, status, onDecide }: { block: ToolBlock; status: TurnStatus; onDecide?: Decide }) {
  if (block.question) return <QuestionCard question={block.question} />;
  return <TraceToolRow block={block} status={status} onDecide={onDecide} />;
});

function TraceToolRow({ block, status, onDecide }: { block: ToolBlock; status: TurnStatus; onDecide?: Decide }) {
  const t = useT();
  const rail = useRail();
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
  const ActionIcon = action === "command" ? Terminal : action === "edit" || action === "write" || action === "memory" ? FilePenLine
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
  const branded = view.identity.logo && !pending && !block.isError;
  const approvalUi = pending ? <div className={cn("space-y-2 text-sm", rail ? "mb-2 pl-7" : "mb-3 ml-6")} role="group" aria-label={t("work_trace.approval")}>
    <p className={cn("[overflow-wrap:anywhere]", rail && "text-foreground")}>{block.approval?.summary}</p>
    {onDecide ? <div className="flex flex-wrap gap-2">
      {(["allow", "allow_always", "deny"] as const).map(decision => <button key={decision} type="button" disabled={busy}
        onClick={() => void decide(decision)} className={cn("rounded-md px-3 py-1.5 text-xs focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
          rail && decision === "allow" ? "bg-primary text-primary-foreground hover:opacity-90" : "border border-border text-foreground hover:bg-secondary")}>
        {t(`work_trace.${decision}`)}
      </button>)}
    </div> : <p className="text-xs text-muted-foreground">{t("work_trace.approval_elsewhere")}</p>}
    {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
  </div> : null;
  if (rail) return <div data-trace-tool={block.callId} data-state={state}>
    <Disclosure
      label={<span className="flex min-w-0 items-baseline gap-2">
        <span className={cn("shrink-0", block.isError ? "text-destructive" : pending ? "text-foreground" : "text-foreground-secondary", view.integration && "font-medium")}><Live on={running}>{label}</Live></span>
        {detail ? <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">{" "}{detail}</span> : null}
      </span>}
      icon={branded
        ? <span className="tool-identity inline-flex" style={toolIdentityStyle(view.row)} data-trace-brand={view.identity.key}><ToolChoiceIcon row={view.row} size={14} /></span>
        : <Icon className={nodeIcon} />}
      live={running}
      trailing={running ? traceDuration(elapsed) : block.durationMs !== null ? traceDuration(block.durationMs) : null}
      tone={block.isError ? "text-destructive" : pending ? "text-foreground" : undefined}
      summary={<>
        {state !== "completed" && state !== "running" && state !== "approval" ? <p className={cn("mb-1 pl-7 text-xs", block.isError && "text-destructive")}>{t(`work_trace.${state}`)}</p> : null}
        {block.isError && block.output ? <p className="mb-1.5 pl-7 line-clamp-4 whitespace-pre-wrap font-mono text-xs leading-5 text-destructive [overflow-wrap:anywhere]">{block.output.slice(0, 500)}</p> : null}
      </>}>
      <ToolDetails block={block} />
    </Disclosure>
    {approvalUi}
  </div>;
  return <div data-trace-tool={block.callId} data-state={state}>
    <Disclosure label={<><span className={view.integration ? "font-medium" : undefined}>{label}</span>{detail ? <span className="ml-2 text-xs text-muted-foreground">{" "}{detail}</span> : null}</>}
      icon={branded ? <span className={cn("tool-identity mt-1 shrink-0", running && "motion-safe:animate-pulse")} style={toolIdentityStyle(view.row)} data-trace-brand={view.identity.key}><ToolChoiceIcon row={view.row} size={16} /></span> : <Icon aria-hidden className={cn(iconClass, "mt-1", running && "motion-safe:animate-pulse")} />}
      trailing={<span className="inline-flex items-center gap-1.5">{running ? <CircleDashed aria-hidden className="h-3 w-3 motion-safe:animate-spin" /> : null}{running ? traceDuration(elapsed) : block.durationMs !== null ? traceDuration(block.durationMs) : null}</span>}
      tone={block.isError ? "text-destructive" : pending ? "text-foreground" : undefined}
      summary={<>
        {state !== "completed" ? <p className={cn("mb-1 ml-6 text-xs", block.isError && "text-destructive")}>{t(`work_trace.${state}`)}</p> : null}
        {block.isError && block.output ? <p className="mb-2 ml-6 whitespace-pre-wrap text-xs text-destructive [overflow-wrap:anywhere]">{block.output.slice(0, 500)}</p> : null}
      </>}>
      <ToolDetails block={block} />
    </Disclosure>
    {approvalUi}
  </div>;
}

function ActivitySummary({ blocks, live }: { blocks: TurnBlock[]; live: boolean }) {
  const t = useT();
  const rail = useRail();
  const parts = activityParts(blocks.filter((block): block is ToolBlock => block.kind === "tool"));
  if (!parts.length) return <Live on={rail && live}>{t("work_trace.thought")}</Live>;
  return <span className={cn("inline-flex flex-wrap items-center gap-x-1.5 gap-y-1", rail && "text-foreground-secondary")}>
    {parts.map((part, index) => <span key={`${part.key}:${part.service ?? ""}`} className="inline-flex items-center gap-1.5">
      {index > 0 ? <span aria-hidden className="text-muted-foreground/50">·</span> : null}
      {part.row && index > 0 ? <span className="tool-identity inline-flex" style={toolIdentityStyle(part.row)}><ToolChoiceIcon row={part.row} size={14} /></span> : null}
      <Live on={rail && live}>{t(`work_trace.${live ? "live_" : ""}${part.key}`).replace("{service}", part.service ?? "")}</Live>
    </span>)}
  </span>;
}

function ActivityIcon({ blocks, live }: { blocks: TurnBlock[]; live: boolean }) {
  const rail = useRail();
  const first = activityParts(blocks.filter((block): block is ToolBlock => block.kind === "tool"))[0];
  if (first?.row) return <span className={cn("tool-identity shrink-0", rail ? "inline-flex" : "mt-1", !rail && live && "motion-safe:animate-pulse")} style={toolIdentityStyle(first.row)}><ToolChoiceIcon row={first.row} size={rail ? 14 : 16} /></span>;
  if (rail && !first) return <span className="trace-dot" />;
  const Icon = live && !rail ? CircleDashed : first?.key === "activity_command" ? Terminal
    : first?.key === "activity_edit" || first?.key === "activity_write" || first?.key === "activity_memory" ? FilePenLine : first ? FileText : Brain;
  return <Icon aria-hidden className={rail ? nodeIcon : cn(iconClass, "mt-1", live && "motion-safe:animate-spin")} />;
}

/** Up to three brand marks of the services a stretch of work touched. */
function traceLogos(blocks: TurnBlock[]): ToolChoice[] {
  const rows: ToolChoice[] = [];
  const seen = new Set<string>();
  for (const block of blocks) {
    if (block.kind !== "tool") continue;
    const view = traceToolIdentity(block);
    const key = view.identity.logo || view.identity.key || view.service || block.name;
    if (!view.identity.logo || seen.has(key)) continue;
    seen.add(key);
    rows.push(view.row);
    if (rows.length === 3) break;
  }
  return rows;
}

/** Small chevron that hides finished work so the reply can stand alone. */
function ConversationWorkFold({ durationMs, attention, blocks, children, timeline, initiallyOpen = false }: {
  durationMs: number | null; attention?: ReactNode; blocks: TurnBlock[]; children: ReactNode;
  /** Start open — the work is the turn's only outcome (it answered nothing). */
  initiallyOpen?: boolean;
  /** Rail look: the timeline behind the fold, for its "Worked for …" line. */
  timeline?: Timeline;
}) {
  const t = useT();
  const rail = useRail();
  const id = useId();
  const [open, setOpen] = useState(initiallyOpen);
  // Counts openings so inner disclosures expand all at once, every time.
  const [seq, setSeq] = useState(initiallyOpen ? 1 : 0);
  const logos = useMemo(() => rail ? traceLogos(blocks) : [], [rail, blocks]);
  const label = timeline ? headerLabel(timeline, durationMs, t)
    : durationMs !== null && durationMs > 0
      ? t("work_trace.thought_for").replace("{duration}", traceDuration(durationMs))
      : t("work_trace.thought");
  const toggle = () => {
    if (!open) setSeq((s) => s + 1);
    setOpen(!open);
  };
  return (
    <div className="min-w-0" data-testid="conversation-work-fold" data-open={open ? "true" : "false"}>
      {rail ? <button type="button" aria-expanded={open} aria-controls={id} onClick={toggle}
        className="group/fold flex max-w-full min-w-0 items-center gap-2 rounded-md py-1 text-left text-sm leading-6 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {logos.length ? <span aria-hidden className="flex shrink-0 items-center">
          {logos.map((row, i) => <span key={i} className={cn("tool-identity inline-flex rounded-full bg-background ring-2 ring-background", i > 0 && "-ml-1")} style={toolIdentityStyle(row)}><ToolChoiceIcon row={row} size={14} /></span>)}
        </span> : null}
        <span className="shrink-0">{label}</span>
        {timeline && timeline.problemCount > 0 ? <span className="shrink-0 text-destructive">
          <span aria-hidden className="mr-1.5 text-muted-foreground/50">·</span>
          {t(`trace_report.failed_count_${timeline.problemCount === 1 ? "one" : "other"}`).replace("{count}", String(timeline.problemCount))}
        </span> : null}
        <ChevronRight aria-hidden className={cn("h-3.5 w-3.5 shrink-0 opacity-50 transition-transform group-hover/fold:opacity-100", open && "rotate-90")} />
      </button> : <button type="button" aria-expanded={open} aria-controls={id}
        className="group/fold mb-1 inline-flex max-w-full items-center gap-1 rounded-md px-1.5 py-1 text-left text-xs leading-5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={toggle}>
        <ChevronRight aria-hidden className={cn("h-3 w-3 shrink-0 opacity-70 transition-transform group-hover/fold:opacity-100", open && "rotate-90")} />
        <span className="truncate">{label}</span>
      </button>}
      {open ? <div id={id} className={rail ? "group/fold-body pb-1 pt-0.5" : undefined}><FoldExpandContext.Provider value={seq}>{children}</FoldExpandContext.Provider></div> : attention}
    </div>
  );
}

function traceGroupItems({ groups, live, status, onDecide, renderText, conversation, rail, t }: {
  groups: Group[]; live: boolean; status: TurnStatus; onDecide?: Decide;
  renderText?: (text: string, id: string) => ReactNode; conversation: boolean; rail: boolean; t: (key: string) => string;
}): RailItem[] {
  const items: RailItem[] = [];
  for (const group of groups) {
    const first = group.blocks[0];
    if (group.family === "activity" && group.blocks.length > 1) {
      const inner = group.blocks.map(block => block.kind === "tool"
        ? <TraceTool key={block.callId} block={block} status={status} onDecide={onDecide} />
        : block.kind === "reasoning" ? <ReasoningTrace key={block.id} block={block} turnLive={live} compact /> : null);
      const disclosure = <Disclosure label={<ActivitySummary blocks={group.blocks} live={live} />}
        icon={<ActivityIcon blocks={group.blocks} live={live} />} initiallyOpen={live}
        forced={live} live={live}>
        {rail ? <Rail items={group.blocks.map((block, i) => ({
          key: block.kind === "tool" ? block.callId : block.id,
          node: inner[i],
          rail: !(block.kind === "tool" && block.question),
        }))} /> : inner}
      </Disclosure>;
      items.push({ key: group.id, rail: true, node: rail ? <div data-trace-summary>{disclosure}</div>
        : <div className={cn("py-1", conversation && "w-full")} data-trace-summary>{disclosure}</div> });
      continue;
    }
    if (group.blocks.length > 1) {
      const tools = group.blocks.map(block => <TraceTool key={(block as ToolBlock).callId} block={block as ToolBlock} status={status} onDecide={onDecide} />);
      items.push({ key: group.id, rail: true, node: <Disclosure
        label={rail ? <span className="text-foreground-secondary">{t(`work_trace.group_${group.family}`).replace("{count}", String(group.blocks.length))}</span>
          : t(`work_trace.group_${group.family}`).replace("{count}", String(group.blocks.length))}
        icon={rail ? group.family === "read" ? <FileText className={nodeIcon} /> : <FolderSearch className={nodeIcon} /> : <Check aria-hidden className={iconClass} />}
        initiallyOpen={live}>
        {rail ? <Rail items={tools.map((node, i) => ({ key: (group.blocks[i] as ToolBlock).callId, node, rail: true }))} /> : tools}
      </Disclosure> });
      continue;
    }
    if (first.kind === "tool") {
      const tool = <TraceTool block={first} status={status} onDecide={onDecide} />;
      items.push({ key: group.id, rail: !first.question, node: rail ? tool
        : <div className={conversation ? "w-full py-1 text-xs [&_button]:text-xs" : undefined}>{tool}</div> });
      continue;
    }
    if (first.kind === "reasoning") {
      const thought = <ReasoningTrace block={first} turnLive={live} compact={conversation} />;
      items.push({ key: group.id, rail: true, node: rail ? thought
        : <div className={conversation ? "w-full text-xs [&_button]:text-xs" : undefined}>{thought}</div> });
      continue;
    }
    if (!first.text.trim()) continue;
    items.push({ key: group.id, rail: false, node: replyNode(first, conversation, renderText) });
  }
  return items;
}

function replyNode(block: TextBlock, conversation: boolean, renderText?: (text: string, id: string) => ReactNode): ReactNode {
  return <div className={cn("min-w-0 py-2", conversation && "w-fit max-w-[min(85%,42rem)] rounded-2xl rounded-bl-md bg-secondary px-4 py-2.5")}>
    {renderText ? renderText(block.text, block.id) : <div className="prose prose-sm max-w-none text-foreground dark:prose-invert [overflow-wrap:anywhere]"><ChatMarkdown text={block.text} /></div>}
  </div>;
}

/**
 * An agent's question shows as its card; the tool calls that only wait on
 * that card (``wait_for`` polls, jarvis/society/ask_tool.py) are plumbing
 * and draw nothing.
 */
function withoutQuestionPolls(blocks: TurnBlock[]): TurnBlock[] {
  const kept = blocks.filter((block) => !(block.kind === "tool" && isQuestionTool(block.name) && !block.question && !block.isError));
  return kept.length === blocks.length ? blocks : kept;
}

const NO_BLOCKS: TurnBlock[] = [];

export function WorkTrace({ look = "rail", ...props }: WorkTraceProps & { look?: TraceLook }) {
  return <TraceLookContext.Provider value={look}><WorkTraceBody {...props} /></TraceLookContext.Provider>;
}

type WorkTraceProps = {
  blocks: TurnBlock[]; status: TurnStatus; startedMs: number; durationMs: number | null; error?: string | null;
  onDecide?: Decide; renderText?: (text: string, id: string) => ReactNode; className?: string;
  receipt?: ReactNode; completionLabel?: string; conversation?: boolean;
  /**
   * The trace is Jarvis's own (the front-page chat, a voice turn): the live
   * line shows the user's pet at work instead of a spinning circle. Agent
   * and coding-pane traces keep the plain node.
   */
  companion?: boolean;
};

function WorkTraceBody({ blocks: rawBlocks, status, startedMs, durationMs, error, onDecide, renderText, className, receipt, completionLabel, conversation = false, companion = false }: WorkTraceProps) {
  const t = useT();
  const rail = useRail();
  const blocks = useMemo(() => withoutQuestionPolls(rawBlocks), [rawBlocks]);
  const live = status === "running";
  const elapsed = useClock(startedMs, live);
  // The rail look folds every finished turn, conversation or not, the way
  // Claude and Codex do: "Worked for …" opens the turn's timeline
  // (TraceTimeline), the reply stands alone. The classic look folds
  // conversation turns only, as before.
  const split = useMemo(() => (conversation || rail) && !live ? splitConversationTurn(blocks) : null, [blocks, conversation, rail, live]);
  // Completed failures and pending approvals stay visible beside the fold.
  // Other work can collapse without hiding an action that needs attention.
  const fold = useMemo(() => {
    if (!split) return null;
    const workAll = [...split.work, ...split.after];
    if (!hasFoldableWork(workAll)) return null;
    return {
      answer: split.answer,
      workAll,
      attention: workAll.filter(needsAttention),
    };
  }, [split]);
  const groups = useMemo(() => conversation ? groupConversationTrace(fold ? fold.workAll : blocks) : groupActivityTrace(blocks), [blocks, conversation, fold]);
  const restGroups = useMemo(() => fold ? groupConversationTrace(fold.answer) : null, [fold]);
  // A live turn keeps its replies and streaming pieces in place; a folded
  // one tells its narration as the timeline's prose.
  const timeline = useTimeline(rail ? (fold ? fold.workAll : blocks) : NO_BLOCKS, status, !fold);
  const asking = blocks.some(isOpenQuestion);
  const pending = asking || blocks.some(block => block.kind === "tool" && block.approval?.decision === null);
  const toolFailed = blocks.some(block => block.kind === "tool" && block.isError);
  const failed = status === "error";
  const outcome = asking ? "question" : pending ? "approval" : live ? "working" : failed ? "failed" : status === "cancelled" ? "stopped" : "done";
  const Icon = asking ? MessageCircleQuestion : pending ? ShieldQuestion : live ? CircleDashed : failed ? CircleAlert : Check;
  const groupProps = { live, status, onDecide, renderText, conversation, rail, t };
  // A turn-level error next to a reply folds with the work — it stays one
  // tap away behind the toggle. With no reply the error IS the outcome, so
  // it stays out where it always was.
  const answered = fold ? fold.answer.length > 0 : blocks.some((block) => block.kind === "text" && block.text.trim());
  const foldedError = fold && answered && error ? error : null;
  const visibleError = error && !foldedError ? error : null;
  const outcomeLabel = outcome === "done" && completionLabel ? completionLabel : t(`work_trace.${outcome}`);
  const receiptNode = receipt ? conversation ? <details className="ml-1"><summary className="cursor-pointer rounded-sm focus-visible:ring-2 focus-visible:ring-ring">{t("society.chat.activity_details")}</summary><div className="flex flex-wrap gap-2 py-1">{receipt}</div></details> : receipt : null;

  if (rail) {
    const working = live && !pending;
    // The state line is the thread's last node while work is under way (or
    // whenever the trace has no reply between it and the work); a finished
    // conversation turn closes with a quiet line under its reply instead.
    const statusOnRail = !fold && (!conversation || live);
    const petState = companion && working ? livePetState(blocks) : null;
    const statusLine = <div role="status" aria-live="polite" data-trace-status={outcome}
      className={cn("flex min-w-0 flex-wrap items-start gap-x-3 text-xs leading-6 text-muted-foreground",
        statusOnRail ? "py-1" : "pb-2 pt-1", failed && "text-destructive", pending && "text-foreground")}>
      {petState
        ? <span aria-hidden className="trace-node trace-node-pet" data-trace-pet={petState}><PetMark size={32} state={petState} /></span>
        : null}
      <span className="inline-flex min-w-0 flex-1 flex-wrap items-center gap-x-2">
        <span><Live on={working}>{outcomeLabel}</Live></span>
        {toolFailed && !live && !failed ? <span className="sr-only">{t("work_trace.tool_failed")}</span> : null}
        {(live || durationMs !== null) ? <span aria-live="off" className="tabular-nums">{traceDuration(live ? elapsed : durationMs ?? 0)}</span> : null}
        {receiptNode ? <span aria-hidden className="text-muted-foreground/50">·</span> : null}
        {receiptNode}
      </span>
    </div>;
    const statusItem: RailItem = { key: "trace:status", node: statusLine, rail: statusOnRail };
    const errorItem = (text: string, key: string): RailItem => ({ key, rail: false,
      node: <p role="alert" className="py-1.5 pl-7 text-sm text-destructive [overflow-wrap:anywhere]">{text}</p> });
    const renderers: TimelineRenderers = {
      renderLive: (block) => block.kind === "tool" ? <TraceTool block={block} status={status} onDecide={onDecide} />
        : block.kind === "reasoning" ? <ReasoningTrace block={block} turnLive={live} compact={conversation} />
        : replyNode(block, conversation, renderText),
      liveOnRail: (block) => block.kind === "reasoning" || (block.kind === "tool" && !block.question),
      renderNarration: (text, id) => renderText ? renderText(text, id)
        : <div className="prose prose-sm max-w-none text-foreground dark:prose-invert [overflow-wrap:anywhere]"><ChatMarkdown text={text} /></div>,
      renderDetails: (block) => <ToolDetails block={block} />,
    };
    const steps = timelineItems(timeline, renderers);
    const rest = restGroups ? traceGroupItems({ groups: restGroups, ...groupProps }) : [];
    const items: RailItem[] = fold
      ? [{ key: "trace:fold", rail: false, node: <ConversationWorkFold durationMs={durationMs} blocks={fold.workAll} timeline={timeline}
          // A turn that ended without an answer (TurnTrace names it so) has
          // nothing to read but its work: the timeline stands open.
          initiallyOpen={!answered && Boolean(completionLabel)}
          attention={<div className="trace-fold-open"><Rail items={fold.attention.map(block => ({ key: block.callId, rail: !block.question, node: <TraceTool block={block} status={status} onDecide={onDecide} /> }))} /></div>}>
          <Rail items={[...steps, ...(foldedError ? [errorItem(foldedError, "trace:folded-error")] : [])]} />
          <CopyTimeline text={() => timelineMarkdown(timeline, headerLabel(timeline, durationMs, t), t)} />
        </ConversationWorkFold> }, ...rest]
      : steps;
    if (visibleError) items.push(errorItem(visibleError, "trace:error"));
    if (toolFailed && !live && !failed) items.push({ key: "trace:tool-failed", rail: false,
      node: <p data-testid="tool-failure-warning" className="flex items-center gap-1.5 py-1 pl-7 text-xs text-destructive"><CircleAlert aria-hidden className="h-3.5 w-3.5" />{t("work_trace.tool_failed")}</p> });
    items.push(statusItem);
    return <div className={cn("min-w-0", conversation && "w-full max-w-[44rem] self-start", className)} data-testid="work-trace" data-look="rail" data-state={status} {...(conversation ? { "data-conversation": "" } : {})}>
      <Rail items={items} />
    </div>;
  }

  const classicGroups = (list: Group[]) => <>{traceGroupItems({ groups: list, ...groupProps }).map(item => <Fragment key={item.key}>{item.node}</Fragment>)}</>;
  return <div className={cn("min-w-0 space-y-0.5", conversation && "w-full max-w-[44rem] self-start", className)} data-testid="work-trace" data-state={status} {...(conversation ? { "data-conversation": "" } : {})}>
    {fold ? <ConversationWorkFold durationMs={durationMs} blocks={fold.workAll} attention={fold.attention.map(block =>
      <div key={block.callId} className="w-full py-1 text-xs [&_button]:text-xs">
        <TraceTool block={block} status={status} onDecide={onDecide} />
      </div>)}>
      {classicGroups(groups)}
      {foldedError ? <p role="alert" className="py-2 text-sm text-destructive [overflow-wrap:anywhere]">{foldedError}</p> : null}
    </ConversationWorkFold> : classicGroups(groups)}
    {restGroups ? classicGroups(restGroups) : null}
    {visibleError ? <p role="alert" className="py-2 text-sm text-destructive [overflow-wrap:anywhere]">{visibleError}</p> : null}
    {toolFailed && !live && !failed ? <p data-testid="tool-failure-warning" className="flex items-center gap-1.5 px-1 py-1 text-xs text-destructive">
      <CircleAlert aria-hidden className="h-3.5 w-3.5" />{t("work_trace.tool_failed")}
    </p> : null}
    <div role="status" aria-live="polite" className={cn("flex flex-wrap items-center gap-2 text-xs text-muted-foreground", conversation ? "px-1 pb-2 pt-1" : "border-t border-border pt-3", failed && "text-destructive")}>
      <Icon aria-hidden className={cn("h-3.5 w-3.5", live && !pending && "motion-safe:animate-spin")} />
      <span>{outcomeLabel}</span>
      {toolFailed && !live && !failed ? <span className="sr-only">{t("work_trace.tool_failed")}</span> : null}
      {(live || durationMs !== null) ? <span aria-live="off" className="tabular-nums">{traceDuration(live ? elapsed : durationMs ?? 0)}</span> : null}
      {receiptNode}
    </div>
  </div>;
}

export function TurnTrace({ turn, ...props }: { turn: TurnItem; onDecide?: Decide; renderText?: (text: string, id: string) => ReactNode; conversation?: boolean; look?: TraceLook; companion?: boolean }) {
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
