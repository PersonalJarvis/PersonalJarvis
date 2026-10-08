import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Bot, Check, ChevronRight, CircleSlash, Loader2, MessageSquare, ShieldAlert, X } from "lucide-react";
import { ChatMarkdown } from "@/components/agentchat/ChatMarkdown";
import type { SubagentStatus, ToolBlock, TurnItem } from "@/components/agentchat/reduce";
import { traceDuration } from "@/components/agentchat/traceEntries";
import { cn } from "@/lib/utils";
import { shortCount, subagentCounts, subagentEntry, type SubagentEntry } from "./subagents";

/**
 * A thread's sub-agents on screen: a card where the main agent spawned one,
 * a switcher above the conversation that counts them and opens any of them,
 * and the header of an opened sub-agent's own conversation.
 */

/**
 * Opens a sub-agent's conversation by the id of the call that spawned it.
 * Null where no conversation can be opened (an agent chat): the card then
 * only reports the sub-agent.
 */
export const OpenSubagent = createContext<((id: string) => void) | null>(null);

const STATUS_WORD: Record<SubagentStatus, string> = {
  running: "Working",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
};

/** A sub-agent's state at a glance: working, waiting for the person, or how it ended. */
export function SubagentMark({ status, waiting = false, className }: { status: SubagentStatus; waiting?: boolean; className?: string }) {
  const box = cn("h-3.5 w-3.5 shrink-0", className);
  if (waiting && status === "running") return <ShieldAlert aria-hidden className={cn(box, "text-warning")} />;
  if (status === "running") return <Loader2 aria-hidden className={cn(box, "animate-spin text-muted-foreground")} />;
  if (status === "done") return <Check aria-hidden className={cn(box, "text-success")} />;
  if (status === "failed") return <X aria-hidden className={cn(box, "text-destructive")} />;
  return <CircleSlash aria-hidden className={cn(box, "text-muted-foreground")} />;
}

/** A clock that writes its own text once a second, so nothing around it re-renders to tick. */
function LiveClock({ since }: { since: number }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    const tick = () => { if (ref.current) ref.current.textContent = traceDuration(Math.max(0, Date.now() - since)); };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [since]);
  return <span ref={ref} className="tabular-nums">{traceDuration(Math.max(0, Date.now() - since))}</span>;
}

function firstLine(text: string): string {
  return text.split(/\r?\n/).map((line) => line.replace(/^[#>*\-\s]+/, "").trim()).find(Boolean) ?? "";
}

/** "4 tools · 23.4k tokens · 12s" — what the sub-agent spent so far. */
function SubagentMeta({ entry }: { entry: SubagentEntry }) {
  const parts: string[] = [];
  if (entry.toolUses) parts.push(`${entry.toolUses} ${entry.toolUses === 1 ? "tool" : "tools"}`);
  if (entry.tokens) parts.push(`${shortCount(entry.tokens)} tokens`);
  const running = entry.status === "running";
  const duration = !running && entry.durationMs ? traceDuration(entry.durationMs) : "";
  if (duration) parts.push(duration);
  if (!parts.length && !running) return null;
  return <span className="shrink-0 tabular-nums">
    {parts.join(" · ")}
    {running && <>{parts.length ? " · " : ""}<LiveClock since={entry.startedMs} /></>}
  </span>;
}

/** The line under a card's title: what it does now, else how it ended. */
function statusLine(entry: SubagentEntry): string {
  if (entry.status === "running") {
    if (entry.waiting) return "Waiting for your approval";
    return entry.activity || (entry.block.subagent?.lastTool ? `Using ${entry.block.subagent.lastTool}` : "Working");
  }
  if (entry.status === "failed") return firstLine(entry.summary) || "Failed";
  if (entry.status === "stopped") return "Stopped before it answered";
  return firstLine(entry.summary) || "Finished";
}

/**
 * Where the main agent spawned a sub-agent: one card with its task, what it
 * is doing or how it ended, and what it spent. Where its conversation can be
 * opened, a click opens it.
 */
export function SubagentCard({ block, turn }: { block: ToolBlock; turn: TurnItem }) {
  const open = useContext(OpenSubagent);
  const entry = useMemo(() => subagentEntry(block, turn), [block, turn]);
  const running = entry.status === "running";
  const card = "group/agent flex w-full min-w-0 items-center gap-3 rounded-xl border border-border bg-card px-3 py-2.5 text-left";
  const body = <SubagentCardBody entry={entry} running={running} />;
  if (!open) return <div data-testid="thread-subagent-card" data-status={entry.status} className={card}>{body}</div>;
  return <button type="button" onClick={() => open(entry.id)} data-testid="thread-subagent-card" data-status={entry.status}
    aria-label={`Open sub-agent ${entry.title}, ${STATUS_WORD[entry.status].toLowerCase()}`}
    className={cn(card, "transition-colors hover:border-border-strong hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring")}>
    {body}
    <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground transition-colors group-hover/agent:text-foreground">
      <MessageSquare aria-hidden className="h-3.5 w-3.5" />
      <span className="hidden sm:inline">Open</span>
      <ChevronRight aria-hidden className="h-3.5 w-3.5" />
    </span>
  </button>;
}

function SubagentCardBody({ entry, running }: { entry: SubagentEntry; running: boolean }) {
  return <>
    <span aria-hidden className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary text-foreground-secondary">
      <Bot className="h-4 w-4" strokeWidth={1.75} />
      <span className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full border border-border bg-card">
        <SubagentMark status={entry.status} waiting={entry.waiting} className="h-2.5 w-2.5" />
      </span>
    </span>
    <span className="min-w-0 flex-1">
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-medium text-foreground-strong">{entry.title}</span>
        {entry.agentType && <span className="shrink-0 rounded-md border border-border px-1.5 text-[11px] leading-4 text-muted-foreground">{entry.agentType}</span>}
      </span>
      <span className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
        <span className={cn("min-w-0 truncate", running && !entry.waiting && "live-tool-shine", entry.waiting && "text-warning", entry.status === "failed" && "text-destructive")}>{statusLine(entry)}</span>
        <span aria-hidden className="text-muted-foreground/50">·</span>
        <SubagentMeta entry={entry} />
      </span>
    </span>
  </>;
}

/** "3 sub-agents · 2 working · 1 waiting" — the switcher's own count. */
export function subagentSummary(entries: readonly SubagentEntry[]): string {
  const counts = subagentCounts(entries);
  const parts = [`${counts.total} ${counts.total === 1 ? "sub-agent" : "sub-agents"}`];
  if (counts.running) parts.push(`${counts.running} working`);
  if (counts.waiting) parts.push(`${counts.waiting} waiting`);
  if (counts.failed) parts.push(`${counts.failed} failed`);
  return parts.join(" · ");
}

/**
 * The switcher above the conversation: the main thread first, then every
 * sub-agent the thread spawned — nested ones after their parent — each with
 * its state. The open one is marked; a click switches the conversation.
 */
export function SubagentSwitcher({ entries, openId, onOpen }: {
  entries: readonly SubagentEntry[];
  openId: string | null;
  onOpen: (id: string | null) => void;
}) {
  const strip = useRef<HTMLDivElement | null>(null);
  // The open tab stays in view when the list is wider than the column.
  useEffect(() => {
    const active = strip.current?.querySelector<HTMLElement>("[aria-current=true]");
    active?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [openId]);
  if (entries.length === 0) return null;
  const tab = (active: boolean) => cn(
    "flex h-7 max-w-[15rem] shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    active ? "bg-secondary text-foreground-strong" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
  );
  return <nav aria-label="Sub-agents" data-testid="thread-subagent-switcher"
    className="flex min-w-0 items-center gap-2 border-b border-border/70 bg-background px-3 py-1.5">
    <div ref={strip} className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      <button type="button" aria-current={openId === null} onClick={() => onOpen(null)} className={tab(openId === null)} data-testid="thread-subagent-main">
        <MessageSquare aria-hidden className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">Main thread</span>
      </button>
      <span aria-hidden className="mx-1 h-4 w-px shrink-0 bg-border" />
      {entries.map((entry) => <button key={entry.id} type="button" aria-current={openId === entry.id} onClick={() => onOpen(entry.id)}
        title={entry.prompt || entry.title} className={tab(openId === entry.id)} data-testid="thread-subagent-tab" data-status={entry.status}>
        {entry.depth > 0 && <span aria-hidden className="shrink-0 text-muted-foreground/60">{"›".repeat(entry.depth)}</span>}
        <SubagentMark status={entry.status} waiting={entry.waiting} />
        <span className="truncate">{entry.title}</span>
      </button>)}
    </div>
    <span className="hidden shrink-0 text-xs tabular-nums text-muted-foreground md:inline" data-testid="thread-subagent-count">{subagentSummary(entries)}</span>
  </nav>;
}

/**
 * The top of an opened sub-agent's conversation: the way back, who it is,
 * where it sits (a sub-agent's own sub-agents name their parents), how it
 * stands, and the task the main agent gave it.
 */
export function SubagentHeader({ entry, path, onOpen }: {
  entry: SubagentEntry;
  path: readonly SubagentEntry[];
  onOpen: (id: string | null) => void;
}) {
  const [taskOpen, setTaskOpen] = useState(false);
  const long = entry.prompt.length > 280 || entry.prompt.split("\n").length > 5;
  const parent = path.length > 1 ? path[path.length - 2] : null;
  return <div className="space-y-3" data-testid="thread-subagent-header">
    <div className="flex min-w-0 items-center gap-2 text-sm">
      <button type="button" onClick={() => onOpen(parent ? parent.id : null)} data-testid="thread-subagent-back"
        className="flex h-7 shrink-0 items-center gap-1.5 rounded-md px-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <ArrowLeft aria-hidden className="h-4 w-4" />
        <span>{parent ? parent.title : "Main thread"}</span>
      </button>
    </div>
    <div className="flex min-w-0 items-start gap-3">
      <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-secondary text-foreground-secondary">
        <Bot className="h-5 w-5" strokeWidth={1.75} />
      </span>
      <div className="min-w-0 flex-1">
        <h2 className="flex min-w-0 items-center gap-2 text-base font-semibold text-foreground-strong">
          <span className="truncate">{entry.title}</span>
          {entry.agentType && <span className="shrink-0 rounded-md border border-border px-1.5 text-[11px] font-normal leading-4 text-muted-foreground">{entry.agentType}</span>}
        </h2>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <SubagentMark status={entry.status} waiting={entry.waiting} />
            {entry.waiting && entry.status === "running" ? "Waiting for your approval" : STATUS_WORD[entry.status]}
          </span>
          {entry.background && <><span aria-hidden>·</span><span>In the background</span></>}
          <span aria-hidden>·</span>
          <SubagentMeta entry={entry} />
        </div>
      </div>
    </div>
    {entry.prompt && <section aria-label="Task" className="rounded-xl border border-border bg-card px-3 py-2.5">
      <div className="mb-1 text-xs font-medium text-muted-foreground">Task from {parent ? parent.title : "the main agent"}</div>
      <div className={cn("relative text-sm leading-6 text-foreground-secondary [overflow-wrap:anywhere]", long && !taskOpen && "max-h-32 overflow-hidden")}>
        <div className="prose prose-neutral max-w-none text-sm dark:prose-invert prose-p:my-1 prose-p:text-foreground-secondary prose-li:text-foreground-secondary"><ChatMarkdown text={entry.prompt} /></div>
        {long && !taskOpen && <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-card to-transparent" />}
      </div>
      {long && <button type="button" aria-expanded={taskOpen} onClick={() => setTaskOpen(!taskOpen)}
        className="mt-1 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {taskOpen ? "Show less" : "Show the whole task"}
      </button>}
    </section>}
    {!entry.streamed && <p className="text-xs text-muted-foreground" data-testid="thread-subagent-unstreamed">
      {entry.status === "running"
        ? "This agent's CLI does not stream a sub-agent's steps. Its answer shows here when it finishes."
        : "This agent's CLI reported only the task and the answer, not the sub-agent's steps."}
    </p>}
  </div>;
}
