import { memo, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  CircleAlert,
  Clock,
  ShieldQuestion,
  X,
} from "lucide-react";

import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { formatThoughtDuration } from "@/components/home/TurnSteps";
import {
  agentToolView,
  formatTokens,
  inputTokens,
  outputTokens,
} from "@/components/agentchat/toolView";
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
 * answered (the provider's mark, the model, the effort).
 *
 * A turn hangs off a hairline rail that GLOWS while the turn runs, so the
 * whole block reads as one live unit instead of a list of disconnected
 * rows. On the rail, in the order they happened: what the model thought
 * (folded to "Thought for Ns"; a vendor that redacts its thinking still
 * gets the row, because the time is the fact), the tool calls under the
 * names the agent's own log uses, approval cards where the runner asked,
 * and the answer as Markdown.
 *
 * While it works there is ALWAYS a line saying so (maintainer, 2026-08-23:
 * "man sieht nicht, dass es nachdenkt"): a turning glyph, a word that
 * changes as the minutes pass, the elapsed time and the tokens counted so
 * far — the Claude CLI's "✳ Vibing… (1m 57s · ↓ 4.8k tokens)", in the
 * product's own colours.
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

  return (
    <div
      className="flex flex-col gap-1.5"
      data-testid="agent-turn"
      data-message-id={turn.id}
      data-status={turn.status}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
        <span
          className={cn("h-1 w-1 rounded-full bg-primary", live && "motion-safe:animate-pulse")}
          aria-hidden
        />
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

      {/* The rail: everything the turn produced hangs off one line. */}
      <div className="relative flex flex-col gap-1.5 pl-4">
        <span
          aria-hidden
          data-testid="agent-turn-rail"
          className={cn(
            "absolute bottom-1 left-[3px] top-1 w-px rounded-full",
            live ? "agent-rail-live" : "bg-border",
          )}
        />

        {turn.blocks.map((block) => {
          if (block.kind === "text") return <Prose key={block.id} block={block} />;
          if (block.kind === "reasoning") return <Reasoning key={block.id} block={block} />;
          return <ToolRow key={block.callId} block={block} onDecide={onDecide} />;
        })}

        {live && <LiveStatus turn={turn} elapsed={elapsed} />}

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
          <div className="text-xs italic text-muted-foreground">
            {t("agent_chat.turn_cancelled")}
          </div>
        )}

        {!live && <TurnFooter turn={turn} />}
      </div>
    </div>
  );
});

/**
 * The line that says the turn is alive.
 *
 * A turning glyph, a word, the elapsed time and the tokens so far. The word
 * changes every few seconds (locale-supplied, comma-separated) so a long
 * turn never looks frozen — the same trick the Claude CLI plays, and the
 * cheapest possible proof that something is still happening.
 */
function LiveStatus({ turn, elapsed }: { turn: TurnItem; elapsed: number }) {
  const t = useT();
  const words = useMemo(() => statusWords(t("agent_chat.status_words")), [t]);
  const word = words[Math.floor(elapsed / STATUS_WORD_MS) % words.length];
  const out = outputTokens(turn.liveUsage);
  const inp = inputTokens(turn.liveUsage);

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-0.5 py-0.5 text-xs"
      data-testid="agent-turn-live"
      role="status"
      aria-live="polite"
    >
      <PulseGlyph />
      <span className="thinking-shimmer font-medium">{word}</span>
      <span className="font-mono text-[11px] tabular-nums text-muted-foreground/80">
        ({formatThoughtDuration(elapsed)}
        {((inp ?? 0) > 0 || (out ?? 0) > 0) && (
          <>
            {" · "}
            <ArrowUp className="inline h-3 w-3 -translate-y-px" aria-hidden />
            {formatTokens(inp ?? 0)}
            <ArrowDown className="ml-1 inline h-3 w-3 -translate-y-px" aria-hidden />
            {formatTokens(out ?? 0)} {t("agent_chat.tokens")}
          </>
        )}
        )
      </span>
      <span className="text-muted-foreground/60">{t("agent_chat.interrupt_hint")}</span>
    </div>
  );
}

/** How long one status word stays before the next takes over. */
const STATUS_WORD_MS = 4000;

/** The locale's comma-separated word list; never empty. */
export function statusWords(raw: string): string[] {
  const words = raw
    .split(",")
    .map((w) => w.trim())
    .filter(Boolean);
  return words.length > 0 ? words : ["Working…"];
}

/** The turning asterisk: one small SVG, animated by CSS (index.css). */
function PulseGlyph() {
  return (
    <svg
      viewBox="0 0 12 12"
      className="agent-glyph h-3.5 w-3.5 shrink-0 text-primary"
      aria-hidden
      data-testid="agent-glyph"
    >
      <g stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <line x1="6" y1="1.2" x2="6" y2="10.8" />
        <line x1="1.85" y1="3.6" x2="10.15" y2="8.4" />
        <line x1="1.85" y1="8.4" x2="10.15" y2="3.6" />
      </g>
    </svg>
  );
}

/** What the finished turn cost: time, tokens, money — the receipt. */
function TurnFooter({ turn }: { turn: TurnItem }) {
  const t = useT();
  const usage = turn.usage ?? turn.liveUsage;
  const inp = inputTokens(usage);
  const out = outputTokens(usage);
  const hasTokens = (inp ?? 0) > 0 || (out ?? 0) > 0;
  if (!turn.durationMs && !hasTokens && !turn.costUsd) return null;

  return (
    <div
      className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 pt-0.5 font-mono text-[10px] tabular-nums text-muted-foreground/70"
      data-testid="agent-turn-footer"
    >
      {turn.durationMs ? (
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3" aria-hidden />
          {formatThoughtDuration(turn.durationMs)}
        </span>
      ) : null}
      {hasTokens && (
        <span className="inline-flex items-center gap-1" title={t("agent_chat.tokens")}>
          <ArrowUp className="h-3 w-3" aria-hidden />
          {formatTokens(inp ?? 0)}
          <ArrowDown className="ml-1 h-3 w-3" aria-hidden />
          {formatTokens(out ?? 0)}
          <span className="ml-0.5">{t("agent_chat.tokens")}</span>
        </span>
      )}
      {turn.costUsd !== null && turn.costUsd > 0 && <span>${turn.costUsd.toFixed(4)}</span>}
    </div>
  );
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

/**
 * What the model thought.
 *
 * Live: a shimmering "Thinking…" with the seconds ticking — shown even when
 * the vendor never streams the thought itself (Claude Code redacts it), so
 * eight silent seconds are visibly eight seconds of thinking. Finished:
 * "Thought for 8s", opening to the text when there IS text; when there is
 * none the row says so instead of opening onto emptiness.
 */
function Reasoning({ block }: { block: ReasoningBlock }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const elapsed = useElapsedMs(block.live ? block.startedMs : null);
  const hasText = Boolean(block.text.trim());
  const duration = block.live ? elapsed : (block.durationMs ?? 0);
  const header = block.live
    ? duration > 900
      ? fill(t("agent_chat.thinking_for"), { duration: formatThoughtDuration(duration) })
      : t("agent_chat.thinking")
    : duration > 0
      ? fill(t("agent_chat.thought_for"), { duration: formatThoughtDuration(duration) })
      : t("agent_chat.thought");

  return (
    <div data-testid="agent-reasoning" data-live={block.live || undefined}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        disabled={!hasText}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md py-0.5 pr-2 text-xs text-muted-foreground transition-colors",
          hasText ? "hover:text-foreground" : "cursor-default",
        )}
      >
        {block.live ? (
          <PulseGlyph />
        ) : (
          <ChevronRight
            className={cn(
              "h-3.5 w-3.5 shrink-0 transition-transform",
              open && "rotate-90",
              !hasText && "opacity-30",
            )}
            aria-hidden
          />
        )}
        <span className={cn(block.live && "thinking-shimmer font-medium")}>{header}</span>
      </button>
      {open && hasText && (
        <div className="ml-2 mt-1 whitespace-pre-wrap border-l border-border pl-3 text-xs italic leading-relaxed text-muted-foreground">
          {block.text}
        </div>
      )}
    </div>
  );
}

function pretty(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/**
 * One tool call.
 *
 * The name is the agent's own — "PowerShell", "ToolSearch", "Grep" — never a
 * prettied-up rename, because the row is read next to the agent's log
 * (maintainer, 2026-08-23). The mark left of it comes from the tool family,
 * or the vendor's SVG for an MCP server. Click to read what went in and
 * what came back.
 */
function ToolRow({
  block,
  onDecide,
}: {
  block: ToolBlock;
  onDecide: (approvalId: string, decision: ApprovalDecision) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [logoFailed, setLogoFailed] = useState(false);
  const pending = Boolean(block.approval && block.approval.decision === null);
  const denied = block.approval?.decision === "deny" || block.approval?.decision === "cancel";
  const running = block.output === null && !pending && !denied;
  const done = block.output !== null;
  const view = agentToolView(block.name, block.input);
  const summary = view.summary;
  const showLogo = Boolean(view.logoUrl) && !logoFailed;

  return (
    <div
      data-testid="agent-tool"
      data-tool={block.name}
      data-family={view.family}
      data-state={
        pending ? "pending" : denied ? "denied" : done ? (block.isError ? "error" : "done") : "running"
      }
      className={cn(
        "overflow-hidden rounded-lg border text-xs transition-colors",
        pending
          ? "border-primary/40 bg-primary/5"
          : block.isError
            ? "border-destructive/30 bg-destructive/5"
            : running
              ? "border-primary/25 bg-secondary/40"
              : "border-border/60 bg-secondary/25 hover:border-border",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-2 px-2.5 py-1.5 text-left"
      >
        <span className="grid h-5 w-5 shrink-0 place-items-center">
          {running ? (
            <Spinner />
          ) : block.isError ? (
            <CircleAlert className="h-3.5 w-3.5 text-destructive" aria-hidden />
          ) : pending ? (
            <ShieldQuestion className="h-3.5 w-3.5 text-primary" aria-hidden />
          ) : showLogo ? (
            <img
              src={view.logoUrl}
              alt=""
              className="h-3.5 w-3.5"
              loading="lazy"
              onError={() => setLogoFailed(true)}
            />
          ) : (
            <view.Icon className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          )}
        </span>
        <span
          className={cn(
            "shrink-0 font-mono font-medium",
            block.isError ? "text-destructive" : "text-foreground",
          )}
        >
          {view.label}
        </span>
        {summary ? (
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
            {summary}
          </span>
        ) : (
          <span className="flex-1" />
        )}
        {block.durationMs !== null && block.durationMs > 0 && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/70">
            {formatStepDuration(block.durationMs)}
          </span>
        )}
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground/70 transition-transform",
            open && "rotate-90",
          )}
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
          {denied && <div className="text-muted-foreground">{t("agent_chat.approval_denied")}</div>}
          {block.output !== null ? (
            <Detail
              label={block.isError ? t("agent_chat.tool_error") : t("agent_chat.tool_output")}
              text={block.output}
              error={block.isError}
            />
          ) : (
            running && <div className="text-muted-foreground">{t("agent_chat.tool_running")}</div>
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

/**
 * Elapsed wall-clock since `startedTs`, ticking while set.
 *
 * Twice a second, not once: the number beside a live glyph is the proof
 * that something is happening, and a second-long freeze on a 1 s cadence
 * reads as a hang.
 */
function useElapsedMs(startedTs: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedTs === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [startedTs]);
  return startedTs === null ? 0 : Math.max(0, now - startedTs);
}
