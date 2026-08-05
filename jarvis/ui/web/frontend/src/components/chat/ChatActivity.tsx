/**
 * What the coding agent is actually doing, as a readable feed.
 *
 * A pane carries a *terminal protocol*, not a log: a coding CLI repaints rows
 * in place, so the raw stream is unreadable and merely stripping the escape
 * codes leaves the surviving text in an order nobody wrote. The backend already
 * solves that — it replays the terminal onto a screen and reads the transcript
 * off it (`jarvis/agentic_ide/transcript.py`) — so this polls THAT and renders
 * the result, rather than inventing a second parser that would be wrong in
 * different ways.
 *
 * The feed is the honest middle ground while the structured per-CLI reader is
 * built: it is what the agent printed, in the order it printed it, with the
 * decoration folded away. It is not a guess, and it is never empty while
 * something is happening.
 */
import { useEffect, useRef, useState } from "react";
import { Loader2, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

/** How often the feed asks. Fast enough to feel live, cheap enough to leave on. */
const POLL_MS = 1200;

/** How much history the feed carries. Bounded — this is a view, not an archive. */
const LINES = 200;

interface Report {
  name: string;
  display_name?: string;
  status?: string;
  transcript?: string[];
}

export interface ChatActivityProps {
  /** Call-sign of the pane to follow. */
  terminal: string;
  /** Is this view on screen? Off-screen polling is stopped rather than throttled. */
  active?: boolean;
}

export function ChatActivity({ terminal, active = true }: ChatActivityProps) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  /*
   * Follow the tail only while the reader IS at the tail.
   *
   * Scrolling up is a deliberate act — somebody is reading what already
   * happened — and yanking them back down every 1.2 seconds makes the history
   * unreadable. The moment they return to the bottom, following resumes.
   */
  const following = useRef(true);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const tick = async () => {
      try {
        const response = await fetch(
          `/api/agentic-ide/terminals/${encodeURIComponent(terminal)}/report?lines=${LINES}`,
        );
        if (!response.ok) throw new Error(await response.text());
        const body = (await response.json()) as Report;
        if (!alive) return;
        setReport(body);
        setError(null);
      } catch {
        if (!alive) return;
        // A pane that has gone away is a state, not a crash: the chat is still
        // readable and the header says the agent stopped.
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

  useEffect(() => {
    if (following.current) bottom.current?.scrollIntoView({ block: "end" });
  }, [report]);

  const lines = report?.transcript ?? [];

  return (
    <div
      ref={scroller}
      data-testid="chat-activity"
      onScroll={(event) => {
        const node = event.currentTarget;
        following.current =
          node.scrollHeight - node.scrollTop - node.clientHeight < 48;
      }}
      className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-6 py-4"
    >
      <div className="mx-auto w-full max-w-3xl">
        {error && (
          <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
            {error}
          </div>
        )}
        {!report && !error && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Starting the agent…
          </div>
        )}
        {lines.length === 0 && report && !error && (
          <div className="text-xs text-muted-foreground">
            The agent is up and has not printed anything yet.
          </div>
        )}
        {lines.length > 0 && (
          <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-foreground/85">
            {lines.join("\n")}
          </pre>
        )}
        <div ref={bottom} />
      </div>
    </div>
  );
}

/** A small live/idle mark for the chat header. */
export function ActivityDot({ status }: { status?: string }) {
  const working = status === "running" || status === "working";
  return (
    <span
      title={working ? "Working" : "Idle"}
      aria-label={working ? "Working" : "Idle"}
      className={cn(
        "h-1.5 w-1.5 shrink-0 rounded-full",
        working ? "animate-pulse bg-emerald-400" : "bg-muted-foreground/50",
      )}
    />
  );
}
