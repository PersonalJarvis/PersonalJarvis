import { useMemo, type ReactNode } from "react";
import { useEventStore, type VoiceState } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { voiceInputLevelRef } from "@/lib/voiceInputLevel";
import { DockRail } from "@/components/layout/DockRail";
import { DeckOrb } from "@/components/deck/DeckOrb";
import { HudLamp } from "@/components/deck/HudFrame";
import {
  IdeGridCard,
  OutputsCard,
  RunsCard,
  TerminalsCard,
} from "@/components/deck/DeckActivityCards";
import { ApiStatsCard, CaptureCard, LiveCounter } from "@/components/deck/DeckSignalCards";
import { LogCard } from "@/components/deck/DeckLogCard";
import { TurnCard } from "@/components/deck/DeckTurnCard";
import { WikiCard } from "@/components/deck/DeckWiki";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useWakeWord } from "@/hooks/useWakeWord";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { writeDeckMode } from "@/lib/deckMode";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The mission deck — the front page.
 *
 * One stage that shows everything the assistant is and does, without a
 * single navigation step. You talk to it by voice; the typed composer lives
 * on the classic chat surface, not here (maintainer decision 2026-08-18).
 * The layout follows the maintainer's sketch of 2026-08-17 (photographed
 * rotated; read upright):
 *
 *   ┌ voice bars · lamps · live counter ─────────── name · brain · switch ┐
 *   │ dock │ [log — the     [response][api]        [ WIKI — 3D, tall    ] │
 *   │      │  terminal]     (      ORB      )       [                    ] │
 *   │      │ [outputs][run]   [capture]            [terminals] [ide grid] │
 *   └──────┴───────────────────────────────────────────────────────────────┘
 *
 * Two of the sketch's cards were re-thought on 2026-08-18 (maintainer): the
 * "right now" trace doubled the run inspector, which the small RUNS card
 * already opens, so the tall left slot is now the LOG — a terminal of what
 * the assistant heard, thought, did and said, with timings, that is never
 * blank; and the live Computer-Use screen mirror gave way to the RESPONSE
 * instrument — the turn's phases and how long each stage took. Computer Use
 * still shows: as the "control" lamp, as the response card's act phase, and
 * as lines in the log.
 *
 * Every card is a window onto a section and reads that section's own data;
 * its title jumps there. The dock on the left is the ONLY place the sections
 * are listed — the sidebar steps aside while the deck is up (App.tsx).
 *
 * The frames differ on purpose (HudFrame.tsx): brackets for pictures and the
 * map, chamfers for readouts, rails for streams — a deck of instruments, not
 * a grid of identical boxes. Nothing on this screen is invented: a number the
 * store cannot source is not shown.
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
  const voiceReady = useEventStore((s) => s.voiceReady);
  const voiceState = useEventStore((s) => s.voiceState);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const thinkingSteps = useEventStore((s) => s.thinkingSteps);
  const messages = useEventStore((s) => s.messages);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const cuActive = useDeckStore((s) => s.cu.active);
  const wordsSession = useDeckStore((s) => s.wordsSession);
  const turnPhase = useDeckStore((s) => s.turn.phase);
  const { warming } = useVoiceReadiness();
  // The orb is the click-shaped wake word: the same start/stop path the
  // classic surface's voice bubble uses, so both do exactly one thing.
  const { active: callActive, busy: callBusy, connecting, toggleCall } = useVoiceCall();
  // The idle line names the real phrase ("Hey Nova"), never "your wake word":
  // a person who just installed the app should read what to say. Until the
  // phrase is known the name it derives from stands in.
  const { config: wakeConfig } = useWakeWord();
  const wakePhrase = wakeConfig?.phrase.trim() || assistantName;

  const running = useMemo(
    () => thinkingSteps.filter((s) => s.status === "active"),
    [thinkingSteps],
  );
  // The deck's own turn counts too: the text chat's thinking flag never arms
  // for a voice turn, and the orb must sweep for those as well.
  const busy =
    chatThinking ||
    running.length > 0 ||
    voiceState === "thinking" ||
    turnPhase === "think" ||
    turnPhase === "act";

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
      : t("deck.idle_headline").replace("{0}", wakePhrase);
  const orbPressLabel = callActive
    ? t("deck.orb_hangup")
    : t("deck.orb_call").replace("{0}", wakePhrase);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Status bar: voice bars, lamps and the live counter left; identity,
          brain and the surface switch right. A thin bracket rule underneath. */}
      <header className="relative flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2">
        <div className="flex items-center gap-3">
          <VoiceWaveform
            levelRef={voiceInputLevelRef}
            phase={waveformPhase(voiceState, connected)}
            count={14}
            className="h-6"
          />
          <span
            className={cn(
              "font-mono text-[10px] uppercase tracking-[0.18em]",
              mood === "fail" ? "text-destructive" : "text-primary",
            )}
          >
            {t(`deck.mood_${mood}`)}
          </span>
        </div>

        {/* Lamp row: the things that have to be true for a voice turn. */}
        <div className="flex items-center gap-3 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
          <Lamp on={connected} label={t("deck.lamp_link")} />
          <Lamp on={voiceReady} label={t("deck.lamp_voice")} />
          <Lamp on={Boolean(brainProvider)} label={t("deck.lamp_brain")} />
          <Lamp on={cuActive} label={t("deck.lamp_cu")} />
        </div>

        <LiveCounter />

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

        <svg
          className="pointer-events-none absolute inset-x-3 bottom-0 h-2 w-[calc(100%-1.5rem)]"
          preserveAspectRatio="none"
          viewBox="0 0 100 8"
          aria-hidden
        >
          <path
            d="M 0 0 V 8 M 0 7.5 H 100 M 100 0 V 8"
            fill="none"
            stroke="hsl(var(--primary))"
            strokeWidth={1}
            opacity={0.5}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </header>

      {/* Stage */}
      <div className="flex min-h-0 flex-1">
        <DockRail />

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-[minmax(200px,3fr)_minmax(0,6fr)_minmax(240px,4fr)] lg:grid-rows-[minmax(0,1fr)_minmax(0,0.6fr)] lg:overflow-hidden">
          {/* LEFT top: the log — the terminal of the session */}
          <LogCard className="min-h-0" />

          {/* CENTRE top: the response instrument + api on a strip, the orb underneath */}
          <div className="flex min-h-0 flex-col gap-3">
            <div className="grid shrink-0 grid-cols-2 gap-3" style={{ height: "36%" }}>
              <TurnCard className="min-h-0" />
              <ApiStatsCard className="min-h-0" />
            </div>
            <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-2 text-center">
              <DeckOrb
                steps={running}
                busy={busy}
                size={236}
                readouts={{
                  nw: t(`deck.mood_${mood}`),
                  ne: `${running.length} ${t("deck.orb_steps")}`,
                  sw: brainProvider || "—",
                  se: `${wordsSession} ${t("deck.orb_words")}`,
                }}
                onPress={() => void toggleCall()}
                pressLabel={orbPressLabel}
                pressDisabled={callBusy || connecting || !connected}
              />
              <p
                className={cn(
                  "max-w-[44ch] text-pretty text-sm leading-relaxed",
                  lastAssistant ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {headline}
              </p>
            </div>
          </div>

          {/* RIGHT top: the wiki, in space, tall */}
          <WikiCard className="min-h-0" />

          {/* LEFT bottom: outputs and runs */}
          <div className="grid min-h-[8rem] grid-cols-2 gap-3">
            <OutputsCard className="min-h-0" />
            <RunsCard className="min-h-0" />
          </div>

          {/* CENTRE bottom: the last capture (briefly), then the ledger; centred and not too wide */}
          <div className="flex min-h-[8rem] items-stretch justify-center">
            <CaptureCard className="w-full max-w-[28rem]" />
          </div>

          {/* RIGHT bottom: terminals and the coding workspace */}
          <div className="grid min-h-[8rem] grid-cols-2 gap-3">
            <TerminalsCard className="min-h-0" />
            <IdeGridCard className="min-h-0" />
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

function Lamp({ on, label }: { on: boolean; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <HudLamp on={on} />
      <span className={on ? "text-foreground/80" : undefined}>{label}</span>
    </span>
  );
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
      className="flex items-center gap-1.5 border border-border px-2 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
      style={{ clipPath: "polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px)" }}
    >
      {label}
    </button>
  );
}
