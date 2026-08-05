/**
 * The conversation, as a conversation.
 *
 * Reads the pane's transcript and renders it the way a chat client does: what
 * you said on one side, what the agent said on the other, and the work it did
 * in between as quiet, collapsible steps rather than as terminal output. The
 * terminal is still there and still running — it is simply not the thing you
 * are asked to read.
 *
 * Sub-agents get their own column on the right the moment one appears, because
 * "what are the helpers doing" is a different question from "what is the main
 * agent saying", and interleaving the two makes both unreadable.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Loader2, TriangleAlert, Users } from "lucide-react";

import { cn } from "@/lib/utils";
import { readTranscript, subagentsOf, type ChatEvent } from "./chatTranscript";

const POLL_MS = 1000;
const LINES = 300;

export interface ChatStreamProps {
  terminal: string;
  active?: boolean;
}

export function ChatStream({ terminal, active = true }: ChatStreamProps) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const following = useRef(true);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const tick = async () => {
      try {
        const response = await fetch(
          `/api/agentic-ide/terminals/${encodeURIComponent(terminal)}/report?lines=${LINES}`,
        );
        if (!response.ok) throw new Error(String(response.status));
        const body = (await response.json()) as { transcript?: string[] };
        if (!alive) return;
        setLines(body.transcript ?? []);
        setError(null);
      } catch {
        if (!alive) return;
        setError("This agent is no longer running.");
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [active, terminal]);

  const events = useMemo(() => readTranscript(lines ?? []), [lines]);
  const helpers = useMemo(() => subagentsOf(events), [events]);

  useEffect(() => {
    if (following.current) bottom.current?.scrollIntoView({ block: "end" });
  }, [events]);

  return (
    <div className="flex min-h-0 flex-1">
      <div
        onScroll={(event) => {
          const node = event.currentTarget;
          following.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
        }}
        data-testid="chat-stream"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-8 py-6"
      >
        <div className="mx-auto w-full max-w-3xl space-y-4">
          {error && (
            <div className="flex items-center gap-2 rounded-xl border border-amber-500/25 bg-amber-500/5 px-4 py-3 text-sm text-amber-300">
              <TriangleAlert className="h-4 w-4 shrink-0" aria-hidden />
              {error}
            </div>
          )}
          {lines === null && !error && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Waking the agent…
            </div>
          )}
          {lines !== null && events.length === 0 && !error && (
            <p className="text-sm text-muted-foreground">
              The agent is ready. Say what you want done.
            </p>
          )}
          {events.map((event, index) => (
            <Block key={`${event.kind}-${index}`} event={event} />
          ))}
          <div ref={bottom} />
        </div>
      </div>

      {helpers.length > 0 && (
        <aside className="w-64 shrink-0 overflow-y-auto scrollbar-jarvis border-l border-border/60 px-3 py-4">
          <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            <Users className="h-3.5 w-3.5" aria-hidden />
            Sub-agents
          </div>
          {helpers.map((helper, index) => (
            <div
              key={index}
              className="mb-1.5 rounded-lg border border-border/60 bg-card/40 px-2.5 py-2 text-[11px] leading-snug"
            >
              {helper.label ?? helper.text}
            </div>
          ))}
        </aside>
      )}
    </div>
  );
}

function Block({ event }: { event: ChatEvent }) {
  const [open, setOpen] = useState(false);

  if (event.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-secondary/70 px-4 py-2.5 text-sm leading-relaxed">
          {event.text}
        </div>
      </div>
    );
  }

  if (event.kind === "status") {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        {event.text}
      </div>
    );
  }

  if (event.kind === "step" || event.kind === "subagent") {
    const long = event.text.length > (event.label?.length ?? 0);
    return (
      <button
        type="button"
        onClick={() => long && setOpen((v) => !v)}
        className={cn(
          "flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors",
          long && "hover:bg-accent/30",
          event.kind === "subagent" ? "text-violet-300" : "text-muted-foreground",
        )}
      >
        <ChevronRight
          className={cn(
            "mt-0.5 h-3 w-3 shrink-0 transition-transform",
            open && "rotate-90",
            !long && "opacity-0",
          )}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{event.label ?? event.text}</span>
          {open && (
            <span className="mt-1 block whitespace-pre-wrap break-words font-mono text-[11px] text-muted-foreground/80">
              {event.text}
            </span>
          )}
        </span>
      </button>
    );
  }

  return (
    <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/90">
      {event.text}
    </div>
  );
}
