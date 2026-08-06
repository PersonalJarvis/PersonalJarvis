/**
 * The conversation, as a conversation.
 *
 * ## Where this reads from, and why it changed
 *
 * It used to read the pane's SCREEN and classify the lines back into a chat.
 * That cannot be made to work, because a TUI is a picture rather than a
 * protocol: it repaints rows in place, wraps to whatever width it happened to
 * have, draws its own logo, and prints spinners. The first version of this view
 * showed a half-rendered ASCII banner, a raw `$ cd … && git diff` line and
 * `Churned for 5s` — all of them faithfully transcribed, none of them anything
 * a person wants to read.
 *
 * So it reads the record the CLI keeps in order to resume itself
 * (`/terminals/{name}/conversation`). There, roles are declared and tool calls
 * carry their name and arguments, so this component renders rather than guesses:
 * prose as Markdown, work as quiet collapsible steps, and nothing at all for
 * the banner — because the banner was never in the conversation to begin with.
 *
 * ## Two sources, on purpose
 *
 * The file says what was SAID; it is written as turns complete. The pane says
 * what is happening RIGHT NOW. So the live status line at the bottom still
 * comes from the pane, and a CLI that keeps no readable record falls back to
 * the pane entirely — honestly, with the terminal offered rather than an error.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Loader2, TriangleAlert } from "lucide-react";
import ReactMarkdown from "react-markdown";

import { cn } from "@/lib/utils";
import {
  fetchConversation,
  type AgentStep,
  type AgentTurn,
} from "@/lib/agentConversationApi";

/**
 * How often the conversation is re-read.
 *
 * Slower than the pane's own poll: this file changes when a turn COMPLETES, not
 * while it is being typed, so asking four times a second would be four times
 * the disk for the same answer. The live line below it is what moves quickly.
 */
const POLL_MS = 1500;

export interface ChatStreamProps {
  terminal: string;
  active?: boolean;
  /**
   * Show the raw pane instead. Called when this CLI keeps no readable record —
   * the surface has the terminal and should mount it rather than leave the
   * reader with an empty column.
   */
  onUnavailable?: () => void;
}

export function ChatStream({ terminal, active = true, onUnavailable }: ChatStreamProps) {
  const [turns, setTurns] = useState<AgentTurn[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  /*
   * Follow the tail only while the reader IS at the tail. Scrolling up is a
   * deliberate act — somebody is reading what already happened — and yanking
   * them back down on every poll makes the history unreadable.
   */
  const following = useRef(true);
  const reportedUnavailable = useRef(false);

  const notifyUnavailable = useCallback(() => {
    if (reportedUnavailable.current) return;
    reportedUnavailable.current = true;
    onUnavailable?.();
  }, [onUnavailable]);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const tick = async () => {
      try {
        const body = await fetchConversation(terminal);
        if (!alive) return;
        // Settled "this CLI keeps no readable record" — hand over to the pane.
        // NOT the same as "no conversation yet": one CLI only reveals its
        // session id after its first turn, and giving up on that would mean a
        // conversation seconds away was never shown.
        if (!body.readable) {
          notifyUnavailable();
          return;
        }
        setTurns(body.available ? body.turns : []);
        setError(null);
      } catch {
        if (!alive) return;
        // A pane that has gone away is a state, not a crash. Said once, in the
        // reader's language of "this stopped", not as a status code.
        setError("This agent is no longer running.");
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [active, notifyUnavailable, terminal]);

  // Keyed on the turn count and the last turn's length: the tail grows both by
  // gaining turns and by the open turn getting longer, and only those two mean
  // "there is new text below".
  const tailKey = useMemo(() => {
    const last = turns?.[turns.length - 1];
    return `${turns?.length ?? 0}:${last?.text.length ?? 0}:${last?.steps.length ?? 0}`;
  }, [turns]);

  useEffect(() => {
    if (following.current) bottom.current?.scrollIntoView({ block: "end" });
  }, [tailKey]);

  return (
    <div
      onScroll={(event) => {
        const node = event.currentTarget;
        following.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
      }}
      data-testid="chat-stream"
      className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-8 py-8"
    >
      <div className="mx-auto flex w-full max-w-[46rem] flex-col gap-7">
        {error && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-3 text-sm text-amber-300">
            <TriangleAlert className="h-4 w-4 shrink-0" aria-hidden />
            {error}
          </div>
        )}
        {turns === null && !error && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Reading the conversation…
          </div>
        )}
        {turns !== null && turns.length === 0 && !error && (
          <p className="text-[15px] text-muted-foreground">
            The agent is ready. Say what you want done.
          </p>
        )}
        {(turns ?? []).map((turn, index) => (
          <TurnBlock key={index} turn={turn} />
        ))}
        <div ref={bottom} />
      </div>
    </div>
  );
}

function TurnBlock({ turn }: { turn: AgentTurn }) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl bg-foreground/[0.07] px-4 py-2.5 text-[15px] leading-[1.6]">
          {turn.text}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {turn.text && <Prose text={turn.text} />}
      {turn.steps.length > 0 && (
        <div className="flex flex-col gap-0.5">
          {turn.steps.map((step, index) => (
            <StepRow key={index} step={step} />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The agent's own words, rendered as the Markdown they are.
 *
 * A coding agent writes in Markdown — headings, lists, fenced code — and
 * showing that as preformatted text was half of why the old view looked like a
 * log file. The type scale is a reading scale rather than a terminal one: this
 * is prose, and it is the only thing on this surface anybody reads end to end.
 */
function Prose({ text }: { text: string }) {
  return (
    <div
      className={cn(
        "text-[15.5px] leading-[1.7] tracking-[-0.003em] text-foreground/90",
        "[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
        "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5",
        "[&_li]:my-0.5",
        "[&_h1]:mb-2 [&_h1]:mt-4 [&_h1]:text-[17px] [&_h1]:font-semibold",
        "[&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-[16px] [&_h2]:font-semibold",
        "[&_h3]:mb-1.5 [&_h3]:mt-3 [&_h3]:text-[15px] [&_h3]:font-semibold",
        "[&_strong]:font-semibold [&_strong]:text-foreground",
        "[&_code]:rounded [&_code]:bg-foreground/[0.07] [&_code]:px-1 [&_code]:py-0.5",
        "[&_code]:font-mono [&_code]:text-[13px]",
        "[&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-foreground/[0.05]",
        "[&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-[12.5px]",
        "[&_a]:underline [&_a]:underline-offset-2",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3",
        "[&_blockquote]:text-muted-foreground",
      )}
    >
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  );
}

/**
 * One tool call, at the weight of a footnote.
 *
 * The name the CLI gave the tool and the one argument worth seeing — a path, a
 * command — on a single line that does not wrap. Everything else is behind the
 * disclosure, because the reason to show steps at all is "I can see what it
 * touched", and that question is answered by forty short lines, never by forty
 * pretty-printed JSON objects.
 */
function StepRow({ step }: { step: AgentStep }) {
  const [open, setOpen] = useState(false);
  const expandable = Boolean(step.detail);

  return (
    <div>
      <button
        type="button"
        onClick={() => expandable && setOpen((v) => !v)}
        aria-expanded={expandable ? open : undefined}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[12.5px]",
          "text-muted-foreground transition-colors",
          expandable && "hover:bg-foreground/[0.05] hover:text-foreground/80",
        )}
      >
        <ChevronRight
          className={cn(
            "h-3 w-3 shrink-0 transition-transform",
            open && "rotate-90",
            !expandable && "opacity-0",
          )}
          aria-hidden
        />
        <span className="shrink-0 font-medium text-foreground/70">{step.tool}</span>
        {step.target && (
          <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-muted-foreground/80">
            {step.target}
          </span>
        )}
      </button>
      {open && step.detail && (
        <pre className="ml-6 mt-1 max-h-72 overflow-auto scrollbar-jarvis whitespace-pre-wrap break-words rounded-md bg-foreground/[0.04] p-2.5 font-mono text-[11.5px] leading-relaxed text-muted-foreground">
          {step.detail}
        </pre>
      )}
    </div>
  );
}
