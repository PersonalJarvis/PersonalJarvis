/**
 * One agent and a compact live view of its real terminal.
 *
 * The Command Deck's whole premise is that you are not reading terminals — you
 * are running a team. So a card answers the three things you would ask about a
 * colleague: who they are, what is actually happening in their terminal, and
 * whether they need you. The card mirrors the existing xterm buffer; it never
 * owns a socket or an agent. Expanding it therefore reveals the very same
 * terminal rather than creating a second session.
 *
 * ## The ring
 *
 * State is carried by a ring around the mark rather than by a coloured label,
 * for the same reason the toolbar spends colour only on what is ON: eight
 * labelled cards read as a form to fill in, eight rings read as a room. The
 * four states are deliberately distinguishable without colour too (motion,
 * weight, a glyph), because "which of these needs me" must survive a
 * colour-blind user and a bad monitor.
 *
 * * **working** — a slow pulse. Something is happening and it is not urgent.
 * * **waiting** — still and warm. Done, and nobody is being asked for anything.
 * * **asking** — the one state that attracts the eye. Work has STOPPED here.
 * * **held** — dimmed, with the user's own mark. Jarvis is not using this one.
 *
 * Nothing on this card claims the work went well. "Finished" means the pane
 * went quiet — the same claim the bell and the spoken report make, worded the
 * same way on purpose.
 */
import { Hand, Loader2, MessageCircleQuestion, Terminal } from "lucide-react";
import { cn } from "@/lib/utils";
import { AgentMark } from "../AgentMark";
import { useTerminalPreview } from "../terminalPreview";

/** What a card is showing, resolved by the stage rather than by the card. */
export type CardState = "working" | "waiting" | "asking" | "held" | "dead";

export interface AgentCardProps {
  name: string;
  /** The CLI behind the pane, for its mark. */
  agent: string;
  /** That CLI's own name, for the mark's alt text. */
  agentLabel: string;
  state: CardState;
  /** Is this card's terminal unfolded right now? */
  expanded: boolean;
  /** Does this agent have news waiting in the lane? */
  reporting?: boolean;
  onToggleExpand: () => void;
  onToggleHold: () => void;
}

const RING: Record<CardState, string> = {
  // A pulse rather than a spinner: a spinner is a thing you wait in front of,
  // and the point of this mode is that you do not have to.
  working: "border-primary/70 shadow-[0_0_0_3px_hsl(var(--primary)/0.10)]",
  waiting: "border-border",
  // The only card that raises its voice, because it is the only one where work
  // has stopped and cannot restart without the user.
  asking: "border-amber-400/80 shadow-[0_0_0_3px_hsl(38_92%_50%/0.16)]",
  held: "border-border/50",
  dead: "border-destructive/50",
};

const LABEL: Record<CardState, string> = {
  working: "Working",
  waiting: "Finished and waiting",
  asking: "Needs your answer",
  held: "You have this one",
  dead: "Its agent stopped",
};

export function AgentCard({
  name,
  agent,
  agentLabel,
  state,
  expanded,
  reporting = false,
  onToggleExpand,
  onToggleHold,
}: AgentCardProps) {
  const held = state === "held";
  const terminal = useTerminalPreview(name);
  return (
    <div
      data-testid={`deck-card-${name}`}
      data-state={state}
      data-expanded={expanded ? "yes" : "no"}
      className={cn(
        "group relative flex min-h-[15rem] min-w-0 flex-col gap-4 rounded-xl border bg-card/60 p-5",
        "transition-colors",
        RING[state],
        held && "opacity-70",
      )}
    >
      <div className="flex min-w-0 items-center gap-3">
        <span
          className={cn(
            "relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full",
            "border bg-background/60",
            RING[state],
            // The pulse is the ring's, not the mark's: a breathing logo reads
            // as decoration, a breathing outline reads as a state.
            state === "working" && "animate-pulse motion-reduce:animate-none",
          )}
        >
          {/* Plain, not boxed: the ring around it is already the frame, and a
              tile inside a ring is two borders for one thing. */}
          <AgentMark
            agent={agent}
            label={agentLabel}
            variant="plain"
            size="md"
          />
          {reporting && (
            <span
              data-testid={`deck-card-dot-${name}`}
              aria-hidden
              className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-primary"
            />
          )}
        </span>
        <span className="flex min-w-0 flex-col">
          {/* The pane call-sign is useful, but it must never hide which coding
              CLI is actually running here. Keeping both in the heading makes
              a Claude Code pane stay recognisable as Claude Code even when
              its default call-sign is only T1. */}
          <span className="flex min-w-0 items-baseline gap-1.5 text-foreground">
            <span className="truncate text-base font-semibold">{name}</span>
            <span aria-hidden className="shrink-0 text-muted-foreground/60">
              ·
            </span>
            <span
              data-testid={`deck-card-agent-label-${name}`}
              className="truncate text-sm font-medium text-muted-foreground"
              title={agentLabel}
            >
              {agentLabel}
            </span>
          </span>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {state === "working" && (
              <Loader2 className="h-3 w-3 shrink-0 animate-spin motion-reduce:animate-none" />
            )}
            {state === "asking" && (
              <MessageCircleQuestion className="h-3 w-3 shrink-0 text-amber-400" />
            )}
            {held && <Hand className="h-3 w-3 shrink-0" />}
            <span className="truncate">{LABEL[state]}</span>
          </span>
        </span>
      </div>

      <div
        data-testid={`deck-card-terminal-${name}`}
        aria-label={`Live terminal output for ${name}`}
        className={cn(
          "relative min-h-[9rem] flex-1 overflow-hidden rounded-xl border border-border/80",
          "bg-background/95 shadow-inner backdrop-blur-sm",
        )}
      >
        {terminal.length > 0 ? (
          <div className="h-full overflow-hidden p-3 font-mono text-xs leading-[1.55] text-foreground/90">
            {terminal.map((line, index) => (
              <div
                key={`${index}-${line}`}
                className="min-h-[1.55em] truncate whitespace-pre"
                title={line || undefined}
              >
                {line || "\u00a0"}
              </div>
            ))}
          </div>
        ) : (
          <div className="flex h-full min-h-[9rem] items-center justify-center px-3 text-center font-mono text-xs text-muted-foreground/70">
            Waiting for live terminal output...
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid={`deck-card-expand-${name}`}
          aria-pressed={expanded}
          onClick={onToggleExpand}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-xs",
            "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
            expanded && "bg-secondary text-foreground",
          )}
        >
          <Terminal className="h-3.5 w-3.5 shrink-0" />
          {expanded ? "Hide terminal" : "Show terminal"}
        </button>
        <button
          type="button"
          data-testid={`deck-card-hold-${name}`}
          aria-pressed={held}
          onClick={onToggleHold}
          title={
            held
              ? "Hand this agent back to Jarvis"
              : "Take this one yourself — Jarvis stops assigning to it and stops reporting it"
          }
          className={cn(
            "ml-auto inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-xs",
            "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
            held &&
              "bg-primary/15 text-primary hover:bg-primary/20 hover:text-primary",
          )}
        >
          <Hand className="h-3.5 w-3.5 shrink-0" />
          {held ? "Yours" : "I'll take it"}
        </button>
      </div>
    </div>
  );
}
