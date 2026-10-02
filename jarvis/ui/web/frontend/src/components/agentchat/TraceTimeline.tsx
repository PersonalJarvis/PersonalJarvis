import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, ChevronRight, CircleAlert, Copy, FilePenLine, FilePlus2, FolderSearch, ShieldX, Terminal } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { ToolBlock, TurnBlock, TurnStatus } from "./reduce";
import { ToolChoiceIcon } from "./ToolChoiceChips";
import { toolIdentityStyle } from "./toolIdentity";
import { traceToolIdentity } from "./traceActivity";
import {
  buildTimeline, plural, tr, traceDuration,
  type CommandEntry, type EditEntry, type EntryStatus, type ExploreEntry, type ThoughtEntry, type Timeline, type TimelineEntry, type ToolEntry,
} from "./traceEntries";

/**
 * The renderer of a turn's timeline (model: traceEntries.ts), drawn the
 * way Claude and Codex draw their traces: the model's words as prose, each
 * call one quiet line on the rail with what came of it underneath — the
 * first lines a command printed, the files an exploration read, the size of
 * an edit — and the raw call one tap away. Live pieces (a running call, an
 * approval, a question card, a streaming thought, a reply) are drawn by the
 * caller, so the live turn and its finished record read as one timeline.
 */

/** One item on a rail; `rail: false` breaks the thread (replies, question cards). */
export type RailItem = { key: string; node: ReactNode; rail: boolean };

export interface TimelineRenderers {
  /** A live piece: running call, approval, question card, streaming thought, reply. */
  renderLive: (block: TurnBlock) => ReactNode;
  /** Whether a live piece hangs on the thread (replies and question cards do not). */
  liveOnRail: (block: TurnBlock) => boolean;
  /** The raw call — input, diff, output — under a line's disclosure. */
  renderDetails: (block: ToolBlock) => ReactNode;
}

/** The timeline of a turn, recomputed when its blocks, state or language change. */
export function useTimeline(blocks: TurnBlock[], status: TurnStatus, live: boolean): Timeline {
  const t = useT();
  // The locale's own tag, read through the dictionary: plurals and lists
  // follow the language the lines are written in.
  const lang = t("trace_report.locale");
  return useMemo(() => buildTimeline(blocks, { t, lang, status, live }), [blocks, t, lang, status, live]);
}

export function timelineItems(timeline: Timeline, renderers: TimelineRenderers): RailItem[] {
  return timeline.entries.map((entry): RailItem => {
    if (entry.kind === "live") {
      return { key: entry.id, rail: renderers.liveOnRail(entry.block), node: renderers.renderLive(entry.block) };
    }
    return { key: entry.id, rail: true, node: <EntryView entry={entry} renderDetails={renderers.renderDetails} /> };
  });
}

function EntryView({ entry, renderDetails }: { entry: Exclude<TimelineEntry, { kind: "live" }>; renderDetails: (block: ToolBlock) => ReactNode }) {
  switch (entry.kind) {
    case "thought": return <ThoughtView entry={entry} />;
    case "explore": return <ExploreView entry={entry} renderDetails={renderDetails} />;
    case "command": return <CommandView entry={entry} renderDetails={renderDetails} />;
    case "edit": return <EditView entry={entry} renderDetails={renderDetails} />;
    case "tool": return <ToolView entry={entry} renderDetails={renderDetails} />;
  }
}

const rowButton = "group/trace flex w-full min-w-0 items-start gap-3 rounded-md py-1 text-left text-sm leading-6 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring enabled:hover:text-foreground disabled:cursor-default";
const glyph = "h-3.5 w-3.5 shrink-0";

/** One line on the rail: glyph, words, time; what came of it underneath; the raw call on tap. */
function Line({ icon, label, trailing, below, details, problem = false, testId }: {
  icon: ReactNode; label: ReactNode; trailing?: ReactNode; below?: ReactNode; details?: ReactNode; problem?: boolean; testId: string;
}) {
  const [open, setOpen] = useState(false);
  return <div className={cn("min-w-0", problem ? "text-destructive" : "text-muted-foreground")} data-trace-entry={testId}>
    <button type="button" className={rowButton} disabled={!details} aria-expanded={details ? open : undefined} onClick={() => setOpen(!open)}>
      <span aria-hidden className="trace-node">{icon}</span>
      <span className="flex min-w-0 flex-1 items-baseline gap-2">{label}</span>
      {trailing ? <span className="shrink-0 text-xs leading-6 tabular-nums text-muted-foreground">{trailing}</span> : null}
      {details ? <ChevronRight aria-hidden className={cn("mt-[5px] h-3.5 w-3.5 shrink-0 opacity-0 transition group-hover/trace:opacity-70 group-focus-visible/trace:opacity-70", open && "rotate-90 opacity-70")} /> : null}
    </button>
    {below ? <div className="min-w-0 pb-1 pl-7">{below}</div> : null}
    {details && open ? <div className="min-w-0 pb-1.5 pl-7">{details}</div> : null}
  </div>;
}

/** The verb of a line, then its object in the mono face. */
function Words({ verb, object, problem = false }: { verb: string; object?: string; problem?: boolean }) {
  return <>
    <span className={cn("shrink-0", problem ? "text-destructive" : "text-foreground-secondary")}>{verb}</span>
    {object ? <span className="min-w-0 truncate font-mono text-[12.5px] text-muted-foreground" title={object}>{object}</span> : null}
  </>;
}

function problemGlyph(status: EntryStatus): ReactNode | null {
  if (status === "blocked" || status === "declined") return <ShieldX className={glyph} />;
  if (status === "failed" || status === "interrupted") return <CircleAlert className={glyph} />;
  return null;
}

/** "Failed: …", "Blocked: a safety rule …" — said once, under the line. */
function Problem({ status, reason }: { status: EntryStatus; reason: string }) {
  const t = useT();
  if (status === "done") return null;
  return <p className={cn("text-xs leading-5 [overflow-wrap:anywhere]", status === "failed" || status === "blocked" ? "text-destructive" : "text-muted-foreground")}>
    {tr(t, `status.${status}`)}{reason ? `: ${reason}` : ""}
  </p>;
}

function duration(ms: number | null | undefined): string | undefined {
  return ms !== null && ms !== undefined && ms > 0 ? traceDuration(ms) : undefined;
}

function ThoughtView({ entry }: { entry: ThoughtEntry }) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setOverflows(el.scrollHeight > el.clientHeight + 1);
  }, [entry.text, open]);
  return <div className="flex min-w-0 gap-3 py-1" data-trace-entry="thought">
    <span aria-hidden className="trace-node"><span className="trace-dot" /></span>
    <div className="min-w-0 flex-1">
      <div ref={ref} className={cn(
        "prose prose-sm max-w-none text-sm leading-6 text-foreground-secondary dark:prose-invert [overflow-wrap:anywhere]",
        "prose-p:my-1 prose-p:text-foreground-secondary prose-li:text-foreground-secondary prose-strong:text-foreground prose-pre:overflow-auto",
        "[&>:first-child]:mt-0 [&>:last-child]:mb-0",
        !open && "max-h-36 overflow-hidden",
        !open && overflows && "trace-thought-fade",
      )}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.text}</ReactMarkdown>
      </div>
      {overflows || open ? <button type="button" onClick={() => setOpen(!open)}
        className="mt-0.5 rounded-sm text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {t(open ? "trace_report.show_less" : "trace_report.show_more")}
      </button> : null}
    </div>
  </div>;
}

function ExploreView({ entry, renderDetails }: { entry: ExploreEntry; renderDetails: (block: ToolBlock) => ReactNode }) {
  const t = useT();
  return <Line testId="explore" icon={<FolderSearch className={glyph} />}
    label={<Words verb={tr(t, "entry.explored")} />}
    trailing={duration(entry.durationMs)}
    below={<ul className="space-y-0.5 text-xs leading-5">
      {entry.lines.map((line, i) => <li key={i} className="flex min-w-0 gap-2">
        <span className="shrink-0 text-muted-foreground">{tr(t, `entry.${line.verb}`)}</span>
        <span className="min-w-0 truncate font-mono text-foreground-secondary" title={line.targets.join(", ")}>{line.targets.join(", ")}</span>
      </li>)}
    </ul>}
    details={<div className="space-y-3">{entry.blocks.map((block) => <div key={block.callId}>{renderDetails(block)}</div>)}</div>} />;
}

function CommandView({ entry, renderDetails }: { entry: CommandEntry; renderDetails: (block: ToolBlock) => ReactNode }) {
  const t = useT();
  const problem = entry.status === "failed" || entry.status === "blocked";
  return <Line testId="command" problem={problem}
    icon={problemGlyph(entry.status) ?? <Terminal className={glyph} />}
    label={<Words verb={tr(t, "entry.ran")} object={entry.command} problem={problem} />}
    trailing={duration(entry.block.durationMs)}
    below={entry.output || entry.status !== "done" ? <>
      {entry.output ? <div data-trace-output className="min-w-0 font-mono text-xs leading-5 text-muted-foreground">
        {entry.output.lines.map((line, i) => <div key={i} className="truncate whitespace-pre" title={line}>{line}</div>)}
        {entry.output.more ? <div className="text-muted-foreground/70">{tr(t, `entry.more_${plural("en", entry.output.more)}`, { count: entry.output.more })}</div> : null}
      </div> : null}
      <Problem status={entry.status} reason={entry.reason} />
    </> : undefined}
    details={renderDetails(entry.block)} />;
}

function EditView({ entry, renderDetails }: { entry: EditEntry; renderDetails: (block: ToolBlock) => ReactNode }) {
  const t = useT();
  const problem = entry.status === "failed" || entry.status === "blocked";
  return <Line testId="edit" problem={problem}
    icon={problemGlyph(entry.status) ?? (entry.verb === "write" ? <FilePlus2 className={glyph} /> : <FilePenLine className={glyph} />)}
    label={<>
      <Words verb={tr(t, entry.verb === "edit" ? "entry.edited" : "entry.created")} object={entry.path} problem={problem} />
      {entry.added || entry.removed ? <span className="shrink-0 font-mono text-xs tabular-nums">
        <span className="diff-count-add">+{entry.added}</span>{" "}<span className="diff-count-del">−{entry.removed}</span>
      </span> : null}
    </>}
    trailing={duration(entry.block.durationMs)}
    below={entry.status !== "done" ? <Problem status={entry.status} reason={entry.reason} /> : undefined}
    details={renderDetails(entry.block)} />;
}

function ToolView({ entry, renderDetails }: { entry: ToolEntry; renderDetails: (block: ToolBlock) => ReactNode }) {
  const view = traceToolIdentity(entry.block);
  const problem = entry.status === "failed" || entry.status === "blocked";
  const Glyph = view.identity.Glyph;
  const icon = problemGlyph(entry.status) ?? (view.identity.logo
    ? <span className="tool-identity inline-flex" style={toolIdentityStyle(view.row)} data-trace-brand={view.identity.key}><ToolChoiceIcon row={view.row} size={14} /></span>
    : <Glyph className={glyph} />);
  return <Line testId="tool" problem={problem} icon={icon}
    label={<Words verb={entry.label} object={entry.detail} problem={problem} />}
    trailing={duration(entry.block.durationMs)}
    below={entry.result || entry.status !== "done" ? <>
      {entry.result ? <p data-trace-result className="truncate text-xs leading-5 text-muted-foreground" title={entry.result}>{entry.result}</p> : null}
      <Problem status={entry.status} reason={entry.reason} />
    </> : undefined}
    details={renderDetails(entry.block)} />;
}

/** "Copy" — the timeline as Markdown on the clipboard. */
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
    className="ml-7 mt-1 inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
    <Icon aria-hidden className="h-3.5 w-3.5" />
    <span aria-live="polite">{t(state === "copied" ? "trace_report.copied" : state === "failed" ? "trace_report.copy_failed" : "trace_report.copy")}</span>
  </button>;
}
