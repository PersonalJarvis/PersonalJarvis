import { createContext, memo, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, Brain, Check, ChevronDown, ChevronRight, Copy, Diff, FileText, Hammer, MessageCircleQuestion, ShieldAlert, X } from "lucide-react";
import { ChatMarkdown } from "@/components/agentchat/ChatMarkdown";
import type { TextBlock, TimelineItem, ToolBlock, TurnBlock, TurnItem, UserItem } from "@/components/agentchat/reduce";
import { toolDiff, type DiffFile } from "@/components/agentchat/toolDiff";
import { CallMark, StretchMark } from "@/components/agentchat/TraceTimeline";
import { readableOutput, traceDuration, type Call } from "@/components/agentchat/traceEntries";
import { useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import { buildThreadRows, isFailed, type ThreadRow, type WorkGroup, type WorkItem } from "./threadWork";

/**
 * A thread's conversation: the person's messages on the right, the agent's
 * answer as plain reading text on the left, and the work in between as a
 * quiet log. Every thought the agent put into words reads as a paragraph of
 * its own, a step quieter than the answer. The tool calls between two
 * paragraphs are one group — "Edited files, ran commands" — that opens to a
 * short scrolling list of the single steps; the group the running turn is in
 * stays open and follows its newest step. A call opens to what it ran, the
 * diff it made or what came back. A running turn ends in "Working for 12s";
 * a finished one says how long it worked and which files it changed.
 *
 * The work-log geometry and the live shine are adapted from pingdotgg/t3code
 * @ e22c880 (apps/web/src/components/chat/WorkLog.tsx, MessagesTimeline.tsx),
 * MIT License, Copyright (c) 2026 T3 Tools Inc. — third_party/t3code/LICENSE.
 */

const PROSE = cn(
  "py-1 prose prose-neutral max-w-none text-base leading-6 text-foreground dark:prose-invert dark:text-foreground [overflow-wrap:anywhere]",
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

/**
 * One row of the work log, set like a line of the answer: body size, a 16 px
 * mark flush with the text's left edge, the words in the quiet tone, then a
 * trailing note. A row on its own gets the air of a paragraph; rows inside an
 * opened group sit tight. The words of a live row carry the shine. The
 * chevron of a row that opens shows only on hover, focus or while open, so a
 * finished log reads as plain grey lines between the agent's words.
 */
function WorkRow({
  icon,
  label,
  trailing,
  expanded,
  onToggle,
  grouped = false,
  live = false,
  stamp,
  ariaLabel,
  children,
}: {
  icon: ReactNode;
  label: ReactNode;
  trailing?: ReactNode;
  expanded?: boolean;
  onToggle?: () => void;
  grouped?: boolean;
  live?: boolean;
  /** When the step began; shown while the row is hovered. */
  stamp?: number;
  ariaLabel?: string;
  children?: ReactNode;
}) {
  const line = <span className={cn("flex min-h-6 min-w-0 items-center gap-2.5 leading-6", grouped ? "text-sm" : "text-base")}>
    <span aria-hidden className="flex h-6 w-4 shrink-0 items-center justify-center text-muted-foreground [&_svg]:h-4 [&_svg]:w-4">{icon}</span>
    <span className={cn(
      "min-w-0 truncate transition-colors",
      "text-muted-foreground",
      onToggle && !live && "group-hover/row:text-foreground",
      live && "live-tool-shine",
    )}>{label}</span>
    {trailing}
    {onToggle && <span aria-hidden className="flex h-4 w-4 shrink-0 items-center justify-center">
      <ChevronRight className={cn(
        "h-3.5 w-3.5 text-muted-foreground opacity-0 transition duration-200 group-hover/row:opacity-70 group-focus-visible/row:opacity-70",
        expanded && "rotate-90 opacity-70",
      )} />
    </span>}
    {stamp ? <span className="ml-auto shrink-0 pl-2 text-xs tabular-nums text-muted-foreground opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-visible/row:opacity-100">{clock(stamp)}</span> : null}
  </span>;
  const row = cn("group/row relative block w-full min-w-0 rounded-md text-left", grouped ? "py-0" : "py-1.5");
  return <div className="min-w-0">
    {onToggle
      ? <button type="button" aria-expanded={expanded} aria-label={ariaLabel} onClick={onToggle}
        className={cn(row, "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring")}>{line}</button>
      : <div className={row}>{line}</div>}
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

/** Pictures a tool call brought back, by call id, and the text blocks that carried them. */
interface CallPictures {
  byCall: ReadonlyMap<string, string[]>;
  moved: ReadonlySet<string>;
}

const CallMedia = createContext<ReadonlyMap<string, string[]>>(new Map());

/**
 * The server posts each picture a tool returned (an image the agent viewed)
 * as its own `media-…` text right after that call. In a thread the picture
 * belongs to the call: it shows once the call's line is opened, never in the
 * answer. A picture after the agent's own prose stays where it is.
 */
export function callPictures(blocks: TurnBlock[]): CallPictures {
  const byCall = new Map<string, string[]>();
  const moved = new Set<string>();
  let call: string | null = null;
  for (const block of blocks) {
    if (block.kind === "tool") call = block.callId;
    else if (block.kind === "text" && block.id.startsWith("media-") && call) {
      byCall.set(call, [...(byCall.get(call) ?? []), block.text]);
      moved.add(block.id);
    } else if (block.kind === "text") call = null;
  }
  return { byCall, moved };
}

/** What a call opened to: its pictures, the diff it made, else what ran and what came back. */
function CallDetails({ call }: { call: Call }) {
  const block = call.block;
  const pictures = useContext(CallMedia).get(block.callId);
  const diff = useMemo(() => toolDiff(block.name, block.input, block.output), [block.name, block.input, block.output]);
  const output = readableOutput(block).trim();
  if (pictures?.length) {
    return <div className={cn("mb-1.5 ml-7 mt-0.5", PROSE, THREAD_MEDIA)}>
      {pictures.map((text, index) => <ChatMarkdown key={index} text={text} />)}
    </div>;
  }
  if (diff && diff.length > 0 && !block.isError) return <div className="mb-1.5 ml-7 mt-0.5"><DiffView files={diff} /></div>;
  const input = call.kind === "command" ? "" : inputText(block.input);
  return <div className="mb-1.5 ml-7 mt-0.5 space-y-1.5 select-text">
    {call.kind === "command" && <pre className="m-0 overflow-x-auto rounded-lg bg-secondary px-3 py-2 font-mono text-xs leading-5 text-foreground scrollbar-jarvis">$ {inputText((block.input as Record<string, unknown> | null)?.command ?? call.text)}</pre>}
    {input && input !== "{}" && <pre className="m-0 max-h-48 overflow-auto rounded-lg bg-secondary px-3 py-2 font-mono text-xs leading-5 text-muted-foreground scrollbar-jarvis">{input}</pre>}
    {output
      ? <pre className={cn("m-0 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-border px-3 py-2 font-mono text-xs leading-5 scrollbar-jarvis", block.isError ? "text-destructive" : "text-foreground-secondary")}>{output}</pre>
      : block.output === null ? null : <p className="text-xs text-muted-foreground">No output.</p>}
  </div>;
}

/** Opening a step inside a group lifts the group's height cap. */
type OpenChange = (open: boolean) => void;

function useDisclosure(onOpenChange?: OpenChange): [boolean, () => void] {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => {
    setOpen(!open);
    onOpenChange?.(!open);
  }, [open, onOpenChange]);
  return [open, toggle];
}

function CallRow({ call, stamp, grouped = false, onOpenChange }: { call: Call; stamp?: number; grouped?: boolean; onOpenChange?: OpenChange }) {
  const [open, toggle] = useDisclosure(onOpenChange);
  const failed = isFailed(call);
  const running = call.status === "running";
  const detail = call.detail || call.result;
  const tail = failed && call.reason ? call.reason : call.status === "interrupted" ? "interrupted" : "";
  // The shine paints the label's own text, so a running row is one run of words.
  const label = running
    ? [call.text, detail].filter(Boolean).join("  ")
    : <>
      <span>{call.text}</span>
      {detail && <span className="ml-1.5 text-muted-foreground/70">{detail}</span>}
      {tail && <span className="ml-1.5 text-muted-foreground/70">— {tail}</span>}
    </>;
  return <WorkRow
    icon={<span className={cn("flex items-center justify-center", failed && "text-destructive/70")}><CallMark call={call} /></span>}
    label={label}
    ariaLabel={failed ? `${call.text}, failed` : undefined}
    trailing={<>
      <DiffStat added={call.added} removed={call.removed} />
      {failed && <X aria-hidden className="h-3 w-3 shrink-0 text-destructive/70" />}
    </>}
    stamp={stamp}
    grouped={grouped}
    live={running}
    expanded={open}
    onToggle={toggle}>
    <CallDetails call={call} />
  </WorkRow>;
}

const THOUGHT_PROSE = cn(PROSE, "text-foreground-secondary dark:text-foreground-secondary prose-p:text-foreground-secondary prose-li:text-foreground-secondary");

/** A thought in the agent's own words: a paragraph between its work, a step quieter than the answer. */
function ThoughtView({ text }: { text: string }) {
  return <div className={THOUGHT_PROSE} data-testid="thread-thought"><ChatMarkdown text={text} /></div>;
}

/** The wordless thought the running turn is having: one live "Thinking" step. */
function ThinkingRow({ grouped = false }: { grouped?: boolean }) {
  return <WorkRow icon={<Brain strokeWidth={1.75} />} label="Thinking" grouped={grouped} live />;
}

/**
 * A picture in the agent's words — an image it viewed or made — reads as a
 * small preview in the thread; a click opens it full size.
 */
const THREAD_MEDIA = "[&_[data-kind=image]_img]:max-h-48 [&_[data-kind=image]_img]:max-w-sm [&_[data-kind=image]_img]:border [&_[data-kind=image]_img]:border-border";

function StepRow({ item, running, grouped, onOpenChange }: { item: WorkItem; running: boolean; grouped?: boolean; onOpenChange?: OpenChange }) {
  if (item.kind === "call") return <CallRow call={item.call} stamp={item.startedMs} grouped={grouped} onOpenChange={onOpenChange} />;
  return running ? <ThinkingRow grouped={grouped} /> : null;
}

const FADE = "1.5rem";

/**
 * An opened group's steps: a short scrolling list that fades at an edge with
 * more behind it. A live list follows its newest step while the reader is at
 * its end; opening a step lifts the height cap so its detail reads in full.
 */
function StepList({ items, running, follow }: { items: WorkItem[]; running: boolean; follow: boolean }) {
  const box = useRef<HTMLDivElement | null>(null);
  const atEnd = useRef(true);
  const [edges, setEdges] = useState({ top: false, bottom: false });
  const [openSteps, setOpenSteps] = useState(0);
  const onOpenChange = useCallback((open: boolean) => setOpenSteps((count) => Math.max(0, count + (open ? 1 : -1))), []);

  const measure = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const rest = el.scrollHeight - el.clientHeight - el.scrollTop;
    atEnd.current = rest <= 1;
    const next = { top: el.scrollTop > 1, bottom: rest > 1 };
    setEdges((prev) => prev.top === next.top && prev.bottom === next.bottom ? prev : next);
  }, []);

  useLayoutEffect(() => {
    const el = box.current;
    if (el && follow && atEnd.current) el.scrollTop = el.scrollHeight;
    measure();
  }, [items.length, follow, measure]);

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, [measure]);

  const mask = edges.top || edges.bottom
    ? `linear-gradient(to bottom, ${edges.top ? "transparent" : "black"} 0, black ${FADE}, black calc(100% - ${FADE}), ${edges.bottom ? "transparent" : "black"} 100%)`
    : undefined;
  return <div ref={box} role="region" aria-label="Steps" tabIndex={-1} onScroll={measure}
    className="overflow-y-auto overflow-x-hidden rounded-md scrollbar-jarvis"
    style={{ maxHeight: openSteps > 0 ? undefined : "min(18rem, 50dvh)", maskImage: mask, WebkitMaskImage: mask }}>
    <div className="flex min-w-0 flex-col">
      {items.map((item) => <StepRow key={item.id} item={item} running={running} grouped onOpenChange={onOpenChange} />)}
    </div>
  </div>;
}

function hasBrand(call: Call): boolean {
  return Boolean(call.brand) && (call.kind === "command" || call.kind === "service");
}

/** A group's mark: the logos it touched, else its one kind of work, else a hammer for mixed work. */
function GroupMark({ group }: { group: WorkGroup }) {
  if (group.sole === "thought") return <Brain strokeWidth={1.75} />;
  const calls = group.items.flatMap((item) => item.kind === "call" ? [item.call] : []);
  if (calls.some(hasBrand)) return <StretchMark calls={calls} />;
  if (group.sole) return <CallMark call={calls[0]} />;
  return <Hammer strokeWidth={1.75} />;
}

/**
 * The calls between two paragraphs: a lone step on its own line, else one
 * line naming what they did that opens to every step. The group the running
 * turn is in opens by itself and follows its newest step; the reader can
 * still close it.
 */
function WorkGroupView({ group, running }: { group: WorkGroup; running: boolean }) {
  const [open, setOpen] = useState(group.live);
  if (group.items.length === 1) return <StepRow item={group.items[0]} running={running} />;
  return <WorkRow
    icon={<GroupMark group={group} />}
    label={group.summary}
    ariaLabel={group.live ? `${group.summary}, ${group.items.length} steps so far` : undefined}
    trailing={<DiffStat added={group.added} removed={group.removed} />}
    stamp={group.startedMs}
    live={group.live}
    expanded={open}
    onToggle={() => setOpen((value) => !value)}>
    <StepList items={group.items} running={running} follow={group.live} />
  </WorkRow>;
}

/**
 * The running turn's clock. It writes its own text once a second, so the
 * timeline never re-renders to tick.
 */
function Elapsed({ since }: { since: number }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    const tick = () => { if (ref.current) ref.current.textContent = traceDuration(Math.max(0, Date.now() - since)); };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [since]);
  return <span ref={ref} className="tabular-nums">{traceDuration(Math.max(0, Date.now() - since))}</span>;
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
    <div className="max-w-[80%] rounded-2xl bg-secondary px-4 py-2.5 text-foreground">
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

/** The thread's project folder, so changed files read relative to it. */
const ThreadFolder = createContext("");

function slashes(path: string): string {
  return path.replace(/\\/g, "/");
}

/**
 * A changed file as the project sees it: relative to the thread's folder,
 * else (an absolute path elsewhere) its last three parts.
 */
export function projectPath(path: string, root: string): string {
  const clean = slashes(path);
  const base = slashes(root).replace(/\/+$/, "");
  if (base && clean.toLowerCase().startsWith(`${base.toLowerCase()}/`)) return clean.slice(base.length + 1);
  const absolute = clean.startsWith("/") || /^[A-Za-z]:\//.test(clean);
  const parts = clean.split("/").filter(Boolean);
  return absolute && parts.length > 3 ? `…/${parts.slice(-3).join("/")}` : clean;
}

/** A path split into its folder (read quieter) and its file name. */
function splitPath(path: string): { folder: string; name: string } {
  const clean = slashes(path);
  const cut = clean.lastIndexOf("/");
  return cut < 0 ? { folder: "", name: clean } : { folder: clean.slice(0, cut + 1), name: clean.slice(cut + 1) };
}

/** How many files a finished turn lists before the rest fold behind "Show N more files". */
export const CHANGED_FILES_FOLDED = 3;

/** Both counts, even a zero — the card's columns line up on them. */
function ChangeCount({ added, removed, className }: { added: number; removed: number; className?: string }) {
  return <span className={cn("shrink-0 tabular-nums", className)}>
    <span className="text-success">+{added}</span> <span className="text-destructive">-{removed}</span>
  </span>;
}

/**
 * The card under a finished turn: how many files it edited and by how much,
 * then one row per file — folder quiet, name bright, counts on the right.
 * Only the first few rows show; the rest fold behind "Show N more files" so
 * a big turn never fills the screen. A row opens to its own diff, and
 * "Show changes" opens every diff at once.
 */
export function ChangedFiles({ turn }: { turn: TurnItem }) {
  const root = useContext(ThreadFolder);
  const rows = useMemo(() => changedFiles(turn), [turn]);
  const [openPaths, setOpenPaths] = useState<ReadonlySet<string>>(() => new Set());
  const [expanded, setExpanded] = useState(false);
  if (rows.length === 0) return null;
  const added = rows.reduce((sum, row) => sum + row.added, 0);
  const removed = rows.reduce((sum, row) => sum + row.removed, 0);
  const hidden = Math.max(0, rows.length - CHANGED_FILES_FOLDED);
  const shown = expanded ? rows : rows.slice(0, CHANGED_FILES_FOLDED);
  const allOpen = rows.every((row) => openPaths.has(row.path));
  const togglePath = (path: string) => setOpenPaths((prev) => {
    const next = new Set(prev);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });
  const toggleAll = () => {
    if (allOpen) { setOpenPaths(new Set()); return; }
    setOpenPaths(new Set(rows.map((row) => row.path)));
    setExpanded(true);
  };
  const title = `${rows.length} ${rows.length === 1 ? "file" : "files"} changed`;
  return <section aria-label="Changed files" data-testid="thread-changed-files" className="mt-3 overflow-hidden rounded-xl border border-border bg-card">
    <div className="flex items-center gap-3 border-b border-border px-3 py-3">
      <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-secondary text-foreground-secondary">
        <span className="flex h-5 w-5 items-center justify-center rounded-[5px] border-[1.5px] border-current"><Diff className="h-3 w-3" strokeWidth={2.25} /></span>
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-foreground-strong">{title}</div>
        <ChangeCount added={added} removed={removed} className="text-sm" />
      </div>
      <button type="button" aria-pressed={allOpen} onClick={toggleAll}
        className="shrink-0 rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {allOpen ? "Hide changes" : "Show changes"}
      </button>
    </div>
    <ul>
      {shown.map((row) => {
        const { folder, name } = splitPath(projectPath(row.path, root));
        const open = openPaths.has(row.path);
        return <li key={row.path}>
          <button type="button" aria-expanded={open} onClick={() => togglePath(row.path)} title={row.path}
            className="flex w-full items-center gap-3 px-3 py-1.5 text-left text-sm hover:bg-secondary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
            {/* The folder gives way first, cut from its left, so the name stays readable. */}
            <span className="flex min-w-0 flex-1">
              {folder && <span dir="rtl" className="min-w-0 shrink-[999] truncate text-left text-muted-foreground">
                <bdi dir="ltr">{folder}</bdi>
              </span>}
              <span className="min-w-0 max-w-full shrink-0 truncate text-foreground">{name}</span>
            </span>
            <ChangeCount added={row.added} removed={row.removed} />
          </button>
          {open && <div className="px-3 pb-2 pt-0.5"><DiffView files={row.files.map((file) => ({ ...file, path: "" }))} /></div>}
        </li>;
      })}
    </ul>
    {hidden > 0 && <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}
      className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-sm text-foreground-secondary hover:bg-secondary/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
      {expanded ? "Collapse files" : `Show ${hidden} more ${hidden === 1 ? "file" : "files"}`}
      <ChevronDown aria-hidden className={cn("h-4 w-4 transition-transform duration-200", expanded && "rotate-180")} />
    </button>}
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

/**
 * A finished turn read the short way: everything up to its last step folds
 * behind one "Worked for …" line, and the text written after that step — the
 * answer — stays in view. A turn with no step at all has nothing to fold. A
 * card the turn ended on (an end-of-turn question) is no step: it stays with
 * the answer it belongs to.
 */
export function foldFinished(rows: ThreadRow[]): { work: ThreadRow[]; answer: ThreadRow[] } {
  let last = -1;
  rows.forEach((row, index) => { if (row.kind === "work" || row.kind === "thought") last = index; });
  return { work: rows.slice(0, last + 1), answer: rows.slice(last + 1) };
}

const TurnView = memo(function TurnView({ turn }: { turn: TurnItem }) {
  const t = useT();
  const lang = t("trace_report.locale");
  const running = turn.status === "running";
  const pictures = useMemo(() => callPictures(turn.blocks), [turn.blocks]);
  const rows = useMemo(
    () => buildThreadRows(turn.blocks, { t, lang, status: turn.status }).filter((row) => !(row.kind === "text" && pictures.moved.has(row.block.id))),
    [turn.blocks, t, lang, turn.status, pictures],
  );
  const answer = answerText(turn);
  const [workOpen, setWorkOpen] = useState(false);
  // Finished: every step and thought folds behind one "Worked for …" line; the answer stays.
  const { work, answer: tail } = useMemo(() => (running ? { work: rows, answer: [] } : foldFinished(rows)), [rows, running]);
  const folded = !running && work.length > 0;
  const duration = turn.durationMs != null ? traceDuration(turn.durationMs) : "";
  const foldLabel = turn.status === "cancelled" ? `Stopped${duration ? ` after ${duration}` : ""}`
    : turn.status === "error" ? `Failed${duration ? ` after ${duration}` : ""}`
      : `Worked${duration ? ` for ${duration}` : ""}`;
  const renderRow = (row: ThreadRow) => {
    if (row.kind === "work") return <WorkGroupView key={row.id} group={row} running={running} />;
    if (row.kind === "thought") return <ThoughtView key={row.id} text={row.text} />;
    if (row.kind === "text") return <div key={row.id} className={cn(PROSE, THREAD_MEDIA)}><ChatMarkdown text={row.text} /></div>;
    const pending = pendingLabel(row.block);
    return pending ? <WorkRow key={row.id} icon={pending.icon} label={pending.text} live /> : null;
  };
  return <CallMedia.Provider value={pictures.byCall}><div className="space-y-1.5" data-testid="thread-turn" data-status={turn.status}>
    {folded && <div>
      <button type="button" aria-expanded={workOpen} onClick={() => setWorkOpen((value) => !value)} data-testid="thread-worked-for"
        className="flex h-7 items-center gap-1 rounded-md px-1 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span className="tabular-nums">{foldLabel}</span>
        <ChevronRight aria-hidden className={cn("h-3.5 w-3.5 transition-transform duration-150", workOpen && "rotate-90")} />
      </button>
      {workOpen && <div className="space-y-1.5 pb-2 pt-1" data-testid="thread-worked-steps">{work.map(renderRow)}</div>}
      <div aria-hidden className="mt-1 border-b border-border/70" />
    </div>}
    {(folded ? tail : rows).map(renderRow)}
    {running
      ? <div className="flex h-6 min-w-0 items-center px-1 text-sm leading-relaxed text-muted-foreground" data-testid="thread-working">
        <span className="whitespace-nowrap">Working for <Elapsed since={turn.startedMs} /></span>
      </div>
      : <>
        {turn.status === "error" && turn.error && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{turn.error}</p>}
        <ChangedFiles turn={turn} />
        <div className="flex items-center gap-1.5 pt-1 text-xs text-muted-foreground">
          {!folded && <span>
            {turn.status === "cancelled" ? "Stopped" : turn.status === "error" ? "Failed" : answer ? "" : "Finished without an answer"}
            {duration && <>{turn.status === "done" && answer ? "Worked for " : " after "}{duration}</>}
          </span>}
          {folded && !answer && <span>Finished without an answer</span>}
          {answer && <CopyButton text={answer} label="Copy answer" />}
        </div>
      </>}
  </div></CallMedia.Provider>;
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
export function ThreadTimeline({ items, sessionId, bottomInset, folder = "" }: {
  items: TimelineItem[]; sessionId: string | null; bottomInset: number;
  /** The thread's project folder; changed files list relative to it. */
  folder?: string;
}) {
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

  return <ThreadFolder.Provider value={folder}><div className="relative min-h-0 flex-1">
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
  </div></ThreadFolder.Provider>;
}
