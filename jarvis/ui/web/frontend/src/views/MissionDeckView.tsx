import { useMemo, type ReactNode } from "react";
import { useEventStore, type VoiceState } from "@/store/events";
import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { voiceInputLevelRef } from "@/lib/voiceInputLevel";
import { DeckDock } from "@/components/deck/DeckDock";
import { DeckOrb } from "@/components/deck/DeckOrb";
import {
  FlowCard,
  IdeGridCard,
  OutputsCard,
  RunsCard,
  TerminalsCard,
  WikiCard,
} from "@/components/deck/DeckActivityCards";
import {
  ApiStatsCard,
  AppShotCard,
  ComputerUseCard,
  LiveCounter,
} from "@/components/deck/DeckSignalCards";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { writeDeckMode } from "@/lib/deckMode";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The mission deck — the front page.
 *
 * One stage that shows everything the assistant is and does, without a
 * single navigation step. You talk to it by voice; the typed composer lives
 * on the classic chat surface, not here (maintainer decision 2026-08-18).
 * The layout follows the maintainer's sketch of 2026-08-17:
 *
 *   ┌ voice bars · live word counter ───────────────── name · brain · switch ┐
 *   │ dock │ flow / runs        ORB        computer-use / api stats           │
 *   │      │ ide grid   terminals · wiki   app-shot / outputs                 │
 *   └──────┴─────────────────────────────────────────────────────────────────┘
 *
 * Every card is a window onto a section and reads that section's own data;
 * clicking its eyebrow jumps there. The dock on the left is the ONLY place
 * the sections are listed — the sidebar steps aside while the deck is up
 * (App.tsx), so nothing appears twice.
 *
 * Nothing on this screen is invented. A number the store cannot source is
 * not shown, because a deck whose figures are decorative teaches the person
 * reading it to stop.
 */
export function MissionDeckView({
  headerAccessory,
}: {
  /** The surface switch, handed down by the shell that owns the mode. */
  headerAccessory?: ReactNode;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const brainProvider = useEventStore((s) => s.brainProvider);
  const brainModel = useEventStore((s) => s.brainModel);
  const connected = useEventStore((s) => s.connected);
  const voiceState = useEventStore((s) => s.voiceState);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const thinkingSteps = useEventStore((s) => s.thinkingSteps);
  const messages = useEventStore((s) => s.messages);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { warming } = useVoiceReadiness();

  const running = useMemo(
    () => thinkingSteps.filter((s) => s.status === "active"),
    [thinkingSteps],
  );
  const busy = chatThinking || running.length > 0 || voiceState === "thinking";

  const mood: DeckMood = !connected
    ? "offline"
    : voiceState === "error"
      ? "fail"
      : voiceState === "listening"
        ? "listening"
        : voiceState === "speaking"
          ? "speaking"
          : busy
            ? "busy"
            : "ready";

  const lastAssistant = useMemo(
    () => [...messages].reverse().find((m) => m.role === "assistant"),
    [messages],
  );
  const headline = lastAssistant
    ? lastAssistant.content
    : warming
      ? t("deck.warming")
      : t("deck.idle_headline");

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header strip: voice bars + live counter left, identity + brain right. */}
      <header className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-border/70 px-4 py-2">
        <div className="flex items-center gap-3">
          <VoiceWaveform
            levelRef={voiceInputLevelRef}
            phase={waveformPhase(voiceState, connected)}
            count={14}
            className="h-6"
          />
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">
            {t(`deck.mood_${mood}`)}
          </span>
        </div>

        <LiveCounter className="ml-2" />

        <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-1">
          <span className="font-display text-sm font-bold uppercase tracking-[0.18em]">
            {assistantName}
          </span>
          <HeaderStat
            label={t("deck.stat_brain")}
            value={brainProvider || "—"}
            hot
            onClick={() => setActiveSection("apikeys")}
          />
          <HeaderStat
            label={t("deck.stat_model")}
            value={brainModel || "—"}
            onClick={() => setActiveSection("apikeys")}
          />
          {headerAccessory}
        </div>
      </header>

      {/* Stage: dock on the left edge, cards around the orb. */}
      <div className="flex min-h-0 flex-1">
        <DeckDock className="border-r border-border/60" />

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-[minmax(200px,3fr)_minmax(0,6fr)_minmax(200px,3fr)] lg:grid-rows-[minmax(0,1fr)_minmax(0,0.85fr)] lg:overflow-hidden">
          {/* Row 1 — left: flow over runs */}
          <div className="flex min-h-0 flex-col gap-3">
            <FlowCard steps={thinkingSteps} className="min-h-0 flex-1" />
            <RunsCard className="max-h-[38%] shrink-0" />
          </div>

          {/* Row 1 — centre: the orb and the headline */}
          <div className="flex min-h-0 flex-col items-center justify-center gap-3 px-2 text-center">
            <DeckOrb steps={running} busy={busy} size={250} />
            <p className="max-w-[46ch] text-pretty text-sm leading-relaxed text-foreground">
              {headline}
            </p>
          </div>

          {/* Row 1 — right: computer use over api stats */}
          <div className="flex min-h-0 flex-col gap-3">
            <ComputerUseCard className="min-h-0 flex-1" />
            <ApiStatsCard className="shrink-0" />
          </div>

          {/* Row 2 — left: the coding workspace, shrunk */}
          <IdeGridCard className="min-h-[9rem]" />

          {/* Row 2 — centre: terminals and the wiki side by side */}
          <div className="grid min-h-[9rem] grid-cols-1 gap-3 sm:grid-cols-[3fr_2fr]">
            <TerminalsCard className="min-h-0" />
            <WikiCard className="min-h-0" />
          </div>

          {/* Row 2 — right: the last capture over the outputs */}
          <div className="flex min-h-[9rem] flex-col gap-3">
            <AppShotCard className="min-h-0 flex-1" />
            <OutputsCard className="max-h-[48%] shrink-0" />
          </div>
        </div>
      </div>
    </div>
  );
}

type DeckMood = "ready" | "busy" | "listening" | "speaking" | "fail" | "offline";

function waveformPhase(state: VoiceState, connected: boolean): WaveformPhase {
  if (!connected) return "idle";
  switch (state) {
    case "connecting":
      return "connecting";
    case "listening":
      return "listening";
    case "thinking":
      return "working";
    case "speaking":
      return "speaking";
    case "error":
      return "error";
    default:
      return "idle";
  }
}

function HeaderStat({
  label,
  value,
  hot,
  onClick,
}: {
  label: string;
  value: string;
  hot?: boolean;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span className={cn("font-mono text-xs tabular-nums", hot ? "text-primary" : "text-foreground")}>
        {value}
      </span>
    </>
  );
  if (!onClick) return <div className="flex items-baseline gap-1.5 whitespace-nowrap">{body}</div>;
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-baseline gap-1.5 whitespace-nowrap transition-colors hover:[&>span]:text-primary"
    >
      {body}
    </button>
  );
}

/**
 * The escape hatch, rendered by the shell above both surfaces.
 *
 * Kept in this module so the deck and the switch that leaves it stay together.
 */
export function SurfaceSwitch({
  mode,
  onChange,
}: {
  mode: "deck" | "classic";
  onChange: (mode: "deck" | "classic") => void;
}) {
  const t = useT();
  const next = mode === "deck" ? "classic" : "deck";
  const label = t(next === "classic" ? "deck.switch_to_classic" : "deck.switch_to_deck");

  return (
    <button
      type="button"
      onClick={() => {
        writeDeckMode(next);
        onChange(next);
      }}
      title={label}
      className="flex items-center gap-1.5 rounded-lg border border-border px-2 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
    >
      {label}
    </button>
  );
}
