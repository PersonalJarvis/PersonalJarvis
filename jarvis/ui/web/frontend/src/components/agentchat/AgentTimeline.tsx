import { memo, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Check,
  ChevronRight,
  CircleAlert,
  FileDiff,
  FilePen,
  FileText,
  FolderSearch,
  Globe,
  ShieldQuestion,
  TerminalSquare,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { formatThoughtDuration } from "@/components/home/TurnSteps";
import type {
  ReasoningBlock,
  TextBlock,
  TimelineItem,
  ToolBlock,
  TurnItem,
} from "@/components/agentchat/reduce";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import { effortLabel } from "@/components/agentchat/AgentComposer";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The agent chat column — the person's lines in a quiet bubble on the
 * right, each assistant turn flush-left under a byline that says who
 * answered (the provider's mark, the model, the effort). Inside a turn, in
 * the order they happened: reasoning folded to "Thought for Ns", tool
 * calls as rows you can open to read the input and output, approval cards
 * where the runner asked, and the answer as Markdown.
 */
export function AgentTimeline({
  items,
  assistantName,
  providerLabel,
  onDecide,
}: {
  items: TimelineItem[];
  assistantName: string;
  providerLabel: (providerId: string) => string;
  onDecide: (approvalId: string, decision: ApprovalDecision) => void;
}) {
  return (
    <>
      {items.map((item) => {
        if (item.type === "user") {
          return (
            <div
              key={item.id}
              className="flex justify-end"
              data-testid="agent-message-user"
              data-message-id={item.id}
            >
              <div className="max-w-[78%] rounded-2xl rounded-br-md border border-border bg-secondary px-4 py-2.5 text-[15px] leading-relaxed text-foreground">
                <div className="whitespace-pre-wrap">{item.text}</div>
              </div>
            </div>
          );
        }
        if (item.type === "error") {
          return (
            <div
              key={item.id}
              data-message-id={item.id}
              className="mx-auto flex max-w-[85%] items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              <CircleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{item.text}</span>
            </div>
          );
        }
        return (
          <Turn
            key={item.id}
            turn={item}
            assistantName={assistantName}
            providerLabel={providerLabel(item.provider)}
            onDecide={onDecide}
          />
        );
      })}
    </>
  );
}

const Turn = memo(function Turn({
  turn,
  assistantName,
  providerLabel,
  onDecide,
}: {
  turn: TurnItem;
  assistantName: string;
  providerLabel: string;
  onDecide: (approvalId: string, decision: ApprovalDecision) => void;
}) {
  const t = useT();
  const live = turn.status === "running";
  const elapsed = useElapsedMs(live ? turn.startedMs : null);
  const hasText = turn.blocks.some((b) => b.kind === "text" && b.text.trim());

  return (
    <div
      className="flex flex-col gap-1.5 px-1"
      data-testid="agent-turn"
      data-message-id={turn.id}
      data-status={turn.status}
    >
      <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
        <span className="h-1 w-1 rounded-full bg-primary" aria-hidden />
        <span>{assistantName}</span>
        <span className="inline-flex items-center gap-1.5 normal-case tracking-normal text-muted-foreground">
          <ProviderLogo providerId={turn.provider} label={providerLabel} size="sm" />
          <span>{providerLabel}</span>
          {turn.model && <span className="text-muted-foreground/70">· {turn.model}</span>}
          {turn.effort && (
            <span className="text-muted-foreground/70">· {effortLabel(turn.effort, t)}</span>
          )}
        </span>
      </div>

      {turn.blocks.length > 0 && (
        <div className="flex flex-col gap-1">
          {turn.blocks.map((block) => {
            if (block.kind === "text") return <Prose key={block.id} block={block} />;
            if (block.kind === "reasoning") return <Reasoning key={block.id} block={block} />;
            return <ToolRow key={block.callId} block={block} onDecide={onDecide} />;
          })}
        </div>
      )}

      {live && !hasText && (
        <div
          className="flex items-center gap-2 text-xs text-muted-foreground"
          data-testid="agent-turn-live"
        >
          <Spinner />
          <span>
            {elapsed > 0
              ? fill(t("agent_chat.working_for"), { duration: formatThoughtDuration(elapsed) })
              : t("agent_chat.working")}
          </span>
        </div>
      )}

      {turn.status === "error" && turn.error && (
        <div
          className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          role="alert"
          data-testid="agent-turn-error"
        >
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="whitespace-pre-wrap break-words">{turn.error}</span>
        </div>
      )}
      {turn.status === "cancelled" && (
        <div className="text-xs italic text-muted-foreground">{t("agent_chat.turn_cancelled")}</div>
      )}
      {turn.status !== "running" && (turn.durationMs || turn.costUsd !== null) && (
        <div className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground/70">
          {turn.durationMs ? <span>{formatThoughtDuration(turn.durationMs)}</span> : null}
          {usageLine(turn.usage) && <span>· {usageLine(turn.usage)}</span>}
          {turn.costUsd !== null && turn.costUsd > 0 && <span>· ${turn.costUsd.toFixed(4)}</span>}
        </div>
      )}
    </div>
  );
});

function usageLine(usage: Record<string, unknown> | null): string {
  if (!usage) return "";
  const inp = num(usage.input_tokens ?? usage.input);
  const out = num(usage.output_tokens ?? usage.output);
  if (inp === null && out === null) return "";
  return `${inp ?? 0} in · ${out ?? 0} out`;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function Prose({ block }: { block: TextBlock }) {
  if (!block.text.trim()) return null;
  return (
    <div
      data-testid="agent-text"
      className={cn(
        "prose prose-neutral max-w-none text-[15px] leading-relaxed dark:prose-invert [overflow-wrap:anywhere]",
        "prose-p:my-2 prose-headings:font-display prose-headings:tracking-tight prose-h1:text-xl prose-h2:text-lg prose-h3:text-base",
        "prose-a:text-primary prose-a:no-underline hover:prose-a:underline",
        "prose-code:rounded prose-code:bg-muted/60 prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:text-[0.85em] prose-code:font-normal prose-code:before:hidden prose-code:after:hidden",
        "prose-pre:my-2 prose-pre:border prose-pre:border-border prose-pre:bg-card/80 prose-pre:text-[13px]",
        "prose-li:my-0.5 prose-ul:my-2 prose-ol:my-2",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
    </div>
  );
}

function Reasoning({ block }: { block: ReasoningBlock }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const header = block.live
    ? t("agent_chat.thinking")
    : block.durationMs
      ? fill(t("agent_chat.thought_for"), { duration: formatThoughtDuration(block.durationMs) })
      : t("agent_chat.thought");
  return (
    <div data-testid="agent-reasoning" data-live={block.live || undefined}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-md py-0.5 pr-2 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        {block.live ? (
          <Spinner />
        ) : (
          <ChevronRight
            className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-90")}
            aria-hidden
          />
        )}
        <span>{header}</span>
      </button>
      {open && block.text && (
        <div className="ml-2 mt-1 border-l border-border pl-3 text-xs italic leading-relaxed text-muted-foreground whitespace-pre-wrap">
          {block.text}
        </div>
      )}
    </div>
  );
}

const TOOL_ICON: Array<[RegExp, LucideIcon]> = [
  [/^(bash|run_?command|shell|command|exec)/i, TerminalSquare],
  [/^(edit|multi_?edit|apply_?patch|str_replace)/i, FileDiff],
  [/^(write|create_?file|notebook_?edit)/i, FilePen],
  [/^(read|cat|view|open_?file)/i, FileText],
  [/^(glob|grep|ls|list|search|find)/i, FolderSearch],
  [/^(web_?fetch|web_?search|fetch|browse)/i, Globe],
];

function toolIcon(name: string): LucideIcon {
  for (const [re, icon] of TOOL_ICON) if (re.test(name)) return icon;
  return Wrench;
}

/** A one-line gist of a tool's input: the command, the path, the pattern. */
function inputSummary(input: unknown): string {
  if (input === null || input === undefined) return "";
  if (typeof input === "string") return input;
  if (typeof input !== "object") return String(input);
  const obj = input as Record<string, unknown>;
  for (const key of ["command", "cmd", "file_path", "path", "pattern", "query", "url", "description"]) {
    const v = obj[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  const first = Object.values(obj).find((v) => typeof v === "string" && v.trim());
  return typeof first === "string" ? first : "";
}

function pretty(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function ToolRow({
  block,
  onDecide,
}: {
  block: ToolBlock;
  onDecide: (approvalId: string, decision: ApprovalDecision) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const pending = Boolean(block.approval && block.approval.decision === null);
  const denied = block.approval?.decision === "deny" || block.approval?.decision === "cancel";
  const done = block.output !== null;
  const Icon = toolIcon(block.name);
  const summary = inputSummary(block.input);

  return (
    <div
      data-testid="agent-tool"
      data-tool={block.name}
      data-state={pending ? "pending" : denied ? "denied" : done ? (block.isError ? "error" : "done") : "running"}
      className={cn(
        "rounded-lg border text-xs",
        pending ? "border-primary/40 bg-primary/5" : "border-border/70 bg-secondary/30",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-2 px-2.5 py-1.5 text-left"
      >
        <span className="grid h-5 w-5 shrink-0 place-items-center">
          {!done && !pending && !denied ? (
            <Spinner />
          ) : block.isError ? (
            <CircleAlert className="h-3.5 w-3.5 text-destructive" aria-hidden />
          ) : pending ? (
            <ShieldQuestion className="h-3.5 w-3.5 text-primary" aria-hidden />
          ) : (
            <Icon className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          )}
        </span>
        <span className="shrink-0 font-medium text-foreground">{block.name}</span>
        {summary && (
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
            {summary}
          </span>
        )}
        {!summary && <span className="flex-1" />}
        {block.durationMs !== null && (
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground/70">
            {formatStepDuration(block.durationMs)}
          </span>
        )}
        <ChevronRight
          className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
          aria-hidden
        />
      </button>

      {pending && block.approval && (
        <div
          className="flex flex-col gap-2 border-t border-primary/20 px-2.5 py-2"
          data-testid="agent-approval"
        >
          <div className="text-foreground">
            <span className="font-medium">{t("agent_chat.approval_title")}</span>{" "}
            <span className="text-muted-foreground">{block.approval.summary || summary}</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => onDecide(block.approval!.approvalId, "allow")}
              className="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
              data-testid="approval-allow"
            >
              <Check className="h-3.5 w-3.5" aria-hidden />
              {t("agent_chat.approval_allow")}
            </button>
            <button
              type="button"
              onClick={() => onDecide(block.approval!.approvalId, "allow_always")}
              className="inline-flex h-7 items-center rounded-md border border-border px-2.5 text-xs font-medium text-foreground hover:bg-secondary"
              data-testid="approval-always"
            >
              {t("agent_chat.approval_always")}
            </button>
            <button
              type="button"
              onClick={() => onDecide(block.approval!.approvalId, "deny")}
              className="inline-flex h-7 items-center gap-1 rounded-md border border-border px-2.5 text-xs font-medium text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              data-testid="approval-deny"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
              {t("agent_chat.approval_deny")}
            </button>
          </div>
        </div>
      )}

      {open && (
        <div className="flex flex-col gap-2 border-t border-border/60 px-2.5 py-2">
          {block.input !== undefined && block.input !== null && (
            <Detail label={t("agent_chat.tool_input")} text={pretty(block.input)} />
          )}
          {denied && (
            <div className="text-muted-foreground">{t("agent_chat.approval_denied")}</div>
          )}
          {block.output !== null && (
            <Detail
              label={block.isError ? t("agent_chat.tool_error") : t("agent_chat.tool_output")}
              text={block.output}
              error={block.isError}
            />
          )}
        </div>
      )}
    </div>
  );
}

function Detail({ label, text, error }: { label: string; text: string; error?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground/70">
        {label}
      </span>
      <pre
        className={cn(
          "scrollbar-jarvis max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-background/60 p-2 font-mono text-[11.5px] leading-relaxed",
          error ? "text-destructive" : "text-foreground",
        )}
      >
        {text}
      </pre>
    </div>
  );
}

function formatStepDuration(ms: number): string {
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  return formatThoughtDuration(ms);
}

function Spinner() {
  return (
    <span
      aria-hidden
      data-testid="agent-spinner"
      className="inline-block h-3.5 w-3.5 rounded-full border-2 border-primary/25 border-t-primary motion-safe:animate-spin"
    />
  );
}

/** Elapsed wall-clock since `startedTs`, ticking once a second while set. */
function useElapsedMs(startedTs: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedTs === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [startedTs]);
  return startedTs === null ? 0 : Math.max(0, now - startedTs);
}
