/**
 * The Command Deck: the conversation is the subject, the terminals recede.
 *
 * Grid and chat both put full terminals on screen. The deck keeps the orb at
 * centre stage and shows each terminal as a compact live viewport, so the user
 * can see what is really running without giving every pane the whole canvas.
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
import { AudioLines, Mic, MicOff, Plus } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore, type VoiceState } from "@/store/events";
import { VoiceOrb } from "../VoiceOrb";
import { useVoiceCall } from "../useVoiceCall";
import type { DeckReport } from "@/lib/agenticIdeApi";
import { AgentCard, type CardState } from "./AgentCard";
import { ReportLane } from "./ReportLane";

export interface DeckAgent {
  name: string;
  agent: string;
  agentLabel: string;
  state: CardState;
}

export interface DeckStageProps {
  agents: DeckAgent[];
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

const VOICE_STATUS_KEY: Record<VoiceState, string> = {
  idle: "agentic_grid.voice_bubble.ready",
  connecting: "voice_state.connecting",
  listening: "agentic_grid.voice_bubble.listening",
  thinking: "agentic_grid.voice_bubble.thinking",
  speaking: "agentic_grid.voice_bubble.speaking",
  paused: "agentic_grid.voice_bubble.paused",
  error: "agentic_grid.voice_bubble.error",
};

function voiceDetail(
  t: (key: string) => string,
  state: VoiceState,
  assistantName: string,
): string {
  if (state === "idle") {
    return t("agentic_grid.voice_bubble.talk_title").replace(
      "{0}",
      assistantName,
    );
  }
  if (state === "connecting") return t("voice_state.warming_hint");
  if (state === "listening") return t("voice_state.ready_title");
  if (state === "thinking") return t("sidebar.realtime_working");
  if (state === "error") return t("sidebar.realtime_error");
  return t(VOICE_STATUS_KEY[state]);
}

function voiceHeadline(
  t: (key: string) => string,
  state: VoiceState,
  assistantName: string,
): string {
  if (state === "idle") {
    return t("agentic_grid.voice_bubble.talk").replace("{0}", assistantName);
  }
  if (state === "connecting") return t("voice_state.warming_title");
  if (state === "listening") return t("voice_state.ready_title");
  if (state === "thinking") return t("sidebar.realtime_working");
  if (state === "error") return t("sidebar.realtime_error");
  return t(VOICE_STATUS_KEY[state]);
}

export function DeckStage({
  agents,
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
  const t = useT();
  const { active, busy, connecting, toggleCall, voiceState } = useVoiceCall();
  const transcription = (
    useEventStore((store) => store.transcription) ?? ""
  ).trim();
  const assistantName =
    (useEventStore((store) => store.assistantName) ?? "").trim() ||
    t("agentic_grid.voice_bubble.assistant_fallback");
  const disabled = busy || connecting;
  const status = t(VOICE_STATUS_KEY[voiceState]);
  const headline = voiceHeadline(t, voiceState, assistantName);
  const liveTranscript = active ? transcription : "";
  const detail = liveTranscript || voiceDetail(t, voiceState, assistantName);

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
        <section
          data-testid="deck-voice-control"
          data-state={voiceState}
          aria-labelledby="deck-voice-title"
          className={cn(
            "relative isolate w-full max-w-2xl shrink-0 overflow-hidden rounded-[1.75rem] border",
            "border-border/70 bg-card/80 px-4 py-3 shadow-[0_18px_55px_-30px_rgb(var(--scrim-rgb)/0.8)]",
            "backdrop-blur-xl sm:px-6 sm:py-4",
            active &&
              "border-primary/35 shadow-[0_18px_60px_-28px_rgb(var(--accent-rgb)/0.5)]",
          )}
        >
          <span
            aria-hidden
            className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl"
          />
          <div className="relative flex flex-col items-center gap-3 sm:flex-row sm:gap-6">
            <div className="relative grid h-32 w-32 shrink-0 place-items-center sm:h-36 sm:w-36">
              <button
                type="button"
                data-testid="deck-orb"
                onClick={() => void toggleCall()}
                disabled={disabled}
                aria-pressed={active}
                aria-label={
                  active
                    ? t("agentic_grid.voice_bubble.hang_up")
                    : t("agentic_grid.voice_bubble.talk").replace(
                        "{0}",
                        assistantName,
                      )
                }
                title={
                  active
                    ? t("agentic_grid.voice_bubble.hang_up_title")
                    : t("agentic_grid.voice_bubble.talk_title").replace(
                        "{0}",
                        assistantName,
                      )
                }
                className={cn(
                  "relative z-10 rounded-full outline-none transition-[transform,opacity] duration-500",
                  "drop-shadow-[0_12px_24px_rgba(0,0,0,0.32)]",
                  "hover:scale-[1.025] active:scale-[0.98] focus-visible:ring-2 focus-visible:ring-primary/60",
                  "motion-reduce:transform-none disabled:cursor-wait disabled:opacity-70",
                )}
              >
                <VoiceOrb state={voiceState} size={116} />
              </button>
            </div>

            <div className="flex min-w-0 flex-1 flex-col items-center text-center sm:items-start sm:text-left">
              <div className="mb-1.5 inline-flex items-center gap-2 rounded-full border border-border/60 bg-background/55 px-2.5 py-1">
                <span
                  aria-hidden
                  data-state={voiceState}
                  className="agentic-voice-state-dot"
                />
                <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  {status}
                </span>
              </div>
              <h2
                id="deck-voice-title"
                className="text-lg font-semibold tracking-tight text-foreground sm:text-xl"
              >
                {headline}
              </h2>
              <p
                data-testid="deck-orb-caption"
                aria-live="polite"
                className={cn(
                  "mt-1 line-clamp-2 min-h-10 max-w-md text-sm leading-relaxed text-muted-foreground",
                  liveTranscript && "text-foreground/80",
                )}
              >
                {detail}
              </p>
              <button
                type="button"
                data-testid="deck-voice-action"
                onClick={() => void toggleCall()}
                disabled={disabled}
                aria-pressed={active}
                className={cn(
                  "mt-2.5 inline-flex min-h-9 items-center gap-2 rounded-full border px-4 py-2 text-xs font-semibold",
                  "transition-[background-color,color,border-color,transform] hover:-translate-y-px active:translate-y-0",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-wait disabled:opacity-60",
                  active
                    ? "border-destructive/30 bg-destructive/10 text-destructive hover:bg-destructive/15"
                    : "border-primary/30 bg-primary text-primary-foreground shadow-sm hover:bg-primary/90",
                )}
              >
                {active ? (
                  <MicOff className="h-3.5 w-3.5 shrink-0" />
                ) : connecting ? (
                  <AudioLines className="h-3.5 w-3.5 shrink-0 animate-pulse motion-reduce:animate-none" />
                ) : (
                  <Mic className="h-3.5 w-3.5 shrink-0" />
                )}
                {active
                  ? t("agentic_grid.voice_bubble.mic_stop")
                  : connecting
                    ? t("voice_state.connecting")
                    : t("agentic_grid.voice_bubble.mic_start").replace(
                        "{0}",
                        assistantName,
                      )}
              </button>
            </div>
          </div>
        </section>

        {/* The room. One card per agent, laid out as a table rather than a list. */}
        {agents.length === 0 ? (
          <div
            data-testid="deck-empty"
            className="flex flex-col items-center gap-3 pt-6 text-center"
          >
            <p className="max-w-sm text-sm text-muted-foreground">
              No agents in this workspace yet. Open one and you can start
              handing out work by voice.
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
            className="grid w-full max-w-6xl grid-cols-1 gap-4 pb-4 sm:grid-cols-2 lg:grid-cols-3"
          >
            {agents.map((entry) => (
              <AgentCard
                key={entry.name}
                name={entry.name}
                agent={entry.agent}
                agentLabel={entry.agentLabel}
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
