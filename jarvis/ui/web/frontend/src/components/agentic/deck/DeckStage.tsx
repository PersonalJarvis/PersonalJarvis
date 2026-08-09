/**
 * The Command Deck: the conversation is the subject, the terminals recede.
 *
 * Grid and chat both put terminal output on screen and differ in how much. The
 * deck differs in KIND — the orb is centre stage, each agent is a card around
 * it, and the terminals are folded away until you ask for one. That is not a
 * styling choice: it is what the mode is for. You brief by voice, Jarvis runs
 * the floor, and the work comes back to you as a spoken report rather than as
 * something you have to notice.
 *
 * ## What this component does and does not own
 *
 * It draws the room. It does NOT own a single pane: the terminals stay mounted
 * in the grid's canvas underneath, and unfolding a card is the grid restyling
 * the pane it already has. Rendering a terminal here would remount it, and a
 * remounted pane is a dead coding agent — the iron rule of this whole section.
 *
 * The orb is the same `VoiceOrb` the floating bubble uses, at a size that makes
 * it the subject rather than an ornament. It is deliberately not a second voice
 * control: clicking it does what the bubble's orb does, through the same
 * handler, because two ways to start a conversation that behave differently is
 * the bug that costs a user their trust in both.
 *
 * ## Silence is a state, and it is drawn
 *
 * The deck opens quiet — the mic is not live until it is asked for — and it
 * says so. A voice-first surface that looks identical whether or not it is
 * listening is the single most uncomfortable thing it could be.
 */
import { Mic, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { VoiceOrb } from "../VoiceOrb";
import type { VoiceState } from "@/store/events";
import type { DeckReport } from "@/lib/agenticIdeApi";
import { AgentCard, type CardState } from "./AgentCard";
import { ReportLane } from "./ReportLane";

export interface DeckAgent {
  name: string;
  agent: string;
  agentLabel: string;
  task: string;
  state: CardState;
}

export interface DeckStageProps {
  agents: DeckAgent[];
  voiceState: VoiceState;
  /** Is a conversation open right now? Drives the orb's caption, not the orb. */
  listening: boolean;
  onToggleVoice?: () => void;
  expanded: string | null;
  onToggleExpand: (name: string) => void;
  onToggleHold: (name: string) => void;
  onOpenTerminal?: () => void;
  /** Panes with news waiting, so their card can carry a dot. */
  reporting: ReadonlySet<string>;
  pending: DeckReport[];
  onAir: DeckReport | null;
  sleeping: boolean;
  onHear: (id: string) => void;
  onDropReport: (id: string) => void;
  onWake: () => void;
}

export function DeckStage({
  agents,
  voiceState,
  listening,
  onToggleVoice,
  expanded,
  onToggleExpand,
  onToggleHold,
  onOpenTerminal,
  reporting,
  pending,
  onAir,
  sleeping,
  onHear,
  onDropReport,
  onWake,
}: DeckStageProps) {
  return (
    <div
      data-testid="deck-stage"
      className="flex h-full min-h-0 w-full gap-4 overflow-hidden p-2"
    >
      <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center gap-6 overflow-y-auto">
        {/*
          The orb, and the one sentence that says whether it is hearing you.
          Both are buttons onto the same handler the bubble uses — a second
          voice control with its own behaviour is two things to keep in step.
        */}
        <div className="flex shrink-0 flex-col items-center gap-3 pt-4">
          <button
            type="button"
            data-testid="deck-orb"
            onClick={onToggleVoice}
            disabled={!onToggleVoice}
            aria-pressed={listening}
            aria-label={listening ? "Hang up" : "Start talking to Jarvis"}
            className={cn(
              "rounded-full transition-transform",
              onToggleVoice
                ? "hover:scale-[1.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                : "cursor-default",
            )}
          >
            <VoiceOrb state={voiceState} size={132} />
          </button>
          <p
            data-testid="deck-orb-caption"
            className="flex items-center gap-1.5 text-xs text-muted-foreground"
          >
            <Mic className={cn("h-3 w-3 shrink-0", listening && "text-primary")} />
            {listening
              ? "Listening — say what you want done."
              : "Tap the orb or say the wake word to hand out work."}
          </p>
        </div>

        {/* The room. One card per agent, laid out as a table rather than a list. */}
        {agents.length === 0 ? (
          <div
            data-testid="deck-empty"
            className="flex flex-col items-center gap-3 pt-6 text-center"
          >
            <p className="max-w-sm text-sm text-muted-foreground">
              No agents in this workspace yet. Open one and you can start handing
              out work by voice.
            </p>
            {onOpenTerminal && (
              <button
                type="button"
                data-testid="deck-open-terminal"
                onClick={onOpenTerminal}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-control border border-border px-3 py-1.5",
                  "text-xs text-muted-foreground transition-colors",
                  "hover:bg-secondary hover:text-foreground",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                )}
              >
                <Plus className="h-3.5 w-3.5 shrink-0" />
                Open a terminal
              </button>
            )}
          </div>
        ) : (
          <div
            data-testid="deck-cards"
            className="grid w-full max-w-4xl grid-cols-1 gap-3 pb-4 sm:grid-cols-2 lg:grid-cols-3"
          >
            {agents.map((entry) => (
              <AgentCard
                key={entry.name}
                name={entry.name}
                agent={entry.agent}
                agentLabel={entry.agentLabel}
                task={entry.task}
                state={entry.state}
                expanded={expanded === entry.name}
                reporting={reporting.has(entry.name)}
                onToggleExpand={() => onToggleExpand(entry.name)}
                onToggleHold={() => onToggleHold(entry.name)}
              />
            ))}
          </div>
        )}
      </div>

      <ReportLane
        pending={pending}
        onAir={onAir}
        sleeping={sleeping}
        onHear={onHear}
        onDrop={onDropReport}
        onWake={onWake}
      />
    </div>
  );
}
