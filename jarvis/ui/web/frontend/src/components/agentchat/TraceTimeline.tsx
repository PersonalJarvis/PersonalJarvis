import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, ChevronRight, Copy, FilePlus2, FileText, FolderOpen, Globe, Image, Pencil, Search, SquareTerminal, Wrench } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { ToolBlock, TurnBlock, TurnStatus } from "./reduce";
import { ToolChoiceIcon } from "./ToolChoiceChips";
import type { ToolChoice } from "./toolChoices";
import { toolIdentity, toolIdentityStyle } from "./toolIdentity";
import { traceToolIdentity } from "./traceActivity";
import {
  buildTimeline, plural, readableOutput, summarize, traceDuration, tr,
  type ActivityEntry, type Call, type ProseEntry, type Timeline,
} from "./traceEntries";

/**
 * The renderer of a turn's timeline (model: traceEntries.ts), drawn with
 * no thread, no bullets and no numbered rings.
 * The model's words are prose; each stretch of calls is one quiet line with
 * the stretch's real mark — a plugin's logo, a CLI vendor's logo, else one
 * plain glyph — that opens to the single calls, and a call opens to what it
 * ran and what came back.
 */

/** One item in the trace; `rail` is kept for the live rows that still hang on one. */
export type RailItem = { key: string; node: ReactNode; rail: boolean };

export interface TimelineRenderers {
  /** Approvals, question cards, a streaming thought, a live reply. */
  renderLive: (block: TurnBlock) => ReactNode;
  liveOnRail: (block: TurnBlock) => boolean;
  /** Narration the model wrote between calls, in the reply's own type. */
  renderNarration: (text: string, id: string) => ReactNode;
  /** A non-command call's raw input, diff and output. */
  renderDetails: (block: ToolBlock) => ReactNode;
}

export function useTimeline(blocks: TurnBlock[], status: TurnStatus, live: boolean): Timeline {
  const t = useT();
  // The locale's own tag, read through the dictionary, so plurals follow
  // the language the lines are written in.
  const lang = t("trace_report.locale");
  return useMemo(() => buildTimeline(blocks, { t, lang, status, live }), [blocks, t, lang, status, live]);
}

export function timelineItems(timeline: Timeline, renderers: TimelineRenderers): RailItem[] {
  return timeline.entries.map((entry): RailItem => {
    if (entry.kind === "live") return { key: entry.id, rail: renderers.liveOnRail(entry.block), node: renderers.renderLive(entry.block) };
    if (entry.kind === "prose") return { key: entry.id, rail: false, node: <ProseView entry={entry} renderers={renderers} /> };
    return { key: entry.id, rail: false, node: <ActivityView entry={entry} renderers={renderers} /> };
  });
}

// ── Marks ────────────────────────────────────────────────────────────────

const glyph = "h-[15px] w-[15px] shrink-0";
const GLYPHS = { command: SquareTerminal, read: FileText, list: FolderOpen, search: Search, edit: Pencil, write: FilePlus2, image: Image, web: Globe } as const;

function cliRow(binary: string): ToolChoice {
  return {
    id: `cli:${binary}`, label: binary, brand: "", group: "", skill: "", category: "cli",
    description: "", available: true, tool_names: [],
  };
}

/** A brand drawn the way the composer draws it: the real logo, themed. */
function BrandMark({ row }: { row: ToolChoice }) {
  return <span className="tool-identity inline-flex shrink-0" style={toolIdentityStyle(row)} data-trace-brand={row.id}>
    <ToolChoiceIcon row={row} size={15} />
  </span>;
}

/** The row a call's brand comes from, when it has one. */
function brandRow(call: Call): ToolChoice | null {
  if (call.kind === "command") return call.brand ? cliRow(call.brand) : null;
  if (call.kind === "service" && call.brand) return traceToolIdentity(call.block).row;
  return null;
}

function CallMark({ call }: { call: Call }) {
  const row = brandRow(call);
  if (row) return <BrandMark row={row} />;
  if (call.kind in GLYPHS) {
    const Glyph = GLYPHS[call.kind as keyof typeof GLYPHS];
    return <Glyph aria-hidden className={glyph} strokeWidth={1.75} />;
  }
  // Jarvis's own families wear the composer's mark for them; a tool nobody
  // described gets a plain wrench, never a plug that suggests an integration.
  if (call.kind === "family") {
    const Glyph = traceToolIdentity(call.block).identity.Glyph;
    return <Glyph aria-hidden className={glyph} strokeWidth={1.75} />;
  }
  return <Wrench aria-hidden className={glyph} strokeWidth={1.75} />;
}

/** A stretch's mark: up to three brand logos it touched, else its first call's glyph. */
function StretchMark({ calls }: { calls: Call[] }) {
  const rows: ToolChoice[] = [];
  const seen = new Set<string>();
  for (const call of calls) {
    const row = brandRow(call);
    // One mark per brand, however many tools of it the stretch used.
    const key = row ? toolIdentity(row).logo ?? row.id : "";
    if (!row || seen.has(key)) continue;
    seen.add(key);
    rows.push(row);
    if (rows.length === 3) break;
  }
  // No brand: the stretch's most telling call speaks for it.
  if (!rows.length) return <CallMark call={calls.find((call) => call.kind !== "tool") ?? calls[0]} />;
  return <span aria-hidden className="flex shrink-0 items-center">
    {rows.map((row, i) => <span key={row.id} className={cn("inline-flex rounded-full bg-background ring-2 ring-background", i > 0 && "-ml-1")}><BrandMark row={row} /></span>)}
  </span>;
}

// ── Prose ────────────────────────────────────────────────────────────────

function ProseView({ entry, renderers }: { entry: ProseEntry; renderers: TimelineRenderers }) {
  // Media in narration stays a thumbnail: the trace is the story, not the gallery.
  if (entry.tone === "narration") return <div className="trace-narration min-w-0 py-1.5" data-trace-entry="narration">{renderers.renderNarration(entry.text, entry.id)}</div>;
  return <Reasoning text={entry.text} />;
}

/** Reasoning text reads quieter than narration and folds when long. */
function Reasoning({ text }: { text: string }) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setOverflows(el.scrollHeight > el.clientHeight + 1);
  }, [text, open]);
  return <div className="min-w-0 py-1.5" data-trace-entry="reasoning">
    <div ref={ref} className={cn(
      "prose prose-sm max-w-none text-sm leading-6 text-muted-foreground dark:prose-invert [overflow-wrap:anywhere]",
      "prose-p:my-1 prose-p:text-muted-foreground prose-li:text-muted-foreground prose-strong:text-foreground-secondary prose-pre:overflow-auto",
      "[&>:first-child]:mt-0 [&>:last-child]:mb-0",
      !open && "max-h-36 overflow-hidden",
      !open && overflows && "trace-thought-fade",
    )}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
    {overflows || open ? <button type="button" onClick={() => setOpen(!open)}
      className="mt-0.5 rounded-sm text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      {t(open ? "trace_report.show_less" : "trace_report.show_more")}
    </button> : null}
  </div>;
}

// ── Activity ─────────────────────────────────────────────────────────────

const lineButton = "group/line flex w-full min-w-0 items-center gap-2 rounded-md py-1 text-left text-sm leading-6 text-muted-foreground transition-colors enabled:hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default";

function Chevron({ open }: { open: boolean }) {
  return <ChevronRight aria-hidden className={cn("h-3.5 w-3.5 shrink-0 opacity-0 transition group-hover/line:opacity-70 group-focus-visible/line:opacity-70", open && "rotate-90 opacity-70")} />;
}

function ActivityView({ entry, renderers }: { entry: ActivityEntry; renderers: TimelineRenderers }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const running = entry.calls.filter((call) => call.status === "running");
  const settled = entry.calls.filter((call) => call.status !== "running");
  if (entry.calls.length === 1) return <CallLine call={entry.calls[0]} renderers={renderers} />;
  const problems = settled.filter((call) => call.status !== "done").length;
  const lang = t("trace_report.locale");
  return <div className="min-w-0" data-trace-entry="activity">
    {settled.length ? <button type="button" className={lineButton} aria-expanded={open} onClick={() => setOpen(!open)}>
      <StretchMark calls={settled} />
      <span className="min-w-0 truncate">{settled.length === entry.calls.length ? entry.summary : summarize(settled, t, lang)}</span>
      {problems ? <span className="shrink-0 text-destructive">{tr(t, `failed_count_${plural(lang, problems)}`, { count: problems })}</span> : null}
      <Chevron open={open} />
    </button> : null}
    {open ? <div className="mb-1 ml-[7px] min-w-0 border-l border-border pl-4">
      {settled.map((call) => <CallLine key={call.id} call={call} renderers={renderers} />)}
    </div> : null}
    {running.map((call) => <CallLine key={call.id} call={call} renderers={renderers} />)}
  </div>;
}

function CallLine({ call, renderers }: { call: Call; renderers: TimelineRenderers }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const running = call.status === "running";
  const problem = call.status === "failed" || call.status === "blocked";
  const quiet = call.status === "declined" || call.status === "interrupted";
  return <div className="min-w-0" data-trace-entry="call" data-kind={call.kind} data-status={call.status}>
    <button type="button" className={lineButton} aria-expanded={running ? undefined : open} disabled={running} onClick={() => setOpen(!open)}>
      <CallMark call={call} />
      <span className={cn("min-w-0 truncate", running && "trace-shimmer")} title={call.text}>{call.text}</span>
      {call.detail ? <span className="min-w-0 truncate text-muted-foreground/70" title={call.detail}>{call.detail}</span> : null}
      {call.added || call.removed ? <span className="shrink-0 font-mono text-xs tabular-nums">
        <span className="diff-count-add">+{call.added}</span>{" "}<span className="diff-count-del">−{call.removed}</span>
      </span> : null}
      {problem || quiet ? <span className={cn("shrink-0", problem ? "text-destructive" : "text-muted-foreground")}>{tr(t, `state.${call.status}`)}</span> : null}
      {!running ? <Chevron open={open} /> : null}
    </button>
    {/* Why a call failed reads without opening anything. */}
    {problem && call.reason && !open ? <p data-trace-reason className="-mt-0.5 mb-1 truncate pl-[23px] text-xs leading-5 text-destructive" title={call.reason}>{call.reason}</p> : null}
    {open ? <div className="mb-2 ml-[7px] min-w-0 border-l border-border pl-4 pt-0.5"><CallDetails call={call} renderers={renderers} /></div> : null}
  </div>;
}

/** What a call ran and what came back. */
function CallDetails({ call, renderers }: { call: Call; renderers: TimelineRenderers }) {
  const t = useT();
  // The measured time lives here, not on the line: the line stays clean.
  const took = call.block.durationMs ? <p className="text-xs leading-5 tabular-nums text-muted-foreground">{tr(t, "took", { duration: traceDuration(call.block.durationMs) })}</p> : null;
  const why = call.reason ? <p className={cn("text-xs leading-5 [overflow-wrap:anywhere]", call.status === "failed" || call.status === "blocked" ? "text-destructive" : "text-muted-foreground")}>
    {tr(t, `state.${call.status}`)}: {call.reason}
  </p> : null;
  if (call.kind === "command") {
    const input = call.block.input && typeof call.block.input === "object" ? call.block.input as Record<string, unknown> : {};
    const full = typeof input.command === "string" ? input.command : typeof input.cmd === "string" ? input.cmd : call.text;
    const output = readableOutput(call.block);
    return <div className="space-y-1.5">
      <pre tabIndex={0} data-trace-output className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted/60 px-3 py-2 font-mono text-xs leading-5 text-foreground-secondary [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
        <span className="text-muted-foreground">$ </span>{full}{output ? `\n\n${output}` : ""}
      </pre>
      {why}
      {took}
    </div>;
  }
  return <div className="space-y-1.5">
    {call.result ? <p className="text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">{call.result}</p> : null}
    {why}
    {renderers.renderDetails(call.block)}
    {took}
  </div>;
}

/** "Copy" — the timeline as Markdown on the clipboard; shown when the fold is hovered. */
export function CopyTimeline({ text }: { text: () => string }) {
  const t = useT();
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copy = async () => {
    const ok = await robustCopy(text());
    setState(ok ? "copied" : "failed");
    window.setTimeout(() => setState("idle"), 2000);
  };
  const Icon = state === "copied" ? Check : Copy;
  return <button type="button" onClick={() => void copy()}
    className={cn("mt-1 inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-opacity hover:bg-secondary hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      state === "idle" ? "opacity-0 group-hover/fold-body:opacity-100" : "opacity-100")}>
    <Icon aria-hidden className="h-3.5 w-3.5" />
    <span aria-live="polite">{t(state === "copied" ? "trace_report.copied" : state === "failed" ? "trace_report.copy_failed" : "trace_report.copy")}</span>
  </button>;
}
