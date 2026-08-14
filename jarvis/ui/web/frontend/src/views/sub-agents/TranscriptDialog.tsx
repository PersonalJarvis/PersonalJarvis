/**
 * Chat-like transcript of one agent row on the operations board.
 *
 * Renders what the worker ACTUALLY did — thinking, every tool call with its
 * input and result, the final answer — from
 * `GET /api/sub-agents/{trace_id}/transcript` (live stream.jsonl while the
 * worker runs, the durable archive afterwards; every preview is redacted +
 * capped server-side). Polls while the agent is still running so the view
 * scrolls along with the work, like the normal chat does.
 *
 * A sha256 in a tool result is treated as a captured-frame reference and
 * rendered inline via /api/screenshots/{hash} — the thumbnail IS the
 * evidence; a hash that resolves to nothing simply hides itself.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertCircle,
  BookOpenText,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  TerminalSquare,
  X,
} from "lucide-react";

import type { SubAgentNode } from "@/store/jarvisAgents";
import { cn } from "@/lib/utils";

interface TranscriptItem {
  kind: "thinking" | "text" | "tool_use" | "tool_result" | "result" | "truncated";
  text?: string;
  tool_use_id?: string;
  tool_name?: string;
  args_preview?: string;
  preview?: string;
  is_error?: boolean;
  dropped_items?: number;
}

interface TranscriptResponse {
  source: "live" | "archive";
  items: TranscriptItem[];
  stream_bytes: number;
  worker_trace_id: string;
}

/** One renderable block: a tool_use merged with its tool_result. */
interface ToolBlock {
  kind: "tool";
  use: TranscriptItem;
  result?: TranscriptItem;
}

type Block = ToolBlock | { kind: "item"; item: TranscriptItem };

function mergeToolBlocks(items: TranscriptItem[]): Block[] {
  const blocks: Block[] = [];
  const openTools = new Map<string, ToolBlock>();
  for (const item of items) {
    if (item.kind === "tool_use") {
      const block: ToolBlock = { kind: "tool", use: item };
      if (item.tool_use_id) openTools.set(item.tool_use_id, block);
      blocks.push(block);
    } else if (item.kind === "tool_result") {
      const block = item.tool_use_id ? openTools.get(item.tool_use_id) : undefined;
      if (block && !block.result) {
        block.result = item;
      } else {
        blocks.push({ kind: "item", item });
      }
    } else {
      blocks.push({ kind: "item", item });
    }
  }
  return blocks;
}

const SCREENSHOT_HASH_RE = /\b[0-9a-f]{64}\b/g;

function screenshotHashes(text: string | undefined): string[] {
  if (!text) return [];
  return [...new Set(text.match(SCREENSHOT_HASH_RE) ?? [])].slice(0, 3);
}

function ScreenshotStrip({ hashes }: { hashes: string[] }) {
  if (hashes.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {hashes.map((hash) => (
        <a
          key={hash}
          href={`/api/screenshots/${hash}`}
          target="_blank"
          rel="noreferrer"
          className="block overflow-hidden rounded-md border border-border"
        >
          <img
            src={`/api/screenshots/${hash}`}
            alt="captured frame"
            loading="lazy"
            className="max-h-40 max-w-full object-contain"
            onError={(event) => {
              // Not every 64-hex token is a captured frame — hide the miss.
              const anchor = event.currentTarget.parentElement;
              if (anchor) anchor.style.display = "none";
            }}
          />
        </a>
      ))}
    </div>
  );
}

function ThinkingBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-border/70 bg-sheen/[0.03]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground transition-colors hover:text-foreground"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <Brain className="h-3.5 w-3.5 text-primary/70" />
        Thinking
      </button>
      {open && (
        <div className="whitespace-pre-wrap border-t border-border/70 px-3 py-2.5 text-xs italic leading-relaxed text-muted-foreground">
          {text}
        </div>
      )}
    </div>
  );
}

function ToolCard({ block }: { block: ToolBlock }) {
  const [open, setOpen] = useState(false);
  const failed = block.result?.is_error === true;
  const pending = block.result === undefined;
  const hashes = screenshotHashes(block.result?.preview);
  return (
    <div className="rounded-lg border border-border bg-card/70">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left font-mono text-xs transition-colors hover:bg-sheen/[0.05]"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
        <TerminalSquare className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="truncate text-foreground">{block.use.tool_name || "tool"}</span>
        <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
          {block.use.args_preview}
        </span>
        <span
          className={cn(
            "shrink-0 font-mono text-[10px] uppercase tracking-[0.16em]",
            pending && "text-primary",
            !pending && !failed && "text-emerald-600 dark:text-emerald-400",
            failed && "text-destructive",
          )}
        >
          {pending ? "running" : failed ? "failed" : "done"}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border px-3 py-2.5">
          {block.use.args_preview && (
            <div>
              <div className="mb-1 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                Input
              </div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-sheen/[0.04] p-2 text-[11px] leading-relaxed text-foreground scrollbar-jarvis">
                {block.use.args_preview}
              </pre>
            </div>
          )}
          {block.result?.preview && (
            <div>
              <div className="mb-1 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                {failed ? "Error" : "Result"}
              </div>
              <pre
                className={cn(
                  "max-h-64 overflow-auto whitespace-pre-wrap rounded bg-sheen/[0.04] p-2 text-[11px] leading-relaxed scrollbar-jarvis",
                  failed ? "text-destructive" : "text-foreground",
                )}
              >
                {block.result.preview}
              </pre>
            </div>
          )}
          <ScreenshotStrip hashes={hashes} />
        </div>
      )}
    </div>
  );
}

export function TranscriptDialog({
  agent,
  open,
  onOpenChange,
}: {
  agent: SubAgentNode | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): JSX.Element {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  const traceId = agent?.trace_id ?? "";
  const running = agent?.status === "running";

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!traceId) return;
    try {
      const res = await fetch(`/api/sub-agents/${traceId}/transcript`, { signal });
      if (res.status === 404) {
        setTranscript(null);
        setError("");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setTranscript((await res.json()) as TranscriptResponse);
      setError("");
    } catch (reason) {
      if ((reason as { name?: string }).name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [traceId]);

  useEffect(() => {
    if (!open || !traceId) return;
    const controller = new AbortController();
    setLoading(true);
    void load(controller.signal).finally(() => setLoading(false));
    // Poll only while the agent still runs — an archived transcript is final.
    const id = running
      ? window.setInterval(() => void load(), 2_500)
      : undefined;
    return () => {
      controller.abort();
      if (id !== undefined) window.clearInterval(id);
    };
  }, [open, traceId, running, load, reloadToken]);

  useEffect(() => {
    // Follow the newest activity like a terminal — but only while the user
    // has not scrolled up to read something.
    const scroller = scrollerRef.current;
    if (scroller && stickToBottomRef.current) {
      scroller.scrollTop = scroller.scrollHeight;
    }
  }, [transcript]);

  const blocks = transcript ? mergeToolBlocks(transcript.items) : [];
  const empty = !loading && !error && blocks.length === 0;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="agent-transcript-dialog"
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex h-[min(84dvh,52rem)] w-[min(880px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card shadow-[0_28px_90px_-24px_rgba(0,0,0,0.75)] outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-2">
                <BookOpenText className="h-4 w-4 text-primary" aria-hidden="true" />
                <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                  Agent transcript
                </Dialog.Title>
                {transcript && (
                  <span
                    className={cn(
                      "rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.18em]",
                      transcript.source === "live"
                        ? "border-primary/30 bg-primary/5 text-primary"
                        : "border-border bg-sheen/[0.04] text-muted-foreground",
                    )}
                  >
                    {transcript.source}
                  </span>
                )}
              </div>
              <Dialog.Description className="truncate text-xs leading-relaxed text-muted-foreground">
                {agent?.utterance || "What this agent thought, called and produced."}
              </Dialog.Description>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                aria-label="Refresh"
                title="Refresh"
                onClick={() => setReloadToken((token) => token + 1)}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:translate-y-px"
              >
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              </button>
              <Dialog.Close asChild>
                <button
                  type="button"
                  aria-label="Close"
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:translate-y-px"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </Dialog.Close>
            </div>
          </header>

          <div
            ref={scrollerRef}
            onScroll={(event) => {
              const el = event.currentTarget;
              stickToBottomRef.current =
                el.scrollHeight - el.scrollTop - el.clientHeight < 48;
            }}
            className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4 scrollbar-jarvis"
          >
            {loading && (
              <div className="py-8 text-center font-mono text-xs text-muted-foreground">
                Loading transcript…
              </div>
            )}
            {error && (
              <div className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {error}
              </div>
            )}
            {empty && (
              <div className="py-8 text-center text-sm text-muted-foreground">
                No transcript recorded for this agent — router turns and
                errands do their work without a worker stream.
              </div>
            )}
            {blocks.map((block, index) => {
              if (block.kind === "tool") {
                return <ToolCard key={index} block={block} />;
              }
              const item = block.item;
              if (item.kind === "truncated") {
                return (
                  <div
                    key={index}
                    className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-center font-mono text-[11px] text-amber-600 dark:text-amber-400"
                  >
                    {item.dropped_items} earlier steps not shown — the full
                    record stays in the archived stream.
                  </div>
                );
              }
              if (item.kind === "thinking") {
                return <ThinkingBlock key={index} text={item.text ?? ""} />;
              }
              if (item.kind === "result") {
                return (
                  <div
                    key={index}
                    className="rounded-lg border border-emerald-600/30 bg-emerald-500/5 px-3.5 py-3"
                  >
                    <div className="mb-1 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-emerald-600 dark:text-emerald-400">
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      Result
                    </div>
                    <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                      {item.text}
                    </div>
                  </div>
                );
              }
              if (item.kind === "tool_result") {
                // Orphaned result (its tool_use fell outside the window).
                return (
                  <pre
                    key={index}
                    className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-sheen/[0.03] px-3 py-2 text-[11px] leading-relaxed text-muted-foreground scrollbar-jarvis"
                  >
                    {item.preview}
                  </pre>
                );
              }
              return (
                <div
                  key={index}
                  className="whitespace-pre-wrap rounded-lg border border-border bg-card/70 px-3.5 py-3 text-sm leading-relaxed text-foreground"
                >
                  {item.text}
                </div>
              );
            })}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
