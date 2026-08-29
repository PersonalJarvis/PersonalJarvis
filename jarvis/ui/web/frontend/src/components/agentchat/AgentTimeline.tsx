import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowDown,
  Ban,
  Check,
  ChevronRight,
  CircleAlert,
  FileText,
  ImageIcon,
  ShieldQuestion,
  X,
} from "lucide-react";

import { LiveCore } from "@/components/LiveCore";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { formatThoughtDuration } from "@/components/home/TurnSteps";
import { agentToolView, formatTokens, outputTokens } from "@/components/agentchat/toolView";
import { diffStat, toolDiff, type DiffFile, type DiffLine } from "@/components/agentchat/toolDiff";
import type {
  ReasoningBlock,
  TextBlock,
  TimelineItem,
  ToolBlock,
  TurnBlock,
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
 * Under the byline, in the order they happened: the model's thinking as a
 * readable scratchpad wherever it thought (a vendor that redacts its
 * thinking still gets a row, because the time is the fact), the tool calls
 * under the names the agent's own log uses, approval cards where the runner
 * asked, and the answer as Markdown.
 *
 * Three rules decide how all of that is drawn (maintainer, 2026-08-25:
 * it looks sloppy, odd boxes everywhere, and a strange stripe down the side):
 *
 * 1. ONE container per turn. Nothing inside draws a box of its own — a
 *    tool call is a row, an opened detail is a hairline and some text.
 *    Boxes inside boxes inside boxes is what made the column look busy,
 *    and a nested frame carries no information the indent does not.
 *    The turn itself is held together by its byline and the spacing; an
 *    earlier version hung it off a glowing hairline rail, which read as a
 *    stray stripe running down the transcript.
 * 2. The thinking is READ, not counted (maintainer, 2026-08-25: the
 *    intermediate steps had stopped showing). A stretch of thought that has
 *    words is a scratchpad in the place it happened: its text streams while
 *    the model writes it, stays open while the turn still works, and folds
 *    to a two-line preview once the turn is done. Only a thought with no
 *    words to show — a vendor that redacts (Claude Code) — is reduced to its
 *    time, and a running one of those draws nothing, because the live line
 *    already says the turn is thinking and two lines saying it at once is
 *    the duplication the maintainer objected to. A streaming scratchpad
 *    carries the live line in its own header for the same reason: the turn
 *    says it is working exactly ONCE.
 * 3. The turn ALWAYS says which state it is in. While it runs: the live
 *    core, a word that changes as the minutes pass, the elapsed time and
 *    the tokens spent — the Claude CLI's "Composing… (2m 35s · ↓ 3.7k
 *    tokens)", in the product's own mark and colours. The moment it stops
 *    there is a line saying so — Done, Stopped, Failed, or "finished
 *    without an answer" when a turn ends on a dead tool call. A live line
 *    that simply disappears leaves the person guessing.
 *
 * Only OUTPUT tokens are ever shown (maintainer, 2026-08-25). The input
 * side of a coding CLI re-counts the whole conversation on every step, so
 * it reads as an absurd number for one question (BUG-173); what the turn
 * produced is the honest, monotonic figure.
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
  const t = useT();
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
                {item.text && <div className="whitespace-pre-wrap">{item.text}</div>}
                {item.attachments.length > 0 && (
                  <div
                    data-testid="agent-message-attachments"
                    className={cn(
                      "flex flex-wrap gap-1.5",
                      item.text && "mt-2 border-t border-border/60 pt-2",
                    )}
                  >
                    {item.attachments.map((file) =>
                      // The picture itself, where there is one to fetch: a
                      // screenshot dropped on a pane is a thing the person
                      // wants to SEE in their turn, not a file name with an
                      // icon (maintainer, 2026-08-27). A file with no url —
                      // a document, or the front page's chat, whose drops
                      // are read and not stored — keeps the chip.
                      file.kind === "image" && file.url ? (
                        <img
                          key={file.name}
                          src={file.url}
                          alt={file.name}
                          title={file.name}
                          loading="lazy"
                          decoding="async"
                          data-testid="agent-message-image"
                          className="max-h-60 max-w-full rounded-lg border border-border/70 bg-background/40 object-contain"
                        />
                      ) : (
                      <span
                        key={file.name}
                        // The receipt says whether the model could actually
                        // READ it. "sent" and "could not be read" are the two
                        // outcomes, and the second happens for real wherever no
                        // provider can see an image.
                        title={
                          file.describedBy === "none"
                            ? t("agent_chat.attach_not_described")
                            : file.name
                        }
                        className="flex items-center gap-1.5 rounded-md border border-border/70 bg-background/40 px-1.5 py-0.5 text-[11px] text-muted-foreground"
                      >
                        {file.kind === "image" ? (
                          <ImageIcon className="h-3 w-3 shrink-0" aria-hidden />
                        ) : (
                          <FileText className="h-3 w-3 shrink-0" aria-hidden />
                        )}
                        <span className="max-w-[12rem] truncate font-mono">{file.name}</span>
                      </span>
                      ),
                    )}
                  </div>
                )}
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
  const blocks = useMemo(() => arrangeThinking(turn.blocks), [turn.blocks]);
  const groups = useMemo(() => groupSteps(blocks), [blocks]);
  // A scratchpad still receiving words carries the turn's live line in its
  // own header; the line below would only say "thinking" a second time.
  const thinkingAloud = blocks.some((b) => b.kind === "reasoning" && b.live);

  return (
    <div
      className="flex flex-col gap-1.5"
      data-testid="agent-turn"
      data-message-id={turn.id}
      data-status={turn.status}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
        <span
          className={cn("h-1 w-1 rounded-full bg-foreground/70", live && "motion-safe:animate-pulse")}
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

      {/* No rail, no frame: the byline above and the spacing are what group
          the turn, and the closing line below says how it ended. */}
      <div className="flex flex-col gap-1.5 px-1">
        {groups.map((group) => {
          if (group.kind === "tools") {
            return (
              <ToolGroup
                key={group.id}
                blocks={group.blocks}
                turnLive={live}
                onDecide={onDecide}
              />
            );
          }
          if (group.block.kind === "text") return <Prose key={group.id} block={group.block} />;
          return (
            <Thought
              key={group.id}
              block={group.block}
              turnLive={live}
              usage={turn.liveUsage}
            />
          );
        })}

        {live && !thinkingAloud && <LiveStatus elapsed={elapsed} usage={turn.liveUsage} />}

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

        {!live && <TurnOutcome turn={turn} />}
      </div>
    </div>
  );
});

/**
 * Where each stretch of a turn's thinking goes.
 *
 * A thought WITH words is a scratchpad and stays exactly where it happened —
 * ahead of the tool call it explains, between two of them, before the
 * answer. Reading the reasoning in the order it was written is the whole
 * point of showing it; the earlier design merged every stretch into one row
 * at the top of the turn, which hid the streaming text altogether and put
 * the finished thoughts behind a single click (maintainer, 2026-08-25: the
 * intermediate steps are no longer shown properly).
 *
 * A thought WITHOUT words is only a duration: a vendor that redacts its
 * thinking (Claude Code), or a runner that announced the thought before any
 * text arrived. Those stay quiet. The finished ones fold into ONE "Thought
 * for Ns" row where the first of them happened, seconds added up — a stack
 * of timing rows said nothing a sum does not. A running one draws nothing at
 * all: the live line below already says the turn is thinking, and saying it
 * twice in a row is the duplication the maintainer objected to.
 */
export function arrangeThinking(blocks: TurnBlock[]): TurnBlock[] {
  const silent = blocks.filter(
    (b): b is ReasoningBlock => b.kind === "reasoning" && !b.live && !b.text.trim(),
  );
  const folded: ReasoningBlock | null =
    silent.length === 0
      ? null
      : {
          kind: "reasoning",
          id: silent[0].id,
          text: "",
          durationMs: silent.reduce((sum, th) => sum + (th.durationMs ?? 0), 0),
          live: false,
          startedMs: silent[0].startedMs,
        };

  const out: TurnBlock[] = [];
  let placed = false;
  for (const block of blocks) {
    if (block.kind !== "reasoning" || block.text.trim()) {
      out.push(block);
      continue;
    }
    if (block.live) continue;
    if (folded && !placed) {
      out.push(folded);
      placed = true;
    }
  }
  return out;
}

/**
 * The line that says the turn is alive.
 *
 * The live core, a word, the elapsed time and what the turn has produced so
 * far. The word changes every few seconds (locale-supplied,
 * comma-separated) so a long turn never looks frozen — the same trick the
 * Claude CLI plays, and the cheapest possible proof that something is still
 * happening. This is the ONLY line in a running turn that claims to be
 * working; the thought rows deliberately stay quiet until they close.
 */
function LiveStatus({
  elapsed,
  usage,
}: {
  elapsed: number;
  usage: Record<string, number> | null;
}) {
  const t = useT();
  const words = useMemo(() => statusWords(t("agent_chat.status_words")), [t]);
  const word = words[Math.floor(elapsed / STATUS_WORD_MS) % words.length];
  const out = outputTokens(usage);

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-0.5 py-0.5 text-xs"
      data-testid="agent-turn-live"
      role="status"
      aria-live="polite"
    >
      <LiveCore />
      <span className="thinking-shimmer font-medium">{word}</span>
      <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-muted-foreground/80">
        <span>({formatThoughtDuration(elapsed)}</span>
        {out !== null && out > 0 && (
          <>
            <span aria-hidden>·</span>
            <OutTokens n={out} />
          </>
        )}
        <span>)</span>
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

/**
 * What the turn produced, in tokens.
 *
 * The OUTPUT side only, everywhere it appears — live and on the receipt.
 * The arrow is the CLI's own shorthand, so the two read the same.
 */
function OutTokens({ n }: { n: number }) {
  const t = useT();
  return (
    <span className="inline-flex items-center gap-1" title={t("agent_chat.tokens_out")}>
      <ArrowDown className="h-3 w-3" aria-hidden />
      {formatTokens(n)}
      <span>{t("agent_chat.tokens")}</span>
    </span>
  );
}

/**
 * The closing line: the turn is over, and this is how it ended.
 *
 * It is drawn for EVERY finished turn, even one that produced nothing at
 * all — the whole point is that the person never has to guess whether the
 * agent is still working (maintainer, 2026-08-25). A turn that ends on a
 * failed tool call with no answer says exactly that instead of trailing
 * off into white space.
 */
function TurnOutcome({ turn }: { turn: TurnItem }) {
  const t = useT();
  const usage = turn.usage ?? turn.liveUsage;
  const out = outputTokens(usage);
  const answered = turn.blocks.some((b) => b.kind === "text" && b.text.trim().length > 0);

  const { Icon, label, tone } =
    turn.status === "error"
      ? { Icon: CircleAlert, label: t("agent_chat.turn_failed"), tone: "text-destructive/80" }
      : turn.status === "cancelled"
        ? { Icon: Ban, label: t("agent_chat.turn_cancelled"), tone: "text-muted-foreground" }
        : answered
          ? { Icon: Check, label: t("agent_chat.turn_done"), tone: "text-muted-foreground/70" }
          : {
              Icon: CircleAlert,
              label: t("agent_chat.turn_no_answer"),
              tone: "text-muted-foreground",
            };

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-2.5 gap-y-0.5 pt-0.5 font-mono text-[10px] tabular-nums",
        tone,
      )}
      data-testid="agent-turn-footer"
      data-outcome={turn.status === "done" && !answered ? "no-answer" : turn.status}
    >
      <span className="inline-flex items-center gap-1 font-sans text-[11px] font-medium normal-case">
        <Icon className="h-3.5 w-3.5" aria-hidden />
        {label}
      </span>
      {turn.durationMs ? <span>{formatThoughtDuration(turn.durationMs)}</span> : null}
      {out !== null && out > 0 && <OutTokens n={out} />}
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

/** One stretch of thinking: a scratchpad when it has words, a timing row when it has none. */
function Thought({
  block,
  turnLive,
  usage,
}: {
  block: ReasoningBlock;
  turnLive: boolean;
  usage: Record<string, number> | null;
}) {
  if (!block.text.trim()) return <SilentThought block={block} />;
  return <Scratchpad block={block} turnLive={turnLive} usage={usage} />;
}

/**
 * Past this many characters (or at the first blank line) a finished thought
 * is more than a preview can hold and earns a fold.
 */
const PREVIEW_CHARS = 200;

/** Does a thought need a fold, or does its preview already show all of it? */
export function thoughtNeedsFold(text: string): boolean {
  const trimmed = text.trim();
  return trimmed.length > PREVIEW_CHARS || /\n\s*\n/.test(trimmed);
}

/**
 * A thought as one flat line for the folded preview — no Markdown marks, no
 * code blocks, no line breaks — so two lines of it read as a sentence.
 */
export function thoughtGist(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[`*_#>~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * The model's thinking, readable — the scratchpad.
 *
 * WHILE THE WORDS ARRIVE it is open. Its header is the turn's live line for
 * as long as the thought runs — the core, "Thinking for 8s", the tokens so
 * far, the interrupt hint — and under a hairline sits the text itself,
 * anchored to its newest lines so the reader watches the thought unfold
 * instead of scrolling after it (the earlier lines fade out at the top once
 * there are more than fit).
 *
 * WHEN THE THOUGHT CLOSES it keeps its place. It stays open while the turn
 * still works, because watching the work is the point of a running turn,
 * and folds to "Thought for 8s" over a two-line preview the moment the turn
 * is done, so the answer is what a finished conversation reads as. A click
 * opens it again; a thought short enough to fit its own preview never needs
 * one and shows whole.
 *
 * The thought reads as prose, not as a log dump: models write their
 * reasoning in Markdown, and a raw `**like this**` on screen is the same
 * defect the local-models panel was fixed for.
 */
function Scratchpad({
  block,
  turnLive,
  usage,
}: {
  block: ReasoningBlock;
  turnLive: boolean;
  usage: Record<string, number> | null;
}) {
  const t = useT();
  // `null` until the person decides; until then the turn's state decides.
  const [manual, setManual] = useState<boolean | null>(null);
  const live = block.live;
  const thinking = useElapsedMs(live ? block.startedMs : null);
  const text = block.text.trim();
  const foldable = thoughtNeedsFold(text);
  const open = live || (foldable && (manual ?? turnLive));
  const duration = block.durationMs ?? 0;
  const out = outputTokens(usage);
  const header =
    duration > 0
      ? fill(t("agent_chat.thought_for"), { duration: formatThoughtDuration(duration) })
      : t("agent_chat.thought");

  return (
    <div
      data-testid="agent-reasoning"
      data-state={live ? "live" : open ? "open" : foldable ? "folded" : "whole"}
    >
      {live ? (
        <div
          className="flex flex-wrap items-center gap-x-2 gap-y-0.5 py-0.5 text-xs"
          data-testid="agent-turn-live"
          role="status"
          aria-live="polite"
        >
          <LiveCore />
          <span className="thinking-shimmer font-medium">
            {fill(t("agent_chat.thinking_for"), { duration: formatThoughtDuration(thinking) })}
          </span>
          {out !== null && out > 0 && (
            <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-muted-foreground/80">
              <span aria-hidden>·</span>
              <OutTokens n={out} />
            </span>
          )}
          <span className="text-muted-foreground/60">{t("agent_chat.interrupt_hint")}</span>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setManual(!open)}
          aria-expanded={open}
          disabled={!foldable}
          className={cn(
            "-ml-1 inline-flex items-center gap-1.5 rounded-md px-1 py-0.5 text-xs text-muted-foreground transition-colors",
            foldable ? "hover:bg-secondary/50 hover:text-foreground" : "cursor-default",
          )}
        >
          <ChevronRight
            className={cn(
              "h-3.5 w-3.5 shrink-0 transition-transform",
              open && "rotate-90",
              !foldable && "opacity-30",
            )}
            aria-hidden
          />
          <span>{header}</span>
        </button>
      )}

      {live ? (
        <Trace>
          <ThoughtTail text={text} />
        </Trace>
      ) : open ? (
        <Trace>
          <ThoughtProse text={text} />
        </Trace>
      ) : (
        <div
          className={cn(
            "ml-[1.25rem] text-[13px] leading-relaxed text-muted-foreground/80 [overflow-wrap:anywhere]",
            foldable && "line-clamp-2",
          )}
          data-testid="agent-reasoning-preview"
        >
          {thoughtGist(text)}
        </div>
      )}
    </div>
  );
}

/**
 * The streaming thought, anchored to its newest lines.
 *
 * The window is a fixed height with the text pinned to its bottom edge, so
 * new words push the old ones up and out of view — the reader always sees
 * what the model is writing NOW. Once more has been written than fits, the
 * top fades out to say so; a thought that fits is shown whole, unfaded.
 */
function ThoughtTail({ text }: { text: string }) {
  const outer = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);
  useLayoutEffect(() => {
    const box = outer.current;
    const body = inner.current;
    if (!box || !body) return;
    setClipped(body.offsetHeight > box.clientHeight + 1);
  }, [text]);
  return (
    <div
      ref={outer}
      className={cn(
        "flex max-h-44 flex-col justify-end overflow-hidden",
        clipped && "[mask-image:linear-gradient(to_bottom,transparent,black_2.75rem)]",
      )}
      data-testid="agent-reasoning-tail"
      data-clipped={clipped ? "true" : undefined}
    >
      <div ref={inner}>
        <ThoughtProse text={text} />
      </div>
    </div>
  );
}

function ThoughtProse({ text }: { text: string }) {
  return (
    <div
      className={cn(
        "prose prose-neutral max-w-none text-[13px] leading-relaxed text-muted-foreground dark:prose-invert [overflow-wrap:anywhere]",
        "prose-p:my-1.5 prose-headings:my-1.5 prose-headings:text-[13px] prose-headings:font-semibold prose-headings:text-foreground/80",
        "prose-strong:text-foreground/80 prose-li:my-0.5 prose-ul:my-1.5 prose-ol:my-1.5",
        "prose-code:font-mono prose-code:text-[0.9em] prose-code:before:hidden prose-code:after:hidden",
        "prose-pre:my-1.5 prose-pre:bg-transparent prose-pre:p-0 prose-pre:text-[11px]",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

/**
 * Thinking with no words to show — the vendor redacted it (Claude Code
 * does). The row still states the time, because the time is the fact, and
 * stays shut rather than opening onto emptiness.
 */
function SilentThought({ block }: { block: ReasoningBlock }) {
  const t = useT();
  const duration = block.durationMs ?? 0;
  const header =
    duration > 0
      ? fill(t("agent_chat.thought_for"), { duration: formatThoughtDuration(duration) })
      : t("agent_chat.thought");

  return (
    <div data-testid="agent-reasoning" data-state="silent">
      <button
        type="button"
        aria-expanded={false}
        disabled
        className="-ml-1 inline-flex cursor-default items-center gap-1.5 rounded-md px-1 py-0.5 text-xs text-muted-foreground"
      >
        <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-30" aria-hidden />
        <span>{header}</span>
      </button>
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
 * Is the raw input worth printing under a row that already shows it?
 *
 * A shell call whose whole payload is `{"command": "ls -la"}` is fully told
 * by the row's own summary; repeating it as JSON below is the same string
 * twice, which is what made an opened tool call read as duplicated output
 * (maintainer, 2026-08-25). Anything with a second field, or a value the
 * summary had to truncate, still earns its lines.
 */
export function inputAddsNothing(input: unknown, summary: string): boolean {
  if (input === undefined || input === null) return true;
  if (!summary) return false;
  if (typeof input === "string") return input.trim() === summary.trim();
  if (typeof input !== "object" || Array.isArray(input)) return false;
  const entries = Object.entries(input as Record<string, unknown>).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (entries.length !== 1) return false;
  const [, only] = entries[0];
  return typeof only === "string" && only.trim() === summary.trim();
}

/** The first line of a tool error, short enough to sit on the row. */
function errorGist(text: string): string {
  const line = text.split("\n").find((l) => l.trim()) ?? "";
  const trimmed = line.trim();
  return trimmed.length > 160 ? `${trimmed.slice(0, 159)}…` : trimmed;
}

/**
 * The change a coding call made, painted the way every code host paints one.
 *
 * A row saying `Edit src/app.ts` names a file and says nothing about what
 * happened in it, and the arguments underneath were worse: two JSON string
 * literals, escapes and all, so the one thing worth reading was the one thing
 * that could not be (maintainer, 2026-08-27, against Claude Code and Codex).
 *
 * No frame — the fold's own indent already holds it (rule 1). What carries
 * the meaning is the ink: removed lines in the destructive red the whole app
 * uses for loss, added lines in the one green the palette now has, and every
 * line that did NOT move in plain grey, which is what makes a change legible
 * rather than a wall of colour.
 */
function DiffView({ files }: { files: DiffFile[] }) {
  const t = useT();
  return (
    <div className="flex flex-col gap-2" data-testid="agent-tool-diff">
      {files.map((file, i) => (
        <div key={`${file.path}:${i}`} className="flex min-w-0 flex-col gap-1">
          {/* The path only when the row above cannot already be carrying it:
              one file is named on the row, several need naming here. */}
          {files.length > 1 && file.path && (
            <span className="truncate font-mono text-[10px] text-muted-foreground/60">
              {file.path}
            </span>
          )}
          <div className="scrollbar-jarvis max-h-80 overflow-auto">
            {file.lines.map((line, n) => (
              <DiffRow key={n} line={line} />
            ))}
          </div>
          {file.truncated > 0 && (
            <span className="font-mono text-[10px] text-muted-foreground/50">
              {fill(t("agent_chat.diff_truncated"), { count: String(file.truncated) })}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * One line of a diff.
 *
 * The +/- mark is unselectable, so copying a diff out of the chat yields the
 * CODE rather than a column of marks to strip afterwards.
 */
function DiffRow({ line }: { line: DiffLine }) {
  const t = useT();
  if (line.kind === "gap") {
    return (
      <div
        data-diff="gap"
        className="select-none px-2 py-0.5 font-mono text-[10px] text-muted-foreground/40"
      >
        {line.text ? fill(t("agent_chat.diff_skipped"), { count: line.text }) : "⋯"}
      </div>
    );
  }
  return (
    <div
      data-diff={line.kind}
      className={cn(
        "flex whitespace-pre-wrap break-words px-2 font-mono text-[11.5px] leading-relaxed",
        line.kind === "add" && "diff-line-add",
        line.kind === "del" && "diff-line-del",
        line.kind === "ctx" && "text-muted-foreground/70",
      )}
    >
      <span aria-hidden className="mr-2 w-2 shrink-0 select-none opacity-70">
        {line.kind === "add" ? "+" : line.kind === "del" ? "-" : " "}
      </span>
      <span className="min-w-0">{line.text || "\u00A0"}</span>
    </div>
  );
}

/** `+12 -3` — how big the change is, in the shape every code host uses. */
function DiffStat({ added, removed }: { added: number; removed: number }) {
  if (added === 0 && removed === 0) return null;
  return (
    <span
      className="shrink-0 font-mono text-[10px] tabular-nums"
      data-testid="agent-diff-stat"
      data-added={added}
      data-removed={removed}
    >
      {added > 0 && <span className="diff-ink-add">+{added}</span>}
      {added > 0 && removed > 0 && <span className="text-muted-foreground/40"> </span>}
      {removed > 0 && <span className="diff-ink-del">-{removed}</span>}
    </span>
  );
}

/**
 * A turn's blocks, with consecutive tool calls collected into one group.
 *
 * Thinking and prose stay exactly where they happened — that is rule 2 and it
 * is not up for negotiation. What CAN be put away is a run of tool calls with
 * nothing between them: twenty greps and reads in a row is the "far too much
 * stuff" the maintainer objected to (2026-08-27), and it is also the part of
 * a finished turn nobody re-reads.
 */
export type TurnGroup =
  | { kind: "tools"; id: string; blocks: ToolBlock[] }
  | { kind: "block"; id: string; block: TextBlock | ReasoningBlock };

export function groupSteps(blocks: TurnBlock[]): TurnGroup[] {
  const out: TurnGroup[] = [];
  for (const block of blocks) {
    if (block.kind === "tool") {
      const last = out[out.length - 1];
      if (last && last.kind === "tools") {
        last.blocks.push(block);
        continue;
      }
      out.push({ kind: "tools", id: `g-${block.callId}`, blocks: [block] });
      continue;
    }
    out.push({ kind: "block", id: block.id, block });
  }
  return out;
}

/** From this many calls in a row, a finished run puts itself away. */
export const FOLD_STEPS_FROM = 4;

/**
 * A run of tool calls — open while it matters, one line once it does not.
 *
 * WHILE THE TURN WORKS every step shows: watching the work is the whole point
 * of a running turn, and a spinner behind a fold proves nothing. The moment
 * the turn is done a long run collapses to "14 steps · +42 -7 · 1m 12s",
 * which is what a finished conversation should read as — the answer, with the
 * work one click away.
 *
 * Two runs never fold, however long: one holding a step that FAILED, and one
 * holding a step still waiting for a decision. Both are the reason the person
 * is looking at the turn at all.
 */
function ToolGroup({
  blocks,
  turnLive,
  onDecide,
}: {
  blocks: ToolBlock[];
  turnLive: boolean;
  onDecide: (approvalId: string, decision: ApprovalDecision) => void;
}) {
  const [manual, setManual] = useState<boolean | null>(null);
  // A call still waiting for a decision is a question to the person: it holds
  // the whole run open, because an approval behind a fold is an approval
  // nobody gives. A call that FAILED does not — it stays in view on its own
  // while the rest of the run folds away around it.
  const asking = blocks.some((b) => b.approval !== null && b.approval.decision === null);
  const foldable = !turnLive && !asking && blocks.length >= FOLD_STEPS_FROM;
  const open = !foldable || (manual ?? false);
  const shown = open ? blocks : blocks.filter((b) => b.isError);
  const duration = blocks.reduce((sum, b) => sum + (b.durationMs ?? 0), 0);
  const stat = useMemo(() => {
    const files = blocks.flatMap((b) => toolDiff(b.name, b.input) ?? []);
    return files.length ? diffStat(files) : null;
  }, [blocks]);

  return (
    <div
      className="flex min-w-0 flex-col gap-1.5"
      data-testid="agent-tool-group"
      data-state={!foldable ? "plain" : open ? "open" : "folded"}
      data-steps={blocks.length}
    >
      {foldable && (
        <GroupToggle
          open={open}
          count={blocks.length}
          duration={duration}
          stat={stat}
          onClick={() => setManual(!open)}
        />
      )}
      {shown.map((b) => (
        <ToolRow key={b.callId} block={b} onDecide={onDecide} />
      ))}
    </div>
  );
}

/** The one line a folded run wears, and the way back out of an open one. */
function GroupToggle({
  open,
  count,
  duration,
  stat,
  onClick,
}: {
  open: boolean;
  count: number;
  duration: number;
  stat: { added: number; removed: number } | null;
  onClick: () => void;
}) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      data-testid="agent-tool-group-toggle"
      className={cn(
        "-ml-1 flex w-full min-w-0 items-center gap-2 rounded-md px-1 py-1",
        "text-left text-xs transition-colors hover:bg-secondary/50",
      )}
    >
      <ChevronRight
        className={cn(
          "h-3.5 w-3.5 shrink-0 text-muted-foreground/50 transition-transform",
          open && "rotate-90",
        )}
        aria-hidden
      />
      <span className="shrink-0 font-medium text-muted-foreground">
        {fill(t("agent_chat.steps_count"), { count: String(count) })}
      </span>
      {stat && <DiffStat added={stat.added} removed={stat.removed} />}
      <span className="flex-1" />
      {duration > 0 && (
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/60">
          {formatStepDuration(duration)}
        </span>
      )}
    </button>
  );
}

/**
 * One tool call — a ROW, not a card.
 *
 * The name is the agent's own — "PowerShell", "ToolSearch", "Grep" — never a
 * prettied-up rename, because the row is read next to the agent's log
 * (maintainer, 2026-08-23). The mark left of it comes from the tool family,
 * or the vendor's SVG for an MCP server. On the right sits the one thing
 * the person is actually asking of a running agent: whether this step is
 * still going. A finished call shows what it took; a running one counts up.
 *
 * A call that failed says why on the row itself, in one line — the point of
 * a failure is that you read it without hunting for it. Click to see the
 * whole of it, and the input when the summary did not already carry it.
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
  const elapsed = useElapsedMs(running ? block.startedMs : null);
  const view = agentToolView(block.name, block.input);
  const summary = view.summary;
  const showLogo = Boolean(view.logoUrl) && !logoFailed;
  const gist = block.isError && block.output ? errorGist(block.output) : "";
  const showInput = !inputAddsNothing(block.input, summary);
  // A coding call carries its change in its arguments. Read it once here: the
  // row wears the size of it, the fold paints the whole thing.
  const diff = useMemo(() => toolDiff(block.name, block.input), [block.name, block.input]);
  const stat = diff ? diffStat(diff) : null;

  return (
    <div
      data-testid="agent-tool"
      data-tool={block.name}
      data-family={view.family}
      data-state={
        pending
          ? "pending"
          : denied
            ? "denied"
            : done
              ? block.isError
                ? "error"
                : "done"
              : "running"
      }
      className={cn(
        "text-xs",
        // Only a call that WANTS something gets a frame of its own: an
        // approval is a question to the person, not a log line.
        pending && "rounded-lg border border-primary/40 bg-primary/5",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(
          "-ml-1 flex w-full min-w-0 items-center gap-2 rounded-md px-1 py-1 text-left transition-colors",
          pending ? "ml-0 px-2 pt-1.5" : "hover:bg-secondary/50",
        )}
      >
        <span className="grid h-4 w-4 shrink-0 place-items-center">
          {pending ? (
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
            <view.Icon
              className={cn(
                "h-3.5 w-3.5",
                block.isError ? "text-destructive/80" : "text-muted-foreground",
              )}
              aria-hidden
            />
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
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground/80">
            {summary}
          </span>
        ) : (
          <span className="flex-1" />
        )}
        {stat && !running && <DiffStat added={stat.added} removed={stat.removed} />}
        {running ? (
          <span className="inline-flex shrink-0 items-center gap-1.5 text-[10px] text-primary/90">
            <Spinner />
            <span className="font-mono tabular-nums">{formatStepDuration(elapsed)}</span>
          </span>
        ) : block.durationMs !== null && block.durationMs > 0 ? (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/60">
            {formatStepDuration(block.durationMs)}
          </span>
        ) : null}
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground/50 transition-transform",
            open && "rotate-90",
          )}
          aria-hidden
        />
      </button>

      {gist && !open && (
        <div
          className="ml-[1.375rem] truncate font-mono text-[11px] text-destructive/80"
          data-testid="agent-tool-gist"
        >
          {gist}
        </div>
      )}

      {pending && block.approval && (
        <div className="flex flex-col gap-2 px-2 pb-2" data-testid="agent-approval">
          <div className="text-foreground">
            <span className="font-medium">{t("agent_chat.approval_title")}</span>{" "}
            <span className="text-muted-foreground">{block.approval.summary || summary}</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => onDecide(block.approval!.approvalId, "allow")}
              className="inline-flex h-7 items-center gap-1 rounded-md bg-foreground/70 px-2.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
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
        <Trace tone={block.isError ? "error" : "plain"}>
          {diff ? (
            <DiffView files={diff} />
          ) : (
            showInput && <Detail label={t("agent_chat.tool_input")} text={pretty(block.input)} />
          )}
          {denied && <div className="text-muted-foreground">{t("agent_chat.approval_denied")}</div>}
          {block.output !== null
            ? // An edit's receipt — "The file … has been updated successfully" —
              // says nothing the diff above did not already show, so a call that
              // painted its change keeps its output for the case that matters:
              // when it failed.
              (!diff || block.isError) && (
                <Detail
                  label={block.isError ? t("agent_chat.tool_error") : t("agent_chat.tool_output")}
                  text={block.output}
                  error={block.isError}
                />
              )
            : running && (
                <div className="text-muted-foreground">{t("agent_chat.tool_running")}</div>
              )}
        </Trace>
      )}
    </div>
  );
}

/**
 * The one shape an opened detail wears: an indent and a hairline.
 *
 * Every unfolded thing in the column — a thought, a tool's input, its
 * output — is drawn this way, so the turn reads as one document with
 * margins rather than a stack of framed cards.
 */
function Trace({
  children,
  tone = "plain",
}: {
  children: ReactNode;
  tone?: "plain" | "error";
}) {
  return (
    <div
      className={cn(
        "ml-[0.375rem] mt-1 flex flex-col gap-2 border-l pl-3",
        tone === "error" ? "border-destructive/40" : "border-border",
      )}
    >
      {children}
    </div>
  );
}

function Detail({ label, text, error }: { label: string; text: string; error?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground/60">
        {label}
      </span>
      <pre
        className={cn(
          "scrollbar-jarvis max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed",
          error ? "text-destructive/90" : "text-muted-foreground",
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
      className="inline-block h-3 w-3 rounded-full border-2 border-primary/25 border-t-primary motion-safe:animate-spin"
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
