import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowDown, Brain, Check, ChevronRight, Copy, Eye, FilePlus2, FileText, FolderOpen, Globe, Image as ImageIcon,
  MessageCircleQuestion, Search, ShieldAlert, SquarePen, SquareTerminal, Wrench, X,
} from "lucide-react";
import { ChatMarkdown } from "@/components/agentchat/ChatMarkdown";
import type { ReasoningBlock, TextBlock, TimelineItem, ToolBlock, TurnItem, UserItem } from "@/components/agentchat/reduce";
import { toolDiff, type DiffFile } from "@/components/agentchat/toolDiff";
import { buildTimeline, readableOutput, traceDuration, type ActivityEntry, type Call } from "@/components/agentchat/traceEntries";
import { useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

/**
 * A thread's conversation: the person's messages on the right, the agent's
 * answer as plain reading text on the left, and the work in between as quiet
 * rows — one line per stretch of tool calls ("Ran 3 commands, read 2 files")
 * that opens to the single calls, and a call that opens to what it ran, the
 * diff it made or what came back. A running turn always ends in a live
 * "Working for 12s" line; a finished one says how long it worked and which
 * files it changed.
 */

const PROSE = cn(
  "prose prose-neutral max-w-none text-base leading-6 text-foreground dark:prose-invert dark:text-foreground [overflow-wrap:anywhere]",
  "[&>div>:first-child]:mt-0 [&>div>:last-child]:mb-0",
  "prose-p:my-2 prose-p:text-foreground prose-li:text-foreground prose-strong:text-foreground-strong",
  "prose-headings:mb-1.5 prose-headings:mt-4 prose-headings:font-semibold prose-headings:tracking-normal prose-headings:text-foreground-strong",
  "prose-h1:text-lg prose-h2:text-base prose-h3:text-base prose-h4:text-base",
  "prose-a:text-foreground-strong prose-a:underline prose-a:decoration-border-strong prose-a:underline-offset-2",
  "prose-code:rounded prose-code:bg-secondary prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:font-normal prose-code:before:hidden prose-code:after:hidden",
  "prose-pre:my-2 prose-pre:rounded-lg prose-pre:border prose-pre:border-border prose-pre:bg-card prose-pre:text-sm",
  "prose-li:my-0.5 prose-ul:my-2 prose-ol:my-2 prose-ul:pl-5 prose-ol:pl-5",
  "prose-table:my-3 prose-table:text-sm prose-th:text-foreground-strong prose-td:text-foreground",
);

const KIND_ICONS: Record<Call["kind"], typeof SquareTerminal> = {
  command: SquareTerminal,
  read: Eye,
  list: FolderOpen,
  search: Search,
  edit: SquarePen,
  write: FilePlus2,
  image: ImageIcon,
  web: Globe,
  family: Wrench,
  service: Wrench,
  tool: Wrench,
};

/** One row of the work log: a 24 px line with an icon, words and a trailing note. */
function WorkRow({
  icon,
  label,
  trailing,
  expanded,
  onToggle,
  live = false,
  failed = false,
  children,
}: {
  icon: ReactNode;
  label: ReactNode;
  trailing?: ReactNode;
  expanded?: boolean;
  onToggle?: () => void;
  live?: boolean;
  failed?: boolean;
  children?: ReactNode;
}) {
  const line = <span className="flex min-h-6 min-w-0 items-center gap-2 text-sm">
    <span className={cn("flex h-5 w-5 shrink-0 items-center justify-center", failed ? "text-destructive" : "text-muted-foreground")}>{icon}</span>
    <span className={cn("min-w-0 flex-1 truncate", failed ? "text-destructive" : "text-muted-foreground", live && "thinking-shimmer")}>{label}</span>
    {trailing}
    {onToggle && <ChevronRight aria-hidden className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition group-hover/row:opacity-100", expanded && "rotate-90 opacity-100")} />}
  </span>;
  return <div className="min-w-0">
    {onToggle
      ? <button type="button" aria-expanded={expanded} onClick={onToggle}
        className="group/row w-full rounded-md px-0.5 py-px text-left hover:bg-secondary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">{line}</button>
      : <div className="px-0.5 py-px">{line}</div>}
    {expanded && children}
  </div>;
}

function DiffStat({ added, removed }: { added: number; removed: number }) {
  if (!added && !removed) return null;
  return <span className="shrink-0 font-mono text-xs tabular-nums">
    {added > 0 && <span className="text-success">+{added}</span>}
    {added > 0 && removed > 0 && " "}
    {removed > 0 && <span className="text-destructive">−{removed}</span>}
  </span>;
}

function DiffView({ files }: { files: DiffFile[] }) {
  return <div className="space-y-2">
    {files.map((file, index) => <div key={`${file.path}:${index}`} className="overflow-hidden rounded-lg border border-border bg-card">
      {file.path && <div className="flex items-center gap-2 border-b border-border px-3 py-1.5 font-mono text-xs text-muted-foreground">
        <span className="min-w-0 flex-1 truncate">{file.path}</span><DiffStat added={file.added} removed={file.removed} />
      </div>}
      <pre className="m-0 max-h-80 overflow-auto py-1 font-mono text-xs leading-5 scrollbar-jarvis">
        {file.lines.map((line, i) => line.kind === "gap"
          ? <div key={i} className="px-3 text-muted-foreground">⋯</div>
          : <div key={i} className={cn("whitespace-pre px-3", line.kind === "add" && "diff-line-add", line.kind === "del" && "diff-line-del")}>
            <span aria-hidden className="mr-2 select-none opacity-60">{line.kind === "add" ? "+" : line.kind === "del" ? "-" : " "}</span>{line.text}
          </div>)}
        {file.truncated > 0 && <div className="px-3 text-muted-foreground">… {file.truncated} more lines</div>}
      </pre>
    </div>)}
  </div>;
}

function inputText(input: unknown): string {
  if (input == null) return "";
  if (typeof input === "string") return input;
  try { return JSON.stringify(input, null, 2); } catch { return String(input); }
}

/** What a call opened to: the diff it made, else what ran and what came back. */
function CallDetails({ call }: { call: Call }) {
  const block = call.block;
  const diff = useMemo(() => toolDiff(block.name, block.input, block.output), [block.name, block.input, block.output]);
  const output = readableOutput(block).trim();
  if (diff && diff.length > 0 && !block.isError) return <div className="mb-2 ml-7 mt-1"><DiffView files={diff} /></div>;
  const input = call.kind === "command" ? "" : inputText(block.input);
  return <div className="mb-2 ml-7 mt-1 space-y-1.5">
    {call.kind === "command" && <pre className="m-0 overflow-x-auto rounded-lg bg-secondary px-3 py-2 font-mono text-xs leading-5 text-foreground scrollbar-jarvis">$ {inputText((block.input as Record<string, unknown> | null)?.command ?? call.text)}</pre>}
    {input && input !== "{}" && <pre className="m-0 max-h-48 overflow-auto rounded-lg bg-secondary px-3 py-2 font-mono text-xs leading-5 text-muted-foreground scrollbar-jarvis">{input}</pre>}
    {output
      ? <pre className={cn("m-0 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-border px-3 py-2 font-mono text-xs leading-5 scrollbar-jarvis", block.isError ? "text-destructive" : "text-foreground-secondary")}>{output}</pre>
      : block.output === null ? null : <p className="text-xs text-muted-foreground">No output.</p>}
  </div>;
}

function CallRow({ call, nested }: { call: Call; nested: boolean }) {
  const [open, setOpen] = useState(false);
  const Icon = KIND_ICONS[call.kind] ?? Wrench;
  const failed = call.status === "failed" || call.status === "blocked" || call.status === "declined";
  const running = call.status === "running";
  const detail = call.detail || call.result;
  return <WorkRow
    icon={running ? <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-jarvis-pulse" /> : failed ? <X className="h-3.5 w-3.5" /> : <Icon className="h-3.5 w-3.5" />}
    label={<>
      <span className={cn(call.kind === "command" && "font-mono text-xs", !nested && call.kind !== "command" && "text-muted-foreground")}>{call.text}</span>
      {detail && <span className="ml-2 text-faint-foreground">{detail}</span>}
      {failed && call.reason && <span className="ml-2">— {call.reason}</span>}
      {call.status === "interrupted" && <span className="ml-2 text-faint-foreground">interrupted</span>}
    </>}
    trailing={<DiffStat added={call.added} removed={call.removed} />}
    live={running}
    failed={failed}
    expanded={open}
    onToggle={() => setOpen((value) => !value)}>
    <CallDetails call={call} />
  </WorkRow>;
}

function ActivityGroup({ entry, live }: { entry: ActivityEntry; live: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  const failed = entry.calls.some((call) => call.status === "failed" || call.status === "blocked" || call.status === "declined");
  if (entry.calls.length === 1) return <CallRow call={entry.calls[0]} nested={false} />;
  // A stretch the turn is still in stays open; a finished one folds to its line.
  const expanded = open ?? live;
  const added = entry.calls.reduce((sum, call) => sum + call.added, 0);
  const removed = entry.calls.reduce((sum, call) => sum + call.removed, 0);
  const running = entry.calls.some((call) => call.status === "running");
  return <WorkRow
    icon={<Wrench className="h-3.5 w-3.5" />}
    label={entry.summary}
    trailing={<DiffStat added={added} removed={removed} />}
    live={running}
    failed={failed && !expanded}
    expanded={expanded}
    onToggle={() => setOpen(!expanded)}>
    <div className="ml-2.5 border-l border-border pl-3">
      {entry.calls.map((call) => <CallRow key={call.id} call={call} nested />)}
    </div>
  </WorkRow>;
}

function ThoughtRow({ block }: { block: ReasoningBlock }) {
  const [open, setOpen] = useState(false);
  const seconds = block.durationMs ? traceDuration(block.durationMs) : "";
  return <WorkRow icon={<Brain className="h-3.5 w-3.5" />} label={seconds ? `Thought for ${seconds}` : "Thought"}
    expanded={open} onToggle={() => setOpen((value) => !value)}>
    <div className="mb-2 ml-7 mt-1 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{block.text}</div>
  </WorkRow>;
}

function LiveThinking({ block }: { block: ReasoningBlock }) {
  const tail = block.text.trim().split(/\r?\n/).filter((line) => line.trim()).slice(-3).join("\n");
  return <div>
    <WorkRow icon={<Brain className="h-3.5 w-3.5" />} label="Thinking" live />
    {tail && <div className="ml-7 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-faint-foreground">{tail}</div>}
  </div>;
}

/** Ticks once a second while a turn runs; the text alone changes, nothing re-lays out. */
function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="tabular-nums">{traceDuration(Math.max(0, now - since))}</span>;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return <button type="button" aria-label={label} title={label}
    onClick={() => { void robustCopy(text).then(() => { setDone(true); window.setTimeout(() => setDone(false), 1200); }); }}
    className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
    {done ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
  </button>;
}

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function UserBubble({ item }: { item: UserItem }) {
  return <div className="group flex flex-col items-end gap-1" data-testid="thread-user-message">
    <div className="jarvis-user-bubble max-w-[80%] rounded-2xl px-4 py-2.5">
      {item.attachments.length > 0 && <div className="mb-2 flex flex-wrap justify-end gap-2">
        {item.attachments.map((file) => file.url && file.kind === "image"
          ? <img key={file.name} src={file.url} alt={file.name} className="h-24 max-w-[200px] rounded-lg border border-border object-cover" />
          : <span key={file.name} className="inline-flex max-w-[240px] items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-1 text-xs text-muted-foreground">
            <FileText aria-hidden className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{file.name}</span>
          </span>)}
      </div>}
      {item.text && <div className="whitespace-pre-wrap text-base leading-6 [overflow-wrap:anywhere]">{item.text}</div>}
    </div>
    <div className="flex items-center gap-1 pr-1 text-xs text-muted-foreground opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100">
      <span className="tabular-nums">{clock(item.tsMs)}</span>
      {item.text && <CopyButton text={item.text} label="Copy message" />}
    </div>
  </div>;
}

/** Every file the turn's edits touched, with the size of the change. */
function changedFiles(turn: TurnItem): { path: string; added: number; removed: number; files: DiffFile[] }[] {
  const byPath = new Map<string, { path: string; added: number; removed: number; files: DiffFile[] }>();
  for (const block of turn.blocks) {
    if (block.kind !== "tool" || block.isError || block.output === null) continue;
    for (const file of toolDiff(block.name, block.input, block.output) ?? []) {
      if (!file.path) continue;
      const row = byPath.get(file.path) ?? { path: file.path, added: 0, removed: 0, files: [] };
      row.added += file.added;
      row.removed += file.removed;
      row.files.push(file);
      byPath.set(file.path, row);
    }
  }
  return [...byPath.values()];
}

function shortFile(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length > 3 ? `…/${parts.slice(-3).join("/")}` : path;
}

function ChangedFiles({ turn }: { turn: TurnItem }) {
  const rows = useMemo(() => changedFiles(turn), [turn]);
  const [openPath, setOpenPath] = useState<string | null>(null);
  if (rows.length === 0) return null;
  const added = rows.reduce((sum, row) => sum + row.added, 0);
  const removed = rows.reduce((sum, row) => sum + row.removed, 0);
  return <section aria-label="Changed files" className="mt-3 overflow-hidden rounded-xl border border-border">
    <div className="flex items-center gap-2 border-b border-border bg-card px-3 py-2 text-sm">
      <span className="font-medium text-foreground">Changed {rows.length} {rows.length === 1 ? "file" : "files"}</span>
      <DiffStat added={added} removed={removed} />
    </div>
    <ul>
      {rows.map((row) => <li key={row.path} className="border-b border-border last:border-b-0">
        <button type="button" aria-expanded={openPath === row.path} onClick={() => setOpenPath(openPath === row.path ? null : row.path)}
          className="flex w-full items-center gap-2 px-3 py-1.5 text-left font-mono text-xs hover:bg-secondary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" title={row.path}>
          <ChevronRight aria-hidden className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform", openPath === row.path && "rotate-90")} />
          <span className="min-w-0 flex-1 truncate text-foreground">{shortFile(row.path)}</span>
          <DiffStat added={row.added} removed={row.removed} />
        </button>
        {openPath === row.path && <div className="px-3 pb-3"><DiffView files={row.files.map((file) => ({ ...file, path: "" }))} /></div>}
      </li>)}
    </ul>
  </section>;
}

function answerText(turn: TurnItem): string {
  return turn.blocks.filter((block): block is TextBlock => block.kind === "text").map((block) => block.text.trim()).filter(Boolean).join("\n\n");
}

function pendingLabel(block: ToolBlock): { icon: ReactNode; text: string } | null {
  if (block.question && !block.question.closed) return { icon: <MessageCircleQuestion className="h-3.5 w-3.5" />, text: "Waiting for your answer below" };
  if (block.approval && block.approval.decision === null) {
    return { icon: <ShieldAlert className="h-3.5 w-3.5" />, text: `Waiting for approval — ${block.approval.summary || block.name}` };
  }
  return null;
}

const TurnView = memo(function TurnView({ turn }: { turn: TurnItem }) {
  const t = useT();
  const lang = t("trace_report.locale");
  const running = turn.status === "running";
  const timeline = useMemo(() => buildTimeline(turn.blocks, { t, lang, status: turn.status, live: true }), [turn.blocks, t, lang, turn.status]);
  const lastActivity = [...timeline.entries].reverse().find((entry) => entry.kind === "activity")?.id;
  const answer = answerText(turn);
  return <div className="space-y-1.5" data-testid="thread-turn" data-status={turn.status}>
    {timeline.entries.map((entry) => {
      if (entry.kind === "activity") return <ActivityGroup key={entry.id} entry={entry} live={running && entry.id === lastActivity} />;
      if (entry.kind === "prose") {
        if (entry.tone === "reasoning") return <ThoughtRow key={entry.id} block={entry.block as ReasoningBlock} />;
        return <div key={entry.id} className={PROSE}><ChatMarkdown text={entry.text} /></div>;
      }
      const block = entry.block;
      if (block.kind === "text") return <div key={entry.id} className={PROSE}><ChatMarkdown text={block.text} /></div>;
      if (block.kind === "reasoning") {
        return block.live && running ? <LiveThinking key={entry.id} block={block} /> : block.text.trim() ? <ThoughtRow key={entry.id} block={block} /> : null;
      }
      const pending = pendingLabel(block);
      return pending ? <WorkRow key={entry.id} icon={pending.icon} label={pending.text} live /> : null;
    })}
    {running
      ? <WorkRow icon={<span className="h-1.5 w-1.5 rounded-full bg-accent animate-jarvis-pulse" />} label={<>Working for <Elapsed since={turn.startedMs} /></>} live />
      : <>
        {turn.status === "error" && turn.error && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{turn.error}</p>}
        <ChangedFiles turn={turn} />
        <div className="flex items-center gap-1.5 pt-1 text-xs text-muted-foreground">
          <span>
            {turn.status === "cancelled" ? "Stopped" : turn.status === "error" ? "Failed" : answer ? "" : "Finished without an answer"}
            {turn.durationMs != null && <>{turn.status === "done" && answer ? "Worked for " : " after "}{traceDuration(turn.durationMs)}</>}
          </span>
          {answer && <CopyButton text={answer} label="Copy answer" />}
        </div>
      </>}
  </div>;
});

function ErrorLine({ text }: { text: string }) {
  return <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{text}</p>;
}

/** One item of the conversation, drawn by its kind. */
function ItemView({ item }: { item: TimelineItem }) {
  if (item.type === "user") return <UserBubble item={item} />;
  if (item.type === "turn") return <TurnView turn={item} />;
  if (item.type === "error") return <ErrorLine text={item.text} />;
  if (item.type === "notice") {
    return <p className="text-center text-xs text-muted-foreground">{item.agentName ? `${item.agentName}: ` : ""}{item.text}</p>;
  }
  return <div className="rounded-xl border border-border px-3 py-2 text-sm">
    <p className="mb-1 text-xs text-muted-foreground">Message from {item.message.sender_name}</p>
    <div className={PROSE}><ChatMarkdown text={item.message.text} /></div>
  </div>;
}

/**
 * The scrolling column. It follows the newest line while the reader is at
 * the bottom and stays put once they scroll up to read; a button brings them
 * back down.
 */
export function ThreadTimeline({ items, sessionId, bottomInset }: { items: TimelineItem[]; sessionId: string | null; bottomInset: number }) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const pinned = useRef(true);
  const [away, setAway] = useState(false);

  // A newly opened thread starts at its newest message.
  useLayoutEffect(() => {
    pinned.current = true;
    setAway(false);
    const box = scroller.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [sessionId]);

  useLayoutEffect(() => {
    const box = scroller.current;
    if (box && pinned.current) box.scrollTop = box.scrollHeight;
  });

  useEffect(() => {
    const box = scroller.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    // Content that grows without a re-render (an image loading) keeps the pin too.
    const observer = new ResizeObserver(() => { if (pinned.current) box.scrollTop = box.scrollHeight; });
    const content = box.firstElementChild;
    if (content) observer.observe(content);
    return () => observer.disconnect();
  }, []);

  return <div className="relative min-h-0 flex-1">
    <div ref={scroller} data-testid="thread-timeline"
      onScroll={(event) => {
        const box = event.currentTarget;
        const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
        pinned.current = atBottom;
        setAway(!atBottom);
      }}
      className="h-full overflow-y-auto scrollbar-jarvis">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-5 pt-6" style={{ paddingBottom: bottomInset + 24 }}>
        {items.map((item) => <ItemView key={item.id} item={item} />)}
      </div>
    </div>
    {away && <button type="button" aria-label="Scroll to the newest message"
      onClick={() => { const box = scroller.current; if (box) box.scrollTo({ top: box.scrollHeight, behavior: "smooth" }); }}
      style={{ bottom: bottomInset + 12 }}
      className="absolute left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-popover text-muted-foreground shadow-float hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <ArrowDown className="h-4 w-4" />
    </button>}
  </div>;
}
